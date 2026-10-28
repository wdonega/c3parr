"""Interface de fonte de legenda: obtém a legenda de referência."""

from typing import Protocol

from c3parr.core.models import Model, Subtitle
from c3parr.providers.base import Health, ProviderInfo
from c3parr.providers.factory import ComponentFactory

INTERFACE = "fonte_legenda"


class SubtitleRequest(Model):
    ref: str
    """Caminho (ou identificador) da legenda de referência."""
    video: str


class SubtitleSource(Protocol):
    @property
    def info(self) -> ProviderInfo: ...

    def health(self) -> Health: ...

    def fetch(self, request: SubtitleRequest) -> Subtitle: ...


FACTORY: ComponentFactory[SubtitleSource] = ComponentFactory(INTERFACE)
