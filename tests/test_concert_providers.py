"""Legenda de show, os providers: o que o Whisper recebe a mais, de onde vêm as letras (com um
MusicBrainz e um LRCLIB de mentira), o encaixe num som inventado e os providers de teste."""

import json
import math
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from concertkit import song

from c3parr.asr.faster_whisper import FasterWhisperConfig
from c3parr.asr.remote import RemoteASRConfig, RemoteTranscriber
from c3parr.asr.whisper_cpp import WhisperCppConfig, listen_flags, parse_output
from c3parr.concert import demucs, device, lrclib, memory
from c3parr.concert.fake import FakePlacer, FakePlacerConfig, FakeSeparator, FakeSeparatorConfig
from c3parr.concert.words import norm
from c3parr.core.errors import Unavailable
from c3parr.interfaces.lyric_fit import PlaceRequest, Span
from c3parr.interfaces.lyrics import LyricsRequest
from c3parr.interfaces.separation import SeparationRequest
from c3parr.interfaces.transcription import AudioRef, TranscriptionRequest
from c3parr.providers.registry import build_factories

# --- o Whisper sem detector de voz e com dica ---


def test_singing_is_heard_without_the_voice_detector_and_a_hint_turns_the_context_on() -> None:
    c, vad = WhisperCppConfig(), Path("/m/vad.bin")
    usual = listen_flags(TranscriptionRequest(), c, vad)
    assert usual[:2] == ["-mc", "0"] and "--vad" in usual
    assert listen_flags(TranscriptionRequest(vad=False), c, vad) == ["-mc", "0"]
    hinted = listen_flags(TranscriptionRequest(vad=False, prompt="amber river"), c, vad)
    assert hinted == ["--prompt", "amber river"]  # com `-mc 0` a dica seria ignorada


def test_without_the_voice_detector_the_word_times_are_the_ones_whisper_gave() -> None:
    data = {"transcription": [{
        "offsets": {"from": 130000, "to": 134000}, "text": " Amber river",
        "tokens": [{"text": " Amber", "offsets": {"from": 131500, "to": 132000}, "p": 0.9},
                   {"text": " river", "offsets": {"from": 132000, "to": 132600}, "p": 0.8}]}]}  # fmt: skip
    (seg,) = parse_output(data, "en", FasterWhisperConfig(), vad=False)
    assert [(w.w, w.start, w.conf) for w in seg.words] == [("Amber", 131.5, 0.9),
                                                           ("river", 132.0, 0.8)]  # fmt: skip
    (moved,) = parse_output(data, "en", FasterWhisperConfig())  # com detector: vão para o começo
    assert moved.words[0].start == 130.0


def remote(
    flavor: Any, seen: list[httpx.Request], tmp_path: Path
) -> tuple[RemoteTranscriber, AudioRef]:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"language": "en", "segments": []})

    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF fake")
    config = RemoteASRConfig(base_url="http://whisper:9000/")
    server = RemoteTranscriber(config, flavor, transport=httpx.MockTransport(handler))
    return server, AudioRef(path=str(wav), sha256="x", duration=20)


def test_the_separate_server_gets_the_same_two_options_in_each_request(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    request = TranscriptionRequest(language="en", vad=False, prompt="amber river cold")
    server, audio = remote("openai", seen, tmp_path)
    server.transcribe(audio, request, lambda *_: None)
    body = seen[0].content.decode(errors="replace")
    for name, value in (("vad", "false"), ("prompt", "amber river cold"), ("max_context", "-1")):
        assert re.search(rf'name="{name}"\r\n\r\n{re.escape(value)}\r\n', body), name
    server.transcribe(audio, TranscriptionRequest(language="en"), lambda *_: None)
    assert 'name="vad"' not in seen[1].content.decode(errors="replace")  # o pedido de sempre

    server, audio = remote("asr-webservice", seen, tmp_path)
    server.transcribe(audio, request, lambda *_: None)
    params = seen[2].url.params
    assert (params["vad_filter"], params["initial_prompt"]) == ("false", "amber river cold")


def test_the_engines_that_can_hear_a_concert_say_so() -> None:
    engines = {p.id: p.capabilities for p in build_factories()["transcricao"].providers()}
    for name in ("whisper-cpp", "remote-openai", "asr-webservice"):
        assert {"no_vad", "prompt"} <= engines[name]
    assert not {"no_vad", "prompt"} & engines["faster-whisper"]


# --- as letras: MusicBrainz e LRCLIB de mentira ---

ALFA, BETA, VOZES = song("Alfa", 1), song("Beta", 2), song("Vozes", 3)
ALBUMS = {
    "id-Banda Teste": [("g1", "Primeiro", "1999", [], ["Alfa", "Beta"]),
                       ("g2", "Ao Vivo", "2000", ["Live"], ["Alfa"]),
                       ("g3", "Depois do Show", "2005", [], ["Gama"]),
                       ("g4", "Suíte", "2000", [], ["Mente: I. Solo / II. Vozes / III. Beta"])],
}  # fmt: skip
LYRICS = {"alfa": ALFA, "beta": BETA, "vozes": VOZES}


class World:
    """Responde como os dois serviços, e conta as perguntas."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, url: str, params: dict[str, Any]) -> Any:
        self.asked.append((url, params))
        if url.endswith("/artist"):
            name = re.search(r'artist:"(.+)"', params["query"]).group(1)  # type: ignore[union-attr]
            known = name in ("Banda Teste", "Outra Banda")
            return {"artists": [{"name": name, "id": f"id-{name}", "score": 100}] if known else []}
        if url.endswith("/release-group") and "artist" in params:
            groups = [{"id": g, "title": title, "first-release-date": year, "secondary-types": kinds}
                      for g, title, year, kinds, _ in ALBUMS.get(params["artist"], [])]  # fmt: skip
            return {"release-groups": groups, "release-group-count": len(groups)}
        if url.endswith("/release-group"):
            title = re.search(r'releasegroup:"(.+)"', params["query"]).group(1)  # type: ignore[union-attr]
            if title != "Noite Sem Nome":
                return {"release-groups": []}
            return {"release-groups": [{
                "title": title, "first-release-date": "2001", "secondary-types": ["Live"],
                "score": 100, "artist-credit": [{"artist": {"name": "Outra Banda", "id": "id-o"}}]}]}  # fmt: skip
        if url.endswith("/release"):
            titles = next(t for albums in ALBUMS.values() for g, *_, t in albums
                          if g == params["release-group"])  # fmt: skip
            tracks = [{"title": t, "length": 240000} for t in titles]
            return {"releases": [{"date": "1999", "media": [{"track-count": len(tracks),
                                                             "tracks": tracks}]}]}  # fmt: skip
        found = LYRICS.get(norm(params["track_name"]))
        if found is None:
            return []
        synced = "\n".join(f"[00:{10 + 4 * n:02d}.00] {line}" for n, line in enumerate(found.lines))
        return [{"trackName": found.title, "artistName": params["artist_name"], "duration": 240,
                 "albumName": "Primeiro", "plainLyrics": "[Verse]\n" + "\n".join(found.lines),
                 "syncedLyrics": synced}]  # fmt: skip


def catalogue(world: World, **request: Any) -> Any:
    provider = lrclib.LrclibLyrics(lrclib.LrclibConfig(), fetch=world)
    return provider.catalogue(LyricsRequest(**request), lambda *_: None)


def test_the_artist_comes_from_the_title_and_the_catalogue_stops_at_the_year_of_the_concert() -> (
    None
):
    got = catalogue(World(), titles=("Banda Teste: Ao Vivo em Lugar Nenhum",), year=2001)
    assert got.artists == ("Banda Teste",) and got.until == 2001
    by_title = {s.title: s for s in got.songs}
    assert by_title["Alfa"].lines == ALFA.lines  # a linha "[Verse]" não é letra
    assert by_title["Alfa"].synced[0] == (10.0, ALFA.lines[0])
    assert "Gama" not in by_title  # o disco de 2005 saiu depois do show
    assert [s.album for s in got.songs if s.title == "Alfa"] == ["Primeiro"]  # o ao vivo não entra


def test_a_suite_in_one_track_gets_the_lyrics_of_the_parts_nobody_else_has() -> None:
    got = catalogue(World(), titles=("Banda Teste - Show",), year=2001)
    titles = [s.title for s in got.songs if s.lines]
    assert titles == [
        "Alfa",
        "Beta",
        "Vozes",
    ]  # "Beta" já tinha letra: não entra de novo; "Solo" não tem


def test_two_artists_in_the_title_and_an_artist_given_by_hand() -> None:
    got = catalogue(World(), titles=("Banda Teste & Outra Banda - Juntos",), year=2001)
    assert got.artists == ("Banda Teste", "Outra Banda")
    assert catalogue(World(), titles=("Sem Nada",), artist="Outra Banda").artists == (
        "Outra Banda",
    )


def test_a_title_without_the_artist_is_looked_up_as_a_live_album() -> None:
    assert catalogue(World(), titles=("Noite Sem Nome",), year=2001).artists == ("Outra Banda",)


def test_nobody_found_gives_an_empty_catalogue_and_no_lyrics_request() -> None:
    world = World()
    got = catalogue(world, titles=("Filme Qualquer",), year=2001)
    assert got.songs == () and not any("lrclib" in url for url, _ in world.asked)


def test_each_answer_is_kept_on_disk_and_asked_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def get(url: str, **kw: Any) -> httpx.Response:
        calls.append(kw["headers"]["User-Agent"])
        return httpx.Response(200, json={"artists": []}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(lrclib, "PAUSE", {})
    http = lrclib.Http(lrclib.LrclibConfig(cache_dir=tmp_path))
    for _ in range(2):
        assert http("https://musicbrainz.org/ws/2/artist", {"query": "x"}) == {"artists": []}
    assert calls == ["lyrics-subtitles/1.0"]  # nada de quem roda vai no pedido


# --- o encaixe num som inventado ---


def sound(spoken: list[tuple[float, str]], total_s: float, per_s: float = 50.0) -> Any:
    """O que o modelo de fonemas "ouviria": cada texto soletrado a partir do seu horário, uma
    letra a cada 60 ms; no resto do tempo, nada. A última coluna é a "qualquer som"."""
    torch = pytest.importorskip("torch")
    from c3parr.concert import wav2vec2

    table = wav2vec2.tokens()
    out = torch.full((int(total_s * per_s), len(table) + 1), math.log(0.001))
    out[:, 0] = math.log(0.9)  # nada sendo dito
    for start, text in spoken:
        at = int(start * per_s)
        for letter in "|".join(norm(text).upper().split()):
            out[at : at + 2] = math.log(0.001)
            out[at : at + 2, table[letter]] = math.log(0.9)
            at += 3
    out[:, -1] = 0.0
    return out


def test_each_line_lands_where_it_is_sung_and_a_line_nobody_sang_scores_low() -> None:
    heard = sound([(4.0, "amber river cold"), (9.0, "silver engine morning")], total_s=20)
    from c3parr.concert import wav2vec2

    lines = ["amber river cold", "quiet thunder garden", "silver engine morning"]
    got = wav2vec2.place(heard, 50.0, 100.0, lines)
    assert [round(p.start) for p in got] == [104, pytest.approx(107, abs=3), 109]
    assert got[0].score > 0.8 and got[2].score > 0.8 and got[1].score < 0.1
    assert got[0].steady > 0.8 and got[1].steady < 0.1


# --- quanta memória o show pode usar ---


def test_the_pieces_follow_the_ceiling_and_what_is_free_with_a_margin() -> None:
    big, mid, small = memory.TIERS
    assert (small.piece_s, mid.piece_s, big.piece_s) == (60.0, 300.0, 900.0)
    assert memory.plan("auto", 16.0) == big
    assert memory.plan("auto", 4.0) == mid  # 4 GB livres, menos a folga: não cabem os 4,5
    assert memory.plan("auto", 2.6) == small
    assert memory.plan("3", 16.0) == mid and memory.plan("2", 16.0) == small  # o teto manda
    assert memory.plan("3", 2.6) == small  # e, abaixo dele, o que está livre
    assert memory.plan("auto", None) == big  # onde o sistema não diz quanto há livre
    assert memory.plan("2", None) == small


def test_a_little_under_two_gb_still_runs_and_much_less_stops_with_the_reason() -> None:
    assert memory.plan("auto", 1.6) == memory.TIERS[-1]  # um pouco de troca: devagar, mas vai
    with pytest.raises(Unavailable, match=r"about 2 GB of free memory.*0\.8 GB available"):
        memory.plan("auto", 0.8, "demucs")
    with pytest.raises(Unavailable):
        memory.plan("2", 1.4)


def test_the_separation_checks_the_memory_before_loading_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = SeparationRequest(video=str(tmp_path / "show.mkv"), dest=str(tmp_path / "voz.wav"))
    monkeypatch.setattr(memory, "free_gb", lambda: 0.8)
    with pytest.raises(Unavailable, match=r"0\.8 GB available"):
        demucs.DemucsSeparator(demucs.DemucsConfig()).separate(request, lambda *_: None)


# --- os providers de teste e a velocidade medida ---


def test_the_fake_separator_writes_a_voice_file_and_the_fake_placer_queues_the_lines(
    tmp_path: Path,
) -> None:
    config = FakeSeparatorConfig(seconds=3.0, spans=((0.5, 2.5),))
    voice = FakeSeparator(config).separate(
        SeparationRequest(video="x.mkv", dest=str(tmp_path / "voz.wav")), lambda *_: None
    )
    assert (voice.duration, voice.spans, voice.missing_files()) == (3.0, ((0.5, 2.5),), [])
    spans = (
        Span(start=10.0, end=30.0, lines=("a", "b")),
        Span(start=50.0, end=53.5, lines=("c", "d")),
    )
    request = PlaceRequest(window=(0.0, 60.0), spans=spans, cache_dir=str(tmp_path))
    first, second = FakePlacer(FakePlacerConfig()).place(voice.audio(), request, lambda *_: None)
    assert [(p.line, p.start, p.end) for p in first] == [(0, 10.0, 13.0), (1, 14.0, 17.0)]
    assert [p.line for p in second] == [0]  # a segunda linha não cabe no trecho


def test_the_speed_measured_here_is_kept_by_step(tmp_path: Path) -> None:
    device.record_speed(tmp_path, "separation", "cuda", 100.0, 3600.0)
    device.record_speed(tmp_path, "fit", "cpu", 240.0, 3600.0)
    device.record_speed(tmp_path, "fit", "cpu", 1.0, 10.0)  # curto demais para medir
    got = json.loads((tmp_path / device.SPEED_FILE).read_text())
    assert (got["separation"]["device"], got["separation"]["seconds_per_minute"]) == ("cuda", 1.67)
    assert got["fit"]["seconds_per_minute"] == 4.0
    assert device.measured_speed(tmp_path / "nada") == {}
