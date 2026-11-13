"""Montar a legenda: a letra encaixada (♪) e o que só a transcrição ouviu.

- linha da letra aceita pelo encaixe → ♪ com o texto oficial;
- frase ouvida dentro de uma música que bate com uma linha dela (refrão a mais, verso fora de
  ordem) → ♪ com o texto oficial, no horário em que foi ouvida;
- o resto do que foi ouvido (fala entre as músicas, gritos) → o texto do Whisper, se for confiável.
"""

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from rapidfuzz import fuzz, process

from c3parr.concert.fit import Played
from c3parr.concert.words import Phrase, norm

MIN_P = 0.5  # confiança média mínima de uma frase ouvida
SAME_LINE = 80  # semelhança para trocar o ouvido pela linha oficial
MAX_HEARD_S = 7.0  # uma frase ouvida fica no máximo tanto tempo na tela
NOISE = frozenset({
    "thank you", "thanks for watching", "we ll be right back", "all right", "amen", "bye",
    "music", "applause", "you",
})  # fmt: skip
"""O que o Whisper inventa em trecho sem voz."""

Kind = Literal["lyric", "lyric-heard", "heard"]


@dataclass
class Row:
    start: float
    end: float
    text: str
    kind: Kind
    """`lyric`: encaixada; `lyric-heard`: oficial, no horário ouvido; `heard`: só ouvida."""


def sung(text: str) -> str:
    return f"♪ {text} ♪"


def build(played: Sequence[Played], heard: Sequence[Phrase], total_s: float,
          doubt: Callable[[float, float, str], bool] | None = None) -> list[Row]:  # fmt: skip
    """`heard`: as frases ouvidas (dentro das músicas, a primeira transcrição; fora, a fala).
    `doubt(início, fim, texto)`: diz se uma frase ouvida é duvidosa demais para ir à legenda."""
    rows = [Row(f.start, f.end, sung(f.text), "lyric")
            for song in played for f in song.fits if f.accepted]  # fmt: skip
    lyric = sorted(rows, key=lambda r: r.start)
    seen = Counter(norm(text) for _, _, text, _ in heard)
    for start, end, text, p in heard:
        plain = norm(text)
        size = len(plain.split())
        covered = sum(max(0.0, min(end, r.end + 0.4) - max(start, r.start - 0.4)) for r in lyric)
        if not plain or covered > 0.3 * max(0.2, end - start):
            continue
        song = next((s for s in played if s.start <= start <= s.end), None)
        if song and size >= 3:
            best = process.extractOne(plain, [norm(x) for x in song.lines],
                                      scorer=fuzz.token_set_ratio)  # fmt: skip
            if best and best[1] >= SAME_LINE:
                rows.append(Row(start, end, sung(song.lines[best[2]]), "lyric-heard"))
                continue
        invented = (plain in NOISE or (seen[plain] >= 3 and size <= 6)
                    or (end - start >= 20 and size <= 6))  # fmt: skip
        if p < MIN_P or invented or (doubt is not None and doubt(start, end, text)):
            continue
        rows.append(Row(start, min(end, start + MAX_HEARD_S), text.strip(), "heard"))
    rows.sort(key=lambda r: r.start)
    for n, row in enumerate(rows):
        limit = rows[n + 1].start - 0.05 if n + 1 < len(rows) else total_s
        row.end = max(min(max(row.end + 0.6, row.start + 1.3), limit), row.start + 0.3)
    return rows


def count(rows: Sequence[Row]) -> dict[str, int]:
    kinds = ("lyric", "lyric-heard", "heard")
    return {kind: sum(1 for r in rows if r.kind == kind) for kind in kinds}
