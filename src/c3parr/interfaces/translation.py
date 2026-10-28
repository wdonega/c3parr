"""Interface de tradução: traduz blocos de fala com contexto.

Capacidades que um provider pode declarar: `contexto`, `glossario`, `incerteza`, `mencoes`,
`genero`. O pipeline se adapta ao que foi declarado e o relatório avisa o que faltou.
"""

from collections.abc import Callable
from typing import Protocol

from pydantic import Field

from c3parr.core.bible import Bible, Gender
from c3parr.core.models import Model
from c3parr.providers.base import Health, ProviderInfo
from c3parr.providers.factory import ComponentFactory

INTERFACE = "traducao"


class BlockLine(Model):
    id: int
    text: str
    lang: str
    start: float
    end: float
    speaker: str | None = None
    speaker_gender: Gender = "?"
    addressee: str | None = None
    addressee_gender: Gender = "?"
    ref: str | None = None
    max_chars: int = 84
    """Quanto texto cabe no tempo da fala (limite de leitura)."""


class Block(Model):
    index: int
    lines: tuple[BlockLine, ...]
    previous: tuple[BlockLine, ...] = ()
    """Últimas falas do bloco anterior: outra cena, sem continuidade."""
    pause_before: float | None = None


class Mention(Model):
    entidade: str
    original: str
    traducao: str
    rastreada: bool = True


class TranslatedLine(Model):
    id: int
    texto: str
    incerto: bool = False
    motivo: str | None = None
    genero_falante: Gender = "?"
    genero_ouvinte: Gender = "?"
    mencoes: tuple[Mention, ...] = ()
    fallback: bool = False
    """True quando o bloco caiu no fallback e a fala ficou no idioma original."""


class TranslationUsage(Model):
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    seconds: float = 0.0


class Translation(Model):
    lines: tuple[TranslatedLine, ...]
    dst: str
    usage: TranslationUsage = Field(default_factory=TranslationUsage)
    missing_capabilities: tuple[str, ...] = ()


class TranslationRequest(Model):
    dst: str
    src: str


class Translator(Protocol):
    @property
    def info(self) -> ProviderInfo: ...

    def health(self) -> Health: ...

    def translate(
        self,
        blocks: list[Block],
        bible: Bible,
        request: TranslationRequest,
        progress: Callable[[float, str], None],
    ) -> Translation: ...


FACTORY: ComponentFactory[Translator] = ComponentFactory(INTERFACE)
