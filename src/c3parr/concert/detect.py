"""É um show?  Pelos metadados do TMDb, quando ninguém disse em que pasta os shows ficam.

Três respostas: `yes`, `no` ou `maybe`. Só `yes` vira legenda de show: tratar um filme comum como
show é o erro caro, então `yes` pede prova. Tirado de 63 títulos rotulados:
- todo filme com enredo tem um gênero além de Música, Documentário e Filme para TV → `no`;
- num filme o elenco faz personagens; num show cada um aparece como si mesmo ou pelo instrumento
  ("Self", "Vocals"). Cinco ou mais personagens, e a maior parte do elenco → `no`. Nos 63
  títulos isso não mudou nenhum veredito (o gênero já pegava todo filme): serve para o filme que
  o TMDb traz sem gênero. Uma série nunca é show. Ter diretor não separa nada: filme de show
  também tem;
- show e documentário de música têm os mesmos gêneros: o que separa são as palavras-chave, o
  título e a sinopse;
- há show sem nada disso no TMDb: fica `maybe`, e aí é a pasta (Settings) que resolve.

Os gêneros entram pelo número (não mudam com o idioma do TMDb) e as palavras-chave são sempre em
inglês. A sinopse vem no idioma configurado: fora do inglês, ela deixa de contar.
"""

import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from typing import Literal

from c3parr.concert.catalogue import plain

MUSIC, DOCUMENTARY, TV_MOVIE = 10402, 99, 10770
# "Filme para TV" diz o formato, não o enredo (um show foi descartado por ele na validação)
NOT_PLOT = frozenset({MUSIC, DOCUMENTARY, TV_MOVIE})
STRONG_KEYS = frozenset({
    "concert", "concert film", "rock concert", "music concert", "live concert", "live music",
    "celebratory concert", "unplugged", "mtv unplugged", "live musical performance", "live album",
})  # fmt: skip
WEAK_KEYS = frozenset({"live performance", "live", "music tour"})
DOC_KEYS = frozenset({
    "music documentary", "biography", "interview", "recording studio", "archive footage",
    "biographical documentary", "behind the scenes", "recording session",
})  # fmt: skip
TITLE = re.compile(
    r"\b(live|unplugged|in concert|concert|tour|rock in rio|festival|ao vivo|en vivo|en directo|"
    r"ac[uú]stico|alive)\b",
    re.IGNORECASE,
)
OVERVIEW_CONCERT = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\bconcert film\b", r"\bconcerts?\b", r"\b(recorded|captured|filmed|shot) live\b",
    r"\blive (recording|performance|concert|presentation|album|set)\b",
    r"\bperform(s|ing|ed|ances?)?\b", r"\bplay(s|ed)? (the|a|at|all)\b",
    r"\bfilmed (at|during|over)\b", r"\b\d+ songs\b",
))  # fmt: skip
OVERVIEW_DOC = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\bdocumentary (on|about|follows)\b", r"\bchronicl", r"\bthe story of\b", r"\bfollows?\b",
    r"\bbehind the scenes\b", r"\binterviews?\b", r"\b(life|history) of\b", r"\bportrait\b",
    r"\bin-depth look\b",
))  # fmt: skip


ROLES, ROLES_SHARE = 5, 0.6
"""Há enredo quando pelo menos tantos do elenco, e essa fração dele, fazem um personagem. A folga
é para o show cujo elenco traz o instrumento com outro nome ("Guitarra", "Wired Strings - Violin":
medido, 2 de 4 e 3 de 9 em dois shows)."""
_SELF = (
    "self", "himself", "herself", "themselves", "ele mesmo", "ela mesma", "eles mesmos",
    "si mismo", "performer", "musician", "singer", "band", "host", "presenter", "narrator",
    "interviewer", "interviewee", "conductor", "dancer", "dj", "orchestra", "choir", "vocals",
    "vocal", "lead vocals", "backing vocals", "background vocals", "guitar", "guitars",
    "lead guitar", "rhythm guitar", "bass guitar", "acoustic guitar", "electric guitar", "bass",
    "drums", "percussion", "keyboards", "keyboard", "piano", "synthesizer", "synthesizers",
    "violin", "cello", "saxophone", "trumpet", "harmonica", "turntables",
)  # fmt: skip
SELF = re.compile(rf"^({'|'.join(sorted(_SELF, key=len, reverse=True))})\b")


def played_roles(cast: Iterable[tuple[str, str | None]]) -> int:
    """Quantos do elenco (personagem, ator) fazem um personagem, e não a si mesmos nem um
    instrumento."""
    count = 0
    for role, actor in cast:
        name = plain(role)
        own = bool(actor) and plain(actor or "") == name
        if name and not own and not SELF.match(name) and "archive footage" not in name:
            count += 1
    return count


@dataclass
class Verdict:
    answer: Literal["yes", "no", "maybe"]
    concert: int = 0
    doc: int = 0
    reasons: list[str] = field(default_factory=list)


def from_metadata(genre_ids: Collection[int], keywords: Collection[str], titles: str,
                  overview: str = "", roles: int = 0, cast: int = 0,
                  series: bool = False) -> Verdict:  # fmt: skip
    """`genre_ids` e `keywords` são os do TMDb; `titles` junta os nomes conhecidos (TMDb, pasta);
    `roles`: quantos do elenco (`cast`) fazem um personagem (`played_roles`); `series`: é um
    episódio de série."""
    if series:
        return Verdict("no", reasons=["a series"])
    genres = set(genre_ids)
    plot = sorted(genres - NOT_PLOT)
    if plot:
        return Verdict("no", reasons=[f"plot genre: {plot}"])
    if roles >= ROLES and roles >= ROLES_SHARE * max(cast, roles):
        return Verdict("no", reasons=[f"{roles} of the cast play characters"])
    if not genres:
        # sem gênero não dá para saber se há enredo ("Live Free or Die Hard" tem "live" no título)
        return Verdict("maybe", reasons=["no genre on TMDb"])
    keys = {k.lower() for k in keywords}
    v = Verdict("maybe")
    strong = sorted(keys & STRONG_KEYS)
    if strong:
        v.concert += 2
        v.reasons.append(f"concert keyword: {', '.join(strong)}")
    if keys & WEAK_KEYS:
        v.concert += 1
        v.reasons.append("keyword: live performance")
    found = TITLE.search(titles)
    if found:
        v.concert += 2
        v.reasons.append(f"title: '{found.group(0)}'")
    hits = [m.group(0) for p in OVERVIEW_CONCERT if (m := p.search(overview))]
    if hits:
        v.concert += min(2, len(hits))
        v.reasons.append(f"overview talks about a concert: {', '.join(hits[:3])}")
    if genres - {TV_MOVIE} == {MUSIC}:
        v.concert += 1
        v.reasons.append("genre: only Music")
    doc_keys = sorted(keys & DOC_KEYS)
    if doc_keys:
        v.doc += min(2, len(doc_keys))
        v.reasons.append(f"documentary keyword: {', '.join(doc_keys)}")
    doc_hits = [m.group(0) for p in OVERVIEW_DOC if (m := p.search(overview))]
    if doc_hits:
        v.doc += min(2, len(doc_hits))
        v.reasons.append(f"overview talks about a documentary: {', '.join(doc_hits[:3])}")
    if v.concert >= 2 and v.concert > v.doc:
        v.answer = "yes"
    elif v.doc > v.concert:
        v.answer = "no"
    return v


def under(video: str, folders: Collection[str]) -> bool:
    """O vídeo está dentro de uma das pastas de show? (comparação por pasta, não por prefixo de
    texto: `/media/shows2` não está em `/media/shows`)."""
    parts = [p for p in video.replace("\\", "/").split("/") if p]
    for folder in folders:
        want = [p for p in folder.strip().replace("\\", "/").split("/") if p]
        if want and parts[: len(want)] == want:
            return True
    return False
