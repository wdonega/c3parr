"""Ouvir de novo com a letra como dica.

O encaixe põe cada linha da letra uma vez, na ordem do disco. Ao vivo o refrão volta e o último
verso se repete, e nesses trechos sobrava só o que a primeira transcrição ouviu. Ela erra canto
com certeza alta (medido: 0,81 numa frase errada), então a certeza do Whisper não decide nada.
Em cada trecho de uma música achada onde algo foi ouvido e nenhuma linha encaixada cobre:
1. o trecho é transcrito de novo com as linhas prováveis da letra como dica do Whisper (`asks`);
2. as linhas da letra que aparecem nessa segunda escuta são encaixadas no som do trecho (`spot`);
3. cada uma só vale com uma prova que não venha da dica (`accept`).

A dica sozinha não prova nada: onde não há voz clara o Whisper copia a dica (medido num show: com
a letra de OUTRA banda como dica ele "achou" 200 linhas). As provas aceitas, medidas contra essas
cópias em três shows (nenhuma de 331 passa):
- `som`: a maioria das palavras da linha soa naquele ponto (`Fit.steady`), e a linha tem pelo
  menos cinco palavras (duas cópias de três palavras bateram com o som por acaso);
- `primeira_escuta`: sem dica nenhuma, a primeira transcrição já tinha ouvido três palavras
  seguidas da linha naquele trecho.

A frase que só a primeira escuta ouviu (a segunda, no mesmo trecho, ouviu outra coisa) sai da
legenda (`stable`); a que as duas ouviram fica, e é o que cobre uma letra trocada de propósito.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from rapidfuzz import fuzz

from c3parr.concert.fit import Fit, plausible
from c3parr.concert.words import Phrase, norm

PAD_S = 1.0  # folga em volta do que foi ouvido
MAX_S = 28.0  # um trecho cabe numa janela do Whisper (30 s)
MIN_S = 2.0
HINT_LINES = 12  # linhas da letra na dica (a dica do Whisper tem limite de tamanho)
HINT_WORDS = 130
SPOT = 85  # semelhança (0 a 100) para dizer que a segunda escuta contém uma linha
MIN_WORDS = 3  # linha mais curta que isso ("yeah", "come on") não se procura
FIT = 0.2  # nota mínima do encaixe da linha no trecho
STEADY = 0.2  # prova pelo som: nota da palavra do meio
STEADY_WORDS = 5  # … numa linha com pelo menos tantas palavras (linha curta bate por acaso)
RUN = 3  # prova pela primeira escuta: palavras seguidas iguais
TIGHT_S = 0.6  # folga da janela em que `steady` é medido de novo, só em volta da linha
SAME = 70  # semelhança para dizer que as duas escutas ouviram a mesma frase

Region = tuple[float, float]


@dataclass(frozen=True)
class Ask:
    """Um trecho para ouvir de novo: as linhas da dica e o que a primeira escuta ouviu ali."""

    region: Region
    among: tuple[int, ...]
    prompt: str
    first: str


@dataclass(frozen=True)
class Second:
    line: int  # índice na letra
    fit: Fit  # onde coube no som do trecho (`steady` medido só em volta da linha)
    run: int  # palavras seguidas da linha que a primeira escuta já tinha ouvido no trecho
    region: Region


def regions(heard: Sequence[Phrase], taken: Sequence[Region], window: Region) -> list[Region]:
    """Trechos de `window` onde algo foi ouvido e nenhuma linha encaixada (`taken`) cobre."""
    free: list[Region] = []
    at = window[0]
    for a, b in sorted(taken):
        if a - at >= MIN_S:
            free.append((at, a))
        at = max(at, b)
    if window[1] - at >= MIN_S:
        free.append((at, window[1]))
    out: list[Region] = []
    for lo, hi in free:
        spans = sorted(
            (max(lo, a - PAD_S), min(hi, b + PAD_S))
            for a, b, text, _ in heard
            if norm(text) and b > lo and a < hi
        )
        group: list[Region] = []
        for a, b in spans:
            if b - a < 0.3:
                continue
            while b - a > MAX_S:  # uma frase mais longa que a janela: em pedaços
                out.append((a, a + MAX_S))
                a += MAX_S
            if group and (a - group[-1][1] > 2.5 or b - group[0][0] > MAX_S):
                out.append((group[0][0], group[-1][1]))
                group = []
            group.append((a, max(b, group[-1][1]) if group else b))
        if group:
            out.append((group[0][0], group[-1][1]))
    return sorted((a, b) for a, b in out if b - a >= MIN_S)


def hint(lines: Sequence[str], heard_text: str, before: int | None, after: int | None) -> list[int]:
    """Quais linhas vão na dica: as mais parecidas com o que foi ouvido, mais a continuação da
    última linha encaixada antes do trecho e o caminho até a primeira encaixada depois dele."""
    plain = norm(heard_text)
    like = [fuzz.token_set_ratio(norm(line), plain) for line in lines]
    ranked = sorted(range(len(lines)), key=lambda n: -like[n])
    picked = [n for n in ranked if like[n] >= 40][: HINT_LINES // 2]
    if before is not None:
        picked += [before, before + 1, before + 2]
    if after is not None:
        picked += [after - 2, after - 1, after]
    return sorted({n for n in picked if 0 <= n < len(lines)})[:HINT_LINES]


def prompt(lines: Sequence[str], among: Sequence[int]) -> str:
    chosen = [lines[n] for n in among]
    while len(" ".join(chosen).split()) > HINT_WORDS and len(chosen) > 1:
        chosen.pop(0)  # o Whisper só lê o fim de uma dica longa
    return " ".join(chosen)


def asks(lines: Sequence[str], heard: Sequence[Phrase], fits: Sequence[Fit],
         window: Region) -> list[Ask]:  # fmt: skip
    """Os trechos de uma música que valem uma segunda escuta, com a dica de cada um."""
    proven = sorted((f for f in fits if f.accepted), key=lambda f: f.start)
    out: list[Ask] = []
    for a, b in regions(heard, [(f.start, f.end) for f in proven], window):
        first = " ".join(text for s, e, text, _ in heard if e > a - 2.0 and s < b + 2.0)
        before = max((f for f in proven if f.end <= a + 0.5), key=lambda f: f.end, default=None)
        after = min((f for f in proven if f.start >= b - 0.5), key=lambda f: f.start, default=None)
        among = hint(lines, first, before.line if before else None, after.line if after else None)
        if among:
            out.append(Ask((a, b), tuple(among), prompt(lines, among), first))
    return out


def spot(text: str, lines: Sequence[str], among: Sequence[int]) -> list[int]:
    """As linhas de `among` que aparecem em `text`, na ordem em que aparecem (uma linha cantada
    duas vezes sai duas vezes)."""
    heard = norm(text).split()
    plain = {n: norm(lines[n]) for n in among if len(norm(lines[n]).split()) >= MIN_WORDS}
    found: list[int] = []
    at = 0
    while at < len(heard):
        best: tuple[float, int, int] | None = None
        for n, line in plain.items():
            size = len(line.split())
            for take in (size, size - 1, size + 1):
                if take < MIN_WORDS or at + take > len(heard):
                    continue
                score = fuzz.ratio(line, " ".join(heard[at : at + take]))
                if score >= SPOT and (best is None or score > best[0]):
                    best = (score, n, take)
        if best:
            found.append(best[1])
            at += best[2]
        else:
            at += 1
    return found


def run(line: str, first: str) -> int:
    """Quantas palavras seguidas da linha a primeira escuta ouviu (a maior sequência igual)."""
    a, b = norm(line).split(), norm(first).split()
    best = 0
    for i in range(len(a)):
        for j in range(len(b)):
            k = 0
            while i + k < len(a) and j + k < len(b) and a[i + k] == b[j + k]:
                k += 1
            best = max(best, k)
    return best


def accept(found: Sequence[Second]) -> list[Fit]:
    """As linhas da segunda escuta que têm prova fora da dica (ver o cabeçalho)."""
    out: list[Fit] = []
    for s in found:
        if not plausible(s.fit) or s.fit.score < FIT:
            continue
        if s.fit.steady >= STEADY and len(norm(s.fit.text).split()) >= STEADY_WORDS:
            out.append(s.fit.model_copy(update={"accepted": True, "why": "som"}))
        elif s.run >= RUN:
            out.append(s.fit.model_copy(update={"accepted": True, "why": "primeira_escuta"}))
    return out


def stable(phrase: str, texts: Mapping[Region, str], start: float, end: float) -> bool | None:
    """A frase da primeira escuta aparece também na segunda? `None`: não houve segunda ali."""
    mine = [text for (a, b), text in texts.items() if a - 0.5 <= start and end <= b + 0.5]
    if not mine:
        return None
    return any(fuzz.partial_ratio(norm(phrase), norm(text)) >= SAME for text in mine)
