"""O que só a API JSON tem (as telas HTML nunca tiveram): pôsteres, a fila na ordem em que
roda, ações em lote, busca do topo, ordenação e o último teste de conexão."""

import dataclasses
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from uikit import World, empty_world, seed_world

from c3parr.web import app as web

DYING = "Supernatural - S02E01 - In My Time of Dying.mkv"


@pytest.fixture
def world(tmp_path: Path) -> World:
    return seed_world(tmp_path)


@pytest.fixture
def api(world: World) -> Iterator[TestClient]:
    with TestClient(world.app, base_url="http://testserver/api/v1") as client:
        yield client


def _fake_posters(world: World, answer: bytes | None) -> list[tuple[str, str]]:
    """Troca quem baixa o pôster por um de mentira e devolve a lista do que foi pedido."""
    asked: list[tuple[str, str]] = []

    def fetch(kind: str, url: str) -> bytes | None:
        asked.append((kind, url))
        return answer

    service = world.app.state.service
    service.clients = dataclasses.replace(service.clients, fetch_poster=fetch)
    return asked


# --- pôsteres ---


def test_posters_are_fetched_once_in_a_list_size_and_served_from_the_cache(
    world: World, api: TestClient
) -> None:
    asked = _fake_posters(world, b"jpeg-bytes")
    movies = {m["title"]: m for m in api.get("/movies").json()["rows"]}
    assert movies["Incendies"]["poster"] == f"/api/v1/poster/movie/{world.ids['Incendies']}"
    assert movies["Oldboy"]["poster"] is None  # o Radarr não informou um

    first = api.get(f"/poster/movie/{world.ids['Incendies']}")
    assert first.status_code == 200 and first.content == b"jpeg-bytes"
    assert (
        first.headers["content-type"] == "image/jpeg"
        and "max-age" in first.headers["cache-control"]
    )
    # do TMDb vem o tamanho de lista, não o original
    assert asked == [("radarr", "https://image.tmdb.org/t/p/w342/incendies.jpg")]
    assert api.get(f"/poster/movie/{world.ids['Incendies']}").content == b"jpeg-bytes"
    assert len(asked) == 1  # a segunda vez saiu do cache

    show = api.get(f"/shows/{world.ids['Supernatural']}").json()
    assert show["poster"] == f"/api/v1/poster/series/{world.ids['Supernatural']}"
    assert api.get(f"/poster/series/{world.ids['Supernatural']}").status_code == 200
    # a capa guardada no Sonarr, na versão menor
    assert asked[-1] == ("sonarr", "/MediaCover/7/poster-500.jpg?lastWrite=1")


def test_a_poster_that_does_not_come_is_not_asked_again_right_away(
    world: World, api: TestClient
) -> None:
    asked = _fake_posters(world, None)
    assert api.get(f"/poster/movie/{world.ids['Incendies']}").status_code == 404
    assert api.get(f"/poster/movie/{world.ids['Incendies']}").status_code == 404
    assert len(asked) == 1
    assert api.get(f"/poster/movie/{world.ids['Oldboy']}").status_code == 404  # não tem pôster
    assert api.get("/poster/movie/999").status_code == 404
    assert api.get("/poster/episode/1").status_code == 404
    assert len(asked) == 1


def test_fetching_a_poster_rejects_what_is_not_an_image(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Answer:
        def __init__(self, status: int, kind: str, body: bytes) -> None:
            self.status_code, self.headers, self.content = status, {"content-type": kind}, body

    answers = {
        "https://p.test/ok.jpg": Answer(200, "image/jpeg", b"img"),
        "https://p.test/page.jpg": Answer(200, "text/html", b"<html>"),
        "https://p.test/gone.jpg": Answer(404, "image/jpeg", b""),
        "https://p.test/huge.jpg": Answer(200, "image/jpeg", b"x" * (web.POSTER_MAX_BYTES + 1)),
    }
    monkeypatch.setattr(web.httpx, "get", lambda url, **_: answers[url])
    fetched = {url: web._fetch_poster(world.library, "radarr", url) for url in answers}
    assert fetched == {"https://p.test/ok.jpg": b"img", "https://p.test/page.jpg": None,
                       "https://p.test/gone.jpg": None, "https://p.test/huge.jpg": None}  # fmt: skip
    # capa guardada no Sonarr sem endereço configurado: não há de onde baixar
    assert web._fetch_poster(world.library, "sonarr", "/MediaCover/7/poster.jpg") is None


# --- fila ---


def test_the_queue_names_each_task_by_its_movie_or_episode(world: World, api: TestClient) -> None:
    rows = {r["id"]: r for r in api.get("/queue").json()["rows"]}
    queued = rows[world.ids["queued"]]["item"]
    assert (queued["kind"], queued["title"], queued["season"], queued["episode"]) == (
        "episode", "Supernatural", 2, 1)  # fmt: skip
    assert queued["episode_title"] == "In My Time of Dying"
    assert queued["show_id"] == world.ids["Supernatural"]
    running = rows[world.ids["running"]]["item"]
    assert (running["kind"], running["title"], running["year"]) == ("movie", "Oldboy", 2003)
    assert api.get("/shell").json()["running"]["item"]["title"] == "Oldboy"

    world.queue.submit("/not/in/the/library.mkv", "pt-BR")
    newest = api.get("/queue").json()["rows"][0]
    assert newest["item"] is None and newest["name"] == "library.mkv"


def test_upcoming_follows_the_run_order_and_a_task_can_jump_to_the_top(
    world: World, api: TestClient
) -> None:
    queue, ids = world.queue, world.ids
    later = queue.submit("/m/later.mkv", "pt-BR")
    manual, _ = queue.submit_step(99, "/m/manual.mkv", "traduzir", "pt-BR", origem="manual")
    assert [j["id"] for j in api.get("/queue/upcoming").json()] == [
        manual.id, ids["queued"], later.id]  # fmt: skip
    assert len(api.get("/queue/upcoming", params={"limit": 1}).json()) == 1

    moved = api.post(f"/queue/{later.id}/top")
    assert moved.status_code == 200 and moved.json()["priority"] is True
    assert [j["id"] for j in api.get("/queue/upcoming").json()] == [
        later.id, manual.id, ids["queued"]]  # fmt: skip
    assert api.post("/queue/999/top").status_code == 404

    # o lugar na fila aparece no item da biblioteca
    show = api.get(f"/shows/{ids['Supernatural']}").json()
    episode = show["seasons"][1]["episodes"][0]
    assert episode["name"] == DYING and episode["title"] == "In My Time of Dying"
    assert episode["job"] == {"id": ids["queued"], "label": "Translate → pt-BR",
                              "language": "pt-BR", "status": "queued", "progress": 0.0,
                              "position": 3}  # fmt: skip
    oldboy = next(m for m in api.get("/movies").json()["rows"] if m["title"] == "Oldboy")
    assert oldboy["job"]["status"] == "running" and oldboy["job"]["position"] is None


def test_cancel_everything_waiting_and_run_the_failed_again(world: World, api: TestClient) -> None:
    queue, ids = world.queue, world.ids
    queue.submit("/m/other.mkv", "pt-BR")
    cancelled = api.post("/queue/cancel-queued").json()
    assert cancelled == {"ok": True, "message": "2 task(s) cancelled."}
    assert queue.get(ids["queued"]).status == "cancelada"
    assert queue.get(ids["running"]).status == "processando"  # a que roda continua
    assert api.post("/queue/cancel-queued").json()["message"] == "Nothing is waiting in the queue."

    retried = api.post("/queue/retry-failed").json()
    assert retried == {"ok": True, "message": "1 task(s) back in the queue."}
    assert queue.get(ids["failed"]).status == "na_fila"
    assert api.post("/queue/retry-failed").json()["message"] == "No failed task to run again."
    counts = api.get("/queue").json()["counts"]
    assert (counts["queued"], counts["failed"], counts["cancelled"]) == (1, 0, 3)


def test_recent_and_the_detail_of_a_task(world: World, api: TestClient) -> None:
    ids = world.ids
    recent = api.get("/queue/recent", params={"limit": 2}).json()
    assert len(recent) == 2 and {j["status"] for j in recent} <= {
        "done", "done_with_warnings", "failed"}  # fmt: skip

    running = api.get(f"/queue/{ids['running']}").json()
    assert running["stage"] == "Translation" and running["message"] == "block 44 of 94"
    assert running["can_cancel"] and not running["can_retry"]

    failed = api.get(f"/queue/{ids['failed']}").json()
    assert failed["stage"] is None and failed["can_retry"] and not failed["can_cancel"]
    # o que a pipeline já registrou para o vídeo: o último resultado de cada passo
    assert [(p["action"], p["label"], p["language"], p["state"], p["detail"], p["attempts"])
            for p in failed["pipeline"]] == [
        ("traduzir", "Translate", "pt-BR", "failed", "LLM fora do ar", 1)]  # fmt: skip
    done = api.get(f"/queue/{ids['done']}").json()
    assert [(p["action"], p["language"], p["state"]) for p in done["pipeline"]] == [
        ("extrair", "en", "done")]  # fmt: skip
    assert api.get(f"/queue/{ids['cancelled']}").json()["pipeline"] == []  # tarefa antiga


# --- biblioteca ---


def test_lists_can_be_sorted_and_carry_a_count_per_filter(world: World, api: TestClient) -> None:
    def titles(**params: str) -> list[str]:
        return [m["title"] for m in api.get("/movies", params=params).json()["rows"]]

    assert titles() == ["Incendies", "Oldboy", "Parasite", "Roma"]
    assert titles(sort="missing") == ["Oldboy", "Parasite", "Roma", "Incendies"]
    assert titles(sort="invented") == titles()
    world.library.upsert_movie("/m/Zeta", "/m/Zeta/z.mkv", "Zeta", 2024, None, True)
    assert set(titles(sort="added")) == {"Incendies", "Oldboy", "Parasite", "Roma", "Zeta"}

    page = api.get("/movies", params={"show": "complete", "q": "oldboy"}).json()
    # a contagem acompanha a busca, não o filtro escolhido
    assert page["counts"] == {"all": 1, "missing": 1, "complete": 0, "queued": 1}
    assert page["rows"] == [] and page["total"] == 0
    assert api.get("/movies").json()["counts"] == {"all": 5, "missing": 4, "complete": 1,
                                                   "queued": 1}  # fmt: skip
    shows = api.get("/shows").json()
    assert shows["counts"] == {"all": 2, "missing": 1, "complete": 1, "queued": 1}


def test_the_top_search_finds_movies_and_shows(world: World, api: TestClient) -> None:
    found = api.get("/search", params={"q": "PARA"}).json()
    assert [m["title"] for m in found["movies"]] == ["Parasite"] and found["shows"] == []
    found = api.get("/search", params={"q": "2005"}).json()  # pelo ano também
    assert [s["title"] for s in found["shows"]] == ["Supernatural"]
    assert api.get("/search").json() == {"movies": [], "shows": []}
    assert api.get("/search", params={"q": "  "}).json() == {"movies": [], "shows": []}


def test_monitoring_can_be_set_to_a_value_or_toggled(world: World, api: TestClient) -> None:
    url = f"/library/movie/{world.ids['Roma']}/monitor"
    assert api.post(url, json={"monitored": True}).json() == {"monitored": True}
    assert api.post(url, json={"monitored": True}).json() == {"monitored": True}  # sem alternar
    assert api.post(url).json() == {"monitored": False}  # sem valor: alterna
    assert api.post(url, json={"unknown": 1}).status_code == 422


# --- settings ---


def test_settings_never_send_a_key_back_only_where_it_comes_from(
    world: World, api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TMDB_READ_ACCESS_TOKEN", "tmdb-env-value")
    view = api.get("/settings")
    assert "radarr-key" not in view.text and "tmdb-env-value" not in view.text
    secrets = view.json()["secrets"]
    assert (
        secrets["RADARR_API_KEY"] == "settings"
        and secrets["TMDB_READ_ACCESS_TOKEN"] == "environment"
    )
    assert secrets["SONARR_API_KEY"] == ""
    status = api.get("/system/status")
    assert "radarr-key" not in status.text and "tmdb-env-value" not in status.text


def test_saving_refuses_unknown_fields_and_a_malformed_arr(world: World, api: TestClient) -> None:
    def save(**changes: Any) -> tuple[int, str]:
        response = api.patch("/settings", json={"changes": changes})
        return response.status_code, response.json()["message"]

    assert save(invented=1) == (422, "Not saved, unknown: invented")
    assert save(radarr={"invented": 1}) == (422, "Not saved, check: radarr")
    assert save(radarr={"port": "abc"}) == (422, "Not saved, check: radarr")
    assert save(radarr={"path_mappings": ["only-one"]}) == (422, "Not saved, check: radarr")
    assert save(theme="robo") == (200, "Saved.")
    assert api.patch("/settings", json={"invented": {}}).status_code == 422  # corpo fora do formato
    assert world.library.config().radarr.host == "radarr.test"  # nada mudou


def test_the_last_connection_test_is_kept_until_what_it_tested_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from c3parr.providers.base import Health

    class FakeTMDb:
        def __init__(self, config: Any) -> None:
            self.good = config.token.get_secret_value() == "good"

        def health(self) -> Health:
            return Health(ok=self.good, detail="TMDb rejected the key (401)")

    monkeypatch.setattr(web, "TMDbProvider", FakeTMDb)
    world = empty_world(tmp_path)
    with TestClient(world.app, base_url="http://testserver/api/v1") as api:
        assert api.get("/settings").json()["tests"] == {}
        bad = api.post("/settings/test/tmdb", json={"secrets": {"TMDB_READ_ACCESS_TOKEN": "bad"}})
        assert bad.json()["ok"] is False and isinstance(bad.json()["ms"], int)
        kept = api.get("/settings").json()["tests"]["tmdb"]
        assert (kept["ok"], kept["message"]) == (False, "TMDb: TMDb rejected the key (401)")

        good = api.post("/settings/test/tmdb", json={"secrets": {"TMDB_READ_ACCESS_TOKEN": "good"}})
        assert good.json()["ok"] is True and good.json()["message"] == "TMDb: connected."
        assert api.get("/settings").json()["tests"]["tmdb"]["ok"] is True

        api.patch("/settings", json={"changes": {"wait_minutes": 5}})  # outra coisa: o teste vale
        assert "tmdb" in api.get("/settings").json()["tests"]
        api.patch("/settings", json={"secrets": {"TMDB_READ_ACCESS_TOKEN": "good"}})
        assert api.get("/settings").json()["tests"] == {}  # a chave mudou: o teste não vale mais
        assert api.post("/settings/test/invented").status_code == 404


# --- a interface nova servida pelo app ---


def test_the_app_serves_the_built_interface_and_keeps_the_api_apart(tmp_path: Path) -> None:
    from uikit import fake_runner, settings_for

    from c3parr.jobs.queue import JobQueue
    from c3parr.library.store import Library
    from c3parr.web.app import create_app

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>the interface</html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (dist / "sw.js").write_text("// service worker")
    (dist / "manifest.webmanifest").write_text("{}")
    (tmp_path / "secret.txt").write_text("outside the build")
    app = create_app(settings=settings_for(tmp_path), queue=JobQueue(tmp_path / "fila.db"),
                     runner=fake_runner(), start_worker=False,
                     library=Library(tmp_path / "c3parr.db"), ui="spa", dist=dist)  # fmt: skip
    with TestClient(app) as client:
        # qualquer endereço de página devolve a interface: as rotas são resolvidas no navegador
        for path in ("/", "/dashboard", "/movies", "/tv/3", "/settings/library/radarr", "/nope"):
            page = client.get(path)
            assert page.status_code == 200 and page.text == "<html>the interface</html>", path
            assert page.headers["cache-control"] == "no-cache"  # sempre a versão atual
        assert client.get("/assets/app.js").text == "console.log(1)"
        worker = client.get("/sw.js")
        assert worker.text == "// service worker" and worker.headers["cache-control"] == "no-cache"
        manifest = client.get("/manifest.webmanifest")
        assert manifest.headers["content-type"].startswith("application/manifest+json")

        # a API e os arquivos continuam o que são: o que não existe é 404, não a página
        assert client.get("/api/v1/shell").json()["pending"] == 0
        assert client.get("/health").json() == {"ok": True}
        for path in ("/api/v1/nope", "/api/nope", "/assets/nope.js", "/static/nope.png"):
            assert client.get(path).status_code == 404, path
        # nada fora da pasta do build sai por aqui
        for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt"):
            assert "outside the build" not in client.get(path).text, path
