"""Provider `falso` de transcrição: devolve um roteiro fixo, sem modelo nem rede."""

from collections.abc import Callable

from pydantic import BaseModel

from c3parr.core.errors import Unavailable
from c3parr.core.models import Segment, Transcript, Word
from c3parr.interfaces import transcription
from c3parr.interfaces.transcription import AudioRef, TranscriptionRequest
from c3parr.providers.base import Health, ProviderInfo


class FakeTranscriberConfig(BaseModel):
    lines: tuple[str, ...] = ("Bonjour.", "Tu es fatigué ?", "Non, ça va.")
    spacing: float = 3.0
    fail: bool = False


INFO = ProviderInfo(
    id="falso",
    interface=transcription.INTERFACE,
    version="1",
    config_schema=FakeTranscriberConfig,
    capabilities=frozenset({"word_timestamps"}),
    description="Roteiro fixo para testes",
)


class FakeTranscriber:
    info = INFO
    calls = 0
    """Conta chamadas entre instâncias, para os testes provarem que o cache evitou trabalho."""

    def __init__(self, config: FakeTranscriberConfig) -> None:
        self.config = config

    def health(self) -> Health:
        return Health(ok=True)

    def transcribe(
        self, audio: AudioRef, request: TranscriptionRequest, progress: Callable[[float, str], None]
    ) -> Transcript:
        FakeTranscriber.calls += 1
        if self.config.fail:
            raise Unavailable("fake transcription set to fail", provider=INFO.id)
        lang = request.language or "fr"
        segments = []
        for i, line in enumerate(self.config.lines):
            start = 1.0 + i * self.config.spacing
            words = tuple(
                Word(w=t, start=round(start + j * 0.3, 3), end=round(start + j * 0.3 + 0.25, 3))
                for j, t in enumerate(line.split())
            )
            segments.append(
                Segment(id=i, start=start, end=words[-1].end, text=line, lang=lang, words=words)
            )
        progress(1.0, "pronto")
        return Transcript(
            segments=tuple(segments), language=lang, model="falso", audio_duration=audio.duration
        )


def register() -> None:
    transcription.FACTORY.register(INFO, FakeTranscriber)
