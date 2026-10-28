"""Providers `falso` de contexto e tradução: respostas fixas, sem modelo nem rede."""

from collections.abc import Callable

from pydantic import BaseModel

from c3parr.core.bible import Bible, Character
from c3parr.core.errors import Unavailable
from c3parr.core.models import Transcript
from c3parr.interfaces import context, translation
from c3parr.interfaces.context import ContextRequest
from c3parr.interfaces.translation import Block, TranslatedLine, Translation, TranslationRequest
from c3parr.providers.base import Health, ProviderInfo


class FakeConfig(BaseModel):
    fail: bool = False


CONTEXT_INFO = ProviderInfo(
    id="falso", interface=context.INTERFACE, version="1", config_schema=FakeConfig
)
TRANSLATION_INFO = ProviderInfo(
    id="falso",
    interface=translation.INTERFACE,
    version="1",
    config_schema=FakeConfig,
    capabilities=frozenset({"contexto", "incerteza"}),
)


class FakeContext:
    info = CONTEXT_INFO

    def __init__(self, config: FakeConfig) -> None:
        self.config = config

    def health(self) -> Health:
        return Health(ok=True)

    def build(self, transcript: Transcript, request: ContextRequest) -> Bible:
        if self.config.fail:
            raise Unavailable("contexto falso configurado para falhar", provider="falso")
        base = request.base or Bible()
        return base.model_copy(update={"personagens": (Character(nome="Marie", genero="F"),)})


class FakeTranslator:
    info = TRANSLATION_INFO
    calls = 0

    def __init__(self, config: FakeConfig) -> None:
        self.config = config

    def health(self) -> Health:
        return Health(ok=True)

    def translate(
        self,
        blocks: list[Block],
        bible: Bible,
        request: TranslationRequest,
        progress: Callable[[float, str], None],
    ) -> Translation:
        FakeTranslator.calls += 1
        if self.config.fail:
            raise Unavailable("LLM falso fora do ar", provider="falso")
        lines = tuple(
            TranslatedLine(id=ln.id, texto=f"[{request.dst}] {ln.text}", incerto="?" in ln.text)
            for b in blocks
            for ln in b.lines
        )
        progress(1.0, "pronto")
        return Translation(lines=lines, dst=request.dst)


def register() -> None:
    context.FACTORY.register(CONTEXT_INFO, FakeContext)
    translation.FACTORY.register(TRANSLATION_INFO, FakeTranslator)
