"""Correção fuzzy de nomes próprios na transcrição: troca grafias erradas pelo nome da bíblia.

Ex.: o ASR escreve "Marouane" e o elenco diz "Marwan". Só troca palavras com inicial maiúscula,
bem parecidas e diferentes do nome; o relatório lista cada troca.
"""

import re
from collections import Counter
from collections.abc import Iterable

from rapidfuzz import fuzz

from c3parr.core.bible import Bible
from c3parr.core.models import Model, Transcript, Word

_TOKEN = re.compile(r"[A-ZÀ-Ý][\wÀ-ÿ'-]+")
_LOWER = re.compile(r"(?<![\wÀ-ÿ])[a-zà-ÿ][\wà-ÿ'-]+")
_POSSESSIVE = re.compile("['\\u2019]s$")


class NameFix(Model):
    wrong: str
    right: str
    count: int


class NamesResult(Model):
    transcript: Transcript
    fixes: tuple[NameFix, ...] = ()


def name_tokens(bible: Bible, min_len: int = 4) -> list[str]:
    """Nomes e sobrenomes dos personagens (e apelidos), sem títulos curtos."""
    out: list[str] = []
    for c in bible.personagens:
        for part in (c.nome, *c.apelidos):
            for raw in re.split(r"[\s.]+", part):
                tok = _POSSESSIVE.sub("", raw)  # "Nawal's Grandmother" → Nawal
                if len(tok) >= min_len and tok[0].isupper() and tok not in out:
                    out.append(tok)
    return out


def hotwords(bible: Bible, limit: int = 40) -> tuple[str, ...]:
    return tuple(name_tokens(bible)[:limit])


def _best(word: str, names: Iterable[str], threshold: float) -> str | None:
    best, score = None, 0.0
    for name in names:
        if word == name:
            return None
        if word[0].lower() != name[0].lower():  # erro de ASR quase sempre mantém o som inicial
            continue
        s = fuzz.ratio(word.lower(), name.lower())
        if s > score:
            best, score = name, s
    return best if best is not None and score >= threshold else None


def correct_names(transcript: Transcript, bible: Bible, threshold: float = 70.0) -> NamesResult:
    names = name_tokens(bible)
    if not names:
        return NamesResult(transcript=transcript)
    counts: Counter[tuple[str, str]] = Counter()
    cache: dict[str, str | None] = {}
    # palavra que aparece em minúscula em outra fala é palavra comum com maiúscula de início de
    # frase ("Maman", "Notre", "Sinon"), não nome mal escrito
    common = {w.lower() for seg in transcript.segments for w in _LOWER.findall(seg.text)}

    def lookup(token: str) -> str | None:
        if token not in cache:
            base = _POSSESSIVE.sub("", token)
            usable = len(base) >= 4 and base.lower() not in common
            right = _best(base, names, threshold) if usable else None
            cache[token] = right + token[len(base) :] if right else None
        return cache[token]

    def fix_text(m: re.Match[str]) -> str:
        right = lookup(m.group(0))
        if right is None:
            return m.group(0)
        counts[(m.group(0), right)] += 1
        return right

    def fix_word(m: re.Match[str]) -> str:
        return lookup(m.group(0)) or m.group(0)

    segments = []
    for seg in transcript.segments:
        text = _TOKEN.sub(fix_text, seg.text)
        words = tuple(
            Word(w=_TOKEN.sub(fix_word, w.w), start=w.start, end=w.end, conf=w.conf)
            for w in seg.words
        )
        segments.append(seg.model_copy(update={"text": text, "words": words}))
    fixes = tuple(NameFix(wrong=w, right=r, count=n) for (w, r), n in counts.most_common())
    corrected = transcript.model_copy(update={"segments": tuple(segments)})
    return NamesResult(transcript=corrected, fixes=fixes)
