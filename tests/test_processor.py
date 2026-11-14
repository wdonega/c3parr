"""O c3parr-cuda-processor: o servidor em memória, rodando os providers falsos num processo filho,
e o c3parr falando com ele pelos providers de verdade (whisper-cpp, demucs, wav2vec2)."""

import hashlib
import os
import time
import wave
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from c3parr.asr.whisper_cpp import WhisperCppConfig, WhisperCppTranscriber
from c3parr.concert import worker
from c3parr.concert.demucs import DemucsConfig, DemucsSeparator
from c3parr.concert.wav2vec2 import Wav2Vec2Config, Wav2Vec2Placer
from c3parr.core.errors import TransientFailure, Unavailable
from c3parr.engine.cache import file_sha256
from c3parr.interfaces.lyric_fit import PlaceRequest, Span
from c3parr.interfaces.separation import SeparationRequest
from c3parr.interfaces.transcription import AudioRef, TranscriptionRequest
from c3parr.processor import client as remote
from c3parr.processor.client import Client, ProcessorRef
from c3parr.processor.protocol import Info, Job, JobIn
from c3parr.processor.server import Runner, Store, create_app

URL = "http://testserver"
FAKE_JOBS = {
    "separate": Job("separacao", "falso", frozenset({"seconds", "spans", "fail"})),
    "place": Job("encaixe", "falso", frozenset({"score", "per_line"})),
    "transcribe": Job("transcricao", "falso", frozenset({"lines", "fail"})),
}
REF = ProcessorRef.model_validate({"url": URL, "api_key": "segredo"})


def fake_info(protocol: int = 1) -> Callable[[Runner], Info]:
    def info(runner: Runner) -> Info:
        busy, queued = runner.load()
        return Info(version="9.9.9", protocol=protocol, cuda=True, gpu="Fake GPU",
                    gpu_memory_gb=12.0, whisper="ready", busy=busy, queued=queued)  # fmt: skip

    return info


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    app = create_app(tmp_path / "processor", api_key="segredo", jobs=FAKE_JOBS,
                     info=fake_info())  # fmt: skip
    with TestClient(app, base_url=URL) as http:

        def factory(ref: ProcessorRef) -> httpx.Client:
            key = ref.api_key.get_secret_value() if ref.api_key else ""
            http.headers["Authorization"] = f"Bearer {key}"
            return http

        monkeypatch.setattr(remote, "http_factory", factory)
        monkeypatch.setattr(remote, "POLL_S", 0.05)
        http.headers["Authorization"] = "Bearer segredo"
        yield http


def voice_file(path: Path, seconds: float = 3.0) -> AudioRef:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\1\0" * int(seconds * 16000))
    return AudioRef(path=str(path), sha256=file_sha256(path), duration=seconds)


def test_the_voice_is_separated_there_and_comes_back_here(
    server: TestClient, tiny_video: Path, tmp_path: Path
) -> None:
    separator = DemucsSeparator(DemucsConfig(processor=REF, memory_limit="2"))
    dest = tmp_path / "work" / "filme.voz.c3parr"
    messages: list[str] = []
    voice = separator.separate(
        SeparationRequest(video=str(tiny_video), dest=str(dest)),
        lambda _, m: messages.append(m),
    )
    assert dest.exists() and file_sha256(dest) == voice.sha256
    assert voice.path == str(dest)
    assert voice.spans == ((0.0, 120.0),)  # o padrão do separador falso, feito do lado de lá
    assert not list(dest.parent.glob("*.send.ogg"))  # o áudio enviado não fica aqui
    assert any("CUDA processor" in m for m in messages)
    # a voz ficou guardada lá: o encaixe não manda de novo
    assert server.head(f"/v1/files/{voice.sha256}").status_code == 200


def test_asking_the_same_job_again_takes_the_one_already_done(
    server: TestClient, tmp_path: Path
) -> None:
    audio = voice_file(tmp_path / "voz.wav")
    request = PlaceRequest(window=(0.0, 3.0), spans=(Span(start=0.0, end=3.0, lines=("a",)),),
                           cache_dir=str(tmp_path / "cache"))  # fmt: skip
    placer = Wav2Vec2Placer(Wav2Vec2Config(processor=REF))
    first = placer.place(audio, request, lambda *_: None)
    again = placer.place(audio, request, lambda *_: None)
    assert first == again and first[0][0].line == 0
    assert len(server.app.state.runner._states) == 1  # type: ignore[attr-defined]


def test_transcription_runs_there_with_the_whisper_cpp_provider(
    server: TestClient, tmp_path: Path
) -> None:
    audio = voice_file(tmp_path / "audio.wav", 10.0)
    engine = WhisperCppTranscriber(WhisperCppConfig(processor=REF, binary="nao-existe-aqui"))
    transcript = engine.transcribe(audio, TranscriptionRequest(language="fr"), lambda *_: None)
    assert [s.text for s in transcript.segments] == ["Bonjour.", "Tu es fatigué ?", "Non, ça va."]
    health = engine.health()
    assert health.ok and "Fake GPU, 12 GB" in health.detail


def test_a_failure_there_comes_back_with_its_kind_and_reason(
    server: TestClient, tmp_path: Path
) -> None:
    audio = voice_file(tmp_path / "audio.wav")
    job = JobIn(method="transcribe", params={"fail": True}, audio=audio.sha256,
                args={"audio": audio.model_dump(mode="json"), "request": {}})  # fmt: skip
    client = Client(REF)
    client.send(Path(audio.path), audio.sha256, lambda *_: None)
    with pytest.raises(Unavailable, match="CUDA processor: fake transcription set to fail"):
        client.run(job, lambda *_: None)


def test_the_processor_only_takes_the_parameters_that_are_its_own(
    server: TestClient, tmp_path: Path
) -> None:
    audio = voice_file(tmp_path / "audio.wav")
    Client(REF).send(Path(audio.path), audio.sha256, lambda *_: None)
    body = {"method": "transcribe", "audio": audio.sha256,
            "params": {"lines": ["oi"], "binary": "/bin/sh", "model_dir": "/"},
            "args": {"audio": audio.model_dump(mode="json"), "request": {}}}  # fmt: skip
    r = server.post("/v1/jobs", json=body)
    state = server.app.state.runner._states[r.json()["id"]]  # type: ignore[attr-defined]
    assert state.job.params == {"lines": ["oi"]}
    bad = {**body, "params": {"model": "../../etc/passwd"}}
    assert server.post("/v1/jobs", json=bad).status_code == 400
    unknown = {**body, "audio": "0" * 64}
    assert server.post("/v1/jobs", json=unknown).status_code == 409


def test_an_upload_that_does_not_match_its_sha_is_refused(server: TestClient) -> None:
    sha = hashlib.sha256(b"certo").hexdigest()
    assert server.put(f"/v1/files/{sha}", content=b"errado").status_code == 400
    assert server.head(f"/v1/files/{sha}").status_code == 404
    assert server.put(f"/v1/files/{sha}", content=b"certo").status_code == 201
    assert server.put(f"/v1/files/{sha}", content=b"certo").json() == {"size": 0}  # já tinha


def test_the_wrong_key_and_another_protocol_are_refused(
    server: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert Client(REF).info().gpu == "Fake GPU"
    # o /health não pede chave (healthcheck do Docker) e diz o que responde na porta
    bare = server.get("/health", headers={"Authorization": ""})
    assert bare.json() == {"ok": True, "service": "c3parr-cuda-processor"}
    assert server.get("/v1/info", headers={"Authorization": ""}).status_code == 401
    wrong = ProcessorRef.model_validate({"url": URL, "api_key": "outra"})
    with pytest.raises(Unavailable, match="refused the API key"):
        Client(wrong).info()
    other = create_app(tmp_path / "outro", info=fake_info(protocol=2))
    with TestClient(other, base_url=URL) as http:
        monkeypatch.setattr(remote, "http_factory", lambda ref: http)
        with pytest.raises(Unavailable, match="protocol 2"):
            Client(ProcessorRef(url=URL)).info()


def test_a_processor_that_does_not_answer_is_a_temporary_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(remote, "http_factory", remote._http)
    with pytest.raises(TransientFailure, match="did not answer"):
        Client(ProcessorRef(url="http://127.0.0.1:9")).info()


def test_the_store_drops_what_was_not_used_for_a_day_and_keeps_the_busy_one(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path, keep_s=3600)
    old, busy, new = ("a" * 64, "b" * 64, "c" * 64)
    for sha in (old, busy, new):
        (store.files / f"{sha}.wav").write_bytes(b"12345")
    store.emission_dir(old)
    now = time.time()
    for path in (store.files / f"{old}.wav", store.files / f"{busy}.wav", store.emissions / old):
        os.utime(path, (now - 7200, now - 7200))
    assert store.clean(busy=frozenset({busy}), now=now) == 2
    assert {p.name[:64] for p in store.files.iterdir()} == {busy, new}
    assert not any(store.emissions.iterdir())
    full = Store(tmp_path / "cheio", max_bytes=6)  # passou do teto: sai o mais antigo
    for n, sha in enumerate((old, new)):
        (full.files / f"{sha}.wav").write_bytes(b"12345")
        os.utime(full.files / f"{sha}.wav", (now - 100 + n, now - 100 + n))
    assert full.clean(now=now) == 1
    assert [p.name[:64] for p in full.files.iterdir()] == [new]


def test_a_large_answer_from_the_child_process_does_not_hang_it(tmp_path: Path) -> None:
    """A transcrição de um show inteiro tem vários MB: maior que o cano entre os processos."""
    audio = voice_file(tmp_path / "audio.wav")
    lines = [f"linha {n} " + "palavra " * 30 for n in range(2000)]
    args = {"audio": audio.model_dump(mode="json"), "request": {}}
    began = time.monotonic()
    done = worker.call("transcricao", "falso", {"lines": lines}, "transcribe", args,
                       lambda *_: None)  # fmt: skip
    assert len(done["segments"]) == 2000
    assert time.monotonic() - began < 60


def test_the_child_process_never_gets_the_processor_key(tmp_path: Path) -> None:
    params = {"seconds": 1.0, "processor": {"url": URL, "api_key": "**********"}}
    args = {"request": {"video": "x", "dest": str(tmp_path / "voz.wav")}}
    done = worker.call("separacao", "falso", params, "separate", args, lambda *_: None)
    assert done["duration"] == 1.0
