"""Quais músicas tocam, e onde: a transcrição (palavras com horário) contra o catálogo (letras).

Cada trio de palavras seguidas da transcrição que existe na letra de alguma música é um indício
dela naquele instante. Trio que aparece em muitas músicas ("i dont know") vale pouco; trio que só
uma música tem vale muito. Os indícios de cada música se agrupam no tempo; onde dois grupos
disputam o mesmo trecho, fica o que explica mais (um mashup ganha das duas músicas que o formam,
porque contém os versos das duas).
"""

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from c3parr.concert.catalogue import MIN_LINES, Catalogue, Song
from c3parr.concert.words import Heard, norm

GAP_S = 150.0  # silêncio de letra que ainda é a mesma música (solo longo)
BIN_S = 15.0  # o show é dividido em trechos; cada trecho é da música com mais indício em volta
AROUND = 4  # trechos de cada lado que contam como "em volta" (1 minuto)
SMOOTH_MIN = 2.0  # indício mínimo em volta (1.0 = um trio que só uma música tem)
MIN_HITS = 6  # trios distintos para acreditar numa música
MIN_SONG_LINES = 3  # em pelo menos tantas linhas diferentes da letra
MIN_WEIGHT = 4.0
SAME_SONG_S = 480.0  # a mesma música de novo, sem outra no meio: é ela ainda (solo longo)
NEAR_S = 45.0  # distância para uma ocorrência ser "colada" na vizinha
ABSORB = 0.6  # fração dos trios dela que a letra da vizinha também tem

Trio = tuple[str, ...]


@dataclass
class Found:
    song: int  # índice em `listed(catalogue)`
    title: str
    artist: str
    first: float  # primeiro e último indício (segundos)
    last: float
    hits: int = 0
    lines: int = 0
    weight: float = 0.0
    start: float = 0.0  # janela para o encaixe: até onde a música pode se estender
    end: float = 0.0
    line_ids: set[int] = field(default_factory=set, repr=False)
    shingles: set[Trio] = field(default_factory=set, repr=False)


def shingles(words: Sequence[str], n: int = 3) -> list[Trio]:
    return [tuple(words[i : i + n]) for i in range(len(words) - n + 1)]


def trios(song: Song) -> set[Trio]:
    return set(shingles(norm(" ".join(song.lines)).split()))


def listed(catalogue: Catalogue) -> list[Song]:
    """As músicas com letra, sem repetidas: a mesma faixa vem de mais de um álbum ou artista
    (um mashup aparece nos dois), com ou sem "(versão tal)" no título."""
    out: dict[str, Song] = {}
    for song in catalogue.songs:
        if len(song.lines) < MIN_LINES:
            continue
        key = norm(re.sub(r"\([^)]*\)", " ", song.title))
        old = out.get(key)
        if old is None:
            out[key] = song
            continue
        a, b = trios(old), trios(song)  # a quebra de linha muda entre fontes; os trios não
        if len(a & b) >= 0.5 * min(len(a), len(b)):  # mesma letra: fica a mais completa
            if len(song.lines) > len(old.lines):
                out[key] = song.model_copy(update={"artist": old.artist})
        else:  # mesmo título, outra música
            out[f"{key}|{norm(song.artist)}"] = song
    return list(out.values())


def index(songs: Sequence[Song]) -> tuple[dict[Trio, dict[int, set[int]]], dict[Trio, float]]:
    """trio → {música → linhas onde aparece}, e o peso de cada trio (raro vale mais; 1.0 = só
    uma música tem)."""
    where: dict[Trio, dict[int, set[int]]] = {}
    for s, song in enumerate(songs):
        for ln, line in enumerate(song.lines):
            # o trio pode atravessar a quebra de linha: junta com o começo da linha seguinte
            nxt = song.lines[ln + 1] if ln + 1 < len(song.lines) else ""
            for sh in shingles(norm(line).split() + norm(nxt).split()[:2]):
                where.setdefault(sh, {}).setdefault(s, set()).add(ln)
    total = max(1, len(songs))
    weight = {
        sh: math.log(1 + total / len(found)) / math.log(1 + total) for sh, found in where.items()
    }
    return where, weight


def _join(items: list[Found], weight: dict[Trio, float]) -> list[Found]:
    """A mesma música em ocorrências seguidas é uma só."""
    out: list[Found] = []
    for cand in items:
        last = out[-1] if out else None
        if last and last.song == cand.song and cand.first - last.last <= SAME_SONG_S:
            last.last = cand.last
            last.shingles |= cand.shingles
            last.line_ids |= cand.line_ids
            last.hits, last.lines = len(last.shingles), len(last.line_ids)
            last.weight = sum(weight[sh] for sh in last.shingles)
        else:
            out.append(cand)
    return out


def find(words: Sequence[Heard], songs: Sequence[Song]) -> list[Found]:
    """As músicas de `songs` (já sem repetidas, ver `listed`) que a transcrição mostra, na ordem."""
    where, weight = index(songs)
    if not words:
        return []
    bins = int(words[-1].end // BIN_S) + 1
    evidence: list[dict[int, float]] = [{} for _ in range(bins)]
    hits: list[dict[int, list[tuple[float, Trio, set[int]]]]] = [{} for _ in range(bins)]
    plain = [w.norm for w in words]
    for i, sh in enumerate(shingles(plain)):
        b = min(bins - 1, int(words[i].start // BIN_S))
        for s, lines in where.get(sh, {}).items():
            evidence[b][s] = evidence[b].get(s, 0.0) + weight[sh]
            hits[b].setdefault(s, []).append((words[i].start, sh, lines))
    # de quem é cada trecho: entre as músicas com indício nele, a que tem mais indício em volta
    winner: list[int | None] = [None] * bins
    for b in range(bins):
        if not evidence[b]:
            continue
        around: dict[int, float] = {}
        for near in range(max(0, b - AROUND), min(bins, b + AROUND + 1)):
            for s, e in evidence[near].items():
                around[s] = around.get(s, 0.0) + e
        best = max(evidence[b], key=lambda s: around[s])
        if around[best] >= SMOOTH_MIN:
            winner[b] = best
    # trechos seguidos da mesma música (com buracos de até GAP_S) viram uma ocorrência
    runs: list[tuple[int, list[int]]] = []
    for b, won in enumerate(winner):
        if won is None:
            continue
        if runs and runs[-1][0] == won and (b - runs[-1][1][-1]) * BIN_S <= GAP_S:
            runs[-1][1].append(b)
        else:
            runs.append((won, [b]))
    found: list[Found] = []
    for s, owned in runs:
        mine = [h for b in owned for h in hits[b][s]]
        cand = Found(s, songs[s].title, songs[s].artist, min(h[0] for h in mine),
                     max(h[0] for h in mine))  # fmt: skip
        cand.shingles = {h[1] for h in mine}
        cand.line_ids = set().union(*(h[2] for h in mine))
        cand.hits, cand.lines = len(cand.shingles), len(cand.line_ids)
        cand.weight = sum(weight[sh] for sh in cand.shingles)
        if cand.hits >= MIN_HITS and cand.lines >= MIN_SONG_LINES and cand.weight >= MIN_WEIGHT:
            found.append(cand)
    # um mashup contém os versos das músicas que o formam: a ocorrência de uma delas, colada na
    # do mashup e explicada pela letra dele, é o próprio mashup. Repete até nada mudar.
    lyric = {c.song: trios(songs[c.song]) for c in found}
    merged = _join(found, weight)
    changed = True
    while changed:
        changed = False
        for n, cand in enumerate(merged):
            sides = (merged[n - 1] if n else None, merged[n + 1] if n + 1 < len(merged) else None)
            for other in sides:
                if other is None or other.song == cand.song or other.weight < cand.weight:
                    continue
                near_by = min(abs(cand.first - other.last), abs(other.first - cand.last)) <= NEAR_S
                shared = len(cand.shingles & lyric[other.song])
                if near_by and shared >= ABSORB * len(cand.shingles):
                    cand.song, cand.title, cand.artist = other.song, other.title, other.artist
                    cand.line_ids = set()
                    changed = True
                    break
        merged = _join(merged, weight)
    total = words[-1].end
    for n, cand in enumerate(merged):
        before = merged[n - 1].last + 5 if n else 0.0
        after = merged[n + 1].first - 5 if n + 1 < len(merged) else total
        cand.start = max(before, cand.first - 120)
        cand.end = min(after, cand.last + 90)
    return merged
