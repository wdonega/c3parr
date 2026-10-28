"""Estruturas de dados do núcleo: simples, imutáveis e serializáveis em JSON."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


SegmentFlag = Literal["baixa_confianca", "repeticao", "fala_rapida", "alucinacao_conhecida"]
GenderSource = Literal["voz", "tmdb", "llm", "usuario"]


class Word(Model):
    w: str
    start: float
    end: float
    conf: float | None = None


class GenderGuess(Model):
    value: Literal["F", "M"]
    conf: float
    source: GenderSource


class Segment(Model):
    id: int
    start: float
    end: float
    text: str
    lang: str
    words: tuple[Word, ...] = ()
    speaker: str | None = None
    speaker_gender: GenderGuess | None = None
    flags: frozenset[SegmentFlag] = frozenset()


class LanguageSpan(Model):
    """Trecho de fala com o idioma detectado nele."""

    start: float
    end: float
    lang: str
    prob: float


class Transcript(Model):
    segments: tuple[Segment, ...]
    language: str
    language_prob: float | None = None
    language_spans: tuple[LanguageSpan, ...] = ()
    model: str
    audio_duration: float


class Cue(Model):
    index: int
    start: float
    end: float
    text: str


class Subtitle(Model):
    cues: tuple[Cue, ...]
    lang: str | None = None


class Degradation(Model):
    """Estágio opcional que foi pulado ou falhou, em linguagem para o usuário."""

    stage: str
    reason: str
    effect: str = ""
