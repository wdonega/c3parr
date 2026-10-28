"""Interface de sincronização: casa a legenda de referência com o áudio e corrige o tempo."""

from typing import Literal, Protocol

from c3parr.core.models import Model, Subtitle, Transcript
from c3parr.providers.base import Health, ProviderInfo
from c3parr.providers.factory import ComponentFactory

INTERFACE = "sincronizacao"


class SyncRequest(Model):
    audio_duration: float
    runtime: float | None = None
    """Duração oficial (TMDb), quando houver."""


class LineFit(Model):
    """t_audio = a * t_ref + b, válido para t_ref em [ref_start, ref_end]."""

    a: float
    b: float
    ref_start: float | None = None
    ref_end: float | None = None


class Checkpoint(Model):
    """Marco ao longo do filme: quanto a referência estava deslocada naquele ponto do áudio."""

    t_audio: float
    offset: float


class SyncedCue(Model):
    index: int
    start: float
    end: float
    text: str
    status: Literal["casada", "sem_par", "nao_fala"]
    segment_id: int | None = None
    conf: float = 0.0


class SyncResult(Model):
    accepted: bool
    reason: str | None = None
    fits: tuple[LineFit, ...] = ()
    matched_ratio: float = 0.0
    inlier_ratio: float = 0.0
    mean_error: float | None = None
    duration_ratio: float | None = None
    framerate_hint: str | None = None
    checkpoints: tuple[Checkpoint, ...] = ()
    drift: float | None = None
    """Variação do deslocamento do começo ao fim (s): drift que se acumula."""
    cuts: tuple[float, ...] = ()
    """Tempos da referência onde o deslocamento salta (cenas cortadas ou inseridas)."""
    via_bridge: bool = False
    cues: tuple[SyncedCue, ...] = ()


class Synchronizer(Protocol):
    @property
    def info(self) -> ProviderInfo: ...

    def health(self) -> Health: ...

    def sync(self, ref: Subtitle, transcript: Transcript, request: SyncRequest) -> SyncResult: ...


FACTORY: ComponentFactory[Synchronizer] = ComponentFactory(INTERFACE)
