"""Os passos da pipeline (um por tarefa da fila) e o executor que a fila chama.

Cada passo termina em `ok`, `nao_encontrado` (resultado normal: o decisor segue para o próximo
ramo) ou levanta erro. Todo erro — de provider, de arquivo, de ffmpeg, inesperado — vira `falhou`
com alerta agrupado pela causa; a rotina tenta de novo na próxima rodada. No fim, o item é
reconciliado para enfileirar o próximo passo com a mesma prioridade.
"""

import errno
import logging
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from c3parr.audio import ffmpeg
from c3parr.core.errors import InvalidInput, ProviderError, Unavailable
from c3parr.core.job import JobParams
from c3parr.core.models import Cue, Model, Segment, Subtitle, Transcript
from c3parr.engine.engine import RunResult, StepReport
from c3parr.interfaces.metadata import MetadataRequest, MetadataResult
from c3parr.interfaces.subtitle_source import SubtitleRequest
from c3parr.interfaces.sync import SyncRequest
from c3parr.jobs.queue import Job
from c3parr.library.store import AppConfig
from c3parr.pipeline.catalog import Media, StepState, sha_of
from c3parr.pipeline.naming import output_file, work_file
from c3parr.pipeline.reconcile import PipelineContext, reconcile
from c3parr.recipes.pipeline import asr_recipe, subtitle_recipe
from c3parr.recipes.transcribe import WrittenFile
from c3parr.subtitle.format import cues_from_segments
from c3parr.subtitle.srt import write_srt

log = logging.getLogger("c3parr.pipeline")
Progress = Callable[[str, float, str], None]


class Outcome(Model):
    state: StepState
    detail: str | None = None
    path: str | None = None


class StepFailed(Exception):
    """Falha com causa conhecida (para agrupar o alerta)."""

    def __init__(self, message: str, key: str, target: str) -> None:
        super().__init__(message)
        self.key = key
        self.target = target


def _ok(detail: str | None = None, path: Path | str | None = None) -> Outcome:
    return Outcome(state="ok", detail=detail, path=str(path) if path else None)


def _not_found(detail: str) -> Outcome:
    return Outcome(state="nao_encontrado", detail=detail)


def _write_atomic(dest: Path, data: str) -> None:
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, dest)


def _read_sub(ctx: PipelineContext, path: str, video: str, lang: str | None) -> Subtitle:
    spec = ctx.components()["fonte_legenda"]
    source = ctx.factories.for_interface("fonte_legenda").create(spec, ctx.secrets)
    sub: Subtitle = source.fetch(SubtitleRequest(ref=path, video=video))
    return sub if sub.lang else sub.model_copy(update={"lang": lang})


def _engine_failure(result: RunResult) -> StepFailed:
    f = result.failed
    assert f is not None
    provider = f.provider or f.name
    return StepFailed(f"{f.name}: {f.reason}", key=f"provider:{provider}:{f.error_kind}",
                      target=provider)  # fmt: skip


# --- passos ---


def do_metadata(ctx: PipelineContext, m: Media, job: Job, cfg: AppConfig, p: Progress,
                cancelled: Callable[[], bool]) -> Outcome:  # fmt: skip
    spec = ctx.components().get("metadados")
    if spec is None:
        return _not_found("no metadata provider configured")
    try:
        provider = ctx.factories.for_interface("metadados").create(spec, ctx.secrets)
    except Unavailable as e:
        return _not_found(f"metadata unavailable: {e.message}")  # sem chave: aviso em Status
    request = MetadataRequest(filename=Path(m.video).name, path=m.video,
                              audio_duration=m.probe.duration if m.probe else None)  # fmt: skip
    try:
        result: MetadataResult = provider.identify(request)
    except InvalidInput as e:
        return _not_found(e.message)
    dest = work_file(Path(m.video), "meta")
    _write_atomic(dest, result.model_dump_json(indent=1))
    ctx.catalog.set_original_language(m.id, ffmpeg.normalize_lang(result.original_language))
    return _ok(f"TMDb {result.tmdb_id}: {result.chosen_reason}", dest)


def do_extract(ctx: PipelineContext, m: Media, job: Job, cfg: AppConfig, p: Progress,
               cancelled: Callable[[], bool]) -> Outcome:  # fmt: skip
    dest = work_file(Path(m.video), "embedded", job.idioma)
    tmp = dest.with_name(dest.name + ".part")
    subprocess.run([shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-y", "-i", m.video,
                    "-map", f"0:{job.arg}", "-c:s", "srt", "-f", "srt", str(tmp)],
                   check=True, capture_output=True, text=True)  # fmt: skip
    os.replace(tmp, dest)
    return _ok(f"track {job.arg} → {dest.name}", dest)


def do_asr(ctx: PipelineContext, m: Media, job: Job, cfg: AppConfig, p: Progress,
           cancelled: Callable[[], bool]) -> Outcome:  # fmt: skip
    params = JobParams(recipe="asr", video=m.video)
    result = ctx.make_engine().run(asr_recipe(), params, p, cancelled)
    if result.failed is not None:
        raise _engine_failure(result)
    transcript = result.artifacts["transcricao"]
    assert isinstance(transcript, Transcript)
    dest = work_file(Path(m.video), "asr")
    _write_atomic(dest, transcript.model_dump_json())
    return _ok(f"{len(transcript.segments)} lines in {transcript.language}", dest)


def _reference_transcript(ctx: PipelineContext, m: Media, exclude: str) -> Transcript | None:
    """Linha do tempo para sincronizar: a transcrição do áudio ou, sem ela, a principal."""
    asr = work_file(Path(m.video), "asr")
    if asr.exists():
        return Transcript.model_validate_json(asr.read_text(encoding="utf-8"))
    if m.main_path and m.main_path != exclude and Path(m.main_path).exists():
        sub = _read_sub(ctx, m.main_path, m.video, m.main_lang)
        segs = tuple(Segment(id=i, start=c.start, end=c.end, text=c.text, lang=m.main_lang or "und")
                     for i, c in enumerate(sub.cues))  # fmt: skip
        return Transcript(segments=segs, language=m.main_lang or "und", model="principal",
                          audio_duration=m.probe.duration if m.probe else segs[-1].end)  # fmt: skip
    return None


def do_sync(ctx: PipelineContext, m: Media, job: Job, cfg: AppConfig, p: Progress,
            cancelled: Callable[[], bool]) -> Outcome:  # fmt: skip
    path = Path(job.arg or "")
    reference = _reference_transcript(ctx, m, str(path))
    if reference is None:
        return _not_found("no timing reference (no ASR and no other main subtitle)")
    sub = _read_sub(ctx, str(path), m.video, job.idioma)
    syncer = ctx.factories.for_interface("sincronizacao").create(
        ctx.components()["sincronizacao"], ctx.secrets)  # fmt: skip
    duration = m.probe.duration if m.probe and m.probe.duration else reference.audio_duration
    result = syncer.sync(sub, reference, SyncRequest(audio_duration=duration))
    if not result.accepted:
        return _not_found(f"sync rejected ({result.reason}): probably another cut of the video")
    cues = tuple(Cue(index=c.index, start=c.start, end=c.end, text=c.text) for c in result.cues)
    synced = Subtitle(cues=cues, lang=sub.lang)
    is_work = path.name.endswith(".c3parr")
    if is_work or (cfg.fix_external_in_place and path.suffix.lower() == ".srt"):
        if not is_work:
            backup = path.with_name(path.name + ".orig.c3parr")
            if not backup.exists():
                shutil.copy2(path, backup)
        tmp = path.with_name(path.name + ".part")
        write_srt(synced, tmp)
        os.replace(tmp, path)
    drift = f", drift {result.drift:.2f} s" if result.drift is not None else ""
    return _ok(f"synced ({result.matched_ratio:.0%} matched{drift})", path)


def do_generate(ctx: PipelineContext, m: Media, job: Job, cfg: AppConfig, p: Progress,
                cancelled: Callable[[], bool]) -> Outcome:  # fmt: skip
    asr = work_file(Path(m.video), "asr")
    transcript = Transcript.model_validate_json(asr.read_text(encoding="utf-8"))
    cues = cues_from_segments(transcript.segments, ctx.format_rules)
    dest = work_file(Path(m.video), "main", job.idioma)
    tmp = dest.with_name(dest.name + ".part")
    write_srt(Subtitle(cues=tuple(cues), lang=job.idioma), tmp)
    os.replace(tmp, dest)
    return _ok(f"{len(cues)} lines from the ASR", dest)


def do_translate(ctx: PipelineContext, m: Media, job: Job, cfg: AppConfig, p: Progress,
                 cancelled: Callable[[], bool]) -> Outcome:  # fmt: skip
    if not m.main_path or not m.main_lang:
        raise InvalidInput("no main subtitle to translate from")
    lang = job.idioma or cfg.target_language
    rules = ctx.format_rules.model_copy(update={"foreign_tag": cfg.foreign_tag})
    params = JobParams(recipe="legenda", video=m.video, src=m.main_lang, dst=lang, ref=m.main_path)
    result = ctx.make_engine().run(subtitle_recipe(rules), params, p, cancelled)
    if result.failed is not None:
        raise _engine_failure(result)
    written = result.artifacts["escrita"]
    assert isinstance(written, WrittenFile)
    ctx.catalog.record_output(m.id, lang, Path(written.path), m.main_version, "translated")
    warn = f"; {len(result.degradations)} warnings" if result.degradations else ""
    return _ok(f"{m.main_lang} → {lang}{warn}", written.path)


def do_publish(ctx: PipelineContext, m: Media, job: Job, cfg: AppConfig, p: Progress,
               cancelled: Callable[[], bool]) -> Outcome:  # fmt: skip
    if not m.main_path:
        raise InvalidInput("no main subtitle to publish")
    lang = job.idioma or m.main_lang or "und"
    sub = _read_sub(ctx, m.main_path, m.video, lang)
    dest = output_file(Path(m.video), lang)
    tmp = dest.with_name(dest.name + ".part")
    write_srt(sub, tmp)
    os.replace(tmp, dest)
    ctx.catalog.record_output(m.id, lang, dest, m.main_version, "published")
    return _ok(f"main subtitle published as {dest.name}", dest)


def do_remove(ctx: PipelineContext, m: Media, job: Job, cfg: AppConfig, p: Progress,
              cancelled: Callable[[], bool]) -> Outcome:  # fmt: skip
    lang = job.idioma or ""
    rec = ctx.catalog.outputs(m.id).get(lang)
    if rec is None:
        return _not_found(f"no subtitle of ours in {lang}")
    path = Path(rec.path)
    if path.exists() and sha_of(path) != rec.sha:
        return _not_found(f"{path.name} was edited by someone; kept")
    path.unlink(missing_ok=True)
    ctx.catalog.forget_output(m.id, lang)
    return _ok(f"removed {path.name}: another source now covers {lang}")


ACTIONS: dict[str, Callable[..., Outcome]] = {
    "metadados": do_metadata,
    "extrair": do_extract,
    "asr": do_asr,
    "sincronizar": do_sync,
    "gerar": do_generate,
    "traduzir": do_translate,
    "publicar": do_publish,
    "remover": do_remove,
}


# --- falhas viram alertas agrupados pela causa ---


def classify(e: BaseException, job: Job) -> tuple[str, str, str]:
    """(chave do alerta, mensagem, alvo). Mesma causa → mesma chave → um alerta só."""
    if isinstance(e, StepFailed):
        return e.key, str(e), e.target
    if isinstance(e, PermissionError):
        where = str(Path(e.filename).parent) if e.filename else job.video
        return f"perm:{where}", f"no permission to write in {where}", where
    if isinstance(e, OSError) and e.errno == errno.ENOSPC:
        return "disk-full", "disk full", "disk"
    if isinstance(e, ProviderError):
        provider = e.provider or job.acao or "?"
        return f"provider:{provider}:{e.kind}", e.message, provider
    if isinstance(e, subprocess.CalledProcessError):
        msg = (e.stderr or "").strip()[-300:] or str(e)
        return (
            f"tool:{job.acao}:{job.media_id}",
            f"{Path(str(e.cmd[0])).name} failed: {msg}",
            job.video,
        )
    return f"step:{job.acao}:{job.media_id}", f"unexpected error: {e}", job.video


def run_step(ctx: PipelineContext, job: Job, progress: Progress,
             cancelled: Callable[[], bool]) -> tuple[RunResult, str | None, str]:  # fmt: skip
    """O que o worker da fila chama para uma tarefa de passo."""
    assert job.media_id is not None and job.acao is not None
    media = ctx.catalog.media(job.media_id)
    cfg = ctx.library.config()
    step_lang = job.arg if job.acao == "sincronizar" else job.idioma
    t0 = time.perf_counter()
    if job.acao not in ACTIONS:  # tarefa de um passo que não existe mais (ex.: busca online)
        report = StepReport(name=job.acao, status="ok", seconds=0.0, reason="step no longer exists")
        return (
            RunResult(recipe="legenda", workdir=Path(media.video).parent, steps=[report]),
            None,
            "",
        )
    try:
        outcome = ACTIONS[job.acao](ctx, media, job, cfg, progress, cancelled)
    except Exception as e:  # toda falha é registrada, alertada e repetida pela rotina
        if not isinstance(e, ProviderError | StepFailed | OSError | subprocess.CalledProcessError):
            log.exception("step %s on %s", job.acao, Path(job.video).name)
        key, message, target = classify(e, job)
        ctx.catalog.raise_alert(key, job.acao, message, target, media.id)
        outcome = Outcome(state="falhou", detail=message)
    ctx.catalog.record_step(media.id, job.acao, step_lang, outcome.state, outcome.detail)
    if outcome.state != "falhou":
        ctx.catalog.resolve_alerts(media_id=media.id, kind=job.acao)
    report = StepReport(name=job.acao, status="falhou" if outcome.state == "falhou" else "ok",
                        seconds=round(time.perf_counter() - t0, 2),
                        reason=("not found: " if outcome.state == "nao_encontrado" else "")
                        + (outcome.detail or ""))  # fmt: skip
    result = RunResult(recipe="legenda", workdir=Path(media.video).parent, steps=[report],
                       failed=report if outcome.state == "falhou" else None)  # fmt: skip
    try:  # próximo passo, com a prioridade de quem gerou este
        reconcile(ctx, media.video, job.origem or "rotina", force=True)
    except Exception:
        log.exception("reconcile after %s", job.acao)
    return result, outcome.path, ""
