"""Provider `embeddings` de sincronização: casa a referência com o áudio mesmo em outro idioma.

Cada cue e cada fala transcrita vira um vetor num modelo multilíngue; a similaridade entra no mesmo
alinhamento monótono e na mesma curva por marcos do provider de texto.
"""

from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from c3parr.align.core import Match, Thresholds, build_result, monotonic_align
from c3parr.align.text import clean
from c3parr.core.errors import Unavailable
from c3parr.core.models import Subtitle, Transcript
from c3parr.interfaces import sync
from c3parr.interfaces.sync import SyncRequest, SyncResult
from c3parr.providers.base import Health, ModelDownload, ProviderInfo, Requirements


class EmbeddingSyncConfig(BaseModel):
    model: str = "intfloat/multilingual-e5-small"
    prefix: str = "query: "
    """Prefixo exigido por alguns modelos (os e5 usam "query: ")."""
    quantile: float = Field(0.97, ge=0.5, le=0.999)
    """Só pares acima deste quantil da similaridade contam (o resto é ruído)."""
    full_weight_tokens: int = 4
    thresholds: Thresholds = Field(default_factory=Thresholds)


INFO = ProviderInfo(
    id="embeddings",
    interface=sync.INTERFACE,
    version="1",
    config_schema=EmbeddingSyncConfig,
    capabilities=frozenset({"mesmo_idioma", "entre_idiomas"}),
    requirements=Requirements(
        extras=("embed",),
        models=(ModelDownload(name="intfloat/multilingual-e5-small", approx_mb=470),),
    ),
    description="Multilingual embeddings + monotonic alignment + landmark curve",
)


class EmbeddingSynchronizer:
    info = INFO

    def __init__(self, config: EmbeddingSyncConfig) -> None:
        self.config = config
        self._model: Any = None

    def health(self) -> Health:
        try:
            import sentence_transformers  # noqa: F401
        except ImportError:
            return Health(ok=False, detail="extra 'embed' not installed")
        return Health(ok=True)

    def _encode(self, texts: list[str]) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as e:
                raise Unavailable("extra 'embed' not installed", provider=INFO.id) from e
            self._model = SentenceTransformer(self.config.model, device="cpu")
        prefixed = [self.config.prefix + t for t in texts]
        return self._model.encode(prefixed, normalize_embeddings=True, batch_size=64)

    def sync(self, ref: Subtitle, transcript: Transcript, request: SyncRequest) -> SyncResult:
        cfg = self.config
        ref_clean = [clean(c.text) for c in ref.cues]
        speech = [bool(t) for t in ref_clean]
        segments = [s for s in transcript.segments if s.text.strip()]
        if not segments or not any(speech):
            return build_result(ref, speech, [], request.audio_duration, cfg.thresholds)

        idx = [i for i, ok in enumerate(speech) if ok]
        er = self._encode([ref_clean[i] for i in idx])
        es = self._encode([s.text for s in segments])
        sim = np.asarray(er @ es.T, dtype=np.float64)
        threshold = float(np.quantile(sim, cfg.quantile))
        tokens = np.array([max(1, len(ref_clean[i].split())) for i in idx], dtype=np.float64)
        length_weight = np.minimum(1.0, tokens / cfg.full_weight_tokens)[:, None]
        weights = np.maximum(0.0, sim - threshold) * length_weight

        matches: list[Match] = []
        seen_units: set[int] = set()
        for row, j in monotonic_align(weights):
            if j in seen_units:  # só a primeira cue de cada fala vira marco de tempo
                continue
            seen_units.add(j)
            seg = segments[j]
            conf = float(sim[row, j])
            matches.append(Match(cue=idx[row], unit=seg.id, t_audio=seg.start, conf=conf))
        return build_result(ref, speech, matches, request.audio_duration, cfg.thresholds)


def register() -> None:
    sync.FACTORY.register(INFO, EmbeddingSynchronizer)
