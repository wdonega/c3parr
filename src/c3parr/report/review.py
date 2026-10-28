"""Relatório de dúvidas: JSON para a interface web e Markdown para o usuário revisar em minutos."""

from collections import defaultdict
from pathlib import Path

from pydantic import Field

from c3parr.core.bible import Bible
from c3parr.core.models import Degradation, LanguageSpan, Model, Subtitle, Transcript
from c3parr.engine.engine import RunResult, StepReport
from c3parr.interfaces.metadata import MetadataResult
from c3parr.interfaces.sync import SyncResult
from c3parr.interfaces.translation import Translation, TranslationUsage
from c3parr.metadata.names import NameFix, NamesResult
from c3parr.recipes.full import ref_texts
from c3parr.subtitle.srt import format_time

FLAG_LABELS = {
    "baixa_confianca": "Low ASR confidence",
    "repeticao": "Suspicious repetition",
    "fala_rapida": "Too fast (possible hallucination)",
    "alucinacao_conhecida": "Typical hallucination text (removed from the subtitle)",
}


class ReviewItem(Model):
    start: float
    end: float
    text: str
    lang: str


class Review(Model):
    video: str
    recipe: str
    ok: bool
    failure: str | None = None
    duration: float | None = None
    language: str | None = None
    language_prob: float | None = None
    model: str | None = None
    steps: tuple[StepReport, ...]
    degradations: tuple[Degradation, ...]
    sync: SyncResult | None = None
    other_languages: tuple[LanguageSpan, ...] = ()
    to_review: dict[str, tuple[ReviewItem, ...]] = Field(default_factory=dict)
    translation: "TranslationSummary | None" = None
    bible: Bible | None = None
    metadata: MetadataResult | None = None
    name_fixes: tuple[NameFix, ...] = ()
    output: str | None = None


class TranslationItem(Model):
    start: float
    original: str
    ref: str | None = None
    traducao: str
    motivo: str | None = None


class TranslationSummary(Model):
    dst: str
    usage: TranslationUsage
    missing_capabilities: tuple[str, ...] = ()
    lines: int = 0
    uncertain_ratio: float = 0.0
    groups: dict[str, tuple[TranslationItem, ...]] = Field(default_factory=dict)
    fast_reading: tuple[TranslationItem, ...] = ()


Review.model_rebuild()


def _group(motivo: str | None, fallback: bool) -> str:
    text = (motivo or "").lower()
    if fallback:
        return "Block fell back (kept the original)"
    if any(k in text for k in ("gênero", "genero", "rótulo", "gender", "label")):
        return "Uncertain gender"
    if any(k in text for k in ("tratamento", "formal", "você")):
        return "Uncertain formality"
    return "Other doubts"


def summarize_translation(
    result: RunResult, transcript: Transcript, max_cps: float
) -> TranslationSummary | None:
    tr = result.artifacts.get("traducao")
    if not isinstance(tr, Translation):
        return None
    segs = {s.id: s for s in transcript.segments}
    s = result.artifacts.get("sincronizacao")
    refs = ref_texts(s if isinstance(s, SyncResult) else None, transcript)
    groups: dict[str, list[TranslationItem]] = defaultdict(list)
    for ln in tr.lines:
        if not ln.incerto or ln.id not in segs:
            continue
        seg = segs[ln.id]
        item = TranslationItem(start=seg.start, original=seg.text, ref=refs.get(ln.id),
                               traducao=ln.texto, motivo=ln.motivo)  # fmt: skip
        groups[_group(ln.motivo, ln.fallback)].append(item)
    fast = []
    sub = result.artifacts.get("formatacao")
    if isinstance(sub, Subtitle):
        for c in sub.cues:
            chars = len(c.text.replace("\n", ""))
            if c.end > c.start and chars / (c.end - c.start) > max_cps * 1.3:
                fast.append(TranslationItem(start=c.start, original="", traducao=c.text))
    uncertain = sum(len(v) for v in groups.values())
    return TranslationSummary(
        dst=tr.dst,
        usage=tr.usage,
        missing_capabilities=tr.missing_capabilities,
        lines=len(tr.lines),
        uncertain_ratio=round(uncertain / max(1, len(tr.lines)), 3),
        groups={k: tuple(v) for k, v in groups.items()},
        fast_reading=tuple(fast),
    )


def build_review(
    result: RunResult, video: str, output: str | None, max_cps: float = 17.0
) -> Review:
    t = result.artifacts.get("transcricao")
    transcript = t if isinstance(t, Transcript) else None
    review: dict[str, list[ReviewItem]] = defaultdict(list)
    others: tuple[LanguageSpan, ...] = ()
    if transcript is not None:
        for seg in transcript.segments:
            for flag in sorted(seg.flags):
                review[flag].append(
                    ReviewItem(start=seg.start, end=seg.end, text=seg.text, lang=seg.lang)
                )
        others = tuple(
            sp
            for sp in transcript.language_spans
            if sp.lang != transcript.language and sp.prob >= 0.5
        )
    s = result.artifacts.get("sincronizacao")
    sync = s.model_copy(update={"cues": ()}) if isinstance(s, SyncResult) else None
    bible = next(
        (b for b in (result.artifacts.get("biblia"), result.artifacts.get("contexto"))
         if isinstance(b, Bible)),
        None,
    )  # fmt: skip
    return Review(
        video=video,
        recipe=result.recipe,
        sync=sync,
        translation=summarize_translation(result, transcript, max_cps) if transcript else None,
        bible=bible,
        metadata=m if isinstance(m := result.artifacts.get("metadados"), MetadataResult) else None,
        name_fixes=n.fixes if isinstance(n := result.artifacts.get("nomes"), NamesResult) else (),
        ok=result.ok,
        failure=result.failed.reason if result.failed else None,
        duration=transcript.audio_duration if transcript else None,
        language=transcript.language if transcript else None,
        language_prob=transcript.language_prob if transcript else None,
        model=transcript.model if transcript else None,
        steps=tuple(result.steps),
        degradations=tuple(result.degradations),
        other_languages=others,
        to_review={k: tuple(v) for k, v in review.items()},
        output=output,
    )


def render_markdown(r: Review) -> str:
    out = [f"# Review report: {Path(r.video).name}", "", "## Summary", ""]
    if r.duration is not None:
        out.append(f"- Duration: {format_time(r.duration)}")
    if r.language:
        prob = f" ({r.language_prob:.0%} of speech)" if r.language_prob is not None else ""
        out.append(f"- Main language: {r.language}{prob}")
    if r.model:
        out.append(f"- ASR model: {r.model}")
    out.append(f"- Recipe: {r.recipe}")
    out.append(f"- Output: {r.output or 'no file written'}")
    if r.failure:
        out.append(f"- **Failed**: {r.failure}")
    out += ["", "| Stage | Status | Provider | Time |", "|---|---|---|---|"]
    for s in r.steps:
        provider = s.provider or "—"
        if s.sends_data_off_machine:
            provider += " (sends data off this machine)"
        detail = f" — {s.reason}" if s.reason else ""
        out.append(f"| {s.name} | {s.status}{detail} | {provider} | {s.seconds:.1f} s |")

    out += ["", "## Degradations", ""]
    if r.degradations:
        out += [f"- **{d.stage}**: {d.reason}. {d.effect}".rstrip() for d in r.degradations]
    else:
        out.append("None.")

    if r.sync is not None:
        sy = r.sync
        out += ["", "## Reference sync", ""]
        reason = f" ({sy.reason})" if sy.reason else ""
        out.append(f"- Accepted: {'yes' if sy.accepted else 'no'}{reason}")
        if sy.via_bridge:
            out.append("- Synced through the bridge subtitle (which was synced to the audio)")
        for f in sy.fits:
            out.append(f"- Line: t_audio = {f.a:.5f} · t_ref {f.b:+.3f} s")
        if sy.framerate_hint:
            out.append(f"- {sy.framerate_hint}")
        if sy.mean_error is not None:
            out.append(f"- Mean error after fitting: {sy.mean_error:.2f} s")
        out.append(f"- Reference lines matched: {sy.matched_ratio:.0%}")
        out.append(f"- Landmarks consistent with neighbours: {sy.inlier_ratio:.0%}")
        if sy.drift is not None and abs(sy.drift) >= 0.5:
            out.append(f"- Accumulated drift from start to end: {sy.drift:+.1f} s")
        if sy.cuts:
            cuts = ", ".join(format_time(c)[:8] for c in sy.cuts)
            out.append(f"- Jumps (cuts or extra scenes in the reference) at: {cuts}")
        if sy.checkpoints:
            out += ["", "| Audio time | Reference offset |", "|---|---|"]
            out += [f"| {format_time(c.t_audio)[:8]} | {c.offset:+.2f} s |" for c in sy.checkpoints]

    if r.metadata is not None:
        md = r.metadata
        out += ["", "## Metadata (TMDb)", ""]
        name = md.bible.titulo.pt or md.bible.titulo.original
        if md.kind == "serie":
            ep = f" S{md.season or 0:02d}E{md.episode or 0:02d}"
            ep += f' "{md.episode_title}"' if md.episode_title else ""
            out.append(f"- Show: {name}{ep}, id {md.tmdb_id}: {md.chosen_reason}")
        else:
            out.append(f"- Movie: {name} ({md.bible.ano}), id {md.tmdb_id}: {md.chosen_reason}")
        for c in md.candidates:
            if c.id != md.tmdb_id:
                out.append(f"- Discarded: {c.titulo} ({c.ano}, id {c.id}): {c.motivo}")
    if r.name_fixes:
        out += ["", "## Names fixed in the transcript", ""]
        out += [f"- {f.wrong} → {f.right} ({f.count} times)" for f in r.name_fixes]
    if r.bible is not None:
        out += _bible_section(r.bible)
    if r.translation is not None:
        out += _translation_section(r.translation)

    if r.other_languages:
        total = sum(sp.end - sp.start for sp in r.other_languages)
        out += ["", "## Spans in another language", ""]
        out += [f"{len(r.other_languages)} spans, {total / 60:.1f} min in total.", ""]
        for sp in r.other_languages[:50]:
            out.append(
                f"- {format_time(sp.start)} to {format_time(sp.end)}: {sp.lang} ({sp.prob:.0%})"
            )

    out += ["", "## Lines to review", ""]
    if not r.to_review:
        out.append("None.")
    for flag, items in r.to_review.items():
        out += [f"### {FLAG_LABELS.get(flag, flag)} ({len(items)})", ""]
        out += [f"- `{format_time(i.start)}` [{i.lang}] {i.text}" for i in items]
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _bible_section(b: Bible) -> list[str]:
    out = ["", "## Bible", "", f"Source: {', '.join(b.origem) or '—'}. "
           "Edit the bible JSON and run again with `--bible` to fix it.", ""]  # fmt: skip
    if b.personagens:
        out += ["| Character | Gender | Source | Confidence |", "|---|---|---|---|"]
        out += [
            f"| {c.nome} | {c.genero} | {c.origem_genero or '—'} | {c.confianca:.0%} |"
            for c in b.personagens
        ]
    if b.relacoes:
        out += ["", "Relations and formality:", ""]
        out += [
            f"- {r.a} e {r.b} ({r.tipo}): {r.a} → {r.tratamento_a_para_b or '?'}, "
            f"{r.b} → {r.tratamento_b_para_a or '?'} ({r.confianca:.0%})"
            for r in b.relacoes
        ]
    if b.glossario:
        out += ["", "Glossary:", ""]
        out += [f"- {g.termo} → {g.traducao} ({g.confianca:.0%})" for g in b.glossario]
    return out


def _translation_section(t: TranslationSummary) -> list[str]:
    u = t.usage
    out = ["", "## Translation", ""]
    out.append(f"- Lines translated: {t.lines}; marked uncertain: {t.uncertain_ratio:.0%}")
    out.append(
        f"- LLM: {u.calls} calls, {u.prompt_tokens} input tokens ({u.cached_tokens} from "
        f"cache), {u.completion_tokens} output, {u.seconds:.0f} s"
    )
    if t.missing_capabilities:
        out.append(f"- The translator does not offer: {', '.join(t.missing_capabilities)}")
    for group, items in t.groups.items():
        out += ["", f"### {group} ({len(items)})", ""]
        for i in items:
            ref = f" / ref: {i.ref}" if i.ref else ""
            motivo = f" — {i.motivo}" if i.motivo else ""
            out.append(f"- `{format_time(i.start)}` {i.original}{ref} → **{i.traducao}**{motivo}")
    if t.fast_reading:
        out += ["", f"### Reading too fast ({len(t.fast_reading)})", ""]
        out += [f"- `{format_time(i.start)}` {i.traducao.replace(chr(10), ' / ')}"
                for i in t.fast_reading]  # fmt: skip
    return out


def write_review(r: Review, json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(r.model_dump_json(indent=1), encoding="utf-8")
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(r), encoding="utf-8")
