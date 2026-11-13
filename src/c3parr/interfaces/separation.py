"""Interface de separação: a voz do vídeo sem a banda, e onde há voz nela."""

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from c3parr.core.models import Model
from c3parr.interfaces.transcription import AudioRef
from c3parr.providers.base import Health, ProviderInfo
from c3parr.providers.factory import ComponentFactory

INTERFACE = "separacao"


class SeparationRequest(Model):
    video: str
    stream_index: int | None = None
    """Faixa de áudio do vídeo; None = a primeira."""
    dest: str
    """Arquivo WAV (16 kHz, mono) onde a voz isolada é gravada."""


class Voice(Model):
    """A voz isolada (16 kHz, mono) e os trechos em que há voz nela (canto ou fala)."""

    path: str
    sha256: str
    duration: float
    spans: tuple[tuple[float, float], ...] = ()
    device: str = "cpu"
    """Onde a separação rodou (`cuda`, `cpu`): vai para o relatório."""

    def missing_files(self) -> list[str]:
        return [] if Path(self.path).exists() else [self.path]

    def audio(self) -> AudioRef:
        return AudioRef(path=self.path, sha256=self.sha256, duration=self.duration)


class Separator(Protocol):
    @property
    def info(self) -> ProviderInfo: ...

    def health(self) -> Health: ...

    def separate(
        self, request: SeparationRequest, progress: Callable[[float, str], None]
    ) -> Voice: ...


FACTORY: ComponentFactory[Separator] = ComponentFactory(INTERFACE)
