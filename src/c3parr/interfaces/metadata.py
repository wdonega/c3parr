"""Interface de metadados: identifica a obra e devolve a ficha (elenco, sinopse, duração)."""

from typing import Literal, Protocol

from c3parr.core.bible import Bible
from c3parr.core.models import Model
from c3parr.providers.base import Health, ProviderInfo
from c3parr.providers.factory import ComponentFactory

INTERFACE = "metadados"


class MetadataRequest(Model):
    filename: str
    path: str | None = None
    """Caminho completo: pastas `Título (Ano)` e `Série/Season N` ajudam a identificar."""
    title: str | None = None
    year: int | None = None
    tmdb_id: int | None = None
    audio_duration: float | None = None
    language: str | None = None
    """Idioma falado, para conferir com o idioma original da obra."""


class Candidate(Model):
    id: int
    titulo: str
    ano: int | None = None
    runtime_min: int | None = None
    idioma: str | None = None
    motivo: str = ""


class MetadataResult(Model):
    bible: Bible
    tmdb_id: int
    kind: Literal["filme", "serie"] = "filme"
    season: int | None = None
    episode: int | None = None
    episode_title: str | None = None
    original_language: str | None = None
    """Idioma original da obra no TMDb (ex.: `fr`): palpite para o idioma principal do ASR."""
    candidates: tuple[Candidate, ...] = ()
    chosen_reason: str = ""


class MetadataProvider(Protocol):
    @property
    def info(self) -> ProviderInfo: ...

    def health(self) -> Health: ...

    def identify(self, request: MetadataRequest) -> MetadataResult: ...


FACTORY: ComponentFactory[MetadataProvider] = ComponentFactory(INTERFACE)
