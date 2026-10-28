"""Receita `completa`: do áudio à legenda traduzida.

Só vídeo, transcrição e tradução impedem o resultado completo. Sem tradução, a legenda sai no
idioma original com os tempos certos, e a CLI termina com código diferente de zero.
"""

from pathlib import Path

from c3parr.core.bible import Bible
from c3parr.core.errors import InvalidInput
from c3parr.core.job import JobParams
from c3parr.core.models import Subtitle, Transcript
from c3parr.engine.engine import Recipe, Step, StepContext
from c3parr.interfaces import (
    context,
    metadata,
    subtitle_source,
    subtitle_writer,
    sync,
    transcription,
    translation,
)
from c3parr.interfaces.context import ContextRequest
from c3parr.interfaces.metadata import MetadataRequest, MetadataResult
from c3parr.interfaces.sync import SyncResult
from c3parr.interfaces.transcription import AudioRef, TranscriptionRequest
from c3parr.interfaces.translation import Translation, TranslationRequest
from c3parr.metadata.names import NamesResult, correct_names, hotwords
from c3parr.recipes.sync import bridge_step, fetch_reference, sync_against_audio
from c3parr.recipes.transcribe import WrittenFile, audio_step, output_stem
from c3parr.store.memory import EpisodeKey, MemoryRecord, record_translation
from c3parr.subtitle.format import FormatRules, cues_from_segments, cues_from_translation
from c3parr.translate.blocks import BlockRules, build_blocks
from c3parr.translate.languages import foreign_tag


def _no_ref(p: JobParams) -> str | None:
    return None if p.ref else "no reference subtitle"


def sync_reference(ctx: StepContext) -> SyncResult:
    result = sync_against_audio(ctx)
    if not result.accepted:
        raise InvalidInput(f"reference discarded: {result.reason}")
    return result


def user_bible(ctx: StepContext) -> Bible:
    bible = Bible.model_validate_json(Path(ctx.params.bible or "").read_text(encoding="utf-8"))
    return bible.model_copy(update={"origem": tuple(dict.fromkeys((*bible.origem, "usuario")))})


def identify(ctx: StepContext) -> MetadataResult:
    p = ctx.params
    audio = ctx.require("audio", AudioRef)
    request = MetadataRequest(
        filename=Path(p.video).name,
        path=p.video,
        title=p.title,
        year=p.year,
        tmdb_id=p.tmdb_id,
        audio_duration=audio.duration,
        language=p.src,
    )
    result: MetadataResult = ctx.component.identify(request)
    return result


def _metadata_bible(ctx: StepContext) -> Bible | None:
    m = ctx.inputs.get("metadados")
    return m.bible if isinstance(m, MetadataResult) else None


def transcribe_with_hints(ctx: StepContext) -> Transcript:
    """Transcrição com os nomes do elenco como dica (hotwords), quando o TMDb achou o filme."""
    audio = ctx.require("audio", AudioRef)
    bible = _metadata_bible(ctx)
    meta = ctx.inputs.get("metadados")
    request = TranscriptionRequest(
        language=ctx.params.src,
        hotwords=hotwords(bible) if bible else (),
        language_hint=meta.original_language if isinstance(meta, MetadataResult) else None,
    )
    result: Transcript = ctx.component.transcribe(audio, request, ctx.progress)
    return result


def build_context(ctx: StepContext) -> Bible:
    transcript = ctx.require("transcricao", Transcript)
    request = ContextRequest(dst=ctx.params.dst, base=_metadata_bible(ctx))
    result: Bible = ctx.component.build(transcript, request)
    return result


def fix_names(ctx: StepContext) -> NamesResult:
    transcript = ctx.require("transcricao", Transcript)
    return correct_names(transcript, current_bible(ctx))


def current_transcript(ctx: StepContext) -> Transcript:
    """A transcrição com os nomes corrigidos, quando houver; senão a original."""
    fixed = ctx.inputs.get("nomes")
    if isinstance(fixed, NamesResult):
        return fixed.transcript
    return ctx.require("transcricao", Transcript)


def ref_texts(result: SyncResult | None, transcript: Transcript) -> dict[int, str]:
    """Texto da referência sincronizada por fala do ASR, pela sobreposição de tempo."""
    out: dict[int, str] = {}
    if result is None:
        return out
    segs = transcript.segments
    for c in result.cues:
        if c.status == "nao_fala":
            continue
        best, overlap = None, 0.0
        for s in segs:
            o = min(c.end, s.end) - max(c.start, s.start)
            if o > overlap:
                best, overlap = s.id, o
        if best is not None and overlap >= 0.3 * (c.end - c.start):
            text = " ".join(c.text.split())
            out[best] = f"{out[best]} {text}" if best in out else text
    return out


def current_bible(ctx: StepContext) -> Bible:
    for name in ("biblia", "contexto", "metadados"):
        value = ctx.inputs.get(name)
        if isinstance(value, Bible):
            return value
        if isinstance(value, MetadataResult):
            return value.bible
    return Bible()


def _sync(ctx: StepContext) -> SyncResult | None:
    s = ctx.inputs.get("sincronizacao")
    return s if isinstance(s, SyncResult) else None


def make_translate(block_rules: BlockRules) -> Step:
    def run(ctx: StepContext) -> Translation:
        transcript = current_transcript(ctx)
        bible = current_bible(ctx)
        blocks = build_blocks(
            transcript.segments, bible, ref_texts(_sync(ctx), transcript), block_rules
        )
        request = TranslationRequest(dst=ctx.params.dst, src=transcript.language)
        result: Translation = ctx.component.translate(blocks, bible, request, ctx.progress)
        missing = {"contexto", "incerteza", "genero"} - ctx.component.info.capabilities
        return result.model_copy(update={"missing_capabilities": tuple(sorted(missing))})

    return Step(
        name="traducao",
        run=run,
        output=Translation,
        interface=translation.INTERFACE,
        required=True,
        needs=("transcricao", "nomes", "sincronizacao", "biblia", "contexto", "metadados"),
        key_params=lambda p: {"dst": p.dst},
    )


def make_format(rules: FormatRules) -> Step:
    def run(ctx: StepContext) -> Subtitle:
        transcript = current_transcript(ctx)
        tr = ctx.inputs.get("traducao")
        if isinstance(tr, Translation):
            texts = {ln.id: ln.texto for ln in tr.lines}
            dst = ctx.params.dst
            cues = cues_from_translation(transcript.segments, texts, rules, transcript.language,
                                         lambda lang: foreign_tag(lang, dst))  # fmt: skip
            return Subtitle(cues=tuple(cues), lang=ctx.params.dst)
        cues = cues_from_segments(transcript.segments, rules)
        return Subtitle(cues=tuple(cues), lang=transcript.language)

    return Step(
        name="formatacao",
        run=run,
        output=Subtitle,
        needs=("transcricao", "nomes", "traducao"),
        key_params=lambda _p: rules.model_dump(),
    )


def write_main(ctx: StepContext) -> WrittenFile:
    subtitle = ctx.require("formatacao", Subtitle)
    stem = output_stem(Path(ctx.params.video), ctx.params.out_dir, subtitle.lang or "und")
    return WrittenFile(path=str(ctx.component.write(subtitle, stem)))


def make_write_original(rules: FormatRules) -> Step:
    def run(ctx: StepContext) -> WrittenFile:
        transcript = ctx.require("transcricao", Transcript)
        cues = cues_from_segments(transcript.segments, rules)
        sub = Subtitle(cues=tuple(cues), lang=transcript.language)
        stem = output_stem(Path(ctx.params.video), ctx.params.out_dir, transcript.language)
        return WrittenFile(path=str(ctx.component.write(sub, stem)))

    return Step(
        name="escrita_original",
        run=run,
        output=WrittenFile,
        interface=subtitle_writer.INTERFACE,
        required=False,
        needs=("transcricao",),
        skip_reason=lambda p: None if p.keep_transcript else "no --keep-transcript",
        cacheable=False,
    )


def remember(ctx: StepContext) -> MemoryRecord:
    tr = ctx.inputs.get("traducao")
    if not isinstance(tr, Translation):
        return MemoryRecord(skipped="no translation")
    transcript = current_transcript(ctx)
    bible = current_bible(ctx)
    video = Path(ctx.params.video)
    meta = ctx.inputs.get("metadados")
    title = bible.titulo.original or ctx.params.title or video.stem
    if isinstance(meta, MetadataResult):
        key = EpisodeKey(work=f"tmdb:{meta.kind}:{meta.tmdb_id}", title=title, kind=meta.kind,
                         season=meta.season or 0, episode=meta.episode or 0)  # fmt: skip
    else:
        key = EpisodeKey(work=title.lower(), title=title)
    db = ctx.workdir.parent / "c3parr.db"
    refs = ref_texts(_sync(ctx), transcript)
    return record_translation(db, key, str(video), transcript, tr, bible, refs)


def recipe(rules: FormatRules | None = None, block_rules: BlockRules | None = None) -> Recipe:
    rules = rules or FormatRules()
    return Recipe(
        name="completa",
        steps=(
            audio_step(),
            Step(
                name="metadados",
                run=identify,
                output=MetadataResult,
                interface=metadata.INTERFACE,
                required=False,
                needs=("audio",),
                skip_reason=lambda p: "no TMDb (--no-tmdb)" if p.no_tmdb else None,
                key_params=lambda p: {
                    "title": p.title,
                    "year": p.year,
                    "tmdb": p.tmdb_id,
                    "src": p.src,
                    "video": Path(p.video).name,
                },
                failure_effect="no cast and no name hints; the bible comes entirely from the LLM",
            ),
            Step(
                name="transcricao",
                run=transcribe_with_hints,
                output=Transcript,
                interface=transcription.INTERFACE,
                needs=("audio", "metadados"),
                key_params=lambda p: {"src": p.src},
            ),
            Step(
                name="referencia",
                run=fetch_reference,
                output=Subtitle,
                interface=subtitle_source.INTERFACE,
                required=False,
                skip_reason=_no_ref,
                key_params=lambda p: {"ref": p.ref},
                cacheable=False,
                failure_effect="continues without a reference",
            ),
            bridge_step(),
            Step(
                name="sincronizacao",
                run=sync_reference,
                output=SyncResult,
                interface=sync.INTERFACE,
                required=False,
                needs=("referencia", "ponte", "transcricao", "audio"),
                skip_reason=_no_ref,
                failure_effect="ASR timings and translation from the original only",
            ),
            Step(
                name="biblia",
                run=user_bible,
                output=Bible,
                required=False,
                skip_reason=lambda p: None if p.bible else "no --bible",
                key_params=lambda p: {"bible": p.bible},
                cacheable=False,
                failure_effect="bible file ignored",
            ),
            Step(
                name="contexto",
                run=build_context,
                output=Bible,
                interface=context.INTERFACE,
                required=False,
                needs=("transcricao", "metadados"),
                skip_reason=lambda p: "bible provided by the user" if p.bible else None,
                key_params=lambda p: {"dst": p.dst},
                failure_effect="translation without a bible: gender and formality are less certain",
            ),
            Step(
                name="nomes",
                run=fix_names,
                output=NamesResult,
                required=False,
                needs=("transcricao", "biblia", "contexto", "metadados"),
                failure_effect="names stay as the ASR wrote them",
            ),
            make_translate(block_rules or BlockRules()),
            make_format(rules),
            Step(
                name="escrita",
                run=write_main,
                output=WrittenFile,
                interface=subtitle_writer.INTERFACE,
                needs=("formatacao",),
                cacheable=False,
            ),
            make_write_original(rules),
            Step(
                name="memoria",
                run=remember,
                output=MemoryRecord,
                required=False,
                needs=(
                    "transcricao",
                    "nomes",
                    "traducao",
                    "biblia",
                    "contexto",
                    "metadados",
                    "sincronizacao",
                ),
                cacheable=False,
                failure_effect="series memory not updated",
            ),
        ),
    )
