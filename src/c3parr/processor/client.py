"""O lado do c3parr: manda o áudio e o pedido ao processador CUDA, acompanha e traz o resultado.

Os providers `whisper-cpp`, `demucs` e `wav2vec2` usam isto quando há processador configurado
(Settings → Integrations); o resultado é o mesmo que o provider daria aqui. O processador fora do
ar é falha temporária: a tarefa tenta de novo depois, e não passa a rodar na CPU daqui.
"""

import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field, SecretStr

from c3parr.audio import ffmpeg
from c3parr.core.errors import (
    InvalidInput,
    ProviderError,
    RateLimited,
    TransientFailure,
    Unavailable,
)
from c3parr.core.models import Transcript
from c3parr.engine.cache import file_sha256
from c3parr.interfaces.lyric_fit import Placed, PlaceRequest
from c3parr.interfaces.separation import SeparationRequest, Voice
from c3parr.interfaces.transcription import AudioRef, TranscriptionRequest
from c3parr.processor.protocol import PROTOCOL, SERVICE, Info, JobIn, JobOut

PROVIDER = "cuda-processor"
LOCAL_ONLY = frozenset({"processor", "model_dir", "binary", "isolate", "memory_limit", "threads",
                        "low_priority"})  # fmt: skip
"""Parâmetros que só valem aqui: o processador usa os dele."""
POLL_S = 2.0
PATIENCE_S = 180.0
"""Quanto tempo sem resposta no meio de uma tarefa antes de desistir (um reinício do processador
cabe aqui: a tarefa é pedida de novo e ele pega a que já fez)."""
ERRORS: dict[str, type[ProviderError]] = {
    cls.kind: cls for cls in (Unavailable, InvalidInput, TransientFailure, RateLimited)
}

Progress = Callable[[float, str], None]


class ProcessorRef(BaseModel):
    """Onde está o processador CUDA. `url` vazio: tudo roda aqui."""

    url: str = ""
    api_key: SecretStr | None = Field(
        None, json_schema_extra={"secret_env": "C3PARR_PROCESSOR_API_KEY"}
    )


HttpFactory = Callable[[ProcessorRef], httpx.Client]


def _http(ref: ProcessorRef) -> httpx.Client:
    headers = {}
    if ref.api_key is not None and ref.api_key.get_secret_value():
        headers["Authorization"] = f"Bearer {ref.api_key.get_secret_value()}"
    return httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0), headers=headers)


http_factory: HttpFactory = _http
"""Os testes trocam por um cliente ligado ao servidor em memória."""


def sent(params: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in params.items() if k not in LOCAL_ONLY}


class Client:
    def __init__(self, ref: ProcessorRef) -> None:
        self.base = ref.url.rstrip("/")
        self.http = http_factory(ref)
        self.poll_s = POLL_S
        self.patience_s = PATIENCE_S

    def _call(self, method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            r = self.http.request(method, self.base + path, **kw)
        except httpx.HTTPError as e:
            raise TransientFailure(
                f"the CUDA processor at {self.base} did not answer ({type(e).__name__})",
                provider=PROVIDER,
            ) from e
        if r.status_code in (401, 403):
            raise Unavailable("the CUDA processor refused the API key", provider=PROVIDER)
        if r.status_code >= 500:
            raise TransientFailure(f"the CUDA processor answered {r.status_code}",
                                   provider=PROVIDER)  # fmt: skip
        return r

    def info(self) -> Info:
        r = self._call("GET", "/v1/info")
        try:
            r.raise_for_status()
            info = Info.model_validate(r.json())
        except (httpx.HTTPError, ValueError) as e:
            raise Unavailable(f"{self.base} is not a {SERVICE} ({r.status_code})",
                              provider=PROVIDER) from e  # fmt: skip
        if info.service != SERVICE:
            raise Unavailable(f"{self.base} is not a {SERVICE}", provider=PROVIDER)
        if info.protocol != PROTOCOL:
            raise Unavailable(
                f"the CUDA processor speaks protocol {info.protocol} and this c3parr {PROTOCOL}: "
                "update both to the same version",
                provider=PROVIDER,
            )
        return info

    def send(self, path: Path, sha: str, progress: Progress) -> None:
        """Manda o arquivo, se o processador ainda não tem."""
        if self._call("HEAD", f"/v1/files/{sha}").status_code == 200:
            return
        size = path.stat().st_size
        mb = size / 2**20

        def chunks() -> Iterator[bytes]:
            done = 0
            last = 0.0
            with path.open("rb") as f:
                while chunk := f.read(1 << 20):
                    done += len(chunk)
                    if time.monotonic() - last > 2:
                        last = time.monotonic()
                        progress(0.0, f"sending the audio to the CUDA processor "
                                      f"({done / 2**20:.0f} of {mb:.0f} MB)")  # fmt: skip
                    yield chunk

        r = self._call("PUT", f"/v1/files/{sha}", params={"suffix": path.suffix or ".bin"},
                       content=chunks(), headers={"Content-Length": str(size)})  # fmt: skip
        if r.status_code not in (200, 201):
            raise TransientFailure(f"the CUDA processor did not take the audio: {r.text[:200]}",
                                   provider=PROVIDER)  # fmt: skip

    def fetch(self, sha: str, dest: Path) -> None:
        part = dest.with_name(dest.name + ".part")
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.http.stream("GET", f"{self.base}/v1/files/{sha}") as r:
                if r.status_code != 200:
                    raise TransientFailure(f"the CUDA processor lost the file {sha[:12]}",
                                           provider=PROVIDER)  # fmt: skip
                with part.open("wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
        except httpx.HTTPError as e:
            part.unlink(missing_ok=True)
            raise TransientFailure(f"could not bring the file from the CUDA processor ({e})",
                                   provider=PROVIDER) from e  # fmt: skip
        if file_sha256(part) != sha:
            part.unlink(missing_ok=True)
            raise TransientFailure("the file from the CUDA processor came broken",
                                   provider=PROVIDER)  # fmt: skip
        part.replace(dest)

    def run(self, job: JobIn, progress: Progress) -> Any:
        """Pede a tarefa e acompanha até o fim. Devolve o resultado (dados de JSON)."""
        body = job.model_dump(mode="json")
        state = self._submit(body)
        quiet_since = time.monotonic()
        while state.status in ("queued", "running"):
            time.sleep(self.poll_s)
            try:
                r = self._call("GET", f"/v1/jobs/{state.id}")
                if r.status_code == 404:  # o processador reiniciou: pede de novo
                    state = self._submit(body)
                else:
                    r.raise_for_status()
                    state = JobOut.model_validate(r.json())
                quiet_since = time.monotonic()
            except (TransientFailure, httpx.HTTPError):
                if time.monotonic() - quiet_since > self.patience_s:
                    raise
                continue
            progress(min(0.99, state.progress), state.message)
        if state.status == "failed":
            error = state.error or {}
            kind = ERRORS.get(error.get("kind", ""), TransientFailure)
            raise kind(f"CUDA processor: {error.get('message', 'failed')}", provider=PROVIDER)
        return state.result

    def _submit(self, body: dict[str, Any]) -> JobOut:
        r = self._call("POST", "/v1/jobs", json=body)
        if r.status_code == 409:
            raise TransientFailure("the CUDA processor lost the audio before starting",
                                   provider=PROVIDER)  # fmt: skip
        if r.status_code >= 400:
            raise InvalidInput(f"the CUDA processor refused the job: {r.text[:300]}",
                               provider=PROVIDER)  # fmt: skip
        return JobOut.model_validate(r.json())


def separate(ref: ProcessorRef, params: dict[str, Any], request: SeparationRequest,
             progress: Progress) -> Voice:  # fmt: skip
    """A voz isolada feita no processador e trazida para `request.dest`."""
    client = Client(ref)
    dest = Path(request.dest)
    audio = dest.with_name(dest.name + ".send.ogg")
    if not audio.exists():  # sobra de uma tentativa anterior: não extrai de novo
        progress(0.0, "preparing the audio for the CUDA processor")
        part = audio.with_name(audio.name + ".part")
        ffmpeg.extract_opus(Path(request.video), request.stream_index, part)
        part.replace(audio)
    sha = file_sha256(audio)
    client.send(audio, sha, progress)
    job = JobIn(method="separate", params=sent(params), audio=sha)
    voice = Voice.model_validate({**client.run(job, progress), "path": str(dest)})
    if not (dest.exists() and file_sha256(dest) == voice.sha256):
        progress(0.99, "bringing the voice from the CUDA processor")
        client.fetch(voice.sha256, dest)
    audio.unlink(missing_ok=True)
    progress(1.0, f"voice separated (CUDA processor, {voice.device})")
    return voice


def place(ref: ProcessorRef, params: dict[str, Any], audio: AudioRef, request: PlaceRequest,
          progress: Progress) -> tuple[tuple[Placed, ...], ...]:  # fmt: skip
    client = Client(ref)
    client.send(Path(audio.path), audio.sha256, progress)
    job = JobIn(method="place", params=sent(params), audio=audio.sha256,
                args={"audio": audio.model_dump(mode="json"),
                      "request": request.model_dump(mode="json")})  # fmt: skip
    done = client.run(job, progress)
    return tuple(tuple(Placed.model_validate(p) for p in group) for group in done)


def transcribe(ref: ProcessorRef, params: dict[str, Any], audio: AudioRef,
               request: TranscriptionRequest, progress: Progress) -> Transcript:  # fmt: skip
    client = Client(ref)
    client.send(Path(audio.path), audio.sha256, progress)
    job = JobIn(method="transcribe", params=sent(params), audio=audio.sha256,
                args={"audio": audio.model_dump(mode="json"),
                      "request": request.model_dump(mode="json")})  # fmt: skip
    return Transcript.model_validate(client.run(job, progress))


def describe(info: Info) -> str:
    """Uma linha para a tela: a GPU e a versão do processador."""
    where = info.gpu or "no NVIDIA GPU"
    if info.gpu and info.gpu_memory_gb:
        where = f"{info.gpu}, {info.gpu_memory_gb:g} GB"
    return f"{where}; {SERVICE} {info.version}"
