"""O servidor do c3parr-cuda-processor: recebe áudio e pedidos, roda uma tarefa por vez.

Cada tarefa roda num processo filho (`concert.worker`), como no c3parr: a memória volta inteira no
fim. O mesmo pedido feito de novo (o c3parr reiniciou no meio, a tarefa voltou à fila) pega a
tarefa que já existe, rodando ou pronta, em vez de começar outra.
"""

import contextlib
import hashlib
import json
import logging
import os
import secrets
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse

from c3parr.concert import device as hw
from c3parr.concert import worker
from c3parr.core.errors import ProviderError
from c3parr.processor.protocol import (
    DEFAULT_PORT,
    JOBS,
    KEY_ENV,
    NAME,
    SERVICE,
    SHA,
    SUFFIX,
    Info,
    Job,
    JobIn,
    JobOut,
)

log = logging.getLogger("c3parr.processor")
KEEP_S = 24 * 3600.0
"""Um arquivo sem uso por um dia sai (um show inteiro passa pelo processador em minutos)."""
MAX_BYTES = 20 * 2**30
FINISHED_KEEP_S = 6 * 3600.0
MAX_UPLOAD = int(float(os.environ.get("C3PARR_PROCESSOR_MAX_UPLOAD_GB", "16")) * 2**30)

Call = Callable[..., Any]


class Store:
    """Os arquivos pelo sha256 (`files/<sha><extensão>`) e as escutas do encaixe por áudio."""

    def __init__(self, root: Path, keep_s: float = KEEP_S, max_bytes: int = MAX_BYTES) -> None:
        self.files = root / "files"
        self.emissions = root / "emissions"
        self.work = root / "work"
        for d in (self.files, self.emissions, self.work):
            d.mkdir(parents=True, exist_ok=True)
        self.keep_s = keep_s
        self.max_bytes = max_bytes
        self._lock = threading.Lock()

    def find(self, sha: str) -> Path | None:
        for path in self.files.glob(f"{sha}*"):
            if not path.name.endswith(".part"):
                with contextlib.suppress(OSError):
                    os.utime(path)  # usado agora: a limpeza conta a partir daqui
                return path
        return None

    def adopt(self, path: Path, sha: str) -> Path:
        """Um arquivo feito aqui (a voz isolada) passa a ser servido pelo sha."""
        with self._lock:
            found = self.find(sha)
            if found is not None:
                path.unlink(missing_ok=True)
                return found
            dest = self.files / f"{sha}{path.suffix}"
            shutil.move(str(path), dest)
            return dest

    def emission_dir(self, sha: str) -> Path:
        d = self.emissions / sha
        d.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.utime(d)
        return d

    def clean(self, busy: frozenset[str] = frozenset(), now: float | None = None) -> int:
        """Apaga o que está sem uso há `keep_s` e, passando do teto, o mais antigo. Devolve quantos
        itens saíram. `busy`: os sha da tarefa que roda agora, que não saem."""
        now = time.time() if now is None else now
        removed = 0
        with self._lock:
            entries = []
            for path in [*self.files.iterdir(), *self.emissions.iterdir()]:
                if path.name[:64] in busy or path.name.endswith(".part"):
                    continue
                try:
                    mtime = path.stat().st_mtime
                    inside = path.rglob("*") if path.is_dir() else (path,)
                    size = sum(p.stat().st_size for p in inside if p.is_file())
                except OSError:
                    continue
                entries.append((mtime, size, path))
            entries.sort()
            total = sum(size for _, size, _ in entries)
            for mtime, size, path in entries:
                if now - mtime > self.keep_s or total > self.max_bytes:
                    if path.is_dir():
                        shutil.rmtree(path, ignore_errors=True)
                    else:
                        path.unlink(missing_ok=True)
                    total -= size
                    removed += 1
        return removed


class Upload:
    """Grava em `.part` conferindo o sha256 enquanto chega; só vira arquivo se bater."""

    def __init__(self, store: Store, sha: str, suffix: str) -> None:
        self.sha = sha
        self.part = store.files / f"{sha}.{uuid.uuid4().hex[:8]}.part"
        self.dest = store.files / f"{sha}{suffix}"
        self.digest = hashlib.sha256()
        self.size = 0
        self.f = self.part.open("wb")

    def write(self, chunk: bytes) -> None:
        self.size += len(chunk)
        if self.size > MAX_UPLOAD:
            raise HTTPException(413, f"larger than {MAX_UPLOAD // 2**30} GB")
        self.digest.update(chunk)
        self.f.write(chunk)

    def finish(self) -> Path:
        self.f.close()
        if self.digest.hexdigest() != self.sha:
            self.part.unlink(missing_ok=True)
            raise HTTPException(400, "the file does not match its sha256")
        self.part.replace(self.dest)
        return self.dest

    def abort(self) -> None:
        self.f.close()
        self.part.unlink(missing_ok=True)


@dataclass
class State:
    id: str
    key: str
    job: JobIn
    status: str = "queued"
    progress: float = 0.0
    message: str = "waiting for the CUDA processor"
    result: Any = None
    error: dict[str, str] | None = None
    finished_at: float | None = None
    proc: subprocess.Popen[str] | None = field(default=None, repr=False)


class Runner:
    """A fila do processador: uma tarefa por vez, na ordem em que chegaram."""

    def __init__(self, store: Store, jobs: Mapping[str, Job] = JOBS,
                 call: Call = worker.call) -> None:  # fmt: skip
        self.store = store
        self.jobs = dict(jobs)
        self.call = call
        self._states: dict[str, State] = {}
        self._cv = threading.Condition()
        self._stop = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="processor", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify_all()
            procs = [s.proc for s in self._states.values() if s.proc is not None]
        for proc in procs:
            with contextlib.suppress(OSError):
                proc.kill()

    def submit(self, job: JobIn) -> State:
        spec = self.jobs.get(job.method)
        if spec is None:
            raise HTTPException(400, f"unknown method {job.method}")
        if "model" in job.params and not NAME.match(str(job.params["model"])):
            raise HTTPException(400, "invalid model name")
        if not SHA.match(job.audio) or self.store.find(job.audio) is None:
            raise HTTPException(409, "send the audio first")
        job = job.model_copy(
            update={"params": {k: v for k, v in job.params.items() if k in spec.params}}
        )
        key = hashlib.sha256(
            json.dumps(job.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()
        with self._cv:
            for state in self._states.values():
                if state.key == key and state.status != "failed":
                    return state
            state = State(id=uuid.uuid4().hex[:12], key=key, job=job)
            self._states[state.id] = state  # dict guarda a ordem de chegada
            self._cv.notify_all()
        return state

    def view(self, job_id: str) -> JobOut:
        with self._cv:
            state = self._states.get(job_id)
            if state is None:
                raise HTTPException(404, "no such job")
            ahead = 0
            if state.status == "queued":
                for other in self._states.values():
                    if other is state:
                        break
                    ahead += other.status in ("queued", "running")
            message = state.message
            if ahead:
                message = f"waiting for the CUDA processor ({ahead} ahead)"
            return JobOut(id=state.id, status=state.status,
                          progress=round(state.progress, 4), message=message, ahead=ahead,
                          result=state.result, error=state.error)  # fmt: skip

    def load(self) -> tuple[bool, int]:
        with self._cv:
            busy = any(s.status == "running" for s in self._states.values())
            queued = sum(1 for s in self._states.values() if s.status == "queued")
        return busy, queued

    def _next(self) -> State | None:
        with self._cv:
            while not self._stop:
                for state in self._states.values():
                    if state.status == "queued":
                        state.status, state.message = "running", "starting"
                        return state
                if not self._cv.wait(timeout=3600):  # uma hora parado: limpa
                    self._forget_old()
                    self.store.clean()
            return None

    def _forget_old(self) -> None:
        now = time.time()
        old = [i for i, s in self._states.items()
               if s.finished_at is not None and now - s.finished_at > FINISHED_KEEP_S]  # fmt: skip
        for job_id in old:
            del self._states[job_id]

    def _loop(self) -> None:
        while (state := self._next()) is not None:
            self._run(state)
            with self._cv:
                self._forget_old()
            self.store.clean()

    def _run(self, state: State) -> None:
        spec = self.jobs[state.job.method]
        work = self.store.work / state.id
        t0 = time.monotonic()

        def progress(fraction: float, message: str) -> None:
            state.progress, state.message = fraction, message

        def started(proc: subprocess.Popen[str]) -> None:
            state.proc = proc

        try:
            audio = self.store.find(state.job.audio)
            if audio is None:
                raise ProviderError("the audio was removed before the job started")
            self.store.clean(busy=frozenset({state.job.audio}))
            args = self._args(state.job, audio, work)
            result = self.call(spec.interface, spec.provider, dict(state.job.params),
                               state.job.method, args, progress, started=started)  # fmt: skip
            if state.job.method == "separate":
                self.store.adopt(Path(result["path"]), result["sha256"])
                result = {**result, "path": ""}
            done: dict[str, Any] = {"status": "done", "result": result, "progress": 1.0}
            log.info("%s done in %.0f s", state.job.method, time.monotonic() - t0)
        except ProviderError as e:
            done = {"status": "failed", "error": {"kind": e.kind, "message": e.message}}
            log.warning("%s failed: %s", state.job.method, e.message)
        except Exception as e:  # a tarefa falha com o motivo; o servidor continua
            reason = f"{type(e).__name__}: {e}"
            done = {"status": "failed", "error": {"kind": "erro", "message": reason}}
            log.exception("%s failed", state.job.method)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        with self._cv:
            for name, value in done.items():
                setattr(state, name, value)
            state.proc = None
            state.finished_at = time.time()

    def _args(self, job: JobIn, audio: Path, work: Path) -> dict[str, Any]:
        """Os caminhos do pedido passam a ser os daqui: o áudio guardado, a pasta da tarefa."""
        args: dict[str, Any] = json.loads(json.dumps(job.args))
        if job.method == "separate":
            work.mkdir(parents=True, exist_ok=True)
            args["request"] = {**args.get("request", {}), "video": str(audio),
                               "stream_index": None, "dest": str(work / "voice.wav")}  # fmt: skip
            return args
        args["audio"] = {**args.get("audio", {}), "path": str(audio), "sha256": job.audio}
        if job.method == "place":
            args["request"] = {**args.get("request", {}),
                               "cache_dir": str(self.store.emission_dir(job.audio))}  # fmt: skip
        return args


def describe(runner: Runner) -> Info:
    from c3parr import __version__
    from c3parr.asr.whisper_cpp import WhisperCppConfig, WhisperCppTranscriber

    gpu = hw.gpu()
    whisper = WhisperCppTranscriber(WhisperCppConfig()).health()
    busy, queued = runner.load()
    return Info(version=__version__, cuda=bool(gpu.get("cuda")), gpu=gpu.get("name"),
                gpu_memory_gb=gpu.get("memory_gb"),
                whisper="ready" if whisper.ok and not whisper.detail else whisper.detail,
                busy=busy, queued=queued)  # fmt: skip


def create_app(
    root: Path,
    api_key: str | None = None,
    jobs: Mapping[str, Job] = JOBS,
    call: Call = worker.call,
    info: Callable[[Runner], Info] = describe,
) -> FastAPI:
    store = Store(root)
    runner = Runner(store, jobs, call)

    def allowed(request: Request) -> None:
        if not api_key or request.url.path == "/health":  # o healthcheck do Docker não tem chave
            return
        given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not secrets.compare_digest(given.encode(), api_key.encode()):
            raise HTTPException(401, "wrong API key")

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        runner.start()
        if info is describe:  # a resposta do /v1/info já pronta quando o c3parr perguntar
            threading.Thread(target=hw.gpu, daemon=True).start()
        yield
        runner.stop()

    app = FastAPI(title="c3parr-cuda-processor", lifespan=lifespan,
                  dependencies=[Depends(allowed)])  # fmt: skip
    app.state.runner = runner
    app.state.store = store

    def checked(sha: str) -> str:
        if not SHA.match(sha):
            raise HTTPException(400, "not a sha256")
        return sha

    @app.get("/health")
    def health() -> dict[str, bool | str]:
        """Sem chave: o healthcheck do Docker, e quem quiser saber o que responde nesta porta."""
        return {"ok": True, "service": SERVICE}

    @app.get("/v1/info")
    def describe_here() -> Info:
        return info(runner)

    @app.head("/v1/files/{sha}")
    def has_file(sha: str) -> Response:
        return Response(status_code=200 if store.find(checked(sha)) else 404)

    @app.get("/v1/files/{sha}")
    def get_file(sha: str) -> FileResponse:
        path = store.find(checked(sha))
        if path is None:
            raise HTTPException(404, "no such file")
        return FileResponse(path, media_type="application/octet-stream")

    @app.put("/v1/files/{sha}", status_code=201)
    async def put_file(sha: str, request: Request, suffix: str = ".bin") -> dict[str, int]:
        if not SUFFIX.match(suffix):
            raise HTTPException(400, "bad suffix")
        if store.find(checked(sha)) is not None:
            return {"size": 0}
        upload = Upload(store, sha, suffix)
        try:
            async for chunk in request.stream():
                upload.write(chunk)
        except BaseException:
            upload.abort()
            raise
        upload.finish()
        return {"size": upload.size}

    @app.post("/v1/jobs")
    def post_job(job: JobIn) -> JobOut:
        return runner.view(runner.submit(job).id)

    @app.get("/v1/jobs/{job_id}")
    def get_job(job_id: str) -> JobOut:
        return runner.view(job_id)

    return app


def serve() -> None:
    import uvicorn

    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt)
    root = Path(os.environ.get("C3PARR_PROCESSOR_DIR") or hw.default_cache_dir() / "processor")
    port = int(os.environ.get("C3PARR_PROCESSOR_PORT") or DEFAULT_PORT)
    key = os.environ.get(KEY_ENV) or None
    log.info("c3parr-cuda-processor on port %d, files in %s%s", port, root,
             ", API key required" if key else "")  # fmt: skip
    uvicorn.run(create_app(root, key), host="0.0.0.0", port=port)  # em todas: é serviço de rede
