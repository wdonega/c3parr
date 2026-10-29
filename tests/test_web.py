"""Fila FIFO e interface web com um runner falso (sem modelos)."""

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from c3parr.core.models import Degradation
from c3parr.engine.engine import RunResult, StepReport
from c3parr.jobs.queue import Job, JobQueue, Worker
from c3parr.library.sidecars import index_subtitles
from c3parr.library.store import Library
from c3parr.web.app import create_app


def fake_runner(outcome: str = "ok") -> Callable[..., tuple[RunResult, str | None, str]]:
    def run(
        job: Job, progress: Callable[[str, float, str], None], cancelled: Callable[[], bool]
    ) -> tuple[RunResult, str | None, str]:
        progress("transcricao", 0.5, "metade")
        result = RunResult(recipe="completa", workdir=Path(job.video).parent)
        result.steps.append(StepReport(name="audio", status="ok", seconds=1.0))
        if outcome == "falha":
            result.failed = StepReport(name="traducao", status="falhou", reason="LLM fora do ar")
            result.steps.append(result.failed)
        elif outcome == "aviso":
            result.degradations.append(Degradation(stage="metadados", reason="TMDb não achou"))
        return result, None, str(Path(job.video).with_suffix(".review.md"))

    return run


@pytest.fixture
def queue(tmp_path: Path) -> JobQueue:
    return JobQueue(tmp_path / "fila.db")


def test_queue_is_fifo(queue: JobQueue) -> None:
    a, b = queue.submit("/m/a.mkv", "pt-BR"), queue.submit("/m/b.mkv", "pt-BR")
    first = queue.take_next()
    assert first is not None and first.id == a.id and first.status == "processando"
    second = queue.take_next()
    assert second is not None and second.id == b.id
    assert queue.take_next() is None


@pytest.mark.parametrize(
    ("outcome", "status"),
    [("ok", "concluida"), ("aviso", "concluida_avisos"), ("falha", "falhou")],
)
def test_worker_records_final_status(queue: JobQueue, outcome: str, status: str) -> None:
    job = queue.submit("/m/a.mkv", "pt-BR")
    done = Worker(queue, fake_runner(outcome)).run_once()
    assert done is not None and done.id == job.id
    assert done.status == status
    assert done.etapas[0].name == "audio"
    if outcome == "falha":
        assert done.erro == "LLM fora do ar"


def test_restart_puts_interrupted_job_back_in_queue(tmp_path: Path) -> None:
    q = JobQueue(tmp_path / "fila.db")
    q.submit("/m/a.mkv", "pt-BR")
    q.take_next()
    again = JobQueue(tmp_path / "fila.db")
    assert again.list()[0].status == "na_fila"


def test_cancel_queued_job(queue: JobQueue) -> None:
    job = queue.submit("/m/a.mkv", "pt-BR")
    queue.cancel(job.id)
    assert queue.get(job.id).status == "cancelada"
    assert queue.take_next() is None


def test_web_add_list_and_detail(tmp_path: Path, queue: JobQueue) -> None:
    media = tmp_path / "Filme (2010).mkv"
    media.write_bytes(b"x")
    app = create_app(
        queue=queue,
        runner=fake_runner(),
        start_worker=False,
        library=Library(queue.path.parent / "c3parr.db"),
    )
    with TestClient(app) as client:
        page = client.get("/queue")
        assert page.status_code == 200
        assert "<title>c3parr</title>" in page.text
        assert "/static/themes/robo.css" in page.text

        queue.submit(str(media), "pt-BR")

        rows = client.get("/queue/rows").text
        assert "Filme (2010).mkv" in rows
        assert "Queued" in rows

        Worker(queue, fake_runner("aviso")).run_once()
        detail = client.get("/queue/1").text
        assert "Done with warnings" in detail
        assert "TMDb não achou" in detail
        assert client.get("/health").json() == {"ok": True}


def test_queue_badge_counts_unfinished_jobs(queue: JobQueue) -> None:
    app = create_app(
        queue=queue,
        runner=fake_runner(),
        start_worker=False,
        library=Library(queue.path.parent / "c3parr.db"),
    )
    with TestClient(app) as client:
        assert "badge" not in client.get("/queue/count").text
        queue.submit("/m/a.mkv", "pt-BR")
        queue.submit("/m/b.mkv", "pt-BR")
        Worker(queue, fake_runner()).run_once()
        badge = client.get("/queue/count").text
        assert 'class="badge"' in badge and ">1</div>" in badge


def test_library_pages_settings_and_tasks(
    tmp_path: Path, queue: JobQueue, tiny_video: Path
) -> None:
    movie = tmp_path / "movies" / "Incendies (2010)" / "Incendies.mkv"
    episode = tmp_path / "tv" / "Show" / "Season 1" / "Show S01E01.mkv"
    for f in (movie, episode):
        f.parent.mkdir(parents=True)
        shutil.copy(tiny_video, f)
    lib = Library(tmp_path / "c3parr.db")
    app = create_app(queue=queue, runner=fake_runner(), start_worker=False, library=lib)
    with TestClient(app) as client:
        assert "Sync with Radarr" in client.get("/movies").text
        saved = client.post("/settings/library", data={
            "radarr_enabled": "1", "radarr_host": "radarr.test", "radarr_port": "7878",
            "radarr_map_remote": "/data/movies", "radarr_map_local": str(tmp_path / "movies"),
            "RADARR_API_KEY": "rk", "sync_interval_minutes": "60"})  # fmt: skip
        assert "Saved." in saved.text
        radarr = lib.config().radarr
        assert radarr.enabled and radarr.host == "radarr.test" and radarr.port == 7878
        assert radarr.path_mappings == (("/data/movies", str(tmp_path / "movies")),)
        assert lib.secrets()["RADARR_API_KEY"] == "rk" and not lib.config().monitor_new
        assert lib.task("sync_radarr").run_now  # gravou: lê o Radarr de novo, como o Bazarr
        assert lib.config().sonarr.host == ""  # a aba do Sonarr não foi mexida

        bad = client.post(
            "/settings/subtitles", data={"wait_minutes": "-1", "search_interval_minutes": "60"}
        )
        assert bad.status_code == 422 and "wait_minutes" in bad.text

        # o que o sync grava (o sync em si tem os testes dele, com Radarr/Sonarr de mentira)
        lib.upsert_movie(str(movie.parent), str(movie), "Incendies", 2010, None, True)
        show_id = lib.upsert_series(str(episode.parents[1]), "Show", None, None, True)
        lib.upsert_episode(show_id, str(episode), 1, 1)
        assert "Incendies" in client.get("/movies").text
        # a página lê as legendas do banco: a do disco só aparece depois do sync ou da pipeline
        sub = movie.with_name("Incendies.es.srt")
        sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nHola\n")
        assert "surface-container-highest" not in client.get("/movies").text
        index_subtitles(lib, str(movie))
        sub.unlink()
        assert "surface-container-highest" in client.get("/movies").text
        show = lib.series_list()[0]
        assert "Show S01E01.mkv" in client.get(f"/tv/{show.id}").text

        added = client.post("/library/queue", data={"item": ["movie:1", f"series:{show.id}"]})
        assert "2 item(s) in the queue (2 new step(s))" in added.text
        again = client.post("/library/queue", data={"item": "movie:1"}).text
        assert "1 item(s) in the queue (0 new step(s))" in again  # não duplica
        assert queue.pending_count() == 2
        broken = tmp_path / "movies" / "Broken (2000)" / "Broken.mkv"
        broken.parent.mkdir()
        broken.write_bytes(b"x")
        lib.upsert_movie(str(broken.parent), str(broken), "Broken", 2000, None, True)
        bid = next(m.id for m in lib.movies() if m.title == "Broken")
        bad_read = client.post("/library/queue", data={"item": f"movie:{bid}"}).text
        assert "could not be read" in bad_read
        assert app.state.catalog.unseen_alerts() == 1
        assert "could not read Broken.mkv" in client.get("/system/alerts").text
        assert app.state.catalog.unseen_alerts() == 0  # abriu a página: viu

        monitor = client.post("/library/movie/1/monitor")
        assert "bookmark" in monitor.text
        for page in ("languages", "engines", "subtitles", "integrations"):
            assert client.get(f"/settings/{page}").status_code == 200
        for page in ("tasks", "logs", "status", "alerts"):
            assert client.get(f"/system/{page}").status_code == 200
        assert "Process library" in client.get("/system/tasks").text
        dash = client.get("/dashboard").text
        assert "Pipeline activity" in dash and "Subtitles by language" in dash
        assert client.get("/", follow_redirects=False).headers["location"] == "/dashboard"
        assert client.get("/fila", follow_redirects=False).headers["location"] == "/queue"


def test_arr_test_button_tabs_and_old_version_warning(
    tmp_path: Path, queue: JobQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    from c3parr.library.arr import ArrConfig
    from c3parr.web import app as web

    class FakeClient:
        def __init__(self, kind: str, cfg: ArrConfig, key: str) -> None:
            assert (kind, cfg.host, key) == ("radarr", "radarr.test", "rk")

        def version(self) -> str:
            return "5.2.0.1"

        def close(self) -> None:
            pass

    monkeypatch.setattr(web, "ArrClient", FakeClient)
    lib = Library(tmp_path / "c3parr.db")
    app = create_app(queue=queue, runner=fake_runner(), start_worker=False, library=lib)
    with TestClient(app) as client:
        test = client.post("/settings/test/radarr",
                           data={"radarr_host": "radarr.test", "RADARR_API_KEY": "rk"})  # fmt: skip
        assert "Radarr 5.2.0.1: connected. Update it: the latest is 6." in test.text
        assert client.post("/api/webhook/radarr", json={}).status_code in (404, 405)  # saiu
        page = client.get("/settings/library/sonarr").text
        assert 'name="sonarr_host"' in page and 'name="SONARR_API_KEY"' in page
        assert "Exclude season zero" in page and 'href="/settings/library/radarr"' in page
        assert "Webhook" not in client.get("/settings/integrations").text
        # Radarr fora da versão major mais nova: aviso fixo (não erro) até atualizar
        lib.update_config(radarr=ArrConfig(enabled=True, host="radarr.test"))
        lib.set_meta("radarr_version", "5.2.0.1")
        lib.set_meta("radarr_latest_major", "6")
        assert "Radarr 5 is out of date" in client.get("/system/status").text
        lib.set_meta("radarr_version", "6.4.4")
        assert "out of date" not in client.get("/system/status").text


def test_integrations_keys_come_from_env_or_db_as_password_fields(
    tmp_path: Path, queue: JobQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    from c3parr.core.config import Settings
    from c3parr.web.app import components_for, effective_secrets

    monkeypatch.setenv("TMDB_READ_ACCESS_TOKEN", "tmdb-env")
    lib = Library(tmp_path / "c3parr.db")
    app = create_app(queue=queue, runner=fake_runner(), start_worker=False, library=lib)
    with TestClient(app) as client:
        page = client.get("/settings/integrations").text
        assert 'type="password" name="TMDB_READ_ACCESS_TOKEN"' in page
        assert 'value="tmdb-env"' in page and "(from .env)" in page  # preenchido de verdade
        client.post("/settings/integrations", data={
            "llm_provider": "anthropic", "llm_model": "", "llm_base_url": "",
            "ANTHROPIC_API_KEY": "sk-ant-test", "TMDB_READ_ACCESS_TOKEN": "tmdb-env"})  # fmt: skip
        assert lib.secrets() == {"ANTHROPIC_API_KEY": "sk-ant-test"}  # igual ao .env: não copia
        assert 'value="sk-ant-test"' in client.get("/settings/integrations").text
        assert "sk-ant-test" not in client.get("/system/status").text
        cfg = lib.config()
        assert cfg.llm_provider == "anthropic"
        secrets = effective_secrets(lib)
        assert secrets["C3PARR_LLM_API_KEY"] == "sk-ant-test"
        llm = components_for(Settings(), cfg)["traducao"].params["llm"]
        assert (llm["flavor"], llm["model"]) == ("anthropic", "claude-haiku-5-5")
        client.post(
            "/settings/integrations", data={"llm_provider": "anthropic", "ANTHROPIC_API_KEY": ""}
        )
        assert lib.secrets() == {}  # campo esvaziado apaga a chave salva


def test_languages_targets_as_chips_and_fallback_as_text(tmp_path: Path, queue: JobQueue) -> None:
    lib = Library(tmp_path / "c3parr.db")
    app = create_app(queue=queue, runner=fake_runner(), start_worker=False, library=lib)
    with TestClient(app) as client:
        data = {"target_languages": ["pt-BR", "es", "es"], "fallback_languages": " en, FR ,, en"}
        assert client.post("/settings/languages", data=data).status_code == 200
        config = lib.config()
        assert config.target_languages == ("pt-BR", "es")
        assert list(config.fallback_languages) == ["en", "fr"]
        assert 'value="en, fr"' in client.get("/settings/languages").text
        empty = client.post("/settings/languages", data={"fallback_languages": "en"})
        assert empty.status_code == 422


def test_llm_fields_show_the_values_in_use(
    tmp_path: Path, queue: JobQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, model = "http://box:8080/v1", "qwen-env"
    monkeypatch.setenv("C3PARR_LLM_BASE_URL", url)
    monkeypatch.setenv("C3PARR_LLM_MODEL", model)
    lib = Library(tmp_path / "c3parr.db")
    app = create_app(queue=queue, runner=fake_runner(), start_worker=False, library=lib)
    with TestClient(app) as client:
        page = client.get("/settings/integrations").text
        assert f'name="llm_base_url" value="{url}"' in page
        assert f'value="{model}"' in page and "(from .env)" in page  # o que está em uso
        client.post("/settings/integrations",
                    data={"llm_provider": "custom", "llm_base_url": url, "llm_model": model})  # fmt: skip
        assert (lib.config().llm_base_url, lib.config().llm_model) == (
            "",
            "",
        )  # igual ao .env: vazio
        client.post("/settings/integrations",
                    data={"llm_provider": "openai", "llm_model": "gpt-5-mini"})  # fmt: skip
        assert lib.config().llm_model == "gpt-5-mini"
        assert 'value="gpt-5-mini"' in client.get("/settings/integrations").text


def test_connection_buttons_use_the_form_values(
    tmp_path: Path, queue: JobQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    from c3parr.providers.base import Health
    from c3parr.web import app as web

    seen: dict[str, str] = {}

    class FakeTMDb:
        def __init__(self, config: web.TMDbConfig) -> None:
            seen["token"] = config.token.get_secret_value()

        def health(self) -> Health:
            return Health(ok=seen["token"] == "good", detail="TMDb rejected the key (401)")

    class FakeLLM:
        def __init__(self, config: web.LLMConfig) -> None:
            seen["llm"] = f"{config.flavor} {config.base_url} {config.model}"

        def health(self) -> tuple[bool, str]:
            return True, "ok"

    monkeypatch.setenv("C3PARR_LLM_MODEL", "qwen-env")
    monkeypatch.setattr(web, "TMDbProvider", FakeTMDb)
    monkeypatch.setattr(web, "make_llm_client", FakeLLM)
    monkeypatch.delenv("TMDB_READ_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(web, "load_secrets", lambda: {})
    lib = Library(tmp_path / "c3parr.db")
    app = create_app(queue=queue, runner=fake_runner(), start_worker=False, library=lib)
    with TestClient(app) as client:
        assert "Test connection" in client.get("/settings/integrations").text
        assert "no read access token" in client.post("/settings/test/tmdb").text
        bad = client.post("/settings/test/tmdb", data={"TMDB_READ_ACCESS_TOKEN": "bad"})
        assert "rejected the key" in bad.text and "error" in bad.text
        good = client.post("/settings/test/tmdb", data={"TMDB_READ_ACCESS_TOKEN": "good"})
        assert "TMDb: connected." in good.text
        ok = client.post("/settings/test/llm", data={
            "llm_provider": "custom", "llm_base_url": "http://box:8080/v1", "llm_model": ""})  # fmt: skip
        assert "LLM: connected." in ok.text
        assert seen["llm"] == "custom http://box:8080/v1 qwen-env"  # modelo do .env
        assert lib.config().llm_base_url == ""  # testar não salva nada
    monkeypatch.undo()
    with TestClient(create_app(queue=queue, runner=fake_runner(), start_worker=False,
                               library=lib)) as client:  # fmt: skip
        nokey = client.post("/settings/test/llm", data={"llm_provider": "openai"})
        assert "no API key for openai" in nokey.text


def test_fresh_install_shows_setup_needed_until_configured(
    tmp_path: Path, queue: JobQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib = Library(tmp_path / "c3parr.db")
    app = create_app(queue=queue, runner=fake_runner(), start_worker=False, library=lib)
    with TestClient(app) as client:
        alerts = client.get("/system/alerts").text
        for title in ("Connect Radarr or Sonarr", "No LLM configured", "No TMDb key"):
            assert title in alerts
        again = client.get("/dashboard").text  # abrir Alerts não faz sumir
        assert 'title="3 alert(s) to look at"' in again and "No LLM configured" in again

        client.post("/settings/library", data={
            "radarr_enabled": "1", "radarr_host": "radarr.test", "radarr_port": "7878"})  # fmt: skip
        assert "Radarr needs its address and API key" in client.get("/system/alerts").text
        client.post("/settings/library", data={"radarr_enabled": "1", "radarr_host": "radarr.test",
                                               "RADARR_API_KEY": "rk"})  # fmt: skip
        client.post("/settings/integrations", data={
            "llm_provider": "custom", "llm_base_url": "http://box:8080/v1", "llm_model": "q",
            "TMDB_READ_ACCESS_TOKEN": "t"})  # fmt: skip
        done = client.get("/system/alerts").text
        assert "Setup needed" not in done and "alert(s) to look at" not in done

        client.post("/settings/integrations", data={"llm_provider": "openai"})
        assert "No API key for the LLM (openai)" in client.get("/system/alerts").text


def test_llm_without_url_is_unavailable() -> None:
    from c3parr.core.errors import Unavailable
    from c3parr.llm.client import LLMConfig, make_llm_client

    with pytest.raises(Unavailable, match="no LLM configured"):
        make_llm_client(LLMConfig())


def test_builtin_tmdb_key_counts_as_configured(
    tmp_path: Path, queue: JobQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    from c3parr.metadata import tmdb

    monkeypatch.setattr(tmdb, "BUILTIN_READ_TOKEN", "builtin")
    lib = Library(tmp_path / "c3parr.db")
    app = create_app(queue=queue, runner=fake_runner(), start_worker=False, library=lib)
    with TestClient(app) as client:
        assert "No TMDb key" not in client.get("/system/alerts").text
        page = client.get("/settings/integrations").text
        assert "empty = c3parr&#39;s key" in page and "builtin" not in page  # não aparece no campo
        assert "not endorsed or certified by TMDB" in page
        assert 'src="/static/tmdb.svg"' in page
        assert client.get("/static/tmdb.svg").headers["content-type"].startswith("image/svg")


def test_llm_effort_defaults_to_low_and_only_reaches_anthropic_and_gemini(
    tmp_path: Path, queue: JobQueue
) -> None:
    from c3parr.core.config import Settings
    from c3parr.web.app import components_for

    lib = Library(tmp_path / "c3parr.db")
    app = create_app(queue=queue, runner=fake_runner(), start_worker=False, library=lib)
    with TestClient(app) as client:
        page = client.get("/settings/integrations").text
        assert 'name="llm_effort"' in page and 'value="low" selected' in page
        assert "https://platform.claude.com/" in page and "github.com/ollama/ollama" in page
        assert "https://platform.openai.com/" in page
        client.post(
            "/settings/integrations", data={"llm_provider": "gemini", "llm_effort": "medium"}
        )
    llm = components_for(Settings(), lib.config())["traducao"].params["llm"]
    assert (llm["flavor"], llm["model"], llm["effort"]) == (
        "gemini",
        "gemini-flash-latest",
        "medium",
    )
    lib.update_config(llm_provider="deepseek")
    assert components_for(Settings(), lib.config())["traducao"].params["llm"]["effort"] is None
