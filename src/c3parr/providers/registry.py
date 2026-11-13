"""Liga cada provider do repositório à fábrica da sua interface."""

from c3parr.align import auto, embed, fuzzy, timing
from c3parr.asr import fake as asr_fake
from c3parr.asr import faster_whisper, remote, whisper_cpp
from c3parr.concert import demucs, lrclib, wav2vec2
from c3parr.concert import fake as concert_fake
from c3parr.interfaces import (
    context,
    lyric_fit,
    lyrics,
    metadata,
    separation,
    subtitle_source,
    subtitle_writer,
    sync,
    transcription,
    translation,
)
from c3parr.metadata import tmdb
from c3parr.providers.factory import AbstractComponentFactoryFactory
from c3parr.subtitle import file_source, srt_writer
from c3parr.translate import fake as translate_fake
from c3parr.translate import llm_context, llm_translator

REGISTRATIONS = (
    faster_whisper.register,
    whisper_cpp.register,
    remote.register,
    asr_fake.register,
    srt_writer.register,
    file_source.register,
    fuzzy.register,
    embed.register,
    timing.register,
    auto.register,
    tmdb.register,
    llm_context.register,
    llm_translator.register,
    translate_fake.register,
    demucs.register,
    wav2vec2.register,
    lrclib.register,
    concert_fake.register,
)


def build_factories() -> AbstractComponentFactoryFactory:
    for register in REGISTRATIONS:
        register()
    return AbstractComponentFactoryFactory(
        {
            f.interface: f
            for f in (
                transcription.FACTORY,
                subtitle_writer.FACTORY,
                subtitle_source.FACTORY,
                sync.FACTORY,
                context.FACTORY,
                metadata.FACTORY,
                translation.FACTORY,
                separation.FACTORY,
                lyric_fit.FACTORY,
                lyrics.FACTORY,
            )
        }
    )
