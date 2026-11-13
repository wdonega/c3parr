"""A receita `show` de ponta a ponta com providers de mentira: da voz "isolada" à legenda."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from concertkit import song

from c3parr.concert.fake import FakePlacer, FakePlacerConfig, FakeSeparator, FakeSeparatorConfig
from c3parr.concert.fit import Fitted
from c3parr.concert.models import Assembled, SecondListen, Spoken
from c3parr.core.errors import Unavailable
from c3parr.core.job import ComponentSpec, JobParams
from c3parr.core.models import Segment, Subtitle, Transcript, Word
from c3parr.engine.engine import Engine, RunResult, StepContext
from c3parr.interfaces.separation import SeparationRequest, Voice
from c3parr.interfaces.transcription import AudioRef, TranscriptionRequest
from c3parr.providers.base import ProviderInfo
from c3parr.providers.registry import build_factories
from c3parr.recipes import show

ALFA = song("Alfa", 1, lines=8)
SUNG = ("Good evening everybody", *ALFA.lines, "Thank you all so much")
"""O que a transcrição de mentira ouve: uma frase a cada 4 s, a primeira em 1 s."""


def specs(**changes: ComponentSpec) -> dict[str, ComponentSpec]:
    base = {
        "separacao": ComponentSpec(provider="falso"),
        "transcricao": ComponentSpec(provider="falso", params={"lines": SUNG, "spacing": 4.0}),
        "letras": ComponentSpec(provider="falso", params={"songs": [ALFA.model_dump()]}),
        "encaixe": ComponentSpec(provider="falso", params={"lead": 5.0}),
        "escrita_legenda": ComponentSpec(provider="srt"),
    }
    return {**base, **changes}


def run(
    tmp_path: Path, video: Path, components: dict[str, ComponentSpec], **params: Any
) -> RunResult:
    engine = Engine(build_factories(), components, {}, tmp_path / "cache")
    job = JobParams(recipe="show", video=str(video), src="en", no_tmdb=True,
                    out_dir=str(tmp_path / "out"), **params)  # fmt: skip
    return engine.run(show.recipe(), job)


def test_the_official_lyrics_come_out_in_time_with_the_speech_around_them(
    tmp_path: Path, tiny_video: Path
) -> None:
    result = run(tmp_path, tiny_video, specs())
    assert result.ok, result.failed
    sub = result.artifacts["formatacao"]
    assert isinstance(sub, Subtitle) and sub.lang == "en"
    texts = [" ".join(c.text.split()) for c in sub.cues]
    assert texts == ["Good evening everybody", *[f"♪ {line} ♪" for line in ALFA.lines],
                     "Thank you all so much"]  # fmt: skip
    assert [round(c.start) for c in sub.cues[1:4]] == [5, 9, 13]  # onde o encaixe pôs cada linha
    assert all(len(row) <= 42 for c in sub.cues for row in c.text.split("\n"))
    done = result.artifacts["montagem"]
    assert isinstance(done, Assembled)
    assert done.counts == {"lyric": 8, "lyric-heard": 0, "heard": 2}
    (played,) = done.songs
    assert (played.title, played.accepted, played.proofs) == ("Alfa", 8, {"forte": 8})
    written = Path(tmp_path / "out" / f"{tiny_video.stem}.en.srt")
    assert "♪" in written.read_text(encoding="utf-8")


def test_a_second_run_reads_everything_from_the_cache(tmp_path: Path, tiny_video: Path) -> None:
    run(tmp_path, tiny_video, specs())
    calls = FakeSeparator.calls
    again = run(tmp_path, tiny_video, specs())
    assert FakeSeparator.calls == calls
    assert {s.name: s.status for s in again.steps}["voz"] == "cache"


def test_without_lyrics_the_subtitle_has_what_was_heard(tmp_path: Path, tiny_video: Path) -> None:
    result = run(tmp_path, tiny_video, specs(), no_lyrics=True)
    assert result.ok and {s.name: s.status for s in result.steps}["letras"] == "pulado"
    done = result.artifacts["montagem"]
    assert isinstance(done, Assembled) and done.songs == () and done.counts["lyric"] == 0


def test_lyrics_that_fail_or_cannot_be_fitted_do_not_stop_the_subtitle(
    tmp_path: Path, tiny_video: Path
) -> None:
    broken = specs(letras=ComponentSpec(provider="falso", params={"fail": True}))
    result = run(tmp_path / "a", tiny_video, broken)
    assert result.ok and [d.stage for d in result.degradations] == ["letras"]
    # sem o extra dos modelos, o encaixe não monta: a legenda sai com a letra só onde foi ouvida
    missing = specs(encaixe=ComponentSpec(provider="nao-existe"))
    result = run(tmp_path / "b", tiny_video, missing)
    assert result.ok and "encaixe" in [d.stage for d in result.degradations]
    done = result.artifacts["montagem"]
    assert isinstance(done, Assembled) and done.counts["lyric"] == 0
    assert done.counts["lyric-heard"] == 8  # cada frase ouvida que bate com uma linha da letra


# --- etapas com um Whisper roteirizado ---


class Scripted:
    """Um Whisper que responde pelo pedido: com dica devolve `hinted`; sem idioma, a "fala"
    detectada; o resto vem vazio. Guarda os pedidos para o teste olhar."""

    info = ProviderInfo(id="roteiro", interface="transcricao", version="1",
                        capabilities=frozenset({"no_vad", "prompt"}))  # fmt: skip

    def __init__(self, hinted: str = "", speech: dict[str, str] | None = None) -> None:
        self.hinted, self.speech = hinted, speech or {}
        self.requests: list[tuple[AudioRef, TranscriptionRequest]] = []

    def transcribe(self, audio: AudioRef, request: TranscriptionRequest,
                   progress: Callable[[float, str], None]) -> Transcript:  # fmt: skip
        self.requests.append((audio, request))
        if request.prompt:
            text, lang = self.hinted, request.language or "en"
        else:
            lang = request.language or "ja"
            text = self.speech.get(lang, "")
        words = tuple(Word(w=w, start=0.2 + 0.3 * k, end=0.45 + 0.3 * k, conf=0.9)
                      for k, w in enumerate(text.split()))  # fmt: skip
        segs = (Segment(id=0, start=0.2, end=words[-1].end, text=text, lang=lang, words=words),
                ) if words else ()  # fmt: skip
        return Transcript(segments=segs, language=lang, model="roteiro",
                          audio_duration=audio.duration)  # fmt: skip


def context(tmp_path: Path, component: Any, **inputs: Any) -> StepContext:
    params = JobParams(recipe="show", video="show.mkv", src="en")
    return StepContext(step="x", params=params, inputs=inputs, component=component,
                       workdir=tmp_path, progress=lambda *_: None, cancelled=lambda: False)  # fmt: skip


def voice(tmp_path: Path, spans: tuple[tuple[float, float], ...] = ()) -> Voice:
    request = SeparationRequest(video="show.mkv", dest=str(tmp_path / "voz.wav"))
    config = FakeSeparatorConfig(seconds=200.0, spans=spans)
    return FakeSeparator(config).separate(request, lambda *_: None)


def heard(*lines: tuple[float, str]) -> Transcript:
    segs = tuple(
        Segment(id=n, start=at, end=at + 3.0, text=text, lang="en", words=tuple(
            Word(w=w, start=at + 0.3 * k, end=at + 0.3 * k + 0.25, conf=0.9)
            for k, w in enumerate(text.split())))
        for n, (at, text) in enumerate(lines)
    )  # fmt: skip
    return Transcript(segments=segs, language="en", model="x", audio_duration=200.0)


def fitted_song(tmp_path: Path, transcript: Transcript, voz: Voice) -> Fitted:
    from c3parr.concert.catalogue import Catalogue

    cat = Catalogue(artists=("Banda Teste",), songs=(ALFA,))
    played = show.set_list(context(tmp_path, None, transcricao=transcript, letras=cat))
    placer = FakePlacer(FakePlacerConfig(lead=10.0))
    return show.fit_lines(
        context(tmp_path, placer, voz=voz, transcricao=transcript, musicas=played)
    )


def test_a_chorus_sung_again_is_heard_with_the_lyrics_as_a_hint_and_placed_with_proof(
    tmp_path: Path,
) -> None:
    sung = [(10.0 + 4 * n, line) for n, line in enumerate(ALFA.lines)]
    # algo é cantado de novo aos 60 s, e há uma despedida no fim
    transcript = heard(*sung, (60.0, "zzz zzz zzz zzz zzz"), (90.0, "See you next time friends"))
    voz = voice(tmp_path)
    fitted = fitted_song(tmp_path, transcript, voz)
    assert [f.accepted for f in fitted.songs[0].fits] == [True] * 8

    whisper = Scripted(hinted=ALFA.lines[7])  # com a dica, o Whisper ouve a última linha ali
    second = show.hear_again(context(tmp_path, whisper, voz=voz, transcricao=transcript,
                                     encaixe=fitted))  # fmt: skip
    assert isinstance(second, SecondListen) and len(second.asked) == 1
    ((clip, request),) = whisper.requests
    assert request.vad is False and ALFA.lines[7] in (request.prompt or "")
    assert clip.duration == pytest.approx(3.45, abs=0.05)  # só o trecho, com 1 s de folga

    placer = FakePlacer(FakePlacerConfig(score=0.4))  # o som confirma (linha de 5+ palavras)
    more = show.fit_again(context(tmp_path, placer, voz=voz, encaixe=fitted, segunda_escuta=second))
    new = more.songs[0].fits[8:]
    assert [(f.line, f.why, round(f.start)) for f in new] == [(7, "som", 59)]
    # e a segunda escuta fica guardada: de novo, não pergunta ao Whisper
    show.hear_again(context(tmp_path, Scripted(), voz=voz, transcricao=transcript, encaixe=fitted))
    assert (tmp_path / show.MEMO).exists()

    done = show.assemble(context(tmp_path, None, transcricao=transcript, letras=None,
                                 repetidas=more, segunda_escuta=second))  # fmt: skip
    assert done.counts == {"lyric": 9, "lyric-heard": 0, "heard": 1}
    assert done.dropped_unstable == 0  # "zzz…" ficou coberta pela linha oficial


def test_speech_outside_the_songs_is_heard_where_there_is_voice_in_its_own_language(
    tmp_path: Path,
) -> None:
    sung = [(10.0 + 4 * n, line) for n, line in enumerate(ALFA.lines)]
    transcript = heard(*sung)
    voz = voice(tmp_path, spans=((10.0, 41.0), (100.0, 110.0), (112.0, 113.5), (150.0, 151.0)))
    fitted = fitted_song(tmp_path, transcript, voz)
    whisper = Scripted(speech={"ja": "konbanwa tokyo", "en": "hello"})
    spoken = show.hear_speech(context(tmp_path, whisper, voz=voz, transcricao=transcript,
                                      repetidas=fitted))  # fmt: skip
    assert isinstance(spoken, Spoken)
    # o trecho longo (100 a 110 s) tem o idioma detectado nele; o curto ao lado segue o vizinho, e o
    # curto longe de todos fica no idioma das letras
    assert [(round(p[0]), p[2]) for p in spoken.phrases] == [
        (100, "konbanwa tokyo"), (112, "konbanwa tokyo"), (150, "hello")]  # fmt: skip
    assert spoken.languages == {"ja": 2, "en": 1}
    assert [r.language for _, r in whisper.requests] == [None, "ja", "en"]
    assert all(r.vad is False for _, r in whisper.requests)


def test_an_engine_that_cannot_hear_singing_says_so(tmp_path: Path) -> None:
    deaf = Scripted()
    deaf.info = ProviderInfo(id="surdo", interface="transcricao", version="1")  # type: ignore[misc]
    with pytest.raises(Unavailable, match="cannot hear a concert"):
        show.hear(context(tmp_path, deaf, voz=voice(tmp_path)))


def test_lyrics_in_a_language_the_fitter_does_not_know_are_not_fitted(tmp_path: Path) -> None:
    sung = [(10.0 + 4 * n, line) for n, line in enumerate(ALFA.lines)]
    transcript = heard(*sung).model_copy(update={"language": "pt"})
    voz = voice(tmp_path)
    fitted = fitted_song(tmp_path, transcript, voz)
    assert [s.title for s in fitted.songs] == ["Alfa"] and fitted.songs[0].fits == ()
