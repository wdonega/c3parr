"""A transcrição como palavras com horário e confiança: é o que se compara com a letra."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from c3parr.core.models import Transcript

Phrase = tuple[float, float, str, float]
"""Uma frase ouvida: início, fim, texto e confiança (0 a 1)."""


@dataclass(frozen=True)
class Heard:
    start: float
    end: float
    text: str  # como o Whisper escreveu
    norm: str  # minúsculas, sem pontuação
    p: float  # a menor confiança entre os pedaços da palavra
    segment: int


def norm(text: str) -> str:
    text = text.lower().replace("\u2019", "'").replace("'", "")
    return re.sub(r"[^a-z0-9à-ÿ]+", " ", text).strip()


def words(transcript: Transcript) -> list[Heard]:
    """Uma "palavra" com espaço dentro (número, hífen) vira várias, com o mesmo horário."""
    out: list[Heard] = []
    for seg in transcript.segments:
        for w in seg.words:
            p = 1.0 if w.conf is None else w.conf
            out.extend(Heard(w.start, w.end, w.w, part, p, seg.id) for part in norm(w.w).split())
    return out


def phrases(heard: Sequence[Heard]) -> list[Phrase]:
    """As frases da transcrição: um segmento do Whisper cada."""
    by_segment: dict[int, list[Heard]] = {}
    for w in heard:
        by_segment.setdefault(w.segment, []).append(w)
    out: list[Phrase] = []
    for group in by_segment.values():
        text: list[str] = []
        last: tuple[float, str] | None = None
        for w in group:  # a palavra partida em várias entra uma vez só
            if (w.start, w.text) != last:
                text.append(w.text)
            last = (w.start, w.text)
        p = sum(w.p for w in group) / len(group)
        out.append((group[0].start, group[-1].end, " ".join(text), p))
    return out
