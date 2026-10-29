"""Provider `tempo` de sincronização: casa pelo ritmo das falas, sem olhar o texto.

Serve para referência e legenda em idiomas diferentes sem modelo nenhum (a imagem não traz o
torch dos embeddings): a legenda em português de outra release contra a principal em inglês tem
as mesmas falas nos mesmos ritmos, só deslocadas. Acha o deslocamento (e a troca de fps) em que os
inícios das falas mais coincidem, casa cada cue com a fala mais próxima e entrega à mesma curva por
marcos e às mesmas validações dos outros providers. Sem um pico claro (legenda de outro corte ou de
outro episódio), recusa antes de casar: com diálogo denso, qualquer tempo acha uma fala perto.
"""

import numpy as np
from pydantic import BaseModel, Field

from c3parr.align.core import (
    FRAMERATE_RATIOS,
    FloatArray,
    Match,
    Thresholds,
    build_result,
    elastic_fit,
    monotonic_align,
)
from c3parr.align.text import clean
from c3parr.core.models import Segment, Subtitle, Transcript
from c3parr.interfaces import sync
from c3parr.interfaces.sync import SyncRequest, SyncResult
from c3parr.providers.base import Health, ProviderInfo


class TimingSyncConfig(BaseModel):
    max_offset: float = Field(120.0, gt=0)
    """Maior deslocamento (s) procurado entre a legenda e a referência."""
    step: float = Field(0.1, gt=0)
    """Resolução (s) da busca pelo deslocamento."""
    tolerance: float = Field(0.8, gt=0)
    """Distância máxima (s) entre o início previsto da cue e o da fala para casarem."""
    min_peak: float = Field(8.0, gt=0)
    """Quantos desvios-padrão o melhor deslocamento precisa ficar acima dos outros. Medido em
    Supernatural S10 (en contra pt/hi): par certo 16-20, episódio errado ~4."""
    thresholds: Thresholds = Field(default_factory=Thresholds)


INFO = ProviderInfo(
    id="tempo",
    interface=sync.INTERFACE,
    version="1",
    config_schema=TimingSyncConfig,
    capabilities=frozenset({"mesmo_idioma", "entre_idiomas"}),
    description="Speech rhythm (offset and frame rate), no text and no model",
)


def _onsets(starts: list[float], length: int, step: float) -> FloatArray:
    """Um pulso por início de fala, espalhado em ±0,3 s (as releases não marcam no mesmo quadro)."""
    out = np.zeros(length, dtype=np.float64)
    for t in starts:
        k = round(t / step)
        if 0 <= k < length:
            out[k] += 1.0
    kernel = np.maximum(0.0, 1.0 - np.abs(np.arange(-3, 4)) / 4)
    return np.convolve(out, kernel, mode="same")


def best_shift(
    cues: list[float], speech: list[float], cfg: TimingSyncConfig
) -> tuple[float, float, float]:
    """(escala, deslocamento, destaque do pico) que levam os inícios das cues aos das falas.
    O destaque é quantos desvios-padrão o melhor deslocamento fica acima da média dos outros."""
    pad = int(cfg.max_offset / cfg.step)
    last = max(cues + speech)
    length = int(last * max(1.0, *FRAMERATE_RATIOS) / cfg.step) + pad + 2
    size = 1 << int(np.ceil(np.log2(2 * length)))
    target = np.fft.rfft(_onsets(speech, length, cfg.step), size)
    best = (-np.inf, 1.0, 0.0, 0.0)
    for scale in (1.0, *FRAMERATE_RATIOS):
        src = _onsets([t * scale for t in cues], length, cfg.step)
        corr = np.fft.irfft(target * np.conj(np.fft.rfft(src, size)), size)
        lags = np.concatenate([corr[: pad + 1], corr[-pad:]])  # deslocamentos 0..+pad, -pad..-1
        k = int(np.argmax(lags))
        z = (lags[k] - lags.mean()) / (lags.std() or 1.0)
        if z > best[0]:
            lag = k if k <= pad else k - len(lags)
            best = (float(z), scale, lag * cfg.step, float(z))
    return best[1], best[2], best[3]


class TimingSynchronizer:
    info = INFO

    def __init__(self, config: TimingSyncConfig) -> None:
        self.config = config

    def health(self) -> Health:
        return Health(ok=True)

    def _match(self, idx: list[int], segments: list[Segment], predicted: FloatArray) -> list[Match]:
        seg_starts = np.array([s.start for s in segments], dtype=np.float64)
        dist = np.abs(predicted[:, None] - seg_starts[None, :])
        weights = np.maximum(0.0, 1.0 - dist / self.config.tolerance)
        matches: list[Match] = []
        seen: set[int] = set()
        for row, j in monotonic_align(weights):
            if j in seen or weights[row, j] <= 0:  # só a primeira cue de cada fala vira marco
                continue
            seen.add(j)
            matches.append(Match(cue=idx[row], unit=segments[j].id, t_audio=segments[j].start,
                                 conf=float(weights[row, j])))  # fmt: skip
        return matches

    def sync(self, ref: Subtitle, transcript: Transcript, request: SyncRequest) -> SyncResult:
        cfg = self.config
        speech = [bool(clean(c.text)) for c in ref.cues]
        segments = [s for s in transcript.segments if s.text.strip()]
        idx = [i for i, ok in enumerate(speech) if ok]
        if not segments or not idx:
            return build_result(ref, speech, [], request.audio_duration, cfg.thresholds)

        starts = np.array([ref.cues[i].start for i in idx], dtype=np.float64)
        scale, offset, peak = best_shift(list(starts), [s.start for s in segments], cfg)
        if peak < cfg.min_peak:
            return SyncResult(accepted=False, reason=(
                f"no clear offset between the subtitle and the reference (peak {peak:.1f}, "
                f"minimum {cfg.min_peak}): probably another cut or another episode"))  # fmt: skip
        matches = self._match(idx, segments, starts * scale + offset)
        if len(matches) >= 2:  # segunda passada pela curva: segue o drift que cresce aos poucos
            x = np.array([ref.cues[m.cue].start for m in matches], dtype=np.float64)
            y = np.array([m.t_audio for m in matches], dtype=np.float64)
            curve = elastic_fit(x, y, cfg.thresholds)
            matches = self._match(idx, segments, np.array([curve(t) for t in starts]))
        return build_result(ref, speech, matches, request.audio_duration, cfg.thresholds)


def register() -> None:
    sync.FACTORY.register(INFO, TimingSynchronizer)
