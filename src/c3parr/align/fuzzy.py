"""Provider `texto` de sincronização: referência no mesmo idioma do áudio, comparação fuzzy."""

import numpy as np
from pydantic import BaseModel, Field
from rapidfuzz import fuzz, process

from c3parr.align.core import Match, Thresholds, build_result, monotonic_align
from c3parr.align.text import normalize
from c3parr.core.errors import Unavailable
from c3parr.core.models import Segment, Subtitle, Transcript
from c3parr.interfaces import sync
from c3parr.interfaces.sync import SyncRequest, SyncResult
from c3parr.providers.base import Health, ProviderInfo


class FuzzySyncConfig(BaseModel):
    min_score: float = Field(0.6, ge=0, le=1)
    """Similaridade mínima (0..1) para um par contar."""
    full_weight_tokens: int = 4
    """Cues com menos palavras que isso pesam menos ("Oui.", "Yes." casam com qualquer coisa)."""
    thresholds: Thresholds = Field(default_factory=Thresholds)


INFO = ProviderInfo(
    id="texto",
    interface=sync.INTERFACE,
    version="1",
    config_schema=FuzzySyncConfig,
    capabilities=frozenset({"mesmo_idioma"}),
    description="Fuzzy text matching, for a reference in the audio language",
)


def unit_text(seg: Segment) -> tuple[str, list[int], list[float]]:
    """Texto normalizado da unidade + início de cada palavra (offset de caractere e tempo)."""
    offsets: list[int] = []
    times: list[float] = []
    parts: list[str] = []
    pos = 0
    for w in seg.words:
        norm = normalize(w.w)
        if not norm:
            continue
        offsets.append(pos)
        times.append(w.start)
        parts.append(norm)
        pos += len(norm) + 1
    if not parts:
        return normalize(seg.text), [0], [seg.start]
    return " ".join(parts), offsets, times


class FuzzySynchronizer:
    info = INFO

    def __init__(self, config: FuzzySyncConfig) -> None:
        self.config = config

    def health(self) -> Health:
        return Health(ok=True)

    def sync(self, ref: Subtitle, transcript: Transcript, request: SyncRequest) -> SyncResult:
        ref_lang = (ref.lang or "").split("-")[0]
        if ref_lang and ref_lang != transcript.language.split("-")[0]:
            raise Unavailable(
                f"reference in '{ref.lang}' and audio in '{transcript.language}': "
                "the 'texto' provider only syncs within the same language",
                provider=INFO.id,
            )
        cfg = self.config
        ref_norm = [normalize(c.text) for c in ref.cues]
        speech = [bool(t) for t in ref_norm]
        units = [unit_text(s) for s in transcript.segments]
        unit_texts = [u[0] for u in units]
        if not unit_texts or not any(speech):
            return build_result(ref, speech, [], request.audio_duration, cfg.thresholds)

        scores = process.cdist(ref_norm, unit_texts, scorer=fuzz.partial_ratio, workers=-1)
        scores = scores.astype(np.float64) / 100.0
        tokens = np.array([max(1, len(t.split())) for t in ref_norm], dtype=np.float64)
        length_weight = np.minimum(1.0, tokens / cfg.full_weight_tokens)[:, None]
        weights = np.where(scores >= cfg.min_score, scores - cfg.min_score + 0.05, 0.0)
        weights = weights * length_weight
        weights[~np.array(speech)] = 0.0

        matches = []
        for i, j in monotonic_align(weights):
            text, offsets, times = units[j]
            al = fuzz.partial_ratio_alignment(ref_norm[i], text)
            k = 0
            if al is not None:
                k = max(0, int(np.searchsorted(offsets, al.dest_start, side="right")) - 1)
            matches.append(Match(cue=i, unit=j, t_audio=times[k], conf=float(scores[i, j])))
        return build_result(ref, speech, matches, request.audio_duration, cfg.thresholds)


def register() -> None:
    sync.FACTORY.register(INFO, FuzzySynchronizer)
