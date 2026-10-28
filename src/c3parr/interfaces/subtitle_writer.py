"""Interface de escrita de legenda: grava o resultado num formato."""

from pathlib import Path
from typing import Protocol

from c3parr.core.models import Subtitle
from c3parr.providers.base import Health, ProviderInfo
from c3parr.providers.factory import ComponentFactory

INTERFACE = "escrita_legenda"


class SubtitleWriter(Protocol):
    @property
    def info(self) -> ProviderInfo: ...

    def health(self) -> Health: ...

    @property
    def extension(self) -> str: ...

    def write(self, subtitle: Subtitle, dest: Path) -> Path: ...


FACTORY: ComponentFactory[SubtitleWriter] = ComponentFactory(INTERFACE)
