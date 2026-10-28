"""Receita `sincronizacao` (modo `--sync-only`): corrige o tempo da referência, sem LLM."""

from pathlib import Path

from c3parr.align.text import clean
from c3parr.core.errors import InvalidInput
from c3parr.core.models import Cue, Segment, Subtitle, Transcript
from c3parr.engine.engine import Recipe, Step, StepContext
from c3parr.interfaces import subtitle_source, subtitle_writer, sync
from c3parr.interfaces.subtitle_source import SubtitleRequest
from c3parr.interfaces.sync import SyncRequest, SyncResult
from c3parr.interfaces.transcription import AudioRef
from c3parr.recipes.transcribe import WrittenFile, audio_step, transcription_step


def fetch_reference(ctx: StepContext) -> Subtitle:
    if not ctx.params.ref:
        raise InvalidInput("--sync-only needs --ref")
    request = SubtitleRequest(ref=ctx.params.ref, video=ctx.params.video)
    result: Subtitle = ctx.component.fetch(request)
    return result


def fetch_bridge(ctx: StepContext) -> Subtitle:
    request = SubtitleRequest(ref=ctx.params.ref_bridge or "", video=ctx.params.video)
    result: Subtitle = ctx.component.fetch(request)
    return result


def bridge_transcript(synced: SyncResult, lang: str, duration: float) -> Transcript:
    """A legenda-ponte já sincronizada vira o alvo de tempo para a legenda traduzida."""
    segments = tuple(
        Segment(id=i, start=c.start, end=c.end, text=clean(c.text), lang=lang)
        for i, c in enumerate(synced.cues)
        if c.status != "nao_fala" and clean(c.text)
    )
    return Transcript(segments=segments, language=lang, model="ponte", audio_duration=duration)


def sync_against_audio(ctx: StepContext) -> SyncResult:
    """Sincroniza a referência; com `--ref-bridge`, primeiro a ponte pelo áudio, depois a
    referência pela ponte (útil quando a ponte está no idioma do áudio e a referência não)."""
    ref = ctx.require("referencia", Subtitle)
    transcript = ctx.require("transcricao", Transcript)
    audio = ctx.require("audio", AudioRef)
    request = SyncRequest(audio_duration=audio.duration)
    bridge = ctx.inputs.get("ponte")
    if isinstance(bridge, Subtitle):
        synced_bridge: SyncResult = ctx.component.sync(bridge, transcript, request)
        if synced_bridge.accepted:
            lang = bridge.lang or transcript.language
            target = bridge_transcript(synced_bridge, lang, audio.duration)
            result: SyncResult = ctx.component.sync(ref, target, request)
            if result.accepted:
                return result.model_copy(update={"via_bridge": True})
    result = ctx.component.sync(ref, transcript, request)
    return result


def synchronize(ctx: StepContext) -> SyncResult:
    result = sync_against_audio(ctx)
    if not result.accepted:
        raise InvalidInput(f"sync rejected: {result.reason}")
    return result


def bridge_step(required: bool = False) -> Step:
    return Step(
        name="ponte",
        run=fetch_bridge,
        output=Subtitle,
        interface=subtitle_source.INTERFACE,
        required=required,
        skip_reason=lambda p: None if p.ref_bridge else "no --ref-bridge",
        key_params=lambda p: {"ref_bridge": p.ref_bridge},
        cacheable=False,
        failure_effect="syncs the reference directly against the audio",
    )


def output_stem(video: Path, out_dir: str | None, lang: str | None, ref: Path) -> Path:
    """Destino `<vídeo>.<idioma>`; nunca sobrescreve a própria referência."""
    folder = Path(out_dir) if out_dir else video.parent
    stem = folder / f"{video.stem}.{lang or 'und'}"
    if stem.with_name(f"{stem.name}.srt").resolve() == ref.resolve():
        stem = stem.with_name(f"{stem.name}.c3parr")
    return stem


def write_synced(ctx: StepContext) -> WrittenFile:
    ref = ctx.require("referencia", Subtitle)
    result = ctx.require("sincronizacao", SyncResult)
    cues = tuple(Cue(index=c.index, start=c.start, end=c.end, text=c.text) for c in result.cues)
    p = ctx.params
    dest = output_stem(Path(p.video), p.out_dir, ref.lang, Path(p.ref or ""))
    return WrittenFile(path=str(ctx.component.write(Subtitle(cues=cues, lang=ref.lang), dest)))


def recipe() -> Recipe:
    return Recipe(
        name="sincronizacao",
        steps=(
            audio_step(),
            transcription_step(),
            Step(
                name="referencia",
                run=fetch_reference,
                output=Subtitle,
                interface=subtitle_source.INTERFACE,
                key_params=lambda p: {"ref": p.ref},
                cacheable=False,
            ),
            bridge_step(),
            Step(
                name="sincronizacao",
                run=synchronize,
                output=SyncResult,
                interface=sync.INTERFACE,
                needs=("referencia", "ponte", "transcricao", "audio"),
            ),
            Step(
                name="escrita",
                run=write_synced,
                output=WrittenFile,
                interface=subtitle_writer.INTERFACE,
                needs=("referencia", "sincronizacao"),
                cacheable=False,
            ),
        ),
    )
