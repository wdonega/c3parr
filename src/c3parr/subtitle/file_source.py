"""Provider `arquivo` da fonte de legenda: lê o .srt/.ass indicado pelo usuário."""

import re
from pathlib import Path

from c3parr.core.errors import InvalidInput
from c3parr.core.models import Subtitle
from c3parr.interfaces import subtitle_source
from c3parr.interfaces.subtitle_source import SubtitleRequest
from c3parr.providers.base import Health, ProviderInfo
from c3parr.subtitle.ass import parse_ass
from c3parr.subtitle.srt import decode, parse_srt

INFO = ProviderInfo(
    id="arquivo",
    interface=subtitle_source.INTERFACE,
    version="1",
    description="A .srt, .ass or .ssa file provided by the user",
)

_IGNORED_TAGS = {"hi", "sdh", "cc", "forced", "default", "sync", "c3parr"}
_LANG = re.compile(r"^[a-z]{2,3}(-[a-z]{2})?$", re.IGNORECASE)


def guess_lang(path: Path) -> str | None:
    """Idioma pelas marcas do nome (`Filme.pt-BR.hi.srt` -> `pt-BR`)."""
    for token in reversed(path.stem.split(".")[1:]):
        token = re.sub(r"\s*\(\d+\)$", "", token).strip()
        if token.lower() in _IGNORED_TAGS:
            continue
        if _LANG.match(token):
            base, _, region = token.partition("-")
            return f"{base.lower()}-{region.upper()}" if region else base.lower()
        return None
    return None


class FileSubtitleSource:
    info = INFO

    def health(self) -> Health:
        return Health(ok=True)

    def fetch(self, request: SubtitleRequest) -> Subtitle:
        path = Path(request.ref)
        if not path.exists():
            raise InvalidInput(f"reference subtitle not found: {path}")
        text = decode(path.read_bytes())
        lang = guess_lang(path)
        if path.suffix.lower() in (".ass", ".ssa"):
            sub = parse_ass(text, lang)
        else:
            sub = parse_srt(text, lang)
        if not sub.cues:
            raise InvalidInput(f"no readable cue in {path.name}")
        return sub


def register() -> None:
    subtitle_source.FACTORY.register(INFO, lambda _config: FileSubtitleSource())
