"""Transcrição num servidor à parte: os dois formatos de API, com o servidor falso do httpx."""

from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from test_web import fake_runner

from c3parr.asr.remote import RemoteASRConfig, RemoteTranscriber
from c3parr.core.config import Settings
from c3parr.core.errors import TransientFailure
from c3parr.interfaces.transcription import AudioRef, TranscriptionRequest
from c3parr.jobs.queue import JobQueue
from c3parr.library.store import Library
from c3parr.web.app import components_for, create_app

WEBSERVICE_JSON = {
    "language": "fr",
    "segments": [
        {"start": 1.0, "end": 2.5, "text": " J'avoue.", "avg_logprob": -0.2,
         "words": [{"word": " J", "start": 1.0, "end": 1.2, "probability": 0.9},
                   {"word": "'avoue.", "start": 1.2, "end": 2.4, "probability": 0.8}]},
        {"start": 3.0, "end": 4.0, "text": "  ", "words": []},
    ],
}  # fmt: skip
OPENAI_JSON = {
    "language": "french",
    "segments": [{"start": 1.0, "end": 2.5, "text": "Bonjour Nawal."}],
    "words": [{"word": "Bonjour", "start": 1.0, "end": 1.5},
              {"word": "Nawal.", "start": 1.6, "end": 2.4}],
}  # fmt: skip


@pytest.fixture
def queue(tmp_path: Path) -> JobQueue:
    return JobQueue(tmp_path / "fila.db")


def audio(tmp_path: Path) -> AudioRef:
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF fake")
    return AudioRef(path=str(wav), sha256="x", duration=60)


def server(response: httpx.Response, seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response

    return httpx.MockTransport(handler)


def transcriber(flavor: Any, transport: httpx.MockTransport, **cfg: Any) -> RemoteTranscriber:
    config = RemoteASRConfig(base_url="http://whisper:9000/", **cfg)
    return RemoteTranscriber(config, flavor, transport=transport)


def test_webservice_sends_vad_and_language_and_reads_word_tokens(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    asr = transcriber("asr-webservice", server(httpx.Response(200, json=WEBSERVICE_JSON), seen))
    t = asr.transcribe(audio(tmp_path), TranscriptionRequest(language="fr"), lambda *_: None)
    q = seen[0].url.params
    assert seen[0].url.path == "/asr" and q["vad_filter"] == "true" and q["language"] == "fr"
    assert t.language == "fr" and len(t.segments) == 1  # o segmento vazio sai
    assert [w.w for w in t.segments[0].words] == ["J'avoue."]  # token sem espaço cola no anterior


def test_openai_format_puts_loose_words_in_their_segment(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    asr = transcriber("openai", server(httpx.Response(200, json=OPENAI_JSON), seen),
                      model="large-v3", api_key="k")  # fmt: skip
    t = asr.transcribe(audio(tmp_path), TranscriptionRequest(), lambda *_: None)
    body = seen[0].content
    assert seen[0].url.path == "/audio/transcriptions"
    assert seen[0].headers["authorization"] == "Bearer k"
    assert b"verbose_json" in body and b"large-v3" in body
    assert b'name="language"' not in body  # sem idioma conhecido, o servidor detecta
    assert t.language == "fr"  # "french" vira o código
    assert [w.w for w in t.segments[0].words] == ["Bonjour", "Nawal."]


def test_server_error_is_transient(tmp_path: Path) -> None:
    asr = transcriber("openai", server(httpx.Response(503, text="busy"), []))
    with pytest.raises(TransientFailure, match="503"):
        asr.transcribe(audio(tmp_path), TranscriptionRequest(), lambda *_: None)


def test_health() -> None:
    models = httpx.Response(200, json={"data": [{"id": "whisper-1"}]})
    assert transcriber("openai", server(models, [])).health().detail == "whisper-1"
    assert not transcriber("asr-webservice", server(httpx.Response(401), [])).health().ok
    no_url = RemoteTranscriber(RemoteASRConfig(), "openai").health()
    assert not no_url.ok and "URL" in no_url.detail


def test_remote_mode_replaces_the_local_engine_and_settings_save_it(
    tmp_path: Path, queue: JobQueue
) -> None:
    lib = Library(tmp_path / "c3parr.db")
    app = create_app(queue=queue, runner=fake_runner(), start_worker=False, library=lib)
    with TestClient(app) as client:
        page = client.get("/settings/integrations").text
        assert 'name="asr_mode"' in page and 'value="local" selected' in page  # padrão: local
        bad = client.post("/settings/integrations", data={"asr_mode": "openai", "asr_url": ""})
        assert bad.status_code == 422 and lib.config().asr_mode == "local"
        client.post("/settings/integrations",
                    data={"asr_mode": "asr-webservice", "asr_url": "http://whisper:9000"})  # fmt: skip
        assert "asr-webservice" not in client.get("/settings/engines").text  # só motores locais
        assert "Transcription on a separate server" in client.get("/system/status").text
    spec = components_for(Settings(), lib.config())["transcricao"]
    assert spec.provider == "asr-webservice" and spec.params == {"base_url": "http://whisper:9000"}


def test_vad_word_times_are_moved_to_the_segment_time() -> None:
    from c3parr.asr.faster_whisper import FasterWhisperConfig
    from c3parr.asr.remote import parse_response

    # whisper-server com VAD: palavras no tempo do áudio sem a música cortada, segmento no real
    data = {"segments": [{"start": 133.2, "end": 135.0, "text": "Mme Nawal",
                          "words": [{"word": " Mme", "start": 5.6, "end": 5.7},
                                    {"word": " Nawal", "start": 5.7, "end": 6.0}]}]}  # fmt: skip
    seg = parse_response(data, "fr", FasterWhisperConfig())[0]
    assert [(w.w, w.start) for w in seg.words] == [("Mme", 133.2), ("Nawal", 133.3)]
