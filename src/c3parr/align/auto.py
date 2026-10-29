"""Provider `auto` de sincronização: texto quando a referência está no idioma do áudio (rápido,
sem modelo); em outro idioma ou idioma desconhecido, embeddings se o extra `embed` estiver
instalado e, senão (a imagem Docker não traz o torch), o ritmo das falas."""

from pydantic import BaseModel, Field

from c3parr.align.embed import EmbeddingSyncConfig, EmbeddingSynchronizer
from c3parr.align.fuzzy import FuzzySyncConfig, FuzzySynchronizer
from c3parr.align.timing import TimingSyncConfig, TimingSynchronizer
from c3parr.core.models import Subtitle, Transcript
from c3parr.interfaces import sync
from c3parr.interfaces.sync import SyncRequest, SyncResult
from c3parr.providers.base import Health, ProviderInfo


class AutoSyncConfig(BaseModel):
    texto: FuzzySyncConfig = Field(default_factory=FuzzySyncConfig)
    embeddings: EmbeddingSyncConfig = Field(default_factory=EmbeddingSyncConfig)
    tempo: TimingSyncConfig = Field(default_factory=TimingSyncConfig)


INFO = ProviderInfo(
    id="auto",
    interface=sync.INTERFACE,
    version="1",
    config_schema=AutoSyncConfig,
    capabilities=frozenset({"mesmo_idioma", "entre_idiomas"}),
    description="Text when same language; embeddings or speech rhythm across languages",
)


class AutoSynchronizer:
    info = INFO

    def __init__(self, config: AutoSyncConfig) -> None:
        self.text = FuzzySynchronizer(config.texto)
        self.embeddings = EmbeddingSynchronizer(config.embeddings)
        self.timing = TimingSynchronizer(config.tempo)

    def health(self) -> Health:
        return Health(ok=True)

    def sync(self, ref: Subtitle, transcript: Transcript, request: SyncRequest) -> SyncResult:
        ref_lang = (ref.lang or "").split("-")[0]
        if ref_lang and ref_lang == transcript.language.split("-")[0]:
            result = self.text.sync(ref, transcript, request)
            if result.accepted:
                return result
        if self.embeddings.health().ok:
            return self.embeddings.sync(ref, transcript, request)
        return self.timing.sync(ref, transcript, request)


def register() -> None:
    sync.FACTORY.register(INFO, AutoSynchronizer)
