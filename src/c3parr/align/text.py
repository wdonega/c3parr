"""Limpeza de texto de legenda para comparação: tira marcação, efeitos sonoros e rótulos."""

import re
import unicodedata

_TAGS = re.compile(r"<[^>]+>|\{[^}]*\}")
_BRACKETS = re.compile(r"\[[^\]]*\]|\([^)]*\)")
_DASHES = "-–—"  # noqa: RUF001 (hífen, meia-risca e travessão aparecem em diálogos)
_SPEAKER = re.compile(rf"^\s*[{_DASHES}]?\s*[A-ZÀ-Ý][A-ZÀ-Ý0-9 .'-]{{1,30}}:\s+")
_MUSIC_LINE = re.compile(r"^\s*[♪♫*#]")
_PUNCT = re.compile(r"[^\w\s']+")


def clean(text: str) -> str:
    """Só o que é fala: sem tags, sem [efeitos], sem rótulo de falante, sem linhas de música."""
    lines = []
    for line in _TAGS.sub("", text).split("\n"):
        if _MUSIC_LINE.match(line):
            continue
        line = _SPEAKER.sub("", _BRACKETS.sub("", line))
        line = line.strip(" ♪♫" + _DASHES)
        if line:
            lines.append(line)
    return " ".join(" ".join(lines).split())


def normalize(text: str) -> str:
    """Forma para comparar: minúsculas, sem acentos e sem pontuação."""
    base = unicodedata.normalize("NFKD", clean(text).lower())
    base = "".join(ch for ch in base if not unicodedata.combining(ch))
    return " ".join(_PUNCT.sub(" ", base).split())


def is_speech(text: str) -> bool:
    return bool(normalize(text))
