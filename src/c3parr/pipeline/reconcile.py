"""Reconciliação: lê o estado de um item (catálogo + arquivos), pergunta ao decisor e enfileira.

Entra por quatro portas, todas iguais: rotina (2-4x/dia), webhook, botão manual e o fim de cada
passo. A fila não duplica (item, passo, idioma) e o que vem de webhook/manual tem prioridade; os
próximos passos herdam a prioridade de quem os gerou.
"""

import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from c3parr.audio import ffmpeg
from c3parr.core.job import ComponentSpec
from c3parr.engine.engine import Engine
from c3parr.jobs.queue import JobQueue
from c3parr.library.store import Library
from c3parr.pipeline.catalog import Catalog, Media, sha_of
from c3parr.pipeline.decide import (
    Decision,
    EmbeddedTrack,
    ExternalSub,
    ItemState,
    MainSub,
    OurOutput,
    decide,
)
from c3parr.pipeline.naming import WORK_EXT, guess_text_lang, sidecar_subtitles, work_file
from c3parr.providers.factory import AbstractComponentFactoryFactory
from c3parr.subtitle.format import FormatRules

log = logging.getLogger("c3parr.pipeline")


@dataclass
class PipelineContext:
    catalog: Catalog
    library: Library
    queue: JobQueue
    factories: AbstractComponentFactoryFactory
    secrets: Mapping[str, str]
    make_engine: Callable[[], Engine]
    components: Callable[[], dict[str, ComponentSpec]]
    format_rules: FormatRules


def _work_files(video: Path, kind: str) -> dict[str, str]:
    """idioma → arquivo `<vídeo>.<kind>.<idioma>.c3parr` que existe."""
    prefix = f"{video.stem}.{kind}."
    out = {}
    try:
        for f in video.parent.iterdir():
            if f.name.startswith(prefix) and f.name.endswith(WORK_EXT):
                lang = f.name[len(prefix) : -len(WORK_EXT)]
                if lang:
                    out[lang] = str(f)
    except OSError:
        pass
    return out


def build_state(cat: Catalog, media: Media) -> ItemState:
    video = Path(media.video)
    probe = media.probe
    embedded = []
    for t in probe.subtitles if probe else ():
        extracted = work_file(video, "embedded", t.lang) if t.lang else None
        embedded.append(
            EmbeddedTrack(
                index=t.index,
                lang=t.lang,
                forced=t.forced,
                text_based=t.text_based,
                extracted_path=str(extracted) if extracted and extracted.exists() else None,
            )
        )
    externals = []
    for sc in sidecar_subtitles(video):
        if sc.ours:
            continue
        lang = sc.lang or guess_text_lang(Path(sc.path))
        externals.append(ExternalSub(path=sc.path, lang=lang, forced=sc.forced))
    outputs = {}
    for lang, rec in cat.outputs(media.id).items():
        p = Path(rec.path)
        exists = p.exists()
        outputs[lang] = OurOutput(path=rec.path, main_version=rec.main_version, exists=exists,
                                  modified=exists and sha_of(p) != rec.sha)  # fmt: skip
    main = None
    if media.main_path and media.main_lang and media.main_source:
        main = MainSub(
            path=media.main_path,
            lang=media.main_lang,
            source=media.main_source,
            version=media.main_version,
        )
    asr = work_file(video, "asr")
    asr_language = None
    if asr.exists():
        try:
            asr_language = json.loads(asr.read_text(encoding="utf-8")).get("language")
        except (OSError, ValueError):
            asr_language = None
    steps = {(f"{a}:{lang}" if lang else a): rec.state
             for (a, lang), rec in cat.steps(media.id).items()}  # fmt: skip
    audio_lang = ffmpeg.normalize_lang(probe.audio[0].lang) if probe and probe.audio else None
    return ItemState(
        video=media.video, original_language=media.original_language, audio_language=audio_lang,
        embedded=tuple(embedded), externals=tuple(externals), outputs=outputs, main=main,
        asr_path=str(asr) if asr.exists() else None, asr_language=asr_language,
        generated=_work_files(video, "main"),
        steps=steps,
    )  # fmt: skip


def _forget_old_work(cat: Catalog, video: Path, media_id: int) -> None:
    """O vídeo mudou (upgrade, outra versão): o trabalho anterior não serve mais."""
    for f in video.parent.iterdir():
        if f.name.startswith(video.stem + ".") and f.name.endswith(WORK_EXT):
            f.unlink(missing_ok=True)
    for rec in cat.outputs(media_id).values():
        p = Path(rec.path)
        if p.exists() and sha_of(p) == rec.sha:
            p.unlink()
        cat.forget_output(media_id, rec.lang)


def reconcile(ctx: PipelineContext, video: str, origem: str = "rotina",
              retry_failed: bool = False, force: bool = False) -> Decision | None:  # fmt: skip
    """Decide e enfileira os próximos passos de um vídeo. A rotina respeita monitorado e espera;
    webhook, manual e o fim de um passo (`force`) não."""
    cfg = ctx.library.config()
    if origem == "rotina" and not force:
        info = ctx.library.watch_info(video)
        if info is None or not info[0]:
            return None
        added = datetime.fromisoformat(info[1])
        if added + timedelta(minutes=cfg.wait_minutes) > datetime.now(UTC):
            return None
    path = Path(video)
    if not path.exists():
        ctx.catalog.remove_media(video)
        return None
    media, changed = ctx.catalog.ensure_media(video)
    if changed:
        log.info("%s changed on disk: starting over", path.name)
        _forget_old_work(ctx.catalog, path, media.id)
    if media.probe is None:
        try:
            ctx.catalog.set_probe(media.id, ffmpeg.probe(path))
        except Exception as e:  # sem sondagem não há decisão: falha com alerta
            msg = f"could not read {path.name}: {e}"
            ctx.catalog.raise_alert(f"probe:{video}", "sondar", msg, video, media.id)
            return Decision(blocked=("sondar",), notes=(msg,))
        ctx.catalog.resolve_alerts(key=f"probe:{video}")
        media = ctx.catalog.media(media.id)

    decision = Decision()
    for _ in range(4):  # trocar a principal muda o estado; decide de novo até estabilizar
        decision = decide(build_state(ctx.catalog, media), cfg, retry_failed)
        m = decision.main
        if m is not None and (media.main_path, media.main_lang, media.main_source) != (
            m.path,
            m.lang,
            m.source,
        ):
            ctx.catalog.set_main(media.id, m.path, m.lang, m.source)
            media = ctx.catalog.media(media.id)
            continue
        break
    created = 0
    for a in decision.actions:
        _, new = ctx.queue.submit_step(media.id, video, a.kind, a.lang, a.arg, origem)
        created += new
    return decision.model_copy(update={"queued": created})


def reconcile_all(ctx: PipelineContext) -> str:
    """Rotina: passa por toda a biblioteca; falhas anteriores ganham nova tentativa."""
    before = ctx.queue.pending_count()
    seen = 0
    videos = ctx.library.videos()
    for video in videos:
        try:
            if reconcile(ctx, video, "rotina", retry_failed=True) is not None:
                seen += 1
        except Exception:  # um item quebrado não para a rotina
            log.exception("reconcile %s", video)
    known = set(videos)
    for m in ctx.catalog.all_media():
        if m.video not in known:
            ctx.catalog.remove_media(m.video)
    added = ctx.queue.pending_count() - before
    return f"{seen} items checked, {max(0, added)} steps queued"
