"""Legenda de show na biblioteca: o que conta como show, o que o decisor pede, a pipeline com
providers de mentira."""

from pathlib import Path
from typing import Any

import pytest
from concertkit import song
from test_pipeline import Env, place

from c3parr.concert import detect
from c3parr.core.bible import Bible, Title
from c3parr.core.job import ComponentSpec
from c3parr.interfaces.metadata import MetadataResult
from c3parr.library.store import AppConfig
from c3parr.pipeline.decide import Decision, ItemState, MainSub, OurOutput, decide
from c3parr.pipeline.naming import output_file, work_file
from c3parr.pipeline.reconcile import concert_state, reconcile

# --- é um show? ---

MUSIC, DOC, DRAMA = detect.MUSIC, detect.DOCUMENTARY, 18


@pytest.mark.parametrize(
    ("genres", "keywords", "title", "overview", "answer"),
    [
        ([MUSIC, DRAMA], ["concert"], "Live Aid: The Movie", "", "no"),  # tem enredo
        ([MUSIC], [], "Banda X: Ao Vivo em Lisboa", "", "yes"),
        ([MUSIC, DOC], ["concert film"], "Banda X", "", "yes"),
        ([MUSIC, DOC], [], "Banda X: Some Name", "", "maybe"),
        ([], [], "Live Free or Die Hard", "", "maybe"),  # sem gênero não se sabe se há enredo
        ([MUSIC, DOC], ["music documentary", "biography"], "Banda X: The Tour",
         "A documentary about the life of the band, with interviews.", "no"),
        ([MUSIC, detect.TV_MOVIE], ["live"], "Banda X", "Filmed at the arena, 21 songs.", "yes"),
    ],
)  # fmt: skip
def test_only_what_is_surely_a_concert_is_called_one(
    genres: list[int], keywords: list[str], title: str, overview: str, answer: str
) -> None:
    assert detect.from_metadata(genres, keywords, title, overview).answer == answer


def test_a_concert_folder_is_matched_by_folder_and_not_by_the_start_of_the_name() -> None:
    folders = ["/data/media/musicvideos/", " /mnt/shows"]
    assert detect.under("/data/media/musicvideos/Banda (2004)/show.mkv", folders)
    assert detect.under("/mnt/shows/a.mkv", folders)
    assert not detect.under("/data/media/musicvideos2/Banda/show.mkv", folders)
    assert not detect.under("/data/media/movies/Filme (2010)/Filme.mkv", folders)
    assert not detect.under("/data/media/movies/x.mkv", [" ", ""])


def meta(video: Path, genres: tuple[int, ...], title: str) -> None:
    result = MetadataResult(bible=Bible(titulo=Title(original=title, pt=title)), tmdb_id=1,
                            genre_ids=genres)  # fmt: skip
    work_file(video, "meta").write_text(result.model_dump_json(), encoding="utf-8")


def test_the_folders_decide_when_given_and_the_metadata_only_when_sure(tmp_path: Path) -> None:
    show = tmp_path / "musicvideos" / "Banda (2004)" / "Banda - Live.mkv"
    film = tmp_path / "movies" / "Filme (2010)" / "Filme.mkv"
    for video in (show, film):
        video.parent.mkdir(parents=True)
    meta(show, (MUSIC,), "Banda: Live in Lisbon")
    meta(film, (DRAMA,), "Live Twice")

    def cfg(**kw: Any) -> AppConfig:
        return AppConfig.model_validate({"concert_mode": "auto", **kw})

    assert concert_state(str(show), cfg(), "rotina") == "yes"  # sem pasta: o TMDb tem certeza
    assert concert_state(str(film), cfg(), "rotina") == "no"
    by_folder = cfg(concert_paths=[str(tmp_path / "movies")])
    assert concert_state(str(film), by_folder, "rotina") == "yes"  # a pasta manda
    assert concert_state(str(show), by_folder, "rotina") == "no"  # e fora dela ninguém adivinha
    assert concert_state(str(show), cfg(concert_mode="off"), "manual") == "no"
    manual = cfg(concert_mode="manual")
    assert concert_state(str(show), manual, "rotina") == "manual"
    assert concert_state(str(show), manual, "manual") == "yes"
    assert concert_state(str(tmp_path / "sem-meta.mkv"), cfg(), "rotina") == "no"


# --- o que o decisor pede ---

V = "/m/musicvideos/Banda (2004)/Banda - Live.mkv"
MAIN = "/m/musicvideos/Banda (2004)/Banda - Live.main.en.c3parr"


MADE = {"metadados": "ok", "show": "ok"}
"""Os passos de um show cuja legenda de show já foi feita."""


def state(**kw: Any) -> ItemState:
    base = {"video": V, "steps": {"metadados": "ok"}, "concert": "yes", "original_language": "pt"}
    return ItemState.model_validate({**base, **kw})


def cfg(**kw: Any) -> AppConfig:
    return AppConfig.model_validate({"target_languages": ["pt-BR"], **kw})


def kinds(d: Decision) -> list[tuple[str, str | None]]:
    return [(a.kind, a.lang) for a in d.actions]


def test_a_concert_gets_its_own_subtitle_in_place_of_the_plain_transcription() -> None:
    assert kinds(decide(state(), cfg())) == [("show", None)]
    assert kinds(decide(state(concert="no"), cfg())) == [("asr", None)]
    waiting = decide(state(steps={"metadados": "ok", "show": "falhou"}), cfg())
    assert waiting.actions == () and waiting.blocked == ("show",)


def test_in_manual_mode_the_routine_waits_for_someone_to_ask() -> None:
    d = decide(state(concert="manual"), cfg())
    assert d.actions == () and "asks for this item" in d.notes[-1]
    # depois de feita, a legenda de show vale mesmo quando é a rotina que passa
    done = decide(state(concert="manual", generated={"en": MAIN}, steps=MADE), cfg())
    assert done.main == MainSub(path=MAIN, lang="en", source="show", version=1)


def test_a_concert_already_subtitled_the_plain_way_is_done_again_as_a_concert() -> None:
    """O show já estava na biblioteca: tem a principal que o ASR gerou e a tradução dela."""
    old = MainSub(path=MAIN, lang="en", source="asr", version=1)
    ours = OurOutput(path="/m/x.pt-BR.c3parr.srt", main_version=1)
    before = state(generated={"en": MAIN}, main=old, outputs={"pt-BR": ours},
                   asr_path="/m/x.asr.c3parr")  # fmt: skip
    d = decide(before, cfg())
    assert kinds(d) == [("show", None)] and d.main == old  # até lá, vale a que existe
    assert decide(before.model_copy(update={"concert": "manual"}), cfg()).actions == ()

    after = before.model_copy(update={"steps": MADE})
    d = decide(after, cfg())
    assert d.main == MainSub(path=MAIN, lang="en", source="show", version=2)
    after = after.model_copy(update={"main": d.main})
    assert kinds(decide(after, cfg())) == [("traduzir", "pt-BR"), ("publicar", "en")]
    # quem pediu para manter as traduções quando a principal muda fica com a antiga
    kept = decide(after, cfg(derived_on_main_change="keep"))
    assert kinds(kept) == [("publicar", "en")] and kept.resolved["pt-BR"] == "ours"


def test_the_concert_subtitle_is_translated_and_also_published_as_it_is_sung() -> None:
    made = state(generated={"en": MAIN}, steps=MADE)
    d = decide(made, cfg())
    assert d.main is not None and (d.main.lang, d.main.source) == ("en", "show")
    assert kinds(d) == [("traduzir", "pt-BR"), ("publicar", "en")]
    assert kinds(decide(made, cfg(concert_publish_original=False))) == [("traduzir", "pt-BR")]
    # quando o idioma cantado já é um alvo, não há nada a mais
    assert kinds(decide(made, cfg(target_languages=["en"]))) == [("publicar", "en")]
    ours = OurOutput(path="/m/x.en.c3parr.srt", main_version=1)
    main = MainSub(path=MAIN, lang="en", source="show", version=1)
    settled = decide(made.model_copy(update={"main": main, "outputs": {"en": ours, "pt-BR": ours}}),
                     cfg())  # fmt: skip
    assert settled.actions == () and settled.resolved == {"pt-BR": "ours", "en": "ours"}


# --- a pipeline, com providers de mentira ---

ALFA = song("Alfa", 1, lines=8)
SUNG = ("Good evening everybody", *ALFA.lines, "Thank you all so much")


def concert_env(tmp_path: Path, mode: str = "auto") -> Env:
    env = Env(tmp_path)
    env.specs.update(
        transcricao=ComponentSpec(provider="falso", params={"lines": SUNG, "spacing": 4.0}),
        separacao=ComponentSpec(provider="falso"),
        letras=ComponentSpec(provider="falso", params={"songs": [ALFA.model_dump()]}),
        encaixe=ComponentSpec(provider="falso", params={"lead": 5.0}),
    )
    config = env.library.config().model_copy(update={
        "concert_mode": mode, "concert_paths": (str(tmp_path / "musicvideos"),)})  # fmt: skip
    env.library.save_config(config)
    return env


def test_a_video_in_the_concert_folder_gets_lyrics_a_translation_and_the_original(
    tmp_path: Path, tiny_video: Path
) -> None:
    env = concert_env(tmp_path)
    video = place(tiny_video, tmp_path / "musicvideos" / "Banda (2004)" / "Banda - Live.mkv")
    reconcile(env.ctx, str(video), "manual")
    done = [(j.acao, j.idioma) for j in env.drain()]
    assert done == [("metadados", None), ("show", None), ("traduzir", "pt-BR"), ("publicar", "fr")]
    main = work_file(video, "main", "fr")  # o falso transcreve "em francês": é o idioma cantado
    assert f"♪ {ALFA.lines[0]} ♪" in " ".join(main.read_text(encoding="utf-8").split())
    assert "♪" in output_file(video, "fr").read_text(encoding="utf-8")
    assert "[pt-BR]" in output_file(video, "pt-BR").read_text(encoding="utf-8")
    assert not work_file(video, "asr").exists()  # a transcrição comum não rodou
    media = env.catalog.media_by_video(str(video))
    assert media is not None and (media.main_lang, media.main_source) == ("fr", "show")
    detail = env.catalog.steps(media.id)[("show", "")].detail or ""
    assert detail.startswith("1 songs, 8 lyric lines, 2 heard only")


def test_turning_concerts_on_redoes_the_concerts_that_were_already_in_the_library(
    tmp_path: Path, tiny_video: Path
) -> None:
    env = concert_env(tmp_path, mode="off")
    video = place(tiny_video, tmp_path / "musicvideos" / "Banda (2004)" / "Banda - Live.mkv")
    reconcile(env.ctx, str(video), "manual")
    assert [j.acao for j in env.drain()] == ["metadados", "asr", "gerar", "traduzir"]
    plain = work_file(video, "main", "fr").read_text(encoding="utf-8")
    assert "♪" not in plain

    env.library.save_config(env.library.config().model_copy(update={"concert_mode": "auto"}))
    reconcile(env.ctx, str(video), "rotina", force=True)  # a rotina de toda hora já pega
    assert [(j.acao, j.idioma) for j in env.drain()] == [
        ("show", None), ("traduzir", "pt-BR"), ("publicar", "fr")]  # fmt: skip
    assert "♪" in work_file(video, "main", "fr").read_text(encoding="utf-8")
    media = env.catalog.media_by_video(str(video))
    assert media is not None and (media.main_source, media.main_version) == ("show", 2)
    reconcile(env.ctx, str(video), "rotina", force=True)
    assert env.drain() == []  # e não refaz de novo


def test_a_film_outside_the_concert_folder_goes_the_usual_way(
    tmp_path: Path, tiny_video: Path
) -> None:
    env = concert_env(tmp_path)
    video = place(tiny_video, tmp_path / "movies" / "Filme (2001)" / "Filme.mkv")
    reconcile(env.ctx, str(video), "manual")
    assert [j.acao for j in env.drain()] == ["metadados", "asr", "gerar", "traduzir"]


def test_in_manual_mode_only_a_request_for_the_item_starts_the_concert_subtitle(
    tmp_path: Path, tiny_video: Path
) -> None:
    env = concert_env(tmp_path, mode="manual")
    video = place(tiny_video, tmp_path / "musicvideos" / "Banda (2004)" / "Banda - Live.mkv")
    reconcile(env.ctx, str(video), "rotina", force=True)
    assert [j.acao for j in env.drain()] == ["metadados"]  # e para: espera alguém pedir
    reconcile(env.ctx, str(video), "manual")
    assert [j.acao for j in env.drain()] == ["show", "traduzir", "publicar"]
