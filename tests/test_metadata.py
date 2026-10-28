"""Metadados com respostas gravadas do TMDb (sem rede) e correção de nomes na transcrição."""

from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from c3parr.core.bible import Bible, Character
from c3parr.core.errors import InvalidInput
from c3parr.core.models import Segment, Transcript, Word
from c3parr.interfaces.metadata import MetadataRequest
from c3parr.metadata.names import correct_names, hotwords
from c3parr.metadata.tmdb import TMDbConfig, TMDbProvider, parse_filename

FILM = {
    "id": 101,
    "title": "Incêndios",
    "original_title": "Incendies",
    "release_date": "2010-09-17",
    "runtime": 131,
    "original_language": "fr",
    "overview": "Gêmeos descobrem o passado da mãe.",
    "genres": [{"name": "Drama"}],
    "credits": {
        "cast": [
            {"name": "Atriz A", "character": "Nawal Marwan", "gender": 1},
            {"name": "Ator B", "character": "Simon Marwan", "gender": 2},
            {"name": "Atriz C", "character": "Jeanne Marwan", "gender": 1},
            {"name": "Ator D", "character": "Waiter", "gender": 2},
            {"name": "Ator E", "character": "Notaire Jean Lebel", "gender": 2},
        ]
    },
}
DOC = {**FILM, "id": 202, "title": "Regards sur Incendies", "runtime": 26, "credits": {"cast": []}}


def provider(routes: dict[str, Any]) -> TMDbProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        body = routes.get(request.url.path)
        return httpx.Response(404) if body is None else httpx.Response(200, json=body)

    config = TMDbConfig(token=SecretStr("teste"), base_url="http://tmdb.test/3")
    return TMDbProvider(config, transport=httpx.MockTransport(handler))


REQ = MetadataRequest(
    filename="Incendies (2010) Bluray-1080p.mp4", audio_duration=7828, language="fr"
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Incendies (2010) Bluray-1080p.mp4", ("Incendies", 2010)),
        ("Nome.do.Filme.2001.1080p.BluRay.x264.mkv", ("Nome do Filme", 2001)),
        ("Some Movie WEBDL-1080p.mkv", ("Some Movie", None)),
    ],
)
def test_parse_filename(name: str, expected: tuple[str, int | None]) -> None:
    assert parse_filename(name) == expected


def test_documentary_is_discarded_by_runtime_and_cast_becomes_bible() -> None:
    routes = {
        "/3/search/movie": {"results": [{"id": 101}, {"id": 202}]},
        "/3/movie/101": FILM,
        "/3/movie/202": DOC,
    }
    result = provider(routes).identify(REQ)
    assert result.tmdb_id == 101
    names = [(c.nome, c.genero, c.origem_genero) for c in result.bible.personagens]
    assert ("Nawal Marwan", "F", "tmdb") in names
    assert all(n != "Waiter" for n, _, _ in names)  # nomes genéricos ficam de fora
    assert result.bible.titulo.pt == "Incêndios"
    assert any("26 min" in c.motivo for c in result.candidates)


def test_tie_fails_and_lists_candidates() -> None:
    twin = {**FILM, "id": 303, "title": "Incendies (remake)"}
    routes = {
        "/3/search/movie": {"results": [{"id": 101}, {"id": 303}]},
        "/3/movie/101": FILM,
        "/3/movie/303": twin,
    }
    with pytest.raises(InvalidInput, match="2 tied") as e:
        provider(routes).identify(REQ)
    assert "id 303" in e.value.message


def test_not_found_fails_cleanly() -> None:
    with pytest.raises(InvalidInput, match="no movie"):
        provider({"/3/search/movie": {"results": []}}).identify(REQ)


def test_tmdb_id_skips_search() -> None:
    request = REQ.model_copy(update={"tmdb_id": 101})
    assert provider({"/3/movie/101": FILM}).identify(request).chosen_reason == "TMDb ID given"


BIBLE = Bible(
    personagens=(
        Character(nome="Nawal Marwan", genero="F"),
        Character(nome="Jeanne Marwan", genero="F"),
        Character(nome="Notaire Jean Lebel", genero="M"),
    )
)


def test_names_become_hotwords() -> None:
    assert hotwords(BIBLE) == ("Nawal", "Marwan", "Jeanne", "Notaire", "Jean", "Lebel")


def test_misspelled_names_are_fixed_in_text_and_words() -> None:
    words = (
        Word(w="Simon", start=0, end=0.3),
        Word(w="Marouane", start=0.3, end=0.8),
        Word(w="et", start=0.8, end=0.9),
        Word(w="Jeanne.", start=0.9, end=1.2),
    )
    seg = Segment(id=0, start=0, end=1.2, text="Simon Marouane et Jeanne.", lang="fr", words=words)
    transcript = Transcript(segments=(seg,), language="fr", model="t", audio_duration=2)
    result = correct_names(transcript, BIBLE)
    fixed = result.transcript.segments[0]
    assert fixed.text == "Simon Marwan et Jeanne."
    assert [w.w for w in fixed.words] == ["Simon", "Marwan", "et", "Jeanne."]
    assert [(f.wrong, f.right, f.count) for f in result.fixes] == [("Marouane", "Marwan", 1)]


def test_common_words_are_not_turned_into_names() -> None:
    seg = Segment(id=0, start=0, end=1, text="Merci, Madame. Nous arrivons.", lang="fr")
    transcript = Transcript(segments=(seg,), language="fr", model="t", audio_duration=1)
    assert correct_names(transcript, BIBLE).fixes == ()


def test_capitalized_common_words_seen_in_lowercase_stay(  # casos do Incendies inteiro
) -> None:
    texts = ["Maman, écoute.", "Notre mère est morte.", "C'est notre maman.", "Sinon, rien."]
    segs = tuple(Segment(id=i, start=i, end=i + 1, text=t, lang="fr") for i, t in enumerate(texts))
    segs += (Segment(id=9, start=9, end=10, text="Il faut, sinon Marouane part.", lang="fr"),)
    transcript = Transcript(segments=segs, language="fr", model="t", audio_duration=10)
    result = correct_names(transcript, BIBLE)
    assert [(f.wrong, f.right) for f in result.fixes] == [("Marouane", "Marwan")]


def test_possessive_is_kept_and_stripped_from_bible_names() -> None:
    bible = Bible(personagens=(Character(nome="Nawal's Grandmother"), Character(nome="Marwan")))
    assert hotwords(bible) == ("Nawal", "Grandmother", "Marwan")
    seg = Segment(id=0, start=0, end=1, text="La maison de Marouane's.", lang="fr")
    transcript = Transcript(segments=(seg,), language="fr", model="t", audio_duration=1)
    fixed = correct_names(transcript, bible).transcript.segments[0]
    assert fixed.text == "La maison de Marwan's."


@pytest.mark.parametrize(
    ("path", "kind", "title", "year", "season", "episode"),
    [
        ("/m/movies/Incendies (2010)/Incendies (2010) Bluray-1080p.mp4", "filme", "Incendies", 2010, None, None),
        ("/m/tv/Criminal Minds/Season 1/Criminal Minds - S01E01 - Extreme Aggressor WEBDL-1080p.mkv",
         "serie", "Criminal Minds", None, 1, 1),
        ("/m/tv/Dark (2017)/Season 2/Dark - S02E05 - Lost and Found.mkv", "serie", "Dark", 2017, 2, 5),
        ("/downloads/Nome.do.Filme.2001.1080p.mkv", "filme", "Nome do Filme", 2001, None, None),
    ],
)  # fmt: skip
def test_parse_path_reads_arr_layout(
    path: str, kind: str, title: str, year: int | None, season: int | None, episode: int | None
) -> None:
    from pathlib import Path

    from c3parr.metadata.tmdb import parse_path

    hint = parse_path(Path(path))
    assert (hint.kind, hint.title, hint.year, hint.season, hint.episode) == (
        kind, title, year, season, episode
    )  # fmt: skip


def test_ids_in_folder_name_skip_the_search() -> None:
    from pathlib import Path

    from c3parr.metadata.tmdb import parse_path

    hint = parse_path(Path("/m/movies/Incendies (2010) [tmdbid-46738]/Incendies.mkv"))
    assert (hint.title, hint.tmdb_id) == ("Incendies", 46738)
    request = REQ.model_copy(update={"path": "/m/movies/Incendies (2010) [tmdbid-101]/x.mp4"})
    assert provider({"/3/movie/101": FILM}).identify(request).chosen_reason == "TMDb ID in the path"


SHOW = {
    "id": 4057,
    "name": "Mentes Criminosas",
    "original_name": "Criminal Minds",
    "first_air_date": "2005-09-22",
    "original_language": "en",
    "episode_run_time": [42],
    "genres": [{"name": "Crime"}],
    "aggregate_credits": {"cast": [
        {"name": "Ator X", "gender": 2, "roles": [{"character": "Aaron Hotchner"}]},
        {"name": "Atriz Y", "gender": 1, "roles": [{"character": "Elle Greenaway"}]},
    ]},
}  # fmt: skip
EPISODE = {"name": "Extreme Aggressor", "runtime": 43, "overview": "Um caso em Seattle.",
           "guest_stars": [{"name": "Ator Z", "gender": 1, "character": "Tracy Belle"}]}  # fmt: skip


def test_tv_episode_is_identified_with_guest_stars() -> None:
    routes = {
        "/3/search/tv": {"results": [{"id": 4057}]},
        "/3/tv/4057": SHOW,
        "/3/tv/4057/season/1/episode/1": EPISODE,
    }
    request = MetadataRequest(
        filename="Criminal Minds - S01E01 - Extreme Aggressor WEBDL-1080p.mkv",
        path="/m/tv/Criminal Minds/Season 1/Criminal Minds - S01E01 - Extreme Aggressor WEBDL-1080p.mkv",
        audio_duration=2502,
        language="en",
    )
    result = provider(routes).identify(request)
    assert (result.kind, result.tmdb_id, result.season, result.episode) == ("serie", 4057, 1, 1)
    assert result.episode_title == "Extreme Aggressor"
    names = {c.nome for c in result.bible.personagens}
    assert {"Aaron Hotchner", "Elle Greenaway", "Tracy Belle"} <= names


def test_builtin_key_is_used_only_without_one_of_yours(monkeypatch: pytest.MonkeyPatch) -> None:
    from c3parr.core.errors import Unavailable
    from c3parr.core.job import ComponentSpec
    from c3parr.metadata import tmdb
    from c3parr.providers.registry import build_factories

    factory = build_factories().for_interface("metadados")
    spec = ComponentSpec(provider="tmdb")
    with pytest.raises(Unavailable, match="TMDB_READ_ACCESS_TOKEN"):
        factory.create(spec, {})  # sem chave nenhuma
    monkeypatch.setattr(tmdb, "BUILTIN_READ_TOKEN", "builtin")
    assert factory.create(spec, {}).config.token.get_secret_value() == "builtin"
    mine = factory.create(spec, {"TMDB_READ_ACCESS_TOKEN": "mine"})
    assert mine.config.token.get_secret_value() == "mine"
