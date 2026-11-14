"""Providers `falso` de separação, encaixe e letras: sem modelo nem rede, para testes."""

import wave
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from c3parr.concert.catalogue import Catalogue, Song
from c3parr.core.errors import Unavailable
from c3parr.engine.cache import file_sha256
from c3parr.interfaces import lyric_fit, lyrics, separation
from c3parr.interfaces.lyric_fit import Placed, PlaceRequest
from c3parr.interfaces.lyrics import LyricsRequest
from c3parr.interfaces.separation import SeparationRequest, Voice
from c3parr.interfaces.transcription import AudioRef
from c3parr.providers.base import Health, ProviderInfo

RATE = 16000


class FakeSeparatorConfig(BaseModel):
    seconds: float = 120.0
    spans: tuple[tuple[float, float], ...] = ((0.0, 120.0),)
    fail: bool = False
    isolate: bool = False
    """Só para o processo filho aceitar o mesmo pedido que os providers de verdade."""


SEPARATOR = ProviderInfo(
    id="falso",
    interface=separation.INTERFACE,
    version="1",
    config_schema=FakeSeparatorConfig,
    description="Silêncio com trechos de voz marcados, para testes",
)


class FakeSeparator:
    info = SEPARATOR
    calls = 0

    def __init__(self, config: FakeSeparatorConfig) -> None:
        self.config = config

    def health(self) -> Health:
        return Health(ok=True)

    def separate(self, request: SeparationRequest, progress: Callable[[float, str], None]) -> Voice:
        FakeSeparator.calls += 1
        if self.config.fail:
            raise Unavailable("fake separation set to fail", provider=SEPARATOR.id)
        dest = Path(request.dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(dest), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(b"\0\0" * int(self.config.seconds * RATE))
        progress(1.0, "pronto")
        return Voice(path=str(dest), sha256=file_sha256(dest), duration=self.config.seconds,
                     spans=self.config.spans)  # fmt: skip


class FakePlacerConfig(BaseModel):
    score: float = 0.9
    """A nota de toda linha encaixada."""
    per_line: float = 4.0
    """Cada linha ocupa 3/4 deste tempo, uma atrás da outra a partir do começo do trecho."""
    lead: float = 0.0
    """Silêncio antes da primeira linha de cada trecho."""
    isolate: bool = False


PLACER = ProviderInfo(id="falso", interface=lyric_fit.INTERFACE, version="1",
                      config_schema=FakePlacerConfig, capabilities=frozenset({"en"}),
                      description="Linhas em fila no trecho, para testes")  # fmt: skip


class FakePlacer:
    info = PLACER

    def __init__(self, config: FakePlacerConfig) -> None:
        self.config = config

    def health(self) -> Health:
        return Health(ok=True)

    def place(
        self, audio: AudioRef, request: PlaceRequest, progress: Callable[[float, str], None]
    ) -> tuple[tuple[Placed, ...], ...]:
        c = self.config
        out = []
        for span in request.spans:
            placed = []
            for n in range(len(span.lines)):
                start = span.start + c.lead + n * c.per_line
                if start + 0.75 * c.per_line > span.end:
                    break
                placed.append(Placed(line=n, start=start, end=start + 0.75 * c.per_line,
                                     score=c.score, steady=c.score))  # fmt: skip
            out.append(tuple(placed))
        return tuple(out)


class FakeLyricsConfig(BaseModel):
    songs: tuple[Song, ...] = ()
    fail: bool = False


LYRICS = ProviderInfo(
    id="falso",
    interface=lyrics.INTERFACE,
    version="1",
    config_schema=FakeLyricsConfig,
    description="Catálogo fixo, para testes",
)


class FakeLyrics:
    info = LYRICS

    def __init__(self, config: FakeLyricsConfig) -> None:
        self.config = config

    def health(self) -> Health:
        return Health(ok=True)

    def catalogue(
        self, request: LyricsRequest, progress: Callable[[float, str], None]
    ) -> Catalogue:
        if self.config.fail:
            raise Unavailable("fake lyrics set to fail", provider=LYRICS.id)
        artists = tuple(dict.fromkeys(s.artist for s in self.config.songs))
        return Catalogue(artists=artists, until=request.year, songs=self.config.songs)


def register() -> None:
    separation.FACTORY.register(SEPARATOR, FakeSeparator)
    lyric_fit.FACTORY.register(PLACER, FakePlacer)
    lyrics.FACTORY.register(LYRICS, FakeLyrics)
