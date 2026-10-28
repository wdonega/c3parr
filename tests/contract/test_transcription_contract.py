"""Bateria de contrato da interface de transcrição: todo provider precisa passar."""

import shutil
import subprocess
import wave

import pytest

from c3parr.core.job import ComponentSpec
from c3parr.interfaces import transcription
from c3parr.interfaces.transcription import AudioRef, TranscriptionRequest
from c3parr.providers.registry import build_factories

PROVIDERS = [
    pytest.param("falso", id="falso"),
    pytest.param("faster-whisper", id="faster-whisper", marks=pytest.mark.models),
]


@pytest.fixture(scope="module")
def french_speech(tmp_path_factory: pytest.TempPathFactory) -> AudioRef:
    """Fala francesa sintética (voz do macOS), sem direitos autorais."""
    if shutil.which("say") is None:
        pytest.skip("precisa do `say` do macOS")
    folder = tmp_path_factory.mktemp("speech")
    aiff, wav = folder / "fr.aiff", folder / "fr.wav"
    text = "Bonjour Marie. Tu es fatiguée ? Non, ça va, merci. À demain."
    subprocess.run(["say", "-v", "Thomas", "-o", str(aiff), text], check=True)
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(aiff), "-ac", "1", "-ar", "16000", str(wav)]
    subprocess.run(cmd, check=True)
    with wave.open(str(wav)) as w:
        duration = w.getnframes() / w.getframerate()
    return AudioRef(path=str(wav), sha256="teste", duration=duration)


@pytest.fixture(params=PROVIDERS)
def transcriber(request: pytest.FixtureRequest) -> transcription.Transcriber:
    build_factories()
    return transcription.FACTORY.create(ComponentSpec(provider=request.param), {})


def test_segments_are_ordered_with_word_times(
    transcriber: transcription.Transcriber, french_speech: AudioRef
) -> None:
    request = TranscriptionRequest(language="fr")
    out = transcriber.transcribe(french_speech, request, lambda *_: None)
    assert out.segments, "nenhuma fala"
    assert out.language == "fr"
    last = 0.0
    for seg in out.segments:
        assert seg.start >= last - 0.05
        assert seg.end >= seg.start
        assert seg.text.strip()
        if "word_timestamps" in transcriber.info.capabilities:
            assert seg.words
            for w in seg.words:
                assert seg.start - 0.5 <= w.start <= w.end <= seg.end + 0.5
        last = seg.start


def test_provider_declares_health_and_version(transcriber: transcription.Transcriber) -> None:
    assert transcriber.health().ok
    assert transcriber.info.version
    assert transcriber.info.interface == transcription.INTERFACE
