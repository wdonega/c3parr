"""Compara a nossa legenda com uma de gabarito (ex.: a PT-BR humana já sincronizada).

Pareia as cues pela sobreposição de tempo e mede cobertura, diferença de início e similaridade de
texto. A amostra lado a lado serve para a revisão manual de gênero e tratamento (50 falas).
"""

import random
import statistics
from pathlib import Path
from typing import Annotated

import typer
from rapidfuzz import fuzz

from c3parr.align.text import clean
from c3parr.core.models import Cue, Model, Subtitle
from c3parr.subtitle.srt import format_time, read_srt


class Pair(Model):
    start: float
    gold: str
    ours: str
    similarity: float
    start_diff: float


class Evaluation(Model):
    gold_cues: int
    our_cues: int
    coverage: float
    median_start_diff: float | None
    p90_start_diff: float | None
    text_similarity: float | None
    pairs: tuple[Pair, ...]


def _overlap(a: Cue, b: Cue) -> float:
    return max(0.0, min(a.end, b.end) - max(a.start, b.start))


def evaluate(ours: Subtitle, gold: Subtitle) -> Evaluation:
    pairs: list[Pair] = []
    j = 0
    mine = list(ours.cues)
    for g in gold.cues:
        text = clean(g.text)
        if not text:
            continue
        while j < len(mine) and mine[j].end < g.start - 2:
            j += 1
        near = [c for c in mine[j : j + 6] if _overlap(c, g) > 0]
        if not near:
            continue
        joined = " ".join(clean(c.text) for c in near)
        sim = fuzz.token_set_ratio(text.lower(), joined.lower()) / 100
        pair = Pair(
            start=g.start,
            gold=text,
            ours=joined,
            similarity=round(sim, 3),
            start_diff=round(abs(near[0].start - g.start), 3),
        )
        pairs.append(pair)
    speech = sum(1 for g in gold.cues if clean(g.text)) or 1
    diffs = sorted(p.start_diff for p in pairs)
    return Evaluation(
        gold_cues=speech,
        our_cues=len(ours.cues),
        coverage=round(len(pairs) / speech, 3),
        median_start_diff=statistics.median(diffs) if diffs else None,
        p90_start_diff=diffs[int(len(diffs) * 0.9)] if diffs else None,
        text_similarity=round(statistics.mean(p.similarity for p in pairs), 3) if pairs else None,
        pairs=tuple(pairs),
    )


def render(ev: Evaluation, sample: int = 50, seed: int = 0) -> str:
    out = ["# Evaluation against the gold subtitle", ""]
    out.append(f"- Gold lines: {ev.gold_cues}; our cues: {ev.our_cues}")
    out.append(f"- Coverage (gold lines with one of ours at the same time): {ev.coverage:.0%}")
    if ev.median_start_diff is not None and ev.p90_start_diff is not None:
        med, p90 = ev.median_start_diff, ev.p90_start_diff
        out.append(f"- Start difference: median {med:.2f} s, p90 {p90:.2f} s")
    if ev.text_similarity is not None:
        out.append(f"- Mean text similarity: {ev.text_similarity:.0%}")
    chosen = random.Random(seed).sample(list(ev.pairs), min(sample, len(ev.pairs)))
    picked = sorted(chosen, key=lambda p: p.start)
    out += [
        "",
        f"## Sample of {len(picked)} lines",
        "",
        "| Time | Gold | Ours | Sim. | Gender ok? | Formality ok? |",
        "|---|---|---|---|---|---|",
    ]
    for p in picked:
        g, o = p.gold.replace("|", "/"), p.ours.replace("|", "/")
        out.append(f"| {format_time(p.start)[:8]} | {g} | {o} | {p.similarity:.0%} |  |  |")
    return "\n".join(out) + "\n"


app = typer.Typer(add_completion=False, help="Compares a subtitle with a gold subtitle.")


@app.command()
def main(
    ours: Annotated[Path, typer.Argument(exists=True, help="Our subtitle")],
    gold: Annotated[Path, typer.Argument(exists=True, help="Gold subtitle (same language)")],
    out: Annotated[Path | None, typer.Option("--out", help="Write the report here")] = None,
    sample: Annotated[int, typer.Option("--sample")] = 50,
) -> None:
    ev = evaluate(read_srt(ours), read_srt(gold))
    text = render(ev, sample)
    if out:
        out.write_text(text, encoding="utf-8")
        text = "\n".join(text.splitlines()[:7])
    typer.echo(text)


def cli() -> None:
    app()
