"""Leitura e escrita de SubRip. A leitura é tolerante; a escrita segue o formato padrão."""

import re
from pathlib import Path

from c3parr.core.models import Cue, Subtitle

_TIME = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)


def _seconds(h: str, m: str, s: str, ms: str) -> float:
    total_ms = ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(ms.ljust(3, "0"))
    return total_ms / 1000


def format_time(t: float) -> str:
    total_ms = max(0, round(t * 1000))
    h, rest = divmod(total_ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt(text: str, lang: str | None = None) -> Subtitle:
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", text):
        lines = block.strip("\n").split("\n")
        for i, line in enumerate(lines):
            m = _TIME.search(line)
            if m is None:
                continue
            g = m.groups()
            body = "\n".join(lines[i + 1 :]).strip()
            if body:
                cues.append(
                    Cue(
                        index=len(cues) + 1, start=_seconds(*g[:4]), end=_seconds(*g[4:]), text=body
                    )
                )
            break
    return Subtitle(cues=tuple(cues), lang=lang)


def decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def read_srt(path: Path, lang: str | None = None) -> Subtitle:
    return parse_srt(decode(path.read_bytes()), lang)


def format_srt(subtitle: Subtitle) -> str:
    blocks = [
        f"{n}\n{format_time(c.start)} --> {format_time(c.end)}\n{c.text}\n"
        for n, c in enumerate(subtitle.cues, start=1)
    ]
    return "\n".join(blocks)


def write_srt(subtitle: Subtitle, path: Path) -> None:
    path.write_text(format_srt(subtitle), encoding="utf-8", newline="\n")
