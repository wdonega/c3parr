"""Interface de transcrição: áudio em segmentos com tempo por palavra."""

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from c3parr.core.models import Model, Transcript
from c3parr.providers.base import Health, ProviderInfo
from c3parr.providers.factory import ComponentFactory

INTERFACE = "transcricao"


class AudioRef(Model):
    path: str
    sha256: str
    duration: float
    sample_rate: int = 16000
    stream_index: int | None = None
    stream_lang: str | None = None
    note: str | None = None

    def missing_files(self) -> list[str]:
        return [] if Path(self.path).exists() else [self.path]


class TranscriptionRequest(Model):
    language: str | None = None
    """Idioma principal (`--src`); None detecta no áudio."""
    hotwords: tuple[str, ...] = ()
    language_hint: str | None = None
    """Idioma provável (original da obra no TMDb). Não força: só pesa na votação da detecção."""
    vad: bool = True
    """Detector de voz. Ele descarta canto: a legenda de show desliga, na voz já isolada."""
    prompt: str | None = None
    """Dica: texto que o Whisper recebe como se tivesse vindo antes do áudio. Só vale para um
    trecho de até 30 s (uma janela), e liga o contexto que normalmente fica desligado."""


class Transcriber(Protocol):
    @property
    def info(self) -> ProviderInfo: ...

    def health(self) -> Health: ...

    def transcribe(
        self, audio: AudioRef, request: TranscriptionRequest, progress: Callable[[float, str], None]
    ) -> Transcript: ...


FACTORY: ComponentFactory[Transcriber] = ComponentFactory(INTERFACE)
