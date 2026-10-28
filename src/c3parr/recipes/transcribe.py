"""Receita `transcricao` (modo `--transcribe-only`): áudio → transcrição → formatação → escrita."""

import wave
from pathlib import Path

from c3parr.audio import ffmpeg
from c3parr.core.models import Model, Subtitle, Transcript
from c3parr.engine.cache import file_sha256
from c3parr.engine.engine import Recipe, Step, StepContext
from c3parr.interfaces import subtitle_writer, transcription
from c3parr.interfaces.transcription import AudioRef, TranscriptionRequest
from c3parr.subtitle.format import FormatRules, cues_from_segments


class WrittenFile(Model):
    path: str


def extract_audio(ctx: StepContext) -> AudioRef:
    video = Path(ctx.params.video)
    info = ffmpeg.probe(video)
    stream, note = ffmpeg.pick_stream(info, ctx.params.src)
    dest = ctx.workdir / f"audio-{stream.index}.wav"
    ctx.progress(0.1, note)
    ffmpeg.extract_audio(video, stream.index, dest)
    with wave.open(str(dest)) as w:
        duration = w.getnframes() / w.getframerate()
    return AudioRef(
        path=str(dest),
        sha256=file_sha256(dest),
        duration=round(duration, 3),
        stream_index=stream.index,
        stream_lang=stream.lang,
        note=note,
    )


def transcribe(ctx: StepContext) -> Transcript:
    audio = ctx.require("audio", AudioRef)
    request = TranscriptionRequest(language=ctx.params.src)
    result: Transcript = ctx.component.transcribe(audio, request, ctx.progress)
    return result


def make_format(rules: FormatRules) -> Step:
    def run(ctx: StepContext) -> Subtitle:
        transcript = ctx.require("transcricao", Transcript)
        cues = cues_from_segments(transcript.segments, rules)
        return Subtitle(cues=tuple(cues), lang=transcript.language)

    return Step(
        name="formatacao",
        run=run,
        output=Subtitle,
        needs=("transcricao",),
        key_params=lambda _p: rules.model_dump(),
    )


def output_stem(video: Path, out_dir: str | None, lang: str) -> Path:
    folder = Path(out_dir) if out_dir else video.parent
    return folder / f"{video.stem}.{lang}"


def write(ctx: StepContext) -> WrittenFile:
    subtitle = ctx.require("formatacao", Subtitle)
    stem = output_stem(Path(ctx.params.video), ctx.params.out_dir, subtitle.lang or "und")
    return WrittenFile(path=str(ctx.component.write(subtitle, stem)))


def audio_step() -> Step:
    return Step(
        name="audio", run=extract_audio, output=AudioRef, key_params=lambda p: {"src": p.src}
    )


def transcription_step() -> Step:
    return Step(
        name="transcricao",
        run=transcribe,
        output=Transcript,
        interface=transcription.INTERFACE,
        needs=("audio",),
        key_params=lambda p: {"src": p.src},
    )


def recipe(rules: FormatRules | None = None) -> Recipe:
    return Recipe(
        name="transcricao",
        steps=(
            audio_step(),
            transcription_step(),
            make_format(rules or FormatRules()),
            Step(
                name="escrita",
                run=write,
                output=WrittenFile,
                interface=subtitle_writer.INTERFACE,
                needs=("formatacao",),
                cacheable=False,
            ),
        ),
    )
