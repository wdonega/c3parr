"""Legendas ao lado do vídeo (`Filme.pt-BR.srt`, `Filme.en.forced.srt`): idiomas e referência."""

import re
from pathlib import Path

from c3parr.audio.ffmpeg import MediaInfo
from c3parr.library.store import Library, SubtitleFile
from c3parr.pipeline.naming import guess_text_lang, sidecar_subtitles

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


def index_subtitles(
    library: Library, video: str, probe: MediaInfo | None = None
) -> list[SubtitleFile]:
    """Lê do disco as legendas do vídeo e grava no banco, de onde as telas leem.

    Roda no sync e na pipeline, nunca ao abrir uma página. Sem `probe` (o sync não sonda o vídeo),
    as faixas embutidas já gravadas ficam. O idioma adivinhado pelo texto (`Filme.hi.srt`) só é
    refeito quando o arquivo muda.
    """
    known = {f.path: f for f in library.subtitle_files([video]).get(video, [])}
    files: list[SubtitleFile] = []
    for t in probe.subtitles if probe else ():
        if t.text_based:
            files.append(SubtitleFile(path=f"{video}#{t.index}", video=video, lang=t.lang,
                                      kind="embedded", forced=t.forced))  # fmt: skip
    for sc in sidecar_subtitles(Path(video)):
        try:
            mtime = Path(sc.path).stat().st_mtime
        except OSError:
            continue
        lang = sc.lang
        if lang is None and not sc.ours:
            old = known.get(sc.path)
            lang = old.lang if old and old.mtime == mtime else guess_text_lang(Path(sc.path))
        files.append(SubtitleFile(path=sc.path, video=video, lang=lang,
                                  kind="ours" if sc.ours else "human", forced=sc.forced,
                                  mtime=mtime))  # fmt: skip
    library.set_subtitle_files(video, files, keep_embedded=probe is None)
    return files
