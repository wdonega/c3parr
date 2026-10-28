"""Partes do alinhamento que não dependem de como a similaridade foi calculada.

A matriz de pesos é injetável, então tudo aqui é testável sem modelo de embeddings.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt

from c3parr.core.models import Model, Subtitle
from c3parr.interfaces.sync import Checkpoint, LineFit, SyncedCue, SyncResult

FloatArray = npt.NDArray[np.float64]
CueStatus = Literal["casada", "sem_par", "nao_fala"]

FRAMERATE_RATIOS = {
    25 / (24000 / 1001): "a 25 fps subtitle on a 23.976 fps video",
    (24000 / 1001) / 25: "a 23.976 fps subtitle on a 25 fps video",
    24 / (24000 / 1001): "a 24 fps subtitle on a 23.976 fps video",
    (24000 / 1001) / 24: "a 23.976 fps subtitle on a 24 fps video",
    25 / 24: "a 25 fps subtitle on a 24 fps video",
    24 / 25: "a 24 fps subtitle on a 25 fps video",
}


class Thresholds(Model):
    min_matched_ratio: float = 0.30
    min_inlier_ratio: float = 0.60
    max_mean_error: float = 0.5
    min_scale: float = 0.9
    max_scale: float = 1.1
    inlier_tolerance: float = 1.0
    snap_tolerance: float = 0.6
    """Distância máxima (s) entre a curva e o início ouvido para usar o tempo do áudio."""
    window: int = 15
    """Âncoras vizinhas usadas para suavizar o desvio local (marcos ao longo do filme)."""
    cut_jump: float = 2.0
    """Salto de desvio (s) entre âncoras vizinhas que conta como corte na referência."""
    min_local_scale: float = 0.8
    max_local_scale: float = 1.25
    min_plausible_gaps: float = 0.8
    """Fração mínima dos intervalos entre âncoras com escala local plausível."""
    checkpoint_every: float = 600.0


def monotonic_align(weights: FloatArray) -> list[tuple[int, int]]:
    """Pares (cue i, unidade j) com i crescente e j não decrescente que maximizam a soma dos pesos.

    Várias cues podem cair na mesma unidade (uma fala longa do ASR pode cobrir duas cues).
    """
    n, m = weights.shape
    if n == 0 or m == 0:
        return []
    w = np.maximum(weights, 0.0)
    best = np.zeros((n + 1, m), dtype=np.float64)
    for i in range(n):
        best[i + 1] = np.maximum.accumulate(best[i] + w[i])
    pairs: list[tuple[int, int]] = []
    i, j = n, m - 1
    while i > 0 and j >= 0:
        if j > 0 and best[i, j] == best[i, j - 1]:
            j -= 1
        elif w[i - 1, j] > 0 and np.isclose(best[i, j], best[i - 1, j] + w[i - 1, j]):
            pairs.append((i - 1, j))
            i -= 1
        else:
            i -= 1
    return pairs[::-1]


def ransac_line(
    x: FloatArray, y: FloatArray, tolerance: float, iters: int = 400, seed: int = 0
) -> tuple[float, float, npt.NDArray[np.bool_]]:
    """Reta robusta y = a·x + b; devolve também a máscara dos pares que ficaram dentro."""
    n = len(x)
    if n < 2:
        return 1.0, float(y[0] - x[0]) if n else 0.0, np.ones(n, dtype=bool)
    rng = np.random.default_rng(seed)
    best_mask = np.zeros(n, dtype=bool)
    for _ in range(iters):
        i, j = rng.choice(n, size=2, replace=False)
        if abs(x[j] - x[i]) < 1.0:
            continue
        a = (y[j] - y[i]) / (x[j] - x[i])
        b = y[i] - a * x[i]
        mask = np.abs(y - (a * x + b)) <= tolerance
        if mask.sum() > best_mask.sum():
            best_mask = mask
    if best_mask.sum() < 2:
        best_mask = np.ones(n, dtype=bool)
    a, b = np.polyfit(x[best_mask], y[best_mask], 1)
    final = np.abs(y - (a * x + b)) <= tolerance
    if final.sum() >= 2:
        a, b = np.polyfit(x[final], y[final], 1)
    return float(a), float(b), final


def framerate_hint(scale: float, tol: float = 0.0004) -> str | None:
    if abs(scale - 1.0) <= tol:
        return None
    for ratio, label in FRAMERATE_RATIOS.items():
        if abs(scale - ratio) <= tol:
            return f"scale {scale:.4f}, consistent with {label}"
    return None


@dataclass(frozen=True)
class Match:
    cue: int
    unit: int
    t_audio: float
    conf: float


def rolling_median(values: FloatArray, window: int) -> FloatArray:
    half = max(1, window // 2)
    return np.array(
        [np.median(values[max(0, i - half) : i + half + 1]) for i in range(len(values))]
    )


@dataclass(frozen=True)
class ElasticFit:
    """Curva tempo da referência -> tempo do áudio que passa pelos marcos confiáveis."""

    xs: FloatArray
    ys: FloatArray
    inliers: npt.NDArray[np.bool_]
    a: float
    b: float
    residuals: FloatArray

    def __call__(self, t: float) -> float:
        if len(self.xs) == 0:
            return self.a * t + self.b
        if t < self.xs[0]:
            return float(self.ys[0] + self.a * (t - self.xs[0]))
        if t > self.xs[-1]:
            return float(self.ys[-1] + self.a * (t - self.xs[-1]))
        return float(np.interp(t, self.xs, self.ys))

    def slope(self, t: float, span: float = 30.0) -> float:
        lo, hi = self(t - span), self(t + span)
        return max(0.5, min(2.0, (hi - lo) / (2 * span)))


def elastic_fit(x: FloatArray, y: FloatArray, th: Thresholds) -> ElasticFit:
    """Desvio local suavizado por mediana móvel: segue drift que cresce aos poucos e cortes,
    em vez de forçar uma reta única no filme inteiro."""
    a, b, _ = ransac_line(x, y, th.inlier_tolerance * 3)
    resid = y - (a * x + b)  # o que a reta global não explica: drift local e cortes
    first = np.abs(resid - rolling_median(resid, th.window)) <= th.inlier_tolerance
    if first.sum() < 2:
        empty = np.array([], dtype=np.float64)
        return ElasticFit(empty, empty, first, a, b, empty)
    xi = x[first]
    smooth = rolling_median(resid[first], th.window)
    ys = np.maximum.accumulate(a * xi + b + smooth)  # o tempo nunca volta para trás
    curve = ElasticFit(xi, ys, first, a, b, np.array([], dtype=np.float64))
    err = np.array([abs(yy - curve(xx)) for xx, yy in zip(x, y, strict=True)])
    inliers = err <= th.inlier_tolerance
    return ElasticFit(xi, ys, inliers, a, b, err[inliers])


def build_result(
    ref: Subtitle,
    speech: Sequence[bool],
    matches: Sequence[Match],
    audio_duration: float,
    th: Thresholds,
) -> SyncResult:
    """Ajusta a curva pelos marcos, valida pelos critérios da spec e devolve os novos tempos."""
    cues = ref.cues
    n_speech = sum(speech) or 1
    last_end = max((c.end for c in cues), default=0.0)
    duration_ratio = round(audio_duration / last_end, 4) if last_end else None
    matched_ratio = len(matches) / n_speech

    def reject(reason: str, fit: LineFit | None = None, inl: float = 0.0) -> SyncResult:
        return SyncResult(
            accepted=False,
            reason=reason,
            fits=(fit,) if fit else (),
            matched_ratio=round(matched_ratio, 3),
            inlier_ratio=round(inl, 3),
            duration_ratio=duration_ratio,
        )

    if matched_ratio < th.min_matched_ratio:
        return reject(
            f"only {matched_ratio:.0%} of the reference lines matched the audio "
            f"(minimum {th.min_matched_ratio:.0%})"
        )
    x = np.array([cues[m.cue].start for m in matches], dtype=np.float64)
    y = np.array([m.t_audio for m in matches], dtype=np.float64)
    curve = elastic_fit(x, y, th)
    fit = LineFit(a=round(curve.a, 6), b=round(curve.b, 4))
    inlier_ratio = float(curve.inliers.mean())
    if not th.min_scale <= curve.a <= th.max_scale:
        reason = f"scale {curve.a:.4f} outside [{th.min_scale}, {th.max_scale}]: wrong match"
        return reject(reason, fit, inlier_ratio)
    if inlier_ratio < th.min_inlier_ratio:
        reason = (
            f"only {inlier_ratio:.0%} of the landmarks agree with their neighbours "
            f"(minimum {th.min_inlier_ratio:.0%}): the reference does not fit this audio"
        )
        return reject(reason, fit, inlier_ratio)
    gaps = np.diff(curve.xs)
    steps = np.diff(curve.ys)
    long_gaps = gaps >= 20.0
    jumps = np.abs(steps - gaps) >= th.cut_jump
    local = steps[long_gaps & ~jumps] / gaps[long_gaps & ~jumps]
    plausible = (
        float(((local >= th.min_local_scale) & (local <= th.max_local_scale)).mean())
        if len(local)
        else 1.0
    )
    if plausible < th.min_plausible_gaps:
        reason = f"only {plausible:.0%} of the stretches have a plausible local scale: wrong match"
        return reject(reason, fit, inlier_ratio)
    mean_error = float(curve.residuals.mean()) if len(curve.residuals) else 0.0
    if mean_error >= th.max_mean_error:
        reason = f"mean error {mean_error:.2f} s above {th.max_mean_error} s"
        return reject(reason, fit, inlier_ratio)

    cuts = tuple(round(float(curve.xs[i + 1]), 1) for i in np.nonzero(jumps)[0])
    checkpoints = []
    t = 0.0
    while t <= last_end:
        mapped = curve(t)
        if 0 <= mapped <= audio_duration + 60:
            checkpoints.append(Checkpoint(t_audio=round(mapped, 1), offset=round(mapped - t, 2)))
        t += th.checkpoint_every

    # só marcos confiáveis puxam o tempo do áudio
    snapped = {m.cue: m for m, ok in zip(matches, curve.inliers, strict=True) if ok}
    out: list[SyncedCue] = []
    prev_start = -1.0
    for idx, c in enumerate(cues):
        start = curve(c.start)
        m = snapped.get(idx)
        if m is not None and abs(m.t_audio - start) <= th.snap_tolerance and m.t_audio > prev_start:
            start = m.t_audio
        if start <= prev_start:
            start = prev_start + 0.001
        end = start + max(0.2, (c.end - c.start) * curve.slope(c.start))
        status: CueStatus = "casada" if m else ("sem_par" if speech[idx] else "nao_fala")
        out.append(
            SyncedCue(
                index=c.index,
                start=round(max(0.0, start), 3),
                end=round(max(0.0, end), 3),
                text=c.text,
                status=status,
                segment_id=m.unit if m else None,
                conf=round(m.conf, 3) if m else 0.0,
            )
        )
        prev_start = start
    for k in range(len(out) - 1):  # sobreposição: ajusta o fim da anterior, nunca o início
        if out[k].end > out[k + 1].start - 0.04:
            new_end = max(out[k].start + 0.1, out[k + 1].start - 0.04)
            out[k] = out[k].model_copy(update={"end": round(new_end, 3)})
    drift = checkpoints[-1].offset - checkpoints[0].offset if len(checkpoints) > 1 else 0.0
    return SyncResult(
        accepted=True,
        fits=(fit,),
        matched_ratio=round(matched_ratio, 3),
        inlier_ratio=round(inlier_ratio, 3),
        mean_error=round(mean_error, 3),
        duration_ratio=duration_ratio,
        framerate_hint=framerate_hint(curve.a),
        checkpoints=tuple(checkpoints),
        drift=round(drift, 2),
        cuts=cuts,
        cues=tuple(out),
    )
