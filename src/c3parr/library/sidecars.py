"""Legendas ao lado do vídeo (`Filme.pt-BR.srt`, `Filme.en.forced.srt`): idiomas e referência."""

import re
from pathlib import Path

SUB_EXTS = {".srt", ".ass", ".ssa", ".vtt"}
_LANG = re.compile(r"^[a-z]{2,3}(-[a-z]{2,4})?$", re.IGNORECASE)
# marcas de tipo de legenda do Bazarr/Plex, não de idioma (`.pt-BR.hi.srt`, `.en.forced.srt`)
_SUB_FLAGS = {"hi", "sdh", "cc", "forced", "default"}
# nomes que o Bazarr e os players usam para o mesmo idioma
LANG_ALIASES = {"pt-br": {"pt-br", "pob", "pb", "ptbr"}, "pt": {"pt", "por", "pt-pt"}}


def subtitle_languages(video: Path) -> set[str]:
    """Idiomas das legendas ao lado do vídeo (`Filme.pt-BR.srt`, `Filme.en.forced.srt`)."""
    langs: set[str] = set()
    try:
        siblings = list(video.parent.iterdir())
    except OSError:
        return langs
    prefix = video.stem + "."
    for sub in siblings:
        if sub.suffix.lower() not in SUB_EXTS or not sub.name.startswith(prefix):
            continue
        tags = sub.name[len(prefix) : -len(sub.suffix)].split(".")
        lang = next((t for t in tags if _LANG.match(t) and t.lower() not in _SUB_FLAGS), None)
        if lang:
            langs.add(lang.lower())
    return langs


def pick_reference(video: Path, target: str) -> Path | None:
    """Legenda ao lado do vídeo para usar de referência (sincronizada e passada ao tradutor).

    Ignora a do idioma de destino; prefere a sem marca de HI/forced e, entre as outras, inglês.
    """
    wanted = LANG_ALIASES.get(target.lower(), {target.lower()})
    best: tuple[int, int, str] | None = None
    chosen: Path | None = None
    prefix = video.stem + "."
    try:
        siblings = sorted(video.parent.iterdir())
    except OSError:
        return None
    for sub in siblings:
        if sub.suffix.lower() != ".srt" or not sub.name.startswith(prefix):
            continue
        tags = [t.lower() for t in sub.name[len(prefix) : -len(sub.suffix)].split(".")]
        lang = next((t for t in tags if _LANG.match(t) and t not in _SUB_FLAGS), None)
        if lang is None or lang in wanted:
            continue
        rank = (int(bool(_SUB_FLAGS & set(tags))), int(lang != "en"), sub.name)
        if best is None or rank < best:
            best, chosen = rank, sub
    return chosen


def has_language(langs: set[str], target: str) -> bool:
    wanted = LANG_ALIASES.get(target.lower(), {target.lower()})
    return bool(langs & wanted)
