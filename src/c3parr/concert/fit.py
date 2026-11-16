"""Cada linha da letra no seu instante, e a prova de que ela é cantada ali.

Um modelo de fonemas diz onde cada linha cabe melhor na voz isolada (o provider de `encaixe`).
Caber não basta: o modelo põe qualquer texto em algum lugar. A linha só vale com prova:
- `forte`: a nota do próprio encaixe é alta;
- `ouvida`: a transcrição ouviu aquelas palavras naquele ponto;
- `vizinhas`: ela está entre duas linhas provadas;
- `disco`: ela coube onde a gravação de estúdio diz que fica (`studio`).
O resto (verso pulado, final diferente ao vivo) fica de fora e a legenda usa o que foi ouvido.
"""

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from statistics import median
from typing import Literal

from rapidfuzz import fuzz

from c3parr.concert.words import Heard, norm
from c3parr.core.models import Model

STRONG, WEAK = 0.5, 0.2  # nota do encaixe: sozinha basta / só com prova em volta
HEARD = 62  # semelhança (0 a 100) entre a linha e o que a transcrição ouviu ali
NEIGHBOUR_S = 12.0
NEAR = 3  # linhas provadas de cada lado que dizem quanto o show está deslocado do disco
FAR_S = 60.0  # … a no máximo tanto tempo (no disco) da linha
SAME_BARS_S = 3.0  # o deslocamento antes e depois difere mais que isso: o arranjo ali é outro
STUDIO_S = 1.5  # folga entre onde a linha coube e onde o disco diz
EDGE_PROOFS = 2  # linhas provadas de um lado só que bastam na ponta de um bloco da letra
EDGE_WORDS = 3  # … para uma linha com pelo menos tantas palavras (linha curta cabe por acaso)
SAME_TEXT = 85

Why = Literal["", "forte", "ouvida", "vizinhas", "disco", "som", "primeira_escuta", "duracao"]
"""A prova de uma linha aceita; `duracao` é a recusa (duração impossível para o texto)."""
SECOND_LISTEN: frozenset[str] = frozenset({"som", "primeira_escuta"})


class Fit(Model):
    line: int  # índice na letra
    text: str
    start: float
    end: float
    score: float
    heard: float = 0.0  # semelhança com a transcrição naquele ponto
    steady: float = 0.0  # nota da palavra do meio: alta só quando a maioria das palavras soa ali
    accepted: bool = False
    why: Why = ""


class Played(Model):
    """Uma música achada no show: onde, a letra dela e as linhas encaixadas."""

    title: str
    artist: str = ""
    first: float  # primeiro e último indício na transcrição
    last: float
    start: float  # a janela do encaixe
    end: float
    matched: int = 0  # linhas da letra com indício na transcrição
    lines: tuple[str, ...] = ()
    synced: tuple[tuple[float, str], ...] = ()
    fits: tuple[Fit, ...] = ()
    studio_pairs: int = 0
    studio_consistent: int = 0
    """Conferência independente do encaixe, medida antes de o disco servir de prova."""


class Fitted(Model):
    songs: tuple[Played, ...] = ()


def plausible(fit: Fit) -> bool:
    count = max(1, len(norm(fit.text).split()))
    each = (fit.end - fit.start) / count
    return 0.12 <= each <= 2.5 and fit.end - fit.start <= 14


def accept(fits: Sequence[Fit], heard: Sequence[Heard]) -> list[Fit]:
    """Marca as linhas que têm prova no som ou na transcrição (`heard`, em ordem de horário)."""
    starts = [w.start for w in heard]
    out: list[Fit] = []
    for fit in fits:
        lo, hi = bisect_left(starts, fit.start - 2.0), bisect_right(starts, fit.end + 2.0)
        near = " ".join(w.norm for w in heard[lo:hi])
        same = float(fuzz.partial_ratio(norm(fit.text), near)) if near else 0.0
        update: dict[str, object] = {"heard": same}
        if not plausible(fit):
            update["why"] = "duracao"
        elif fit.score >= STRONG:
            update.update(accepted=True, why="forte")
        elif same >= HEARD and fit.score >= 0.08:
            update.update(accepted=True, why="ouvida")
        out.append(fit.model_copy(update=update))
    proven = [f for f in out if f.accepted]
    for n, fit in enumerate(out):
        if fit.accepted or fit.why or fit.score < WEAK:
            continue
        before = any(p.line < fit.line and 0 <= fit.start - p.end <= NEIGHBOUR_S for p in proven)
        after = any(p.line > fit.line and 0 <= p.start - fit.end <= NEIGHBOUR_S for p in proven)
        if before and after:
            out[n] = fit.model_copy(update={"accepted": True, "why": "vizinhas"})
    return out


def studio(fits: Sequence[Fit], synced: Sequence[tuple[float, str]]) -> list[Fit]:
    """Aceita a linha de nota fraca que está onde o disco diz que ela fica.

    `synced`: o horário de cada linha na gravação de estúdio. As linhas já provadas em volta dão
    quanto o show está deslocado do disco naquele ponto; se o deslocamento é o mesmo antes e
    depois (a banda toca ali os mesmos compassos do disco) e a linha coube nesse horário, vale.
    Medido em três shows: 92% a 96% das linhas provadas ficam a até 1,5 s do horário do disco, e
    com o horário de outra música no lugar do certo passa 1 de 237.

    Na ponta de um bloco da letra (ver `_edge`) a prova vem de um lado só, e cada linha aceita
    serve de prova às vizinhas. Medido em quatro shows: de 1 a 10 linhas a mais por show; com o
    horário de outra música, de 0 a 4 a mais (em 49 a 124 candidatas).
    """
    out = list(fits)
    if not synced or len(synced) != max((f.line for f in fits), default=-1) + 1:
        return out
    at = [stamp for stamp, _ in synced]
    changed = True
    while changed:  # uma linha aceita vira prova para as vizinhas: repete até nada mudar
        changed = False
        proven = sorted((f for f in out if f.accepted), key=lambda f: f.line)
        for n, fit in enumerate(out):
            if fit.accepted or fit.why or fit.score < WEAK:
                continue
            if fuzz.ratio(norm(synced[fit.line][1]), norm(fit.text)) < SAME_TEXT:
                continue  # a letra com horário não é a mesma linha
            reach = [p for p in proven if abs(at[p.line] - at[fit.line]) <= FAR_S]
            before = [p.start - at[p.line] for p in reversed(reach) if p.line < fit.line][:NEAR]
            after = [p.start - at[p.line] for p in reach if p.line > fit.line][:NEAR]
            if before and after:
                if abs(median(before) - median(after)) > SAME_BARS_S:
                    continue
                shift = median(before + after)
            else:
                edge = _edge(fit.line, at, before, after)
                if edge is None or len(fit.text.split()) < EDGE_WORDS:
                    continue
                shift = edge
            if abs(fit.start - at[fit.line] - shift) <= STUDIO_S:
                out[n] = fit.model_copy(update={"accepted": True, "why": "disco"})
                changed = True
    return out


def _edge(line: int, at: Sequence[float], before: Sequence[float],
          after: Sequence[float]) -> float | None:  # fmt: skip
    """O deslocamento para a linha na ponta de um bloco da letra: do outro lado dela o disco não
    tem linha nenhuma por perto (começo ou fim da música, ou um solo de minutos), então a prova
    só pode vir de um lado. Vale com duas linhas provadas desse lado dizendo o mesmo. Sem isso, o
    verso que volta depois de um solo longo ficava sem prova: a primeira linha dele nunca tem
    vizinha antes."""
    side = before or after
    if len(side) < EDGE_PROOFS or max(side) - min(side) > STUDIO_S:
        return None
    others = range(line) if not before else range(line + 1, len(at))
    if any(abs(at[k] - at[line]) <= FAR_S for k in others):
        return None  # há linha do disco desse lado: ela é que tinha de estar provada
    return median(side)


def studio_check(fits: Sequence[Fit], synced: Sequence[tuple[float, str]]) -> tuple[int, int]:
    """Conferência independente do encaixe: o intervalo entre duas linhas seguidas ao vivo deve
    parecer com o do disco (0,6x a 1,6x). Devolve (pares iguais, pares conferidos)."""
    if not synced or len(synced) != max((f.line for f in fits), default=-1) + 1:
        return 0, 0
    good = total = 0
    ok = {f.line: f for f in fits if f.accepted}
    for n in sorted(ok):
        if n + 1 not in ok:
            continue
        gap = synced[n + 1][0] - synced[n][0]
        if not 1.0 <= gap <= 30.0:
            continue
        total += 1
        good += 0.6 <= (ok[n + 1].start - ok[n].start) / gap <= 1.6
    return good, total
