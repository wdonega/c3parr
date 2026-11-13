"""Interface de encaixe: em que instante do áudio cada linha de um texto é dita ou cantada."""

from collections.abc import Callable
from typing import Protocol

from c3parr.core.models import Model
from c3parr.interfaces.transcription import AudioRef
from c3parr.providers.base import Health, ProviderInfo
from c3parr.providers.factory import ComponentFactory

INTERFACE = "encaixe"


class Span(Model):
    """Um trecho do áudio e as linhas que devem caber nele, na ordem."""

    start: float
    end: float
    lines: tuple[str, ...]


class PlaceRequest(Model):
    window: tuple[float, float]
    """O trecho do áudio que o provider "escuta" de uma vez (a música inteira). Todos os `spans`
    ficam dentro dele; a escuta é guardada em `cache_dir` e serve aos pedidos seguintes."""
    spans: tuple[Span, ...]
    cache_dir: str


class Placed(Model):
    line: int
    """Índice em `Span.lines`."""
    start: float
    end: float
    score: float
    """Quanto o som daquele ponto parece com a linha (0 a 1)."""
    steady: float = 0.0
    """A nota da palavra do meio: alta só quando a maioria das palavras da linha soa ali."""


class Placer(Protocol):
    @property
    def info(self) -> ProviderInfo: ...

    def health(self) -> Health: ...

    def place(
        self, audio: AudioRef, request: PlaceRequest, progress: Callable[[float, str], None]
    ) -> tuple[tuple[Placed, ...], ...]:
        """Para cada trecho de `request.spans`, onde cada linha coube (uma lista por trecho)."""
        ...


FACTORY: ComponentFactory[Placer] = ComponentFactory(INTERFACE)
