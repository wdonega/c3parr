"""Interface web no padrão *arr: menu à esquerda, conteúdo à direita.

Movies e TV Shows mostram o que a rotina de scan indexou; Queue é a fila FIFO; Settings e System
abrem os subitens como no Bazarr. Configs e biblioteca ficam no SQLite (`c3parr.db`).

Aqui fica a montagem do app e as rotas HTML, que só traduzem HTTP, formulário e template: o que
cada tela mostra e o que cada ação faz está em `service.py`.
"""

import logging
import os
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.openapi.utils import get_openapi
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from c3parr import REPO_URL, __version__
from c3parr.asr.remote import RemoteASRConfig, RemoteTranscriber
from c3parr.core.config import Settings, load_secrets
from c3parr.engine.engine import Engine, RunResult
from c3parr.jobs.queue import FINISHED, Job, JobQueue, ProgressFn, Runner, Worker, engine_runner
from c3parr.library.arr import API_KEY_ENV as ARR_KEY_ENV
from c3parr.library.arr import SERIES_TYPES, ArrClient, ArrConfig, ArrKind, latest_major
from c3parr.library.store import Library
from c3parr.library.tasks import Scheduler, default_tasks
from c3parr.llm.client import LLM_KEY_ENV, LLMConfig, make_llm_client
from c3parr.metadata.tmdb import TMDbConfig, TMDbProvider
from c3parr.pipeline.actions import run_step
from c3parr.pipeline.catalog import Catalog
from c3parr.pipeline.reconcile import PipelineContext, index_library, reconcile_all
from c3parr.providers.factory import AbstractComponentFactoryFactory
from c3parr.providers.registry import build_factories
from c3parr.recipes import full as full_recipe
from c3parr.web import service
from c3parr.web.api import build_api
from c3parr.web.service import (
    LANGUAGE_NAMES,
    LANGUAGES,
    SETTINGS_PAGES,
    STEP_LABELS,
    SUBTITLE_FLAGS,
    SYSTEM_PAGES,
    Clients,
    LogBuffer,
    NotFound,
    Outcome,
    Service,
    components_for,
)

log = logging.getLogger("c3parr.web")
HERE = Path(__file__).parent
STATIC = HERE / "static"

STATUS_ICONS = {
    "na_fila": "schedule",
    "processando": "progress_activity",
    "concluida": "check_circle",
    "concluida_avisos": "warning",
    "falhou": "error",
    "cancelada": "block",
}
# cores de status com as classes do BeerCSS (tema e paleta), sem CSS próprio
STATUS_CLASSES = {
    "na_fila": "surface-container-highest",
    "processando": "secondary-container",
    "concluida": "green9 white-text",
    "concluida_avisos": "tertiary",
    "falhou": "error-container",
    "cancelada": "surface-container-highest",
}
STEP_CLASSES = {
    "ok": "green-text",
    "cache": "secondary-text",
    "aviso": "tertiary-text",
    "falhou": "error-text",
    "running": "primary-text",
}
STEP_ICONS = {
    "ok": "check_circle",
    "cache": "cached",
    "pulado": "skip_next",
    "falhou": "error",
    "aviso": "warning",
    "running": "progress_activity",
    "pending": "radio_button_unchecked",
}
LEVEL_CLASSES = {"WARNING": "tertiary", "ERROR": "error-container", "CRITICAL": "error"}
RunOutput = tuple[RunResult, str | None, str]

__all__ = ["components_for", "create_app", "effective_secrets", "serve"]


def _when(value: str | None) -> str:
    if not value:
        return ""
    return datetime.fromisoformat(value).astimezone().strftime("%b %d %H:%M")


def arr_from_form(kind: str, form: Any) -> ArrConfig:
    """Campos `radarr_*`/`sonarr_*` de Settings → Library (ou do botão Test) numa ArrConfig."""

    def val(name: str) -> str:
        return str(form.get(f"{kind}_{name}") or "").strip()

    remotes = [str(v).strip() for v in form.getlist(f"{kind}_map_remote")]
    locals_ = [str(v).strip() for v in form.getlist(f"{kind}_map_local")]
    return ArrConfig(
        enabled=f"{kind}_enabled" in form,
        host=val("host"),
        port=int(val("port")) if val("port").isdigit() else None,
        base_url=val("base_url"),
        ssl=f"{kind}_ssl" in form,
        timeout_s=int(val("timeout_s")) if val("timeout_s").isdigit() else 60,
        only_monitored=f"{kind}_only_monitored" in form,
        excluded_tags=tuple(t.strip() for t in val("excluded_tags").split(",") if t.strip()),
        excluded_series_types=tuple(
            str(v) for v in form.getlist(f"{kind}_excluded_series_types") if v in SERIES_TYPES
        ),
        exclude_season_zero=f"{kind}_exclude_season_zero" in form,
        path_mappings=tuple((r, lo) for r, lo in zip(remotes, locals_, strict=False) if r and lo),
    )


def effective_secrets(lib: Library) -> dict[str, str]:
    """Chaves do ambiente/.env com as digitadas em Settings por cima (ver `service`)."""
    return service.effective_secrets(lib, load_secrets())


# Quem fala com o mundo de fora. Cada função procura o nome neste módulo na hora da chamada:
# é aqui que os testes trocam `ArrClient`, `TMDbProvider`, `make_llm_client` e companhia.


def _arr(kind: ArrKind, config: ArrConfig, key: str) -> Any:
    return ArrClient(kind, config, key)


def _tmdb(config: TMDbConfig) -> Any:
    return TMDbProvider(config)


def _llm(config: LLMConfig) -> Any:
    return make_llm_client(config)


def _remote_asr(config: RemoteASRConfig, flavor: Any) -> Any:
    return RemoteTranscriber(config, flavor)


def _env_secrets() -> dict[str, str]:
    return load_secrets()


def _latest_major(kind: ArrKind) -> int:
    return latest_major(kind)


def create_app(
    settings: Settings | None = None,
    queue: JobQueue | None = None,
    runner: Runner | None = None,
    start_worker: bool = True,
    library: Library | None = None,
) -> FastAPI:
    settings = settings or Settings()
    jobs = queue or JobQueue(settings.cache_dir / "fila.db")
    lib = library or Library(settings.config_dir / "c3parr.db")
    factories: AbstractComponentFactoryFactory = build_factories()
    secrets = effective_secrets(lib)  # dicionário vivo: Settings → Integrations atualiza

    def make_engine() -> Engine:
        components = components_for(settings, lib.config())
        return Engine(factories, components, secrets, settings.cache_dir)

    catalog = Catalog(lib.path)
    pipeline = PipelineContext(
        catalog=catalog,
        library=lib,
        queue=jobs,
        factories=factories,
        secrets=secrets,
        make_engine=make_engine,
        components=lambda: components_for(settings, lib.config()),
        format_rules=settings.format,
    )
    legacy = runner or engine_runner(make_engine, lambda: full_recipe.recipe(settings.format))

    def dispatch(job: Job, progress: ProgressFn, cancelled: Callable[[], bool]) -> RunOutput:
        """Passo da pipeline (tarefa nova) ou receita completa (tarefa antiga, sem `acao`)."""
        if job.acao:
            return run_step(pipeline, job, progress, cancelled)
        return legacy(job, progress, cancelled)

    worker = Worker(jobs, dispatch)

    def arr_key(kind: str) -> str:
        return effective_secrets(lib).get(ARR_KEY_ENV[kind], "")

    scheduler = Scheduler(
        lib, default_tasks(lib, jobs, api_key=arr_key, process=lambda: reconcile_all(pipeline))
    )
    planned = [s.name for s in full_recipe.recipe().steps]
    themes = sorted(p.stem for p in (STATIC / "themes").glob("*.css"))
    logs = LogBuffer()
    logging.getLogger().addHandler(logs)
    svc = Service(
        settings=settings,
        library=lib,
        queue=jobs,
        catalog=catalog,
        factories=factories,
        secrets=secrets,
        pipeline=pipeline,
        scheduler=scheduler,
        logs=logs,
        planned=planned,
        themes=themes,
        clients=Clients(arr=_arr, tmdb=_tmdb, llm=_llm, remote_asr=_remote_asr,
                        env_secrets=_env_secrets, latest_major=_latest_major),
    )  # fmt: skip

    env = Environment(loader=FileSystemLoader(HERE / "templates"),
                      autoescape=select_autoescape(["html"]))  # fmt: skip
    env.filters["when"] = _when
    released = __version__.count(".") == 2 and __version__.startswith("1.")
    common: dict[str, Any] = {
        "version": __version__,
        "repo_url": REPO_URL,
        "release_url": f"{REPO_URL}/releases/tag/v{__version__}"
        if released
        else f"{REPO_URL}/releases",
        "labels": STEP_LABELS,
        "languages": LANGUAGES,
        "language_names": LANGUAGE_NAMES,
        "finished": FINISHED,
        "icons": STATUS_ICONS,
        "step_icons": STEP_ICONS,
        "status_classes": STATUS_CLASSES,
        "step_classes": STEP_CLASSES,
        "level_classes": LEVEL_CLASSES,
        "settings_pages": SETTINGS_PAGES,
        "system_pages": SYSTEM_PAGES,
    }

    def render(name: str, **ctx: Any) -> HTMLResponse:
        theme = svc.theme()
        ctx.setdefault("pending", svc.pending())
        ctx.setdefault("setup", svc.setup_needed())
        ctx.setdefault("alerts_unseen", svc.unseen_alerts() + len(ctx["setup"]))
        return HTMLResponse(env.get_template(name).render(**common, theme=theme, **ctx))

    def toast(outcome: Outcome) -> HTMLResponse:
        html = env.get_template("toast.html").render(message=outcome.message, error=outcome.error)
        return HTMLResponse(html, status_code=outcome.status)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if start_worker:
            worker.start()
            scheduler.start()
            if lib.meta("subtitle_index") is None:  # biblioteca de antes do índice de legendas
                threading.Thread(target=index_library, args=(pipeline,), name="c3parr-index",
                                 daemon=True).start()  # fmt: skip
        yield
        worker.stop()
        scheduler.stop()
        logging.getLogger().removeHandler(logs)

    app = FastAPI(title="c3parr", version=__version__, lifespan=lifespan, redoc_url=None,
                  docs_url="/api/docs", openapi_url="/api/openapi.json")  # fmt: skip
    app.include_router(build_api(svc))

    def api_schema() -> dict[str, Any]:
        """O contrato da API: só as rotas JSON entram (as páginas HTML ficam de fora)."""
        if app.openapi_schema is None:
            schema = get_openapi(title="c3parr", version=__version__, routes=app.routes)
            schema["paths"] = {path: item for path, item in schema["paths"].items()
                               if path.startswith("/api/v1/")}  # fmt: skip
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = api_schema  # type: ignore[method-assign]
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.state.queue = jobs
    app.state.library = lib
    app.state.worker = worker
    app.state.scheduler = scheduler
    app.state.catalog = catalog
    app.state.pipeline = pipeline
    app.state.service = svc

    @app.exception_handler(NotFound)
    def not_found(_request: Request, error: NotFound) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=404)

    @app.get("/")
    def home() -> RedirectResponse:
        return RedirectResponse("/dashboard")

    @app.get("/fila")
    def old_queue_url() -> RedirectResponse:
        return RedirectResponse("/queue")

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard() -> HTMLResponse:
        return render("dashboard.html", section="dashboard", **svc.dashboard())

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    # --- fila ---

    @app.get("/queue", response_class=HTMLResponse)
    def queue_page(q: str = "", status: str = "", page: int = 1) -> HTMLResponse:
        return render("queue.html", section="queue", target=svc.target_language(),
                      **svc.queue(q, status, page))  # fmt: skip

    @app.get("/queue/count", response_class=HTMLResponse)
    def pending_badge() -> HTMLResponse:
        return render("badge.html")

    @app.get("/queue/rows", response_class=HTMLResponse)
    def rows(q: str = "", status: str = "", page: int = 1) -> HTMLResponse:
        ctx = svc.queue(q, status, page)
        response = render("rows.html", **ctx)
        response.headers["HX-Replace-Url"] = ctx["pager"]["href"]  # recarregar mantém o filtro
        return response

    @app.get("/queue/{job_id}", response_class=HTMLResponse)
    def detail(job_id: int) -> HTMLResponse:
        return render("detail.html", **svc.job_detail(job_id))

    @app.post("/queue/{job_id}/cancel", response_class=HTMLResponse)
    def cancel(job_id: int) -> HTMLResponse:
        return render("detail.html", **svc.cancel(job_id))

    @app.post("/queue/{job_id}/retry", response_class=HTMLResponse)
    def retry(job_id: int) -> HTMLResponse:
        return render("detail.html", **svc.retry(job_id))

    @app.get("/queue/{job_id}/subtitle")
    def subtitle(job_id: int) -> FileResponse:
        path = svc.subtitle_file(job_id)
        return FileResponse(path, filename=path.name)

    @app.get("/queue/{job_id}/report", response_class=PlainTextResponse)
    def report(job_id: int) -> str:
        return svc.report_text(job_id)

    # --- biblioteca ---

    @app.get("/movies", response_class=HTMLResponse)
    def movies_page(q: str = "", show: str = "", page: int = 1) -> HTMLResponse:
        return render("movies.html", section="movies", **svc.movies(q, show, page))

    @app.get("/tv", response_class=HTMLResponse)
    def tv_page(q: str = "", show: str = "", page: int = 1) -> HTMLResponse:
        return render("tv.html", section="tv", **svc.shows(q, show, page))

    @app.get("/tv/{series_id}", response_class=HTMLResponse)
    def series_page(series_id: int) -> HTMLResponse:
        return render("series.html", section="tv", **svc.show(series_id))

    @app.post("/library/{kind}/{item_id}/monitor", response_class=HTMLResponse)
    def toggle_monitor(kind: str, item_id: int) -> HTMLResponse:
        return render("_monitor.html", kind=kind, item=svc.toggle_monitor(kind, item_id))

    @app.post("/library/queue", response_class=HTMLResponse)
    async def queue_items(request: Request) -> HTMLResponse:
        form = await request.form()
        return toast(svc.add_to_queue([str(v) for v in form.getlist("item")]))

    # --- settings ---

    @app.get("/settings")
    def settings_home() -> RedirectResponse:
        return RedirectResponse("/settings/library")

    @app.get("/settings/library/{tab}", response_class=HTMLResponse)
    def library_tab(tab: str, request: Request) -> HTMLResponse:
        if tab not in ARR_KEY_ENV:
            raise HTTPException(404)
        return settings_page("library", request, tab)

    @app.get("/settings/{page}", response_class=HTMLResponse)
    def settings_page(page: str, request: Request, tab: str = "radarr") -> HTMLResponse:
        return render("settings.html", section="settings", **svc.settings_view(page, tab))

    @app.post("/settings/test/{target}", response_class=HTMLResponse)
    async def test_connection(target: str, request: Request) -> HTMLResponse:
        """Botão "Test": usa o que está no formulário, mesmo antes de salvar (como nos *arr)."""
        form = await request.form()
        typed = {k: str(v).strip() for k, v in form.items()}
        if target in ARR_KEY_ENV:
            kind: ArrKind = "radarr" if target == "radarr" else "sonarr"
            arr = arr_from_form(kind, form)
            key = typed.get(ARR_KEY_ENV[kind], "")
            return toast(await run_in_threadpool(svc.test_arr, kind, arr, key))
        if target == "tmdb":
            token = typed.get("TMDB_READ_ACCESS_TOKEN", "")
            return toast(await run_in_threadpool(svc.test_tmdb, token))
        if target == "llm":
            provider = typed.get("llm_provider", "")
            key = typed.get(LLM_KEY_ENV.get(provider or lib.config().llm_provider, ""), "")
            return toast(await run_in_threadpool(
                svc.test_llm, provider, typed.get("llm_base_url", ""),
                typed.get("llm_model", ""), typed.get("llm_effort", ""), key))  # fmt: skip
        if target == "asr":
            return toast(await run_in_threadpool(
                svc.test_asr, typed.get("asr_mode", ""), typed.get("asr_url", ""),
                typed.get("asr_model", ""), typed.get("C3PARR_ASR_API_KEY", "")))  # fmt: skip
        raise HTTPException(404)

    @app.post("/settings/{page}", response_class=HTMLResponse)
    async def save_settings(page: str, request: Request) -> HTMLResponse:
        form = await request.form()
        values: dict[str, Any] = {k: str(v) for k, v in form.items()}
        if page == "library":
            # cada aba só manda os campos dela
            arrs = {kind: arr_from_form(kind, form) for kind in ("radarr", "sonarr")
                    if f"{kind}_host" in values}  # fmt: skip
            shared: dict[str, Any] = {}
            if "sync_interval_minutes" in values:
                shared = {"monitor_new": "monitor_new" in values,
                          "sync_interval_minutes": values.get("sync_interval_minutes")}  # fmt: skip
            return toast(svc.save_library(arrs, shared, values))
        if page == "languages":
            targets = [str(v) for v in form.getlist("target_languages")]
            fallback = [c.strip() for c in values.get("fallback_languages", "").split(",")]
            return toast(svc.save_languages(targets, fallback, values))
        if page == "subtitles":  # caixa marcada vem no formulário; desmarcada não vem
            flags = {flag: True for flag in SUBTITLE_FLAGS if flag in values}
            return toast(svc.save_subtitles({**values, **flags}, values))
        if page == "engines":
            return toast(svc.save_engines(values, values))
        if page == "integrations":
            return toast(svc.save_integrations(values, values))
        raise HTTPException(404)

    # --- system ---

    @app.get("/system")
    def system_home() -> RedirectResponse:
        return RedirectResponse("/system/tasks")

    @app.get("/system/tasks", response_class=HTMLResponse)
    def tasks_page() -> HTMLResponse:
        return render("system_tasks.html", section="system", page="tasks", tasks=svc.task_rows())

    @app.get("/system/tasks/rows", response_class=HTMLResponse)
    def task_rows() -> HTMLResponse:
        return render("_task_rows.html", tasks=svc.task_rows())

    @app.post("/system/tasks/{name}/run", response_class=HTMLResponse)
    def run_task(name: str) -> HTMLResponse:
        return toast(svc.run_task(name))

    @app.get("/system/alerts", response_class=HTMLResponse)
    def alerts_page() -> HTMLResponse:
        return render("system_alerts.html", section="system", page="alerts", **svc.alerts_view())

    @app.post("/system/alerts/retry", response_class=HTMLResponse)
    async def retry_alert(request: Request) -> HTMLResponse:
        form = await request.form()
        return toast(svc.retry_alert(str(form.get("key", ""))))

    @app.get("/system/logs", response_class=HTMLResponse)
    def logs_page() -> HTMLResponse:
        return render("system_logs.html", section="system", page="logs",
                      records=svc.log_records())  # fmt: skip

    @app.get("/system/logs/rows", response_class=HTMLResponse)
    def log_rows() -> HTMLResponse:
        return render("_log_rows.html", records=svc.log_records())

    @app.get("/system/status", response_class=HTMLResponse)
    def status_page() -> HTMLResponse:
        return render("system_status.html", section="system", page="status", **svc.status_view())

    return app


def serve() -> None:
    import uvicorn

    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt)
    host = os.environ.get("C3PARR_HOST", "127.0.0.1")
    port = int(os.environ.get("C3PARR_PORT", "8484"))
    uvicorn.run(create_app(), host=host, port=port)
