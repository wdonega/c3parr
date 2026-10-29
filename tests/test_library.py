"""Biblioteca: configs no SQLite, sync com o Radarr/Sonarr, monitorado + espera e rotinas."""

from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from c3parr.audio.ffmpeg import MediaInfo, SubtitleStream
from c3parr.jobs.queue import JobQueue
from c3parr.library.arr import ArrConfig, map_path
from c3parr.library.sidecars import index_subtitles, subtitle_languages
from c3parr.library.store import Library, now
from c3parr.library.sync import sync_radarr, sync_sonarr
from c3parr.library.tasks import Scheduler, default_tasks, search_missing, wanted


def touch(path: Path, size: int = 10) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


@pytest.fixture
def media(tmp_path: Path) -> Path:
    root = tmp_path / "media"
    touch(root / "movies" / "Incendies (2010)" / "Incendies.2010.1080p.mkv", 100)
    touch(root / "movies" / "Incendies (2010)" / "Incendies.2010.1080p.en.srt")
    touch(root / "movies" / "Tagged (2020)" / "Tagged.mkv")
    touch(root / "tv" / "Criminal Minds" / "Season 1" / "Criminal Minds S01E01.mkv")
    touch(root / "tv" / "Criminal Minds" / "Season 1" / "Criminal Minds S01E02.mkv")
    touch(root / "tv" / "Criminal Minds" / "Season 1" / "Criminal Minds S01E02.pt-BR.srt")
    touch(root / "tv" / "Criminal Minds" / "Specials" / "Criminal Minds S00E01.mkv")
    return root


class FakeArr:
    """Radarr e Sonarr de mentira: as rotas da API v3 que o sync usa, com caminhos /data/..."""

    def __init__(self) -> None:
        self.movies: list[dict[str, Any]] = [
            {"id": 1, "title": "Incendies", "year": 2010, "tmdbId": 46738, "hasFile": True,
             "monitored": True, "tags": [], "path": "/data/movies/Incendies (2010)",
             "movieFile": {"path": "/data/movies/Incendies (2010)/Incendies.2010.1080p.mkv"}},
            {"id": 2, "title": "Not Downloaded", "hasFile": False, "monitored": True, "tags": []},
            {"id": 3, "title": "Tagged", "year": 2020, "hasFile": True, "monitored": True,
             "tags": [7], "path": "/data/movies/Tagged (2020)",
             "movieFile": {"relativePath": "Tagged.mkv"}},
        ]  # fmt: skip
        self.files = [
            {"id": 100, "seasonNumber": 1, "path": "/data/tv/Criminal Minds/Season 1/Criminal Minds S01E01.mkv"},
            {"id": 101, "seasonNumber": 1, "relativePath": "Season 1/Criminal Minds S01E02.mkv"},
            {"id": 102, "seasonNumber": 0, "relativePath": "Specials/Criminal Minds S00E01.mkv"},
        ]  # fmt: skip
        self.episodes = [
            {"seasonNumber": 1, "episodeNumber": 1, "episodeFileId": 100, "monitored": True},
            {"seasonNumber": 1, "episodeNumber": 2, "episodeFileId": 101, "monitored": False},
            {"seasonNumber": 0, "episodeNumber": 1, "episodeFileId": 102, "monitored": True},
        ]
        self.version = {"radarr": "6.4.4.10685", "sonarr": "3.0.10.1567"}
        self.seen: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        kind = request.url.host.split(".")[0]
        path = request.url.path.removeprefix("/api/v3")
        self.seen.append(f"{kind}{path}")
        if request.headers.get("x-api-key") != "k":
            return httpx.Response(401)
        routes: dict[str, Any] = {
            "/system/status": {"version": self.version[kind]},
            "/tag": [{"id": 7, "label": "no-subs"}],
            "/movie": self.movies,
            "/series": [
                {"id": 10, "title": "Criminal Minds", "year": 2005, "monitored": True, "tags": [],
                 "seriesType": "standard", "path": "/data/tv/Criminal Minds",
                 "statistics": {"episodeFileCount": 3}},
                {"id": 11, "title": "Nothing Yet", "statistics": {"episodeFileCount": 0}},
            ],
            "/episodefile": self.files,
            "/episode": self.episodes,
        }  # fmt: skip
        return httpx.Response(200, json=routes[path]) if path in routes else httpx.Response(404)


@pytest.fixture
def arr() -> FakeArr:
    return FakeArr()


@pytest.fixture
def library(tmp_path: Path, media: Path) -> Library:
    lib = Library(tmp_path / "c3parr.db")
    lib.update_config(
        radarr=ArrConfig(enabled=True, host="radarr.test", excluded_tags=("no-subs",),
                         path_mappings=(("/data/movies", str(media / "movies")),)),
        sonarr=ArrConfig(enabled=True, host="sonarr.test",
                         path_mappings=(("/data/tv", str(media / "tv")),)),
        wait_minutes=0,
    )  # fmt: skip
    return lib


def sync(library: Library, arr: FakeArr) -> tuple[str, str]:
    t = httpx.MockTransport(arr)
    return sync_radarr(library, "k", t), sync_sonarr(library, "k", t)


def test_config_round_trips_through_sqlite(tmp_path: Path) -> None:
    lib = Library(tmp_path / "c3parr.db")
    assert lib.config().target_language == "pt-BR"
    lib.update_config(target_language="es", wait_minutes=30)
    again = Library(tmp_path / "c3parr.db").config()
    assert (again.target_language, again.wait_minutes) == ("es", 30)


def test_sync_reads_the_arr_library_with_its_metadata(library: Library, arr: FakeArr) -> None:
    movies, episodes = sync(library, arr)
    assert movies.startswith("1 movies, 1 new") and "1 left out by the filters" in movies
    movie = library.movies()[0]
    assert (movie.title, movie.year, movie.tmdb_id) == ("Incendies", 2010, 46738)  # do Radarr
    assert [(e.season, e.episode) for e in library.episodes()] == [(0, 1), (1, 1), (1, 2)]
    assert episodes.startswith("3 episodes, 3 new")
    assert "sonarr/episode" in "|".join(arr.seen) and "sonarr/series" in "|".join(arr.seen)
    assert library.meta("radarr_version") == "6.4.4.10685"
    assert sync(library, arr)[0].startswith("1 movies, 0 new")


def test_sonarr_filters(library: Library, arr: FakeArr) -> None:
    cfg = library.config().sonarr
    library.update_config(
        sonarr=cfg.model_copy(update={"only_monitored": True, "exclude_season_zero": True})
    )
    _, episodes = sync(library, arr)
    assert [(e.season, e.episode) for e in library.episodes()] == [(1, 1)]
    assert "2 left out by the filters" in episodes
    library.update_config(sonarr=cfg.model_copy(update={"excluded_series_types": ("standard",)}))
    sync(library, arr)
    assert library.episodes() == []


def test_removed_in_arr_is_removed_here_but_never_when_files_are_not_found(
    library: Library, arr: FakeArr, media: Path
) -> None:
    sync(library, arr)
    arr.files = arr.files[:1]
    assert "2 removed" in sync(library, arr)[1]
    # caminho errado (mapeamento ou disco): nada é apagado e o resumo diz o que olhar
    library.update_config(radarr=library.config().radarr.model_copy(update={"path_mappings": ()}))
    movies, _ = sync(library, arr)
    assert "check the Path Mappings" in movies and "nothing was removed" in movies
    assert len(library.movies()) == 1


def test_sync_records_the_subtitles_next_to_each_video(
    library: Library, arr: FakeArr, media: Path
) -> None:
    sync(library, arr)
    subs = library.subtitle_files()
    langs = {Path(v).name: [(f.lang, f.kind) for f in fs] for v, fs in subs.items()}
    assert langs == {"Incendies.2010.1080p.mkv": [("en", "human")],
                     "Criminal Minds S01E02.mkv": [("pt-BR", "human")]}  # fmt: skip
    arr.files = arr.files[:1]  # o episódio saiu do Sonarr: as legendas dele saem do banco
    sync(library, arr)
    assert {Path(v).name for v in library.subtitle_files()} == {"Incendies.2010.1080p.mkv"}


def test_subtitle_index_keeps_embedded_and_guesses_language_once(
    library: Library, media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = str(media / "movies" / "Incendies (2010)" / "Incendies.2010.1080p.mkv")
    unnamed = Path(video).with_name("Incendies.2010.1080p.hi.srt")
    unnamed.write_text(" ".join(["the you and is it to of that"] * 10))
    probe = MediaInfo(duration=1.0, audio=(), subtitles=(
        SubtitleStream(index=3, lang="fr", codec="subrip"),))  # fmt: skip
    index_subtitles(library, video, probe)
    calls: list[Path] = []
    monkeypatch.setattr("c3parr.library.sidecars.guess_text_lang",
                        lambda p: calls.append(p) or "xx")  # fmt: skip
    files = {(f.lang, f.kind) for f in index_subtitles(library, video)}  # sync: sem sondagem
    assert calls == []  # arquivo igual: o idioma adivinhado vem do banco
    assert files == {("en", "human")}  # `.en.srt` pelo nome, `.hi.srt` pelo texto
    stored = {(f.lang, f.kind) for f in library.subtitle_files([video])[video]}
    assert ("fr", "embedded") in stored  # o sync não apaga as faixas embutidas


def test_arr_address_forms_and_path_mapping() -> None:
    assert ArrConfig(host="sonarr", port=8989).url() == "http://sonarr:8989"
    assert ArrConfig(host="sonarr", port=8989, ssl=True, base_url="/sonarr/").url() == (
        "https://sonarr:8989/sonarr"
    )
    assert (
        ArrConfig(host="https://sonarr.bigode.ai/", port=8989).url() == "https://sonarr.bigode.ai"
    )
    maps = (("/data", "/a"), ("/data/tv", "/b"))
    assert map_path("/data/tv/x.mkv", maps) == Path("/b/x.mkv")  # o mais longo vence
    assert map_path("/data/tvx/x.mkv", maps) == Path("/a/tvx/x.mkv")


def test_wrong_key_and_disabled(library: Library, arr: FakeArr) -> None:
    from c3parr.core.errors import Unavailable

    with pytest.raises(Unavailable, match="rejected the API key"):
        sync_radarr(library, "wrong", httpx.MockTransport(arr))
    library.update_config(radarr=library.config().radarr.model_copy(update={"enabled": False}))
    assert sync_radarr(library, "k", httpx.MockTransport(arr)).startswith("Radarr is off")


def test_subtitle_languages_reads_sidecars(media: Path) -> None:
    video = media / "movies" / "Incendies (2010)" / "Incendies.2010.1080p.mkv"
    assert subtitle_languages(video) == {"en"}


def test_only_monitored_items_without_target_subtitle_are_wanted(
    library: Library, arr: FakeArr
) -> None:
    sync(library, arr)
    titles = sorted(w.title for w in wanted(library))
    assert titles == ["Criminal Minds S00E01", "Criminal Minds S01E01", "Incendies (2010)"]
    library.set_monitored("movie", library.movies()[0].id, False)
    assert "Incendies (2010)" not in [w.title for w in wanted(library)]


def test_search_respects_wait_time_and_does_not_requeue(
    library: Library, arr: FakeArr, tmp_path: Path
) -> None:
    queue = JobQueue(tmp_path / "fila.db")
    sync(library, arr)
    library.update_config(wait_minutes=60)
    assert search_missing(library, queue).startswith("0 added")
    library.update_config(wait_minutes=0)
    assert search_missing(library, queue).startswith("3 added")
    assert search_missing(library, queue).startswith("0 added")


def test_scheduler_runs_the_syncs_at_start_and_on_request(
    library: Library, arr: FakeArr, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from c3parr.library import tasks

    t = httpx.MockTransport(arr)
    monkeypatch.setattr(tasks, "sync_radarr", lambda lib, key: sync_radarr(lib, key, t))
    monkeypatch.setattr(tasks, "sync_sonarr", lambda lib, key: sync_sonarr(lib, key, t))
    queue = JobQueue(tmp_path / "fila.db")
    sched = Scheduler(library, default_tasks(library, queue, api_key=lambda kind: "k"))
    assert [t.name for t in sched.due()] == ["sync_radarr", "sync_sonarr"]
    sched.run_once()
    state = library.task("sync_radarr")
    assert state.last_result and state.last_result.startswith("1 movies")
    assert state.next_run and state.next_run > (now() + timedelta(minutes=59)).isoformat()
    library.request_run("search_missing")
    assert [t.name for t in sched.due()] == ["search_missing"]
    sched.run_once()
    assert len(queue.list()) == 3


def test_old_path_mappings_move_to_both_arrs(tmp_path: Path) -> None:
    import json
    import sqlite3

    lib = Library(tmp_path / "c3parr.db")
    with sqlite3.connect(tmp_path / "c3parr.db") as db:  # config do tempo do webhook
        db.execute("INSERT INTO setting (key, value) VALUES (?, ?)",
                   ("path_mappings", json.dumps([["/data", "/mnt"]])))  # fmt: skip
    cfg = lib.config()
    assert cfg.radarr.path_mappings == (("/data", "/mnt"),) == cfg.sonarr.path_mappings
    assert (cfg.radarr.port, cfg.sonarr.port) == (7878, 8989)


def test_pick_reference_prefers_plain_english_and_skips_target(tmp_path: Path) -> None:
    from c3parr.library.sidecars import pick_reference

    video = touch(tmp_path / "Film.mkv")
    assert pick_reference(video, "pt-BR") is None
    touch(tmp_path / "Film.pt-BR.srt")
    touch(tmp_path / "Film.en.hi.srt")
    assert pick_reference(video, "pt-BR") == tmp_path / "Film.en.hi.srt"
    touch(tmp_path / "Film.fr.srt")
    assert pick_reference(video, "pt-BR") == tmp_path / "Film.fr.srt"
    touch(tmp_path / "Film.en.srt")
    assert pick_reference(video, "pt-BR") == tmp_path / "Film.en.srt"
