"""Sincronização com dados sintéticos de drift conhecido (reta, outliers, referência errada)."""

from pathlib import Path

import numpy as np
import pytest

from c3parr.align.core import Match, Thresholds, build_result, monotonic_align, ransac_line
from c3parr.align.text import clean, normalize
from c3parr.core.job import ComponentSpec, JobParams
from c3parr.core.models import Cue, Subtitle
from c3parr.engine.engine import Engine
from c3parr.providers.registry import build_factories
from c3parr.recipes import sync as sync_recipe
from c3parr.subtitle.ass import parse_ass
from c3parr.subtitle.file_source import guess_lang
from c3parr.subtitle.srt import read_srt, write_srt

A, B = 25 / (24000 / 1001), -2.0  # legenda de 25 fps num vídeo 23,976, com 2 s de atraso


def ref_subtitle(n: int, spacing: float = 4.0) -> Subtitle:
    cues = tuple(
        Cue(index=i + 1, start=10 + i * spacing, end=12 + i * spacing, text=f"fala {i}")
        for i in range(n)
    )
    return Subtitle(cues=cues, lang="fr")


def test_monotonic_align_ignores_out_of_order_distractor() -> None:
    w = np.eye(6) * 0.8
    w[1, 5] = 0.95  # casamento forte mas fora de ordem
    assert monotonic_align(w) == [(i, i) for i in range(6)]


def test_monotonic_align_allows_two_cues_in_one_unit() -> None:
    w = np.array([[0.9, 0.0], [0.9, 0.0], [0.0, 0.9]])
    assert monotonic_align(w) == [(0, 0), (1, 0), (2, 1)]


def test_ransac_recovers_line_with_outliers() -> None:
    rng = np.random.default_rng(1)
    x = np.linspace(0, 7000, 300)
    y = A * x + B + rng.normal(0, 0.1, 300)
    y[::5] += rng.uniform(-60, 60, 60)  # 20% de pares mal casados
    a, b, inliers = ransac_line(x, y, tolerance=1.0)
    assert a == pytest.approx(A, abs=1e-4)
    assert b == pytest.approx(B, abs=0.2)
    assert 0.75 < inliers.mean() < 0.85


def test_linear_drift_is_corrected_and_labelled() -> None:
    ref = ref_subtitle(200)
    matches = [
        Match(cue=i, unit=i, t_audio=A * c.start + B, conf=0.9) for i, c in enumerate(ref.cues)
    ]
    result = build_result(ref, [True] * 200, matches, 1000.0, Thresholds())
    assert result.accepted, result.reason
    assert result.fits[0].a == pytest.approx(A, abs=1e-5)
    assert result.mean_error is not None and result.mean_error < 0.01
    assert result.framerate_hint is not None and "25 fps" in result.framerate_hint
    for c, s in zip(ref.cues, result.cues, strict=True):
        assert s.start == pytest.approx(A * c.start + B, abs=0.002)


def test_wrong_reference_is_rejected() -> None:
    ref = ref_subtitle(200)
    rng = np.random.default_rng(3)
    times = np.sort(rng.uniform(0, 800, 70))
    matches = [Match(cue=i * 2, unit=i, t_audio=float(t), conf=0.7) for i, t in enumerate(times)]
    result = build_result(ref, [True] * 200, matches, 800.0, Thresholds())
    assert not result.accepted
    assert result.reason is not None


def test_too_few_matches_is_rejected() -> None:
    ref = ref_subtitle(100)
    matches = [Match(cue=i, unit=i, t_audio=ref.cues[i].start, conf=0.9) for i in range(10)]
    result = build_result(ref, [True] * 100, matches, 500.0, Thresholds())
    assert not result.accepted
    assert "10%" in (result.reason or "")


def test_clean_strips_hearing_impaired_marks() -> None:
    assert clean("[MUSIC PLAYING]\nJOHN: Hey, <i>you</i>!") == "Hey, you!"
    assert clean("* THERE'S NO NEED TO BLEED") == ""
    assert normalize("Ça va, Hélène ?") == "ca va helene"


@pytest.mark.parametrize(
    ("name", "lang"),
    [
        ("Incendies 2010 1080p BluRay x264 AAC - Ozlem.pt-BR.hi.srt", "pt-BR"),
        ("Incendies 2010 1080p BluRay x264 AAC - Ozlem.en.hi.srt", "en"),
        ("Criminal Minds - S01E01 - Extreme Aggressor WEBDL-1080p.BR.pt (1).srt", "pt"),
        ("Incendies (2010) Bluray-1080p.hi.srt", None),
    ],
)
def test_guess_lang(name: str, lang: str | None) -> None:
    assert guess_lang(Path(name)) == lang


def test_parse_ass_reduces_to_text_and_time() -> None:
    text = (
        "[Script Info]\nTitle: x\n\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.50,0:00:03.00,Default,,0,0,0,,{\\an8}Olá,\\Nmundo\n"
    )
    sub = parse_ass(text)
    assert [(c.start, c.end, c.text) for c in sub.cues] == [(1.5, 3.0, "Olá,\nmundo")]


LINES = tuple(f"Phrase numéro {i}, avec quelques mots différents {i * 7}." for i in range(30))


def test_sync_recipe_end_to_end(tiny_video: Path, tmp_path: Path) -> None:
    # o falso fala a linha i em t = 1 + 3i; a referência tem drift linear conhecido
    a, b = 1.02, -0.3
    cues = tuple(
        Cue(index=i + 1, start=((1 + 3 * i) - b) / a, end=((3 + 3 * i) - b) / a, text=line)
        for i, line in enumerate(LINES)
    )
    ref_path = tmp_path / "ref.fr.srt"
    write_srt(Subtitle(cues=cues), ref_path)
    engine = Engine(
        build_factories(),
        {
            "transcricao": ComponentSpec(provider="falso", params={"lines": LINES}),
            "fonte_legenda": ComponentSpec(provider="arquivo"),
            "sincronizacao": ComponentSpec(provider="texto"),
            "escrita_legenda": ComponentSpec(provider="srt"),
        },
        {},
        tmp_path / "cache",
    )
    params = JobParams(
        recipe="sincronizacao",
        video=str(tiny_video),
        src="fr",
        ref=str(ref_path),
        out_dir=str(tmp_path / "out"),
    )
    result = engine.run(sync_recipe.recipe(), params)
    assert result.ok, result.failed
    out = read_srt(tmp_path / "out" / "Filme Teste (2001).fr.srt")
    assert [c.text for c in out.cues] == list(LINES)
    for i, c in enumerate(out.cues):
        assert c.start == pytest.approx(1 + 3 * i, abs=0.05)


def _matches_from(curve, ref: Subtitle, noise: float = 0.1, seed: int = 0) -> list[Match]:  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(seed)
    return [
        Match(cue=i, unit=i, t_audio=float(curve(c.start) + rng.normal(0, noise)), conf=0.9)
        for i, c in enumerate(ref.cues)
    ]


def test_slowly_growing_drift_is_followed_by_landmarks() -> None:
    # deslocamento que cresce sem ser reta: 0 s no começo, ~25 s no fim de 2h
    ref = ref_subtitle(1800, spacing=4.0)

    def curve(t: float) -> float:
        return t + 25 * (t / 7200) ** 2

    result = build_result(ref, [True] * 1800, _matches_from(curve, ref), 7300.0, Thresholds())
    assert result.accepted, result.reason
    errors = [abs(s.start - curve(c.start)) for c, s in zip(ref.cues, result.cues, strict=True)]
    assert max(errors) < 0.6
    assert result.drift is not None and result.drift > 15
    assert len(result.checkpoints) >= 10


def test_cut_in_reference_is_detected_and_both_sides_line_up() -> None:
    # a referência perdeu 20 s de cena em t=3000: depois disso o áudio está 20 s à frente
    ref = ref_subtitle(1500, spacing=4.0)

    def curve(t: float) -> float:
        return t + (20.0 if t >= 3000 else 0.0)

    result = build_result(ref, [True] * 1500, _matches_from(curve, ref), 6100.0, Thresholds())
    assert result.accepted, result.reason
    assert any(2950 <= c <= 3050 for c in result.cuts)
    far = [
        abs(s.start - curve(c.start))
        for c, s in zip(ref.cues, result.cues, strict=True)
        if abs(c.start - 3000) > 60
    ]
    assert max(far) < 0.6
