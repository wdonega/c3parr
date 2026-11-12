"""O que a interface faz, conferido pelo resultado: banco, fila, alertas.

Cada cenário fala só com o driver (`ui.do`), em termos do domínio. Nasceram rodando também
contra as páginas HTML antigas, para provar que a API fazia o mesmo que elas.
"""

import shutil
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from fastapi.testclient import TestClient
from uikit import ApiDriver, World, empty_world, seed_world

from c3parr.asr.remote import REMOTE_IDS
from c3parr.core.config import Settings
from c3parr.jobs.queue import CANCEL_REQUESTED
from c3parr.library.arr import ArrConfig
from c3parr.web import app as web
from c3parr.web.app import components_for, effective_secrets

PILOT = "Supernatural - S01E01 - Pilot.mkv"
WENDIGO = "Supernatural - S01E02 - Wendigo.mkv"
DYING = "Supernatural - S02E01 - In My Time of Dying.mkv"


class Ui(NamedTuple):
    do: ApiDriver
    world: World


@pytest.fixture
def connect() -> Iterator[Callable[[World], Ui]]:
    """Abre a interface (a API JSON) sobre um mundo."""
    with ExitStack() as stack:

        def open_(world: World) -> Ui:
            return Ui(ApiDriver(stack.enter_context(TestClient(world.app))), world)

        yield open_


@pytest.fixture
def fresh(tmp_path: Path, connect: Callable[[World], Ui]) -> Ui:
    """Instalação nova, sem nada configurado."""
    return connect(empty_world(tmp_path))


@pytest.fixture
def seeded(tmp_path: Path, connect: Callable[[World], Ui]) -> Ui:
    """A biblioteca de exemplo (`uikit.seed_world`)."""
    return connect(seed_world(tmp_path))


def _by_title(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {r["title"]: r for r in rows}


# --- Settings → Library ---


def test_saving_one_arr_stores_it_and_asks_for_its_sync(fresh: Ui) -> None:
    lib = fresh.world.library
    saved = fresh.do.save_library(
        "radarr", enabled=True, host="radarr.test", port=7878, api_key="rk", monitor_new=False,
        path_mappings=[("/data/movies", "/mnt/movies")],
    )  # fmt: skip
    assert saved.ok
    radarr = lib.config().radarr
    assert radarr.enabled and radarr.host == "radarr.test" and radarr.port == 7878
    assert radarr.path_mappings == (("/data/movies", "/mnt/movies"),)
    assert lib.secrets()["RADARR_API_KEY"] == "rk" and not lib.config().monitor_new
    assert lib.task("sync_radarr").run_now  # gravou: lê o Radarr de novo
    assert lib.config().sonarr.host == "" and not lib.task("sync_sonarr").run_now


def test_arr_options_are_parsed_and_incomplete_mappings_dropped(fresh: Ui) -> None:
    lib = fresh.world.library
    saved = fresh.do.save_library(
        "sonarr", enabled=True, host="https://sonarr.example.com", port=None, ssl=True,
        timeout_s=120, only_monitored=True, excluded_tags=["skip", " kids "],
        excluded_series_types=["anime", "invented"], exclude_season_zero=True,
        path_mappings=[("/tv", "/mnt/tv"), ("/only-remote", "")], sync_interval_minutes=30,
    )  # fmt: skip
    assert saved.ok
    sonarr = lib.config().sonarr
    assert (sonarr.port, sonarr.ssl, sonarr.timeout_s, sonarr.only_monitored) == (
        None, True, 120, True)  # fmt: skip
    assert sonarr.excluded_tags == ("skip", "kids")
    assert sonarr.excluded_series_types == ("anime",)  # tipo desconhecido não entra
    assert sonarr.exclude_season_zero and sonarr.path_mappings == (("/tv", "/mnt/tv"),)
    assert lib.config().sync_interval_minutes == 30


def test_changing_one_field_keeps_the_rest_and_a_disabled_arr_is_not_synced(seeded: Ui) -> None:
    lib = seeded.world.library
    before = lib.config().radarr
    assert seeded.do.save_library("radarr", enabled=False).ok
    after = lib.config().radarr
    assert not after.enabled
    assert after.model_copy(update={"enabled": True}) == before  # host, tags e caminhos ficam
    assert lib.secrets()["RADARR_API_KEY"] == "radarr-key"
    assert not lib.task("sync_radarr").run_now


def test_sync_interval_below_the_minimum_is_refused(fresh: Ui) -> None:
    refused = fresh.do.save_library("radarr", sync_interval_minutes=1)
    assert not refused.ok and refused.status == 422
    assert "sync_interval_minutes" in refused.message
    assert fresh.world.library.config().sync_interval_minutes == 60


# --- Settings → Languages, Subtitles, Engines ---


def test_languages_are_normalized_deduplicated_and_one_target_is_required(fresh: Ui) -> None:
    lib = fresh.world.library
    assert fresh.do.save_languages(["pt-BR", "es", "es"], ["en", "FR", "", "en"]).ok
    assert lib.config().target_languages == ("pt-BR", "es")
    assert list(lib.config().fallback_languages) == ["en", "fr"]
    refused = fresh.do.save_languages([], ["en"])
    assert not refused.ok and refused.status == 422
    assert lib.config().target_languages == ("pt-BR", "es")


def test_subtitle_rules_are_saved_and_bad_numbers_refused(fresh: Ui) -> None:
    lib = fresh.world.library
    saved = fresh.do.save_subtitles(
        use_embedded=False, forced_resolves=True, asr_always=True, wait_minutes=0,
        search_interval_minutes=30, derived_on_main_change="mark", foreign_tag="off",
    )  # fmt: skip
    assert saved.ok
    cfg = lib.config()
    assert (cfg.use_embedded, cfg.forced_resolves, cfg.asr_always) == (False, True, True)
    assert cfg.embedded_resolves and cfg.asr_enabled  # o que não foi mexido fica
    assert (cfg.wait_minutes, cfg.search_interval_minutes) == (0, 30)
    assert (cfg.derived_on_main_change, cfg.foreign_tag) == ("mark", "off")

    refused = fresh.do.save_subtitles(wait_minutes=-1)
    assert not refused.ok and refused.status == 422 and "wait_minutes" in refused.message
    assert lib.config().wait_minutes == 0


def test_engines_accept_only_what_each_interface_offers(fresh: Ui) -> None:
    lib = fresh.world.library
    assert fresh.do.save_engines(sincronizacao="tempo", fonte_legenda="invented").ok
    assert lib.config().components.get("sincronizacao") == "tempo"
    assert "fonte_legenda" not in lib.config().components
    assert components_for(Settings(), lib.config())["sincronizacao"].provider == "tempo"


# --- Settings → Integrations ---


def test_keys_are_stored_unless_the_environment_already_has_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, connect: Callable[[World], Ui]
) -> None:
    monkeypatch.setenv("TMDB_READ_ACCESS_TOKEN", "tmdb-env")
    ui = connect(empty_world(tmp_path))
    lib = ui.world.library
    assert ui.do.secret_state()["TMDB_READ_ACCESS_TOKEN"] == "environment"
    saved = ui.do.save_integrations(
        llm_provider="anthropic", secrets={"ANTHROPIC_API_KEY": "sk-ant-test"}
    )
    assert saved.ok
    assert lib.secrets() == {"ANTHROPIC_API_KEY": "sk-ant-test"}  # o do .env não é copiado
    assert ui.do.secret_state()["ANTHROPIC_API_KEY"] == "settings"
    assert lib.config().llm_provider == "anthropic"
    assert effective_secrets(lib)["C3PARR_LLM_API_KEY"] == "sk-ant-test"
    llm = components_for(Settings(), lib.config())["traducao"].params["llm"]
    assert (llm["flavor"], llm["model"]) == ("anthropic", "claude-haiku-5-5")

    assert ui.do.save_integrations(secrets={"ANTHROPIC_API_KEY": ""}).ok
    assert lib.secrets() == {}  # campo esvaziado apaga a chave salva


def test_model_and_url_equal_to_the_default_are_stored_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, connect: Callable[[World], Ui]
) -> None:
    monkeypatch.setenv("C3PARR_LLM_BASE_URL", "http://box:8080/v1")
    monkeypatch.setenv("C3PARR_LLM_MODEL", "qwen-env")
    ui = connect(empty_world(tmp_path))
    lib = ui.world.library
    assert ui.do.save_integrations().ok  # salvar sem mexer: o que vem do .env continua de lá
    assert (lib.config().llm_base_url, lib.config().llm_model) == ("", "")
    typed = ui.do.save_integrations(llm_base_url="http://box:8080/v1", llm_model="qwen-env")
    assert typed.ok  # digitar o mesmo que o padrão também não grava
    assert (lib.config().llm_base_url, lib.config().llm_model) == ("", "")
    assert ui.do.save_integrations(llm_provider="openai").ok  # leva o modelo padrão do novo
    assert (lib.config().llm_provider, lib.config().llm_model) == ("openai", "")
    assert ui.do.save_integrations(llm_model="gpt-4.1-mini").ok  # o padrão da OpenAI
    assert lib.config().llm_model == ""
    assert ui.do.save_integrations(llm_model="gpt-5-mini").ok
    assert lib.config().llm_model == "gpt-5-mini"
    assert ui.do.save_integrations(llm_provider="deepseek").ok  # modelo escolhido à mão fica
    assert (lib.config().llm_provider, lib.config().llm_model) == ("deepseek", "gpt-5-mini")


def test_effort_only_reaches_the_providers_that_have_it(fresh: Ui) -> None:
    lib = fresh.world.library
    assert lib.config().llm_effort == "low"
    assert fresh.do.save_integrations(llm_provider="gemini", llm_effort="medium").ok
    llm = components_for(Settings(), lib.config())["traducao"].params["llm"]
    assert (llm["flavor"], llm["model"], llm["effort"]) == (
        "gemini", "gemini-flash-latest", "medium")  # fmt: skip
    assert fresh.do.save_integrations(llm_provider="deepseek").ok
    assert components_for(Settings(), lib.config())["traducao"].params["llm"]["effort"] is None
    assert fresh.do.save_integrations(llm_effort="invented").ok  # valor desconhecido: fica o salvo
    assert lib.config().llm_effort == "medium"


def test_a_transcription_server_needs_its_url(fresh: Ui) -> None:
    lib = fresh.world.library
    refused = fresh.do.save_integrations(asr_mode="openai")
    assert not refused.ok and refused.status == 422
    assert lib.config().asr_mode == "local"
    saved = fresh.do.save_integrations(
        asr_mode="openai", asr_url="http://whisper:9000/v1", asr_model="large-v3",
        secrets={"C3PARR_ASR_API_KEY": "asr-key"},
    )  # fmt: skip
    assert saved.ok
    asr = components_for(Settings(), lib.config())["transcricao"]
    assert asr.provider == REMOTE_IDS["openai"]
    assert asr.params == {"base_url": "http://whisper:9000/v1", "model": "large-v3"}
    assert lib.secrets()["C3PARR_ASR_API_KEY"] == "asr-key"


# --- botões de teste ---


def test_connection_tests_use_what_is_on_screen_and_save_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, connect: Callable[[World], Ui]
) -> None:
    from c3parr.providers.base import Health

    seen: dict[str, str] = {}

    class FakeTMDb:
        def __init__(self, config: Any) -> None:
            seen["token"] = config.token.get_secret_value()

        def health(self) -> Health:
            return Health(ok=seen["token"] == "good", detail="TMDb rejected the key (401)")

    class FakeLLM:
        def __init__(self, config: Any) -> None:
            seen["llm"] = f"{config.flavor} {config.base_url} {config.model}"
            seen["llm_key"] = config.api_key.get_secret_value() if config.api_key else ""

        def health(self) -> tuple[bool, str]:
            return True, "ok"

    monkeypatch.setenv("C3PARR_LLM_MODEL", "qwen-env")
    monkeypatch.setattr(web, "TMDbProvider", FakeTMDb)
    monkeypatch.setattr(web, "make_llm_client", FakeLLM)
    ui = connect(empty_world(tmp_path))
    lib = ui.world.library

    assert not ui.do.test_connection("tmdb").ok  # sem chave nenhuma
    bad = ui.do.test_connection("tmdb", secrets={"TMDB_READ_ACCESS_TOKEN": "bad"})
    assert not bad.ok and "rejected the key" in bad.message
    assert ui.do.test_connection("tmdb", secrets={"TMDB_READ_ACCESS_TOKEN": "good"}).ok

    ok = ui.do.test_connection("llm", llm_base_url="http://box:8080/v1",
                               secrets={"C3PARR_LLM_API_KEY": "typed"})  # fmt: skip
    assert ok.ok
    assert (
        seen["llm"] == "custom http://box:8080/v1 qwen-env"
    )  # o modelo do .env, que a tela mostra
    assert seen["llm_key"] == "typed"
    assert lib.config().llm_base_url == "" and lib.secrets() == {}  # testar não salva nada


def test_llm_test_without_the_provider_key_fails(fresh: Ui) -> None:
    refused = fresh.do.test_connection("llm", llm_provider="openai")
    assert not refused.ok and "no API key for openai" in refused.message


def test_arr_test_reports_the_version_and_an_old_major(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, connect: Callable[[World], Ui]
) -> None:
    class FakeClient:
        def __init__(self, kind: str, cfg: ArrConfig, key: str) -> None:
            assert (kind, cfg.host, cfg.port, key) == ("radarr", "radarr.test", 7878, "rk")

        def version(self) -> str:
            return "5.2.0.1"

        def close(self) -> None:
            pass

    monkeypatch.setattr(web, "ArrClient", FakeClient)
    ui = connect(empty_world(tmp_path))
    tested = ui.do.test_connection("radarr", host="radarr.test", api_key="rk")
    assert tested.ok and "5.2.0.1" in tested.message and "latest is 6" in tested.message
    assert ui.world.library.config().radarr.host == ""  # não salvou


def test_remote_transcription_test_asks_the_typed_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, connect: Callable[[World], Ui]
) -> None:
    from c3parr.providers.base import Health

    seen: dict[str, str] = {}

    class FakeRemote:
        def __init__(self, config: Any, flavor: str) -> None:
            seen["asr"] = f"{flavor} {config.base_url} {config.model}"

        def health(self) -> Health:
            return Health(ok=True, detail="large-v3 loaded")

    monkeypatch.setattr(web, "RemoteTranscriber", FakeRemote)
    ui = connect(empty_world(tmp_path))
    tested = ui.do.test_connection("asr", asr_mode="asr-webservice", asr_url="http://w:9000")
    assert tested.ok and "large-v3 loaded" in tested.message
    assert seen["asr"] == "asr-webservice http://w:9000 whisper-1"
    assert ui.world.library.config().asr_mode == "local"


# --- o que falta configurar ---


def test_a_fresh_install_lists_what_to_set_up_until_it_is_done(fresh: Ui) -> None:
    do = fresh.do
    assert do.setup() == ["Connect Radarr or Sonarr", "No LLM configured", "No TMDb key"]
    do.alerts()
    assert do.alerts_badge() == 3  # abrir Alerts não faz sumir o que falta configurar

    do.save_library("radarr", enabled=True, host="radarr.test", port=7878)
    assert do.setup()[0] == "Radarr needs its address and API key"
    do.save_library("radarr", api_key="rk")
    do.save_integrations(llm_base_url="http://box:8080/v1", llm_model="q",
                         secrets={"TMDB_READ_ACCESS_TOKEN": "t"})  # fmt: skip
    assert do.setup() == [] and do.alerts_badge() == 0

    do.save_integrations(llm_provider="openai")
    assert do.setup() == ["No API key for the LLM (openai)"]


def test_the_builtin_tmdb_key_counts_as_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, connect: Callable[[World], Ui]
) -> None:
    from c3parr.metadata import tmdb

    monkeypatch.setattr(tmdb, "BUILTIN_READ_TOKEN", "builtin")
    ui = connect(empty_world(tmp_path))
    assert "No TMDb key" not in ui.do.setup()
    assert ui.do.secret_state()["TMDB_READ_ACCESS_TOKEN"] == ""  # a embutida não aparece no campo


def test_an_old_radarr_is_a_standing_warning_until_updated(seeded: Ui) -> None:
    assert seeded.do.warnings() == ["Radarr 5 is out of date"]
    seeded.world.library.set_meta("radarr_version", "6.4.4")
    assert seeded.do.warnings() == []


# --- biblioteca ---


def test_library_shows_each_subtitle_by_source_and_what_is_missing(seeded: Ui) -> None:
    movies = _by_title(seeded.do.movies()["rows"])
    assert list(movies) == ["Incendies", "Oldboy", "Parasite", "Roma"]
    incendies = movies["Incendies"]  # a forced em francês não conta
    assert incendies["subs"] == [["en", "embedded"], ["es", "external"], ["pt-BR", "external"]]
    assert incendies["missing"] == [] and incendies["job"] is None
    # pt (Portugal) não cobre pt-BR: são alvos distintos
    assert movies["Parasite"]["subs"] == [["pt", "external"], ["pt-BR", "ours"]]
    assert movies["Parasite"]["missing"] == ["es"]
    assert movies["Oldboy"]["missing"] == ["pt-BR", "es"]
    assert movies["Oldboy"]["job"] == {"label": "Translate → pt-BR", "status": "running"}
    assert not movies["Roma"]["monitored"] and movies["Incendies"]["monitored"]

    shows = _by_title(seeded.do.shows()["rows"])
    spn = shows["Supernatural"]
    assert (spn["total"], spn["done"], spn["queued"]) == (4, 1, True)
    assert spn["missing"] == 2  # sem legenda e sem tarefa na fila: o S02E01 já está nela
    assert (shows["Dark"]["total"], shows["Dark"]["done"], shows["Dark"]["missing"]) == (1, 1, 0)


def test_library_search_filters_and_pages(seeded: Ui) -> None:
    lib, do = seeded.world.library, seeded.do
    for n in range(101):
        lib.upsert_movie(f"/movies/Filme {n:03d}", f"/movies/Filme {n:03d}/f.mkv",
                         f"Filme {n:03d}", 2000, None, True)  # fmt: skip
    first = do.movies()
    assert (first["total"], first["page"], first["pages"], len(first["rows"])) == (105, 1, 2, 100)
    assert len(do.movies(page=2)["rows"]) == 5
    assert do.movies(page=9)["page"] == 2  # além do fim: a última

    assert [r["title"] for r in do.movies(q="INCÉND")["rows"]] == ["Incendies"]  # sem acento, caixa
    assert [r["title"] for r in do.movies(show="complete")["rows"]] == ["Incendies"]
    assert [r["title"] for r in do.movies(show="queued")["rows"]] == ["Oldboy"]
    assert do.movies(show="missing")["total"] == 104  # todos menos o Incendies
    missing = [r["title"] for page in (1, 2) for r in do.movies(show="missing", page=page)["rows"]]
    assert "Incendies" not in missing and "Parasite" in missing and "Filme 000" in missing
    assert do.movies(q="nada")["total"] == 0

    assert [r["title"] for r in do.shows(q="super")["rows"]] == ["Supernatural"]
    assert [r["title"] for r in do.shows(show="missing")["rows"]] == ["Supernatural"]
    assert [r["title"] for r in do.shows(show="complete")["rows"]] == ["Dark"]
    assert [r["title"] for r in do.shows(show="queued")["rows"]] == ["Supernatural"]


def test_a_show_lists_seasons_in_order_with_loose_files_last(seeded: Ui) -> None:
    show = seeded.do.show(seeded.world.ids["Supernatural"])
    assert show is not None
    assert (show["title"], show["year"], show["monitored"]) == ("Supernatural", 2005, True)
    assert [s["season"] for s in show["seasons"]] == [1, 2, None]
    first = show["seasons"][0]["episodes"]
    assert [(e["episode"], e["name"]) for e in first] == [(1, PILOT), (2, WENDIGO)]
    assert first[0]["missing"] == [] and first[1]["missing"] == ["pt-BR", "es"]
    queued = show["seasons"][1]["episodes"][0]
    assert queued["name"] == DYING
    assert queued["job"] == {"label": "Translate → pt-BR", "status": "queued"}
    assert seeded.do.show(999) is None


def test_monitoring_toggles_for_movies_and_shows_only(seeded: Ui) -> None:
    do, ids, lib = seeded.do, seeded.world.ids, seeded.world.library
    assert do.toggle_monitor("movie", ids["Roma"]) is True
    assert do.toggle_monitor("movie", ids["Roma"]) is False
    assert do.toggle_monitor("series", ids["Dark"]) is False
    assert not lib.series(ids["Dark"]).monitored
    assert do.toggle_monitor("movie", 999) is None
    assert do.toggle_monitor("episode", 1) is None  # episódio segue a série


def test_adding_to_the_queue_decides_per_item_and_never_duplicates(
    fresh: Ui, tiny_video: Path
) -> None:
    root, lib, queue, do = fresh.world.root, fresh.world.library, fresh.world.queue, fresh.do
    movie = root / "movies" / "Incendies (2010)" / "Incendies.mkv"
    episode = root / "tv" / "Show" / "Season 1" / "Show S01E01.mkv"
    for f in (movie, episode):
        f.parent.mkdir(parents=True)
        shutil.copy(tiny_video, f)
    lib.upsert_movie(str(movie.parent), str(movie), "Incendies", 2010, None, True)
    show_id = lib.upsert_series(str(episode.parents[1]), "Show", None, None, True)
    lib.upsert_episode(show_id, str(episode), 1, 1)
    ids = {m.title: m.id for m in lib.movies()}

    assert not do.add_to_queue().ok and queue.pending_count() == 0  # nada escolhido
    assert do.add_to_queue(f"movie:{ids['Incendies']}", f"series:{show_id}").ok
    assert queue.pending_count() == 2  # um passo por item (o primeiro: metadados)
    assert do.add_to_queue(f"movie:{ids['Incendies']}").ok
    assert queue.pending_count() == 2  # pedir de novo não duplica
    assert {j["priority"] for j in do.queue()["rows"]} == {True}  # pedido manual passa na frente

    broken = root / "movies" / "Broken (2000)" / "Broken.mkv"
    broken.parent.mkdir()
    broken.write_bytes(b"x")
    lib.upsert_movie(str(broken.parent), str(broken), "Broken", 2000, None, True)
    bid = next(m.id for m in lib.movies() if m.title == "Broken")
    assert not do.add_to_queue(f"movie:{bid}").ok  # não deu para ler o arquivo
    assert fresh.world.catalog.unseen_alerts() == 1
    assert do.add_to_queue("movie:999").status == 404
    assert do.add_to_queue("invented:1").status == 404


# --- fila ---


def test_the_queue_lists_newest_first_and_filters_by_text_and_status(seeded: Ui) -> None:
    do, ids = seeded.do, seeded.world.ids
    everything = do.queue()
    assert [r["id"] for r in everything["rows"]] == sorted(ids[k] for k in (
        "done", "warned", "failed", "cancelled", "running", "queued"))[::-1]  # fmt: skip
    assert do.pending() == 2  # rodando e na fila
    by_status = {name: [r["id"] for r in do.queue(status=name)["rows"]]
                 for name in ("running", "queued", "done", "failed", "cancelled")}  # fmt: skip
    assert by_status == {"running": [ids["running"]], "queued": [ids["queued"]],
                         "done": [ids["warned"], ids["done"]], "failed": [ids["failed"]],
                         "cancelled": [ids["cancelled"]]}  # fmt: skip
    assert [r["id"] for r in do.queue(q="wendigo")["rows"]] == [ids["failed"]]
    assert [r["id"] for r in do.queue(q="fora do ar")["rows"]] == [ids["failed"]]  # pelo erro
    assert do.queue(q="nada")["total"] == 0
    running = do.queue(status="running")["rows"][0]
    assert (running["step"], running["progress"], running["message"], running["priority"]) == (
        "Translate → pt-BR", 0.47, "block 44 of 94", True)  # fmt: skip


def test_the_queue_pages_and_falls_back_to_the_last_page(fresh: Ui) -> None:
    queue, do = fresh.world.queue, fresh.do
    for n in range(105):
        queue.submit(f"/tv/Show/Show S01E{n:03d}.mkv", "pt-BR")
    first = do.queue()
    assert (first["total"], first["pages"], len(first["rows"])) == (105, 2, 100)
    assert [r["name"] for r in do.queue(page=2)["rows"]][-1] == "Show S01E000.mkv"
    assert do.queue(page=9)["page"] == 2


def test_task_details_tell_each_state_apart(seeded: Ui) -> None:
    do, ids = seeded.do, seeded.world.ids
    done = do.job(ids["done"])
    assert done is not None and done["status"] == "done"
    assert done["has_subtitle"] and done["has_report"]
    assert done["steps"] == [
        {"name": "audio", "label": "Audio", "state": "done", "text": "done · 3 s",
         "seconds": 3.0, "provider": None, "reason": None},
        {"name": "transcricao", "label": "Transcription", "state": "skipped",
         "text": "skipped · já há legenda", "seconds": None, "provider": None,
         "reason": "já há legenda"},
        {"name": "traducao", "label": "Translation", "state": "cached",
         "text": "from cache · llm", "seconds": None, "provider": "llm", "reason": None},
    ]  # fmt: skip
    warned = do.job(ids["warned"])
    assert warned is not None and warned["status"] == "done_with_warnings"
    assert warned["warnings"] == [{"stage": "metadados", "reason": "TMDb não achou",
                                   "effect": "nomes sem conferir"}]  # fmt: skip
    assert warned["steps"][0]["state"] == "warning"  # falhou, mas só degradou o resultado
    failed = do.job(ids["failed"])
    assert failed is not None and failed["error"] == "LLM fora do ar"

    running = do.job(ids["running"])
    assert running is not None
    states = [s["state"] for s in running["steps"]]
    current = states.index("running")
    assert running["steps"][current] == {
        "name": "traducao", "label": "Translation", "state": "running", "text": "block 44 of 94",
        "seconds": None, "provider": None, "reason": "block 44 of 94"}  # fmt: skip
    assert set(states[:current]) == {"done"} and set(states[current + 1 :]) == {"pending"}
    queued = do.job(ids["queued"])
    assert queued is not None and {s["state"] for s in queued["steps"]} == {"pending"}
    assert do.job(999) is None


def test_cancel_and_run_again(seeded: Ui) -> None:
    do, ids, queue = seeded.do, seeded.world.ids, seeded.world.queue
    assert do.cancel(ids["queued"]) == 200
    assert queue.get(ids["queued"]).status == "cancelada"  # na fila: sai na hora
    assert do.cancel(ids["running"]) == 200
    running = queue.get(ids["running"])  # rodando: para entre uma etapa e outra
    assert running.status == "processando" and running.mensagem == CANCEL_REQUESTED
    assert do.cancel(ids["done"]) == 200 and queue.get(ids["done"]).status == "concluida"

    assert do.retry(ids["failed"]) == 200
    again = queue.get(ids["failed"])
    assert (again.status, again.erro, again.progresso) == ("na_fila", None, 0.0)
    assert do.retry(ids["running"]) == 200 and queue.get(ids["running"]).status == "processando"
    assert do.cancel(999) == 404 and do.retry(999) == 404


def test_subtitle_and_report_downloads(seeded: Ui) -> None:
    do, ids = seeded.do, seeded.world.ids
    status, text = do.download(ids["done"], "subtitle")
    assert status == 200 and "Hola" in text
    status, text = do.download(ids["done"], "report")
    assert status == 200 and "relatório" in text
    assert do.download(ids["failed"], "subtitle")[0] == 404  # ainda não há legenda
    assert do.download(ids["failed"], "report")[0] == 404
    assert do.download(999, "subtitle")[0] == 404


# --- System ---


def test_running_a_task_asks_the_scheduler(seeded: Ui) -> None:
    do, lib = seeded.do, seeded.world.library
    tasks = do.tasks()
    assert list(tasks) == ["sync_radarr", "sync_sonarr", "search_missing"]
    assert tasks["sync_radarr"] == {"label": "Sync with Radarr", "interval": "1 h",
                                    "result": "4 movies, 0 new", "running": False,
                                    "requested": False}  # fmt: skip
    assert tasks["sync_sonarr"]["requested"] and tasks["search_missing"]["interval"] == "6 h"
    assert do.run_task("search_missing").ok
    assert lib.task("search_missing").run_now and do.tasks()["search_missing"]["requested"]
    assert do.run_task("invented").status == 404


def test_alerts_are_seen_when_opened_and_retried_on_request(seeded: Ui) -> None:
    do, catalog = seeded.do, seeded.world.catalog
    assert do.alerts_badge() == 3  # 1 alerta novo + 2 coisas por configurar
    alerts = do.alerts()
    assert [(a["key"], a["count"], a["seen"], a["resolved"], a["items"]) for a in alerts] == [
        ("traduzir:pt-BR:llm", 2, False, False, [WENDIGO]),
        ("probe:old", 1, False, True, ["Roma.mkv"]),
    ]
    assert do.alerts_badge() == 2 and catalog.unseen_alerts() == 0  # abriu a página: viu

    assert do.retry_alert("probe:old").ok  # já resolvido: nada a fazer
    retried = do.retry_alert("traduzir:pt-BR:llm")  # o arquivo do exemplo não é um vídeo de fato
    assert not retried.ok and catalog.unseen_alerts() == 1
