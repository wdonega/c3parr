"""Leitura de ASS/SSA reduzida a texto e tempo (posição, cor e efeitos são descartados)."""

import re

from c3parr.core.models import Cue, Subtitle

_OVERRIDE = re.compile(r"\{[^}]*\}")


def _time(value: str) -> float:
    h, m, s = value.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def parse_ass(text: str, lang: str | None = None) -> Subtitle:
    fields: list[str] | None = None
    in_events = False
    rows: list[tuple[float, float, str]] = []
    for raw in text.lstrip("﻿").splitlines():
        line = raw.strip()
        if line.startswith("["):
            in_events = line.lower() == "[events]"
            continue
        if not in_events or ":" not in line:
            continue
        kind, rest = line.split(":", 1)
        if kind.lower() == "format":
            fields = [f.strip().lower() for f in rest.split(",")]
        elif kind.lower() == "dialogue" and fields:
            parts = rest.split(",", len(fields) - 1)
            row = dict(zip(fields, (p.strip() for p in parts), strict=False))
            body = _OVERRIDE.sub("", row.get("text", ""))
            body = body.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ").strip()
            if body:
                rows.append((_time(row["start"]), _time(row["end"]), body))
    rows.sort(key=lambda r: r[0])
    cues = tuple(Cue(index=i, start=s, end=e, text=t) for i, (s, e, t) in enumerate(rows, start=1))
    return Subtitle(cues=cues, lang=lang)
