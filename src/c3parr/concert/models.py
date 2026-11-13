"""O que as etapas da receita `show` passam umas às outras (e o que vai para o relatório)."""

from typing import Literal

from pydantic import Field

from c3parr.core.models import Model


class Asked(Model):
    """Um trecho de uma música ouvido de novo, com a letra como dica."""

    song: int
    """Índice da música em `Fitted.songs`."""
    start: float
    end: float
    among: tuple[int, ...]
    """As linhas da letra que foram na dica."""
    first: str
    """O que a primeira escuta (sem dica) ouviu ali."""
    text: str
    """O que a segunda escuta ouviu."""


class SecondListen(Model):
    asked: tuple[Asked, ...] = ()


class Spoken(Model):
    """A fala fora das músicas: frases (início, fim, texto, confiança) e quantas por idioma."""

    phrases: tuple[tuple[float, float, str, float], ...] = ()
    languages: dict[str, int] = Field(default_factory=dict)


class Line(Model):
    start: float
    end: float
    text: str
    kind: Literal["lyric", "lyric-heard", "heard"]


class SongReport(Model):
    title: str
    artist: str = ""
    start: float
    end: float
    lines: int
    accepted: int
    proofs: dict[str, int] = Field(default_factory=dict)
    """Quantas linhas entraram por cada prova (`forte`, `ouvida`, `vizinhas`, `disco`, `som`…)."""
    studio_pairs: int = 0
    studio_consistent: int = 0


class Assembled(Model):
    """A legenda montada, linha a linha, e os números dela."""

    rows: tuple[Line, ...] = ()
    counts: dict[str, int] = Field(default_factory=dict)
    songs: tuple[SongReport, ...] = ()
    languages: dict[str, int] = Field(default_factory=dict)
    dropped_unstable: int = 0
    """Frases ouvidas que saíram porque as duas escutas discordam."""
    language: str = "und"
