"""Lógica do provider faster-whisper que não precisa do modelo."""

from c3parr.asr.faster_whisper import (
    FasterWhisperConfig,
    flags_for,
    majority_language,
    merge_tokens,
    sample_regions,
    uncovered_regions,
)
from c3parr.core.models import LanguageSpan, Segment, SegmentFlag


def test_french_elisions_are_glued() -> None:
    tokens = [(" J", 0.0, 0.2, 0.9), ("'avoue", 0.2, 0.4, 0.8), (" que", 0.4, 0.5, 0.95),
              (" c", 0.5, 0.52, 0.9), ("'est", 0.52, 0.6, 0.9), (" ça", 0.6, 0.7, 0.9),
              (" ?", 0.7, 0.75, 0.9)]  # fmt: skip
    words = merge_tokens(tokens)
    assert [w.w for w in words] == ["J'avoue", "que", "c'est", "ça", "?"]
    assert (words[0].start, words[0].end, words[0].conf) == (0.0, 0.4, 0.8)


def span(start: float, end: float, lang: str, prob: float) -> LanguageSpan:
    return LanguageSpan(start=start, end=end, lang=lang, prob=prob)


def seg(i: int, start: float, end: float, *flags: SegmentFlag) -> Segment:
    return Segment(id=i, start=start, end=end, text="x", lang="fr", flags=frozenset(flags))


def test_uncovered_regions_finds_speech_without_reliable_text() -> None:
    regions = [(0.0, 10.0), (20.0, 30.0), (40.0, 44.0), (50.0, 50.5)]
    segments = [seg(0, 0.5, 9.0), seg(1, 41.0, 43.0, "alucinacao_conhecida")]
    assert uncovered_regions(regions, segments) == [(20.0, 30.0), (40.0, 44.0)]


def test_sample_regions_spreads_over_the_audio() -> None:
    regions = [(float(t), float(t) + 3) for t in range(0, 700, 10)]
    picked = sample_regions(regions, n=7)
    assert len(picked) == 7
    assert picked[0][0] < 100 and picked[-1][0] >= 600


def test_majority_language_weights_by_duration_and_confidence() -> None:
    spans = [span(0, 30, "fr", 0.9), span(40, 45, "ar", 0.99), span(50, 60, "en", 0.4)]
    assert majority_language(spans) == "fr"


def test_fast_but_real_dialogue_is_not_flagged() -> None:
    cfg = FasterWhisperConfig()
    text = "On sait de qui on parle, il n'y a rien d'étonnant là-dedans."
    assert "fala_rapida" not in flags_for(text, 0.0, 1.94, -0.2, 1.3, cfg)
    assert "fala_rapida" in flags_for(text * 2, 0.0, 1.94, -0.2, 1.3, cfg)
    assert "alucinacao_conhecida" in flags_for("Sous-titrage ST' 501", 0, 3, -0.2, 1.0, cfg)


def test_language_hint_wins_with_a_fair_share_but_not_on_dubbed_audio() -> None:
    from c3parr.core.models import LanguageSpan

    film = [LanguageSpan(start=0, end=30, lang="ar", prob=0.9),
            LanguageSpan(start=40, end=70, lang="ar", prob=0.9),
            LanguageSpan(start=80, end=100, lang="fr", prob=0.9)]  # fmt: skip
    assert majority_language(film) == "ar"
    assert majority_language(film, hint="fr") == "fr"
    dubbed = [LanguageSpan(start=0, end=60, lang="en", prob=0.9),
              LanguageSpan(start=60, end=62, lang="fr", prob=0.5)]  # fmt: skip
    assert majority_language(dubbed, hint="fr-FR") == "en"
