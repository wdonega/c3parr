"""Letras e transcrições inventadas para os testes de show (nenhuma letra de verdade aqui)."""

import random

from c3parr.concert.catalogue import Catalogue, Song
from c3parr.concert.fit import Fit, Played
from c3parr.concert.words import Heard, norm

VOCAB = [
    "amber",
    "river",
    "cold",
    "lantern",
    "window",
    "paper",
    "harbor",
    "silver",
    "engine",
    "morning",
    "quiet",
    "thunder",
    "garden",
    "shadow",
    "copper",
    "winter",
    "letter",
    "mountain",
    "candle",
    "ocean",
    "ribbon",
    "static",
    "velvet",
    "orbit",
    "hollow",
    "ember",
    "signal",
    "marble",
    "north",
    "falling",
    "running",
    "waiting",
    "burning",
    "turning",
    "broken",
    "open",
    "under",
    "over",
    "after",
    "before",
    "never",
    "always",
    "maybe",
    "slowly",
    "again",
    "alone",
    "together",
    "away",
    "home",
]


def song(title: str, seed: int, lines: int = 24, artist: str = "Banda Teste") -> Song:
    rng = random.Random(seed)
    text = tuple(
        " ".join(rng.choice(VOCAB) for _ in range(rng.randint(5, 8))).capitalize()
        for _ in range(lines)
    )
    synced = tuple((10.0 + 4.0 * n, line) for n, line in enumerate(text))
    return Song(artist=artist, title=title, album="Disco", year=2000, length_s=240.0,
                lines=text, synced=synced)  # fmt: skip


def catalogue(*songs: Song) -> Catalogue:
    return Catalogue(artists=("Banda Teste",), until=2001, songs=songs)


def sung(lines: tuple[str, ...] | list[str], start: float, per_line: float = 4.0,
         wrong_every: int = 0, segment: int = 0) -> list[Heard]:  # fmt: skip
    """As palavras de `lines` como se o Whisper as ouvisse a partir de `start`; `wrong_every`
    troca uma palavra a cada tantas (o Whisper erra)."""
    out, count = [], 0
    for n, line in enumerate(lines):
        words = norm(line).split()
        for k, w in enumerate(words):
            count += 1
            heard = "zzz" if wrong_every and count % wrong_every == 0 else w
            at = start + n * per_line + k * (per_line * 0.8 / len(words))
            out.append(Heard(at, at + 0.3, heard, heard, 0.9, segment + n))
    return out


def talk(text: str, start: float, segment: int, p: float = 0.95) -> list[Heard]:
    return [Heard(start + 0.3 * k, start + 0.3 * k + 0.25, w, norm(w), p, segment)
            for k, w in enumerate(text.split())]  # fmt: skip


def fit(line: int, start: float, end: float, score: float,
        text: str = "amber river cold lantern window", **more: object) -> Fit:  # fmt: skip
    return Fit.model_validate({"line": line, "text": text, "start": start, "end": end,
                               "score": score, **more})  # fmt: skip


def played(lines: tuple[str, ...], fits: list[Fit], start: float, end: float) -> Played:
    return Played(title="Alfa", artist="Banda Teste", first=start, last=end, start=start, end=end,
                  lines=lines, fits=tuple(fits))  # fmt: skip
