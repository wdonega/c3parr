from itertools import pairwise
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from c3parr.core.models import Cue, Segment, Subtitle, Word
from c3parr.subtitle.format import FormatRules, cues_from_segments, wrap_lines
from c3parr.subtitle.srt import format_srt, format_time, parse_srt, read_srt

SAMPLE = (
    "﻿1\r\n00:00:01,000 --> 00:00:02,500\r\nOlá.\r\n\r\n"
    "2\r\n00:00:03,000 --> 00:00:04,000 X1:0\r\n- Oi.\r\n- Tudo bem?\r\n"
)


def test_parse_tolerates_bom_crlf_and_positions() -> None:
    sub = parse_srt(SAMPLE)
    assert [(c.start, c.end, c.text) for c in sub.cues] == [
        (1.0, 2.5, "Olá."),
        (3.0, 4.0, "- Oi.\n- Tudo bem?"),
    ]


def test_cp1252_file_is_decoded(tmp_path: Path) -> None:
    path = tmp_path / "antiga.srt"
    path.write_bytes("1\n00:00:01,000 --> 00:00:02,000\nAção\n".encode("cp1252"))
    assert read_srt(path).cues[0].text == "Ação"


text_st = st.text(
    alphabet=st.characters(blacklist_categories=("Cs", "Cc"), blacklist_characters="\r"),
    min_size=1,
).map(lambda s: "\n".join(line.strip() for line in s.split("\n") if line.strip()))


@given(st.lists(st.tuples(st.integers(0, 10_000_000), st.integers(1, 9000), text_st), max_size=20))
def test_srt_round_trip(items: list[tuple[int, int, str]]) -> None:
    cues, t = [], 0
    for gap, dur, text in items:
        if not text or "-->" in text:
            continue
        start, t = t + gap, t + gap + dur
        cues.append(Cue(index=len(cues) + 1, start=start / 1000, end=t / 1000, text=text))
    sub = Subtitle(cues=tuple(cues))
    assert parse_srt(format_srt(sub)) == sub


def test_format_time() -> None:
    assert format_time(3661.5) == "01:01:01,500"
    assert format_time(-1) == "00:00:00,000"


def test_wrap_keeps_lines_within_limit() -> None:
    text = "Conformément à la volonté, au règlement et au droit de Mme Nawal Marwan,"
    lines = wrap_lines(text, 42).split("\n")
    assert len(lines) == 2
    assert all(len(line) <= 42 for line in lines)


def test_wrap_prefers_punctuation_when_it_fits() -> None:
    lines = wrap_lines("Je dois vous dire ici, c'est la décision de votre mère.", 42).split("\n")
    assert lines == ["Je dois vous dire ici,", "c'est la décision de votre mère."]


def _segment(i: int, start: float, words: list[tuple[str, float]]) -> Segment:
    t, out = start, []
    for w, dur in words:
        out.append(Word(w=w, start=round(t, 3), end=round(t + dur, 3)))
        t += dur + 0.05
    text = " ".join(w for w, _ in words)
    return Segment(id=i, start=start, end=t, text=text, lang="fr", words=tuple(out))


word_st = st.tuples(
    st.text("abcdefghijklmnopqrstuvwxyzçé.,?", min_size=1, max_size=14), st.floats(0.05, 0.9)
)
segments_st = st.lists(
    st.tuples(st.floats(0.0, 6.0), st.lists(word_st, min_size=1, max_size=40)), max_size=12
)


@given(segments_st)
def test_formatter_invariants(raw: list[tuple[float, list[tuple[str, float]]]]) -> None:
    rules = FormatRules()
    segments, t = [], 0.0
    for i, (gap, words) in enumerate(raw):
        seg = _segment(i, t + gap, words)
        segments.append(seg)
        t = seg.end
    cues = cues_from_segments(segments, rules)
    for a, b in pairwise(cues):
        assert b.start >= a.end + rules.min_gap - 1e-6
    for c in cues:
        assert c.end > c.start
        assert c.end - c.start <= rules.max_duration + 1e-6
        lines = c.text.split("\n")
        assert len(lines) <= rules.max_lines
        for line in lines:
            assert len(line) <= rules.max_chars_per_line or " " not in line


def test_known_hallucination_is_dropped() -> None:
    seg = _segment(0, 0.0, [("Sous-titrage", 0.5), ("ST'", 0.2), ("501", 0.3)])
    flagged = seg.model_copy(update={"flags": frozenset({"alucinacao_conhecida"})})
    assert cues_from_segments([flagged]) == []


def test_evaluation_pairs_by_time_and_scores_text() -> None:
    from c3parr.report.evaluate import evaluate

    gold = Subtitle(cues=(Cue(index=1, start=10, end=12, text="Você está cansado?"),
                          Cue(index=2, start=20, end=22, text="Não, estou bem."),
                          Cue(index=3, start=40, end=42, text="Sem par.")))  # fmt: skip
    ours = Subtitle(cues=(Cue(index=1, start=10.2, end=12, text="Você está cansado?"),
                          Cue(index=2, start=20.5, end=22, text="Não, tudo bem.")))  # fmt: skip
    ev = evaluate(ours, gold)
    assert ev.coverage == pytest.approx(2 / 3, abs=0.01)
    assert ev.pairs[0].similarity == 1.0
    assert ev.median_start_diff == pytest.approx(0.35)


def test_lines_in_another_language_get_a_tag_once_per_run() -> None:
    from c3parr.core.models import Segment
    from c3parr.subtitle.format import FormatRules, cues_from_translation
    from c3parr.translate.languages import foreign_tag

    langs = ["fr", "ar", "ar", "fr", "ar"]
    segs = [Segment(id=i, start=i * 3, end=i * 3 + 2, text="x", lang=lang)
            for i, lang in enumerate(langs)]  # fmt: skip
    texts = {i: f"fala {i}" for i in range(5)}

    def tag(lang: str) -> str:
        return foreign_tag(lang, "pt-BR")

    cues = cues_from_translation(segs, texts, FormatRules(), "fr", tag)
    assert [c.text for c in cues] == [
        "fala 0", "[em árabe] fala 1", "fala 2", "fala 3", "[em árabe] fala 4"]  # fmt: skip
    every = cues_from_translation(segs, texts, FormatRules(foreign_tag="every"), "fr", tag)
    assert [c.text for c in every][2] == "[em árabe] fala 2"
    off = cues_from_translation(segs, texts, FormatRules(foreign_tag="off"), "fr", tag)
    assert not any("[" in c.text for c in off)
    assert foreign_tag("en", "es") == "[in English]"
