"""O que se ouve fora das linhas da letra: fala entre as músicas, gritos, versos mudados.

Medido nos shows de exemplo: transcrever tudo sem detector de voz inventa texto onde só há banda
ou plateia (de 24 trechos "falados" fora das músicas, só 1 a 3 tinham fala), e a fala em outro
idioma sai errada quando o idioma fica fixo no das letras. Então:
- só se transcreve onde há voz na faixa isolada e nenhuma linha da letra já encaixada;
- "há voz" é o que o detector de voz diz, mais onde a voz isolada soa alto (canto que ele perde);
- fora das músicas o idioma é detectado trecho a trecho; o trecho curto demais para isso segue o
  vizinho mais próximo.
"""

import wave
from collections.abc import Mapping, Sequence
from pathlib import Path

Chunk = tuple[float, float]

MAX_S = 28.0  # um trecho cabe numa janela do Whisper
DETECT_MIN_S = 4.0  # abaixo disso o idioma detectado não é confiável: vale o do vizinho
NEIGHBOUR_S = 30.0  # até onde o idioma do vizinho vale
GAP_S = 1.0  # silêncio posto entre dois trechos curtos que vão juntos para o Whisper
EDGE_S = 0.15  # folga de cada lado do trecho recortado
LOUD_DB = -40.0  # volume da voz isolada a partir do qual há alguém cantando ou falando
LOUD_FRAME_S = 0.5  # … medido a cada meio segundo
LOUD_GAP_S = 1.0  # respiro dentro do mesmo trecho
LOUD_MIN_S = 3.0  # menos que isso não é verso: é um grito, um resto de guitarra


def free_chunks(spans: Sequence[Chunk], taken: Sequence[Chunk], max_s: float = MAX_S,
                min_s: float = 0.5, join_gap: float = 0.8) -> list[Chunk]:  # fmt: skip
    """Os trechos com voz, sem o que as linhas já encaixadas cobrem; vizinhos próximos se juntam."""
    pieces: list[Chunk] = []
    for start, end in spans:
        cuts = sorted((a - 0.3, b + 0.3) for a, b in taken if b + 0.3 > start and a - 0.3 < end)
        at = start
        for a, b in cuts:
            if a - at >= min_s:
                pieces.append((at, a))
            at = max(at, b)
        if end - at >= min_s:
            pieces.append((at, end))
    out: list[Chunk] = []
    for start, end in pieces:
        if out and start - out[-1][1] <= join_gap and end - out[-1][0] <= max_s:
            out[-1] = (out[-1][0], end)
        else:
            out.append((start, end))
    return out


def loud(path: Path, level_db: float = LOUD_DB, min_s: float = LOUD_MIN_S,
         max_s: float = MAX_S) -> list[Chunk]:  # fmt: skip
    """Onde a voz isolada soa alto por alguns segundos seguidos. O detector de voz foi treinado
    com fala e perde canto (nota longa, coro com a plateia): um verso inteiro ficava sem legenda
    quando a letra dele não era conhecida. Num solo a voz isolada fica quase muda (-70 dB) e no
    canto fica perto de -27 dB, então o volume separa os dois. Lido aos poucos: um show inteiro
    não cabe na memória de uma máquina pequena. Trechos longos saem em pedaços de até `max_s`."""
    import numpy as np

    levels: list[float] = []
    with wave.open(str(path), "rb") as src:
        rate, width = src.getframerate(), src.getsampwidth()
        if width != 2 or src.getnchannels() != 1:
            return []
        frame = int(rate * LOUD_FRAME_S)
        while True:
            data = np.frombuffer(src.readframes(frame * 120), dtype="<i2")
            if len(data) < frame:
                break
            whole = len(data) // frame
            block = data[: whole * frame].astype(np.float32).reshape(whole, frame) / 32768.0
            rms = np.sqrt((block * block).mean(axis=1))
            levels += [float(x) for x in 20.0 * np.log10(np.maximum(rms, 1e-6))]
    runs: list[list[int]] = []  # [primeiro quadro, último quadro]
    for k, level in enumerate(levels):
        if level < level_db:
            continue
        if runs and (k - runs[-1][1]) * LOUD_FRAME_S <= LOUD_GAP_S:
            runs[-1][1] = k
        else:
            runs.append([k, k])
    out: list[Chunk] = []
    per_piece = int(max_s / LOUD_FRAME_S)
    for first, last in runs:
        if (last + 1 - first) * LOUD_FRAME_S < min_s:
            continue
        at = first
        while last + 1 - at > per_piece:  # corta onde a voz está mais baixa, perto do limite
            tail = range(at + per_piece * 2 // 3, at + per_piece)
            cut = min(tail, key=lambda k: levels[k])
            out.append((at * LOUD_FRAME_S, cut * LOUD_FRAME_S))
            at = cut
        out.append((at * LOUD_FRAME_S, (last + 1) * LOUD_FRAME_S))
    return out


def union(a: Sequence[Chunk], b: Sequence[Chunk]) -> list[Chunk]:
    """Os trechos das duas listas, sem repetir o que as duas cobrem."""
    out: list[Chunk] = []
    for start, end in sorted([*a, *b]):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def outside(chunks: Sequence[Chunk], songs: Sequence[Chunk]) -> list[Chunk]:
    """Os trechos que começam fora de toda música."""
    return [c for c in chunks if not any(a <= c[0] <= b for a, b in songs)]


def by_language(short: Sequence[Chunk], known: Mapping[Chunk, str],
                fallback: str) -> dict[str, list[Chunk]]:  # fmt: skip
    """Os trechos curtos agrupados pelo idioma do vizinho mais próximo que tem idioma detectado
    (`known`); sem vizinho por perto, o idioma das letras (`fallback`)."""
    groups: dict[str, list[Chunk]] = {}
    for chunk in short:
        near = min(known, key=lambda k: abs(k[0] - chunk[0]), default=None)
        close = near is not None and abs(near[0] - chunk[0]) <= NEIGHBOUR_S
        lang = known[near] if near is not None and close else fallback
        groups.setdefault(lang or fallback, []).append(chunk)
    return groups
