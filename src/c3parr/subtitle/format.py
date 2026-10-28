"""Transforma segmentos com tempo por palavra em cues legíveis. Determinístico, sem LLM."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from c3parr.core.models import Cue, Model, Segment, Word

SENTENCE_END = (".", "!", "?", "…")
CLAUSE_END = (",", ";", ":")
DROP_FLAGS = frozenset({"alucinacao_conhecida"})


class FormatRules(Model):
    max_lines: int = 2
    max_chars_per_line: int = 42
    max_cps: float = 17.0
    min_duration: float = 1.0
    max_duration: float = 7.0
    min_gap: float = 0.08
    pause_break: float = 0.8
    """Pausa entre palavras (s) que força uma cue nova."""
    foreign_tag: Literal["first", "every", "off"] = "first"
    """Fala em língua diferente da principal ganha uma marca (`[em árabe]`): só na primeira de
    cada trecho seguido nessa língua (padrão das legendas profissionais), em todas, ou nunca."""


@dataclass
class _Group:
    words: list[Word]

    @property
    def text(self) -> str:
        return " ".join(w.w for w in self.words)


def wrap_lines(text: str, max_chars: int, max_lines: int = 2) -> str:
    """Quebra em até `max_lines` linhas equilibradas, preferindo fronteira de frase."""
    text = " ".join(text.split())
    if len(text) <= max_chars or max_lines < 2:
        return text
    words = text.split(" ")
    best: tuple[float, int] | None = None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        over = max(0, len(a) - max_chars) + max(0, len(b) - max_chars)
        score = over * 100 + abs(len(a) - len(b))
        if words[i - 1].endswith(SENTENCE_END):
            score -= 30
        elif words[i - 1].endswith(CLAUSE_END):
            score -= 15
        if best is None or score < best[0]:
            best = (score, i)
    if best is None:
        return text
    i = best[1]
    first, rest = " ".join(words[:i]), " ".join(words[i:])
    if max_lines > 2:
        return first + "\n" + wrap_lines(rest, max_chars, max_lines - 1)
    return first + "\n" + rest


def _segment_words(seg: Segment) -> list[Word]:
    words = [w for w in seg.words if w.w.strip()]
    if words:
        return [
            Word(w=w.w.strip(), start=w.start, end=max(w.end, w.start), conf=w.conf) for w in words
        ]
    return [Word(w=seg.text.strip(), start=seg.start, end=seg.end)] if seg.text.strip() else []


def _fits(words: list[Word], rules: FormatRules) -> bool:
    if words[-1].end - words[0].start > rules.max_duration:
        return False
    text = wrap_lines(" ".join(w.w for w in words), rules.max_chars_per_line, rules.max_lines)
    lines = text.split("\n")
    return len(lines) <= rules.max_lines and all(
        len(line) <= rules.max_chars_per_line or " " not in line for line in lines
    )


def _cut_point(words: list[Word]) -> int:
    """Onde dividir um grupo que não cabe: última pontuação na segunda metade, senão o fim."""
    for j in range(len(words) - 2, max(0, (len(words) - 1) // 2) - 1, -1):
        if words[j].w.endswith(SENTENCE_END + CLAUSE_END):
            return j + 1
    return len(words) - 1


def _split(words: list[Word], rules: FormatRules) -> list[_Group]:
    groups: list[_Group] = []
    cur: list[Word] = []
    for w in words:
        if cur and w.start - cur[-1].end > rules.pause_break:
            groups.append(_Group(cur))
            cur = []
        cur.append(w)
        while len(cur) > 1 and not _fits(cur, rules):
            cut = _cut_point(cur)
            groups.append(_Group(cur[:cut]))
            cur = cur[cut:]
    if cur:
        groups.append(_Group(cur))
    return groups


def cues_from_segments(segments: Sequence[Segment], rules: FormatRules | None = None) -> list[Cue]:
    rules = rules or FormatRules()
    groups: list[_Group] = []
    for seg in sorted(segments, key=lambda s: s.start):
        if seg.flags & DROP_FLAGS:
            continue
        groups.extend(_split(_segment_words(seg), rules))

    return _finalize([(g.words[0].start, g.words[-1].end, g.text) for g in groups], rules)


def _finalize(spans: list[tuple[float, float, str]], rules: FormatRules) -> list[Cue]:
    """Quebra as linhas, estende o fim para dar tempo de leitura e garante o intervalo mínimo."""
    timed: list[tuple[float, float, str]] = []
    for start, end, raw in sorted(spans, key=lambda s: s[0]):
        text = wrap_lines(raw, rules.max_chars_per_line, rules.max_lines)
        needed = max(rules.min_duration, len(text.replace("\n", "")) / rules.max_cps)
        end = max(end, start + needed)
        timed.append((start, min(end, start + rules.max_duration), text))

    cues: list[Cue] = []
    for i, (start, end, text) in enumerate(timed):
        if i + 1 < len(timed):
            end = min(end, timed[i + 1][0] - rules.min_gap)
        if cues and start < cues[-1].end + rules.min_gap:
            start = cues[-1].end + rules.min_gap
        end = max(end, start + 0.04)
        cues.append(Cue(index=len(cues) + 1, start=round(start, 3), end=round(end, 3), text=text))
    return cues


def _text_fits(text: str, rules: FormatRules) -> bool:
    lines = wrap_lines(text, rules.max_chars_per_line, rules.max_lines).split("\n")
    return len(lines) <= rules.max_lines and all(
        len(line) <= rules.max_chars_per_line or " " not in line for line in lines
    )


def split_text(text: str, rules: FormatRules) -> list[str]:
    """Divide um texto (traduzido) em pedaços que cabem numa cue, preferindo pontuação."""
    chunks: list[str] = []
    cur: list[str] = []
    for word in text.split():
        cur.append(word)
        while len(cur) > 1 and not _text_fits(" ".join(cur), rules):
            cut = len(cur) - 1
            for j in range(len(cur) - 2, max(0, (len(cur) - 1) // 2) - 1, -1):
                if cur[j].endswith(SENTENCE_END + CLAUSE_END):
                    cut = j + 1
                    break
            chunks.append(" ".join(cur[:cut]))
            cur = cur[cut:]
    if cur:
        chunks.append(" ".join(cur))
    return chunks


def _time_at(seg: Segment, fraction: float) -> float:
    """Instante da fala original que corresponde a uma fração do texto (pelos tempos de palavra)."""
    words = _segment_words(seg)
    total = sum(len(w.w) for w in words) or 1
    acc = 0
    for w in words:
        if acc / total >= fraction:
            return w.start
        acc += len(w.w)
    return seg.end


def cues_from_translation(
    segments: Sequence[Segment],
    texts: Mapping[int, str],
    rules: FormatRules | None = None,
    main_lang: str | None = None,
    tag: Callable[[str], str] | None = None,
) -> list[Cue]:
    """Cues a partir do texto traduzido de cada segmento, com o tempo do áudio original.

    Quando a tradução não cabe numa cue, ela é dividida e o tempo de cada pedaço vem dos tempos
    por palavra do ASR, na proporção do texto. Com `main_lang` e `tag`, a fala em outra língua
    ganha a marca de `tag(lang)` conforme `rules.foreign_tag`.
    """
    rules = rules or FormatRules()
    main = main_lang.lower().split("-")[0] if main_lang else None
    spans: list[tuple[float, float, str]] = []
    previous: str | None = None
    for seg in segments:
        text = " ".join(texts.get(seg.id, "").split())
        if not text or seg.flags & DROP_FLAGS:
            continue
        lang = seg.lang.lower().split("-")[0] if seg.lang else None
        foreign = lang if main and lang and lang != main else None
        new_run = rules.foreign_tag == "every" or foreign != previous
        if foreign and tag and rules.foreign_tag != "off" and new_run:
            text = f"{tag(foreign)} {text}"
        previous = foreign
        chunks = split_text(text, rules)
        total = sum(len(c) for c in chunks)
        acc = 0
        for k, chunk in enumerate(chunks):
            start = seg.start if k == 0 else _time_at(seg, acc / total)
            acc += len(chunk)
            end = seg.end if k == len(chunks) - 1 else _time_at(seg, acc / total)
            spans.append((start, max(end, start + 0.2), chunk))
    return _finalize(spans, rules)
