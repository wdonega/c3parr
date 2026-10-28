"""Interface de contexto: monta ou completa a bíblia do filme."""

from typing import Protocol

from c3parr.core.bible import Bible
from c3parr.core.models import Model, Transcript
from c3parr.providers.base import Health, ProviderInfo
from c3parr.providers.factory import ComponentFactory

INTERFACE = "contexto"


class ContextRequest(Model):
    dst: str
    base: Bible | None = None
    """Bíblia de partida (TMDb ou arquivo do usuário), que o provider completa."""


class ContextBuilder(Protocol):
    @property
    def info(self) -> ProviderInfo: ...

    def health(self) -> Health: ...

    def build(self, transcript: Transcript, request: ContextRequest) -> Bible: ...


FACTORY: ComponentFactory[ContextBuilder] = ComponentFactory(INTERFACE)
