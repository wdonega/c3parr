"""Receitas usadas pelos passos da pipeline de legendas.

- `asr`: áudio → transcrição (com o idioma original do TMDb como palpite), salva em `.asr.c3parr`.
- `legenda`: tradução direta da legenda principal, com os tempos dela e sem ASR. Reaproveita
  contexto, correção de nomes, tradução e formatação da receita completa; a legenda principal
  entra no lugar da transcrição.
"""

from pathlib import Path

from c3parr.core.bible import Bible
from c3parr.core.errors import InvalidInput
from c3parr.core.models import Segment, Subtitle, Transcript
from c3parr.engine.cache import file_sha256
from c3parr.engine.engine import Recipe, Step, StepContext
from c3parr.interfaces import context, subtitle_writer, transcription
from c3parr.interfaces.metadata import MetadataResult
from c3parr.metadata.names import NamesResult
from c3parr.pipeline.naming import output_file, work_file
from c3parr.recipes.full import (
    build_context,
    fix_names,
    make_format,
    make_translate,
    transcribe_with_hints,
)
from c3parr.recipes.transcribe import WrittenFile, audio_step
from c3parr.subtitle.format import FormatRules
from c3parr.subtitle.srt import read_srt
from c3parr.translate.blocks import BlockRules


def load_metadata(ctx: StepContext) -> MetadataResult:
    path = work_file(Path(ctx.params.video), "meta")
    if not path.exists():
        raise InvalidInput("no metadata for this item (TMDb did not find it)")
    return MetadataResult.model_validate_json(path.read_text(encoding="utf-8"))


def metadata_step() -> Step:
    return Step(
        name="metadados",
        run=load_metadata,
        output=MetadataResult,
        required=False,
        cacheable=False,
        failure_effect="no cast: the bible comes only from the LLM",
    )


def main_as_transcript(ctx: StepContext) -> Transcript:
    """A legenda principal como transcrição: cada fala vira um segmento com o tempo dela."""
    p = ctx.params
    sub = read_srt(Path(p.ref or ""), p.src)
    segments = tuple(
        Segment(id=i, start=c.start, end=c.end, text=" ".join(c.text.split()), lang=p.src or "und")
        for i, c in enumerate(sub.cues)
        if c.text.strip()
    )
    if not segments:
        raise InvalidInput(f"main subtitle {Path(p.ref or '').name} has no lines")
    return Transcript(segments=segments, language=p.src or "und", model="legenda",
                      audio_duration=segments[-1].end)  # fmt: skip


def write_ours(ctx: StepContext) -> WrittenFile:
    sub = ctx.require("formatacao", Subtitle)
    dest = output_file(Path(ctx.params.video), ctx.params.dst)
    stem = dest.with_name(dest.name.removesuffix(".srt"))  # o escritor põe a extensão
    return WrittenFile(path=str(ctx.component.write(sub, stem)))


def asr_recipe() -> Recipe:
    return Recipe(name="asr", steps=(
        audio_step(),
        metadata_step(),
        Step(name="transcricao", run=transcribe_with_hints, output=Transcript,
             interface=transcription.INTERFACE, needs=("audio", "metadados"),
             key_params=lambda p: {"src": p.src}),
    ))  # fmt: skip


def subtitle_recipe(rules: FormatRules | None = None,
                    block_rules: BlockRules | None = None) -> Recipe:  # fmt: skip
    rules = rules or FormatRules()
    return Recipe(name="legenda", steps=(
        metadata_step(),
        Step(name="transcricao", run=main_as_transcript, output=Transcript,
             key_params=lambda p: {"ref": p.ref, "sha": file_sha256(Path(p.ref or "")),
                                   "src": p.src}),
        Step(name="contexto", run=build_context, output=Bible, interface=context.INTERFACE,
             required=False, needs=("transcricao", "metadados"),
             key_params=lambda p: {"dst": p.dst},
             failure_effect="translation without a bible: gender and formality are less certain"),
        Step(name="nomes", run=fix_names, output=NamesResult, required=False,
             needs=("transcricao", "contexto", "metadados"),
             failure_effect="names stay as they are in the main subtitle"),
        make_translate(block_rules or BlockRules()),
        make_format(rules),
        Step(name="escrita", run=write_ours, output=WrittenFile,
             interface=subtitle_writer.INTERFACE, needs=("formatacao",), cacheable=False),
    ))  # fmt: skip
