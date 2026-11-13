"""Interface de letras: o que o artista de um show pode tocar, com a letra de cada música."""

from collections.abc import Callable
from typing import Protocol

from c3parr.concert.catalogue import Catalogue
from c3parr.core.models import Model
from c3parr.providers.base import Health, ProviderInfo
from c3parr.providers.factory import ComponentFactory

INTERFACE = "letras"


class LyricsRequest(Model):
    titles: tuple[str, ...]
    """Os nomes do show (TMDb, Radarr, arquivo): é deles que sai o artista."""
    year: int | None = None
    """O ano do show: músicas lançadas depois ficam de fora."""
    overview: str = ""
    artist: str | None = None
    """Informado por quem pediu: dispensa adivinhar pelo título."""


class LyricsProvider(Protocol):
    @property
    def info(self) -> ProviderInfo: ...

    def health(self) -> Health: ...

    def catalogue(
        self, request: LyricsRequest, progress: Callable[[float, str], None]
    ) -> Catalogue: ...


FACTORY: ComponentFactory[LyricsProvider] = ComponentFactory(INTERFACE)
