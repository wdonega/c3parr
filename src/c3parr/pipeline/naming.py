"""Nomes dos arquivos ao lado do vídeo.

- Trabalho do c3parr: `<vídeo>.<tipo>[.<idioma>].c3parr` (extensão própria: players ignoram).
- Nossa legenda: `<vídeo>.<idioma>.c3parr.srt` (o player mostra como faixa separada).
- Legenda de outra fonte (humana, Bazarr): `<vídeo>.<idioma>[.hi|.forced].srt`.
"""

import re
from pathlib import Path

from c3parr.core.models import Model

WORK_EXT = ".c3parr"
OURS_TAG = "c3parr"
SUB_EXTS = frozenset({".srt", ".ass", ".ssa", ".vtt"})
_LANG = re.compile(r"^[a-z]{2,3}(-[a-z]{2,4})?$", re.IGNORECASE)
_FLAGS = {"hi", "sdh", "cc", "forced", "default"}
_ALIASES = {"pob": "pt-BR", "pb": "pt-BR", "ptbr": "pt-BR", "pt-br": "pt-BR", "por": "pt",
            "eng": "en", "fre": "fr", "fra": "fr", "spa": "es", "ger": "de", "deu": "de",
            "ita": "it", "ara": "ar"}  # fmt: skip


def work_file(video: Path, kind: str, lang: str | None = None) -> Path:
    tail = f".{lang}" if lang else ""
    return video.with_name(f"{video.stem}.{kind}{tail}{WORK_EXT}")


def output_file(video: Path, lang: str) -> Path:
    return video.with_name(f"{video.stem}.{lang}.{OURS_TAG}.srt")


def norm_lang(tag: str) -> str:
    low = tag.lower()
    if low in _ALIASES:
        return _ALIASES[low]
    if "-" in low:
        base, region = low.split("-", 1)
        return f"{base}-{region.upper()}"
    return low


def same_lang(a: str | None, b: str | None) -> bool:
    """`pt-BR` e `pt-br` são o mesmo; `pt` e `pt-BR` não (Portugal e Brasil são alvos distintos)."""
    if not a or not b:
        return False
    return norm_lang(a) == norm_lang(b)


def same_base(a: str | None, b: str | None) -> bool:
    """Mesmo idioma base (`pt` e `pt-BR`): serve para escolher a legenda principal."""
    if not a or not b:
        return False
    return a.split("-")[0].lower() == b.split("-")[0].lower()


class SidecarSub(Model):
    path: str
    lang: str | None
    """None quando o nome não diz o idioma (ex.: `Filme.hi.srt`)."""
    ours: bool = False
    forced: bool = False
    hearing_impaired: bool = False


def sidecar_subtitles(video: Path) -> list[SidecarSub]:
    """Legendas ao lado do vídeo com o mesmo nome-base (convenção Plex/Jellyfin/Bazarr)."""
    prefix = video.stem + "."
    try:
        siblings = sorted(video.parent.iterdir())
    except OSError:
        return []
    out = []
    for f in siblings:
        if f.suffix.lower() not in SUB_EXTS or not f.name.startswith(prefix):
            continue
        tags = f.name[len(prefix) : -len(f.suffix)].split(".")
        lowered = {t.lower() for t in tags}
        lang = next((t for t in tags if _LANG.match(t) and t.lower() not in _FLAGS), None)
        if "br" in lowered and any(t.startswith("pt") for t in lowered):
            lang = "pt-BR"  # `Filme.br.pt.srt`: convenção de alguns sites, não é bretão
        out.append(SidecarSub(path=str(f), lang=norm_lang(lang) if lang else None,
                              ours=OURS_TAG in lowered, forced="forced" in lowered,
                              hearing_impaired=bool(lowered & {"hi", "sdh", "cc"})))  # fmt: skip
    return out


# palavras muito comuns de cada idioma: bastam para dizer a língua de uma legenda inteira
_STOPWORDS = {
    "en": {"the", "you", "and", "is", "it", "to", "of", "that", "what", "this", "i'm", "don't"},
    "fr": {"le", "la", "les", "et", "est", "je", "tu", "vous", "pas", "que", "une", "c'est"},
    "es": {"el", "la", "los", "y", "es", "que", "no", "una", "por", "qué", "pero", "está"},
    "pt": {"o", "a", "os", "que", "não", "você", "é", "uma", "isso", "está", "para", "com"},
    "it": {"il", "la", "che", "non", "è", "un", "sono", "per", "questo", "cosa", "ma", "mi"},
    "de": {"der", "die", "und", "ist", "nicht", "ich", "du", "das", "ein", "zu", "sie", "es"},
}
_WORD = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?", re.UNICODE)


def guess_text_lang(path: Path, max_chars: int = 20000) -> str | None:
    """Idioma provável de uma legenda pelo texto (para `Filme.hi.srt`, sem idioma no nome)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:max_chars].lower()
    except OSError:
        return None
    words = _WORD.findall(text)
    if len(words) < 30:
        return None
    scores = {lang: sum(w in sw for w in words) for lang, sw in _STOPWORDS.items()}
    best = max(scores, key=lambda k: scores[k])
    ranked = sorted(scores.values(), reverse=True)
    if ranked[0] < 0.05 * len(words) or ranked[0] < 1.3 * ranked[1]:
        return None  # pouca evidência ou empate: melhor não chutar
    if best == "pt":
        # Brasil trata por "você"; Portugal usa o "tu" com o verbo na 2ª pessoa
        br = sum(w in _PT_BR for w in words)
        pt = sum(w in _PT_PT for w in words)
        if br > pt:
            return "pt-BR"
    return best


_PT_BR = {"você", "vocês", "ônibus", "trem", "cara", "tá", "pra", "legal", "garota"}
_PT_PT = {"estás", "tens", "queres", "podes", "sabes", "fazes", "vais", "autocarro", "comboio",
          "rapariga", "fixe", "miúdo"}  # fmt: skip
