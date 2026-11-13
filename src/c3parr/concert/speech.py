"""O que se ouve fora das linhas da letra: fala entre as músicas, gritos, versos mudados.

Medido nos shows de exemplo: transcrever tudo sem detector de voz inventa texto onde só há banda
ou plateia (de 24 trechos "falados" fora das músicas, só 1 a 3 tinham fala), e a fala em outro
idioma sai errada quando o idioma fica fixo no das letras. Então:
- só se transcreve onde há voz na faixa isolada e nenhuma linha da letra já encaixada;
- fora das músicas o idioma é detectado trecho a trecho; o trecho curto demais para isso segue o
  vizinho mais próximo.
"""

from collections.abc import Mapping, Sequence

Chunk = tuple[float, float]

MAX_S = 28.0  # um trecho cabe numa janela do Whisper
DETECT_MIN_S = 4.0  # abaixo disso o idioma detectado não é confiável: vale o do vizinho
NEIGHBOUR_S = 30.0  # até onde o idioma do vizinho vale
GAP_S = 1.0  # silêncio posto entre dois trechos curtos que vão juntos para o Whisper
EDGE_S = 0.15  # folga de cada lado do trecho recortado


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
