"""Interface web no padrão *arr: menu à esquerda, conteúdo à direita.

Movies e TV Shows mostram o que a rotina de scan indexou; Queue é a fila FIFO; Settings e System
abrem os subitens como no Bazarr. Configs e biblioteca ficam no SQLite (`c3parr.db`).
"""

import logging
import os
import platform
import threading
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import ValidationError

from c3parr import REPO_URL, __version__
from c3parr.asr.remote import REMOTE_IDS, RemoteASRConfig, RemoteTranscriber
from c3parr.asr.whisper_cpp import measured_speed
from c3parr.core.config import LLM_ENV, SECRET_NAMES, Settings, load_env, load_secrets
from c3parr.core.errors import ProviderError
from c3parr.core.job import ComponentSpec
from c3parr.engine.engine import Engine, RunResult
from c3parr.jobs.queue import (
    ACTION_LABELS,
    FINISHED,
    Job,
    JobQueue,
    ProgressFn,
    Runner,
    Worker,
    engine_runner,
)
from c3parr.library.arr import API_KEY_ENV as ARR_KEY_ENV
from c3parr.library.arr import SERIES_TYPES, ArrClient, ArrConfig, ArrKind, latest_major, major
from c3parr.library.store import AppConfig, Library
from c3parr.library.tasks import Scheduler, default_tasks
from c3parr.llm.client import LLM_KEY_ENV, LLM_PRESETS, LLMConfig, llm_defaults, make_llm_client
from c3parr.metadata import tmdb as tmdb_meta
from c3parr.metadata.tmdb import TMDbConfig, TMDbProvider
from c3parr.pipeline.actions import run_step
from c3parr.pipeline.catalog import Catalog
from c3parr.pipeline.naming import norm_lang, same_lang
from c3parr.pipeline.reconcile import PipelineContext, index_library, reconcile, reconcile_all
from c3parr.providers.factory import AbstractComponentFactoryFactory
from c3parr.providers.registry import build_factories
from c3parr.recipes import full as full_recipe

log = logging.getLogger("c3parr.web")
HERE = Path(__file__).parent
STATIC = HERE / "static"

STEP_LABELS = {
    "audio": "Audio",
    "metadados": "Metadata (TMDb)",
    "transcricao": "Transcription",
    "referencia": "Reference subtitle",
    "ponte": "Bridge subtitle",
    "sincronizacao": "Sync",
    "biblia": "User bible",
    "contexto": "Context (bible)",
    "nomes": "Name correction",
    "traducao": "Translation",
    "formatacao": "Formatting",
    "escrita": "Write subtitle",
    "escrita_original": "Original transcript",
    "memoria": "Series memory",
}
STEP_STATUS = {"ok": "done", "cache": "from cache", "pulado": "skipped", "falhou": "failed"}
LANGUAGES = [
    ("pt-BR", "Portuguese (Brazil)"), ("pt", "Portuguese (Portugal)"), ("en", "English"),
    ("es", "Spanish"), ("fr", "French"), ("it", "Italian"), ("de", "German"), ("nl", "Dutch"),
    ("ar", "Arabic"), ("he", "Hebrew"), ("tr", "Turkish"), ("ru", "Russian"),
    ("uk", "Ukrainian"), ("pl", "Polish"), ("cs", "Czech"), ("ro", "Romanian"),
    ("hu", "Hungarian"), ("el", "Greek"), ("sv", "Swedish"), ("no", "Norwegian"),
    ("da", "Danish"), ("fi", "Finnish"), ("ja", "Japanese"), ("ko", "Korean"),
    ("zh", "Chinese"), ("hi", "Hindi"), ("th", "Thai"), ("vi", "Vietnamese"),
    ("id", "Indonesian"), ("ca", "Catalan"),
]  # fmt: skip
LANGUAGE_NAMES = dict(LANGUAGES)
INTERFACE_LABELS = {
    "transcricao": "Transcription (ASR)",
    "sincronizacao": "Sync",
    "metadados": "Metadata",
    "contexto": "Context (bible)",
    "traducao": "Translation",
    "fonte_legenda": "Subtitle source",
    "escrita_legenda": "Subtitle format",
}
# o que roda dentro do app (Settings → Engines); o resto é serviço externo (Integrations)
ENGINE_INTERFACES = ("transcricao", "sincronizacao", "fonte_legenda", "escrita_legenda")
LLM_EFFORT_FLAVORS = ("anthropic", "gemini")
# onde pegar a chave (ou, no servidor próprio, um jeito fácil de subir um)
LLM_SITES = {
    "custom": "https://github.com/ollama/ollama#ollama",
    "openai": "https://platform.openai.com/",
    "anthropic": "https://platform.claude.com/",
    "gemini": "https://aistudio.google.com/",
    "deepseek": "https://platform.deepseek.com/",
}
LLM_PROVIDERS = [
    ("custom", "Local / custom (OpenAI-compatible URL)"),
    ("openai", "OpenAI (ChatGPT)"),
    ("anthropic", "Anthropic (Claude)"),
    ("gemini", "Google (Gemini)"),
    ("deepseek", "DeepSeek"),
]
INTEGRATION_SECRETS = (
    "TMDB_READ_ACCESS_TOKEN",
    *LLM_KEY_ENV.values(),
    "C3PARR_ASR_API_KEY",
    *ARR_KEY_ENV.values(),
)
SETTINGS_PAGES = [
    ("library", "Library"),
    ("integrations", "Integrations"),
    ("languages", "Languages"),
    ("engines", "Engines"),
    ("subtitles", "Subtitles"),
]
SYSTEM_PAGES = [("tasks", "Tasks"), ("alerts", "Alerts"), ("logs", "Logs"), ("status", "Status")]

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
ACTIVE = frozenset({"na_fila", "processando"})
SUBTITLE_FLAGS = (
    "use_embedded", "embedded_resolves", "external_resolves", "forced_resolves",
    "sync_new_external", "fix_external_in_place", "delete_ours_on_human",
    "new_external_becomes_main", "asr_enabled", "asr_always",
)  # fmt: skip
RunOutput = tuple[RunResult, str | None, str]


class LogBuffer(logging.Handler):
    """Guarda as últimas linhas de log para System → Logs."""

    def __init__(self, size: int = 500) -> None:
        super().__init__(level=logging.INFO)
        self.records: deque[dict[str, str]] = deque(maxlen=size)

    def emit(self, record: logging.LogRecord) -> None:
        when = datetime.fromtimestamp(record.created).strftime("%b %d %H:%M:%S")
        self.records.append({"level": record.levelname, "time": when, "name": record.name,
                             "message": record.getMessage()})  # fmt: skip


def _when(value: str | None) -> str:
    if not value:
        return ""
    return datetime.fromisoformat(value).astimezone().strftime("%b %d %H:%M")


def _interval(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes % 1440 == 0:
        return f"{minutes // 1440} d"
    if minutes % 60 == 0:
        return f"{minutes // 60} h"
    return f"{minutes} min"


def _steps(job: Job, planned: list[str]) -> list[dict[str, str]]:
    """Linha do tempo das etapas: as já rodadas (com motivo) e as que ainda vêm."""
    if job.etapas:
        out = []
        degraded = {d.get("stage") for d in job.degradacoes}
        for s in job.etapas:
            state: str = "aviso" if s.status == "falhou" and s.name in degraded else s.status
            text = "with warning" if state == "aviso" else STEP_STATUS.get(s.status, s.status)
            if s.seconds:
                text += f" · {s.seconds:.0f} s"
            if s.provider:
                text += f" · {s.provider}"
            if s.reason:
                text += f" · {s.reason}"
            out.append({"label": STEP_LABELS.get(s.name, s.name), "state": state, "text": text})
        return out
    current = planned.index(job.etapa) if job.etapa in planned else -1
    out = []
    for i, name in enumerate(planned):
        if job.status == "processando" and i < current:
            state, text = "ok", "done"
        elif job.status == "processando" and i == current:
            state, text = "running", job.mensagem or "running"
        else:
            state, text = "pending", "waiting"
        out.append({"label": STEP_LABELS.get(name, name), "state": state, "text": text})
    return out


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


def components_for(settings: Settings, config: AppConfig) -> dict[str, ComponentSpec]:
    """Padrões do `Settings` com o motor escolhido em Settings → Engines por cima, e o LLM de
    Settings → Integrations (provedor, endereço e modelo) no contexto e na tradução."""
    out = dict(settings.components)
    for iface, provider in config.components.items():
        base = out.get(iface)
        same = base is not None and base.provider == provider
        out[iface] = base if same and base else ComponentSpec(provider=provider)
    flavor = config.llm_provider if config.llm_provider in LLM_PRESETS else "custom"
    url, model = llm_defaults(flavor)
    llm = {
        "flavor": flavor,
        "base_url": (config.llm_base_url if flavor == "custom" and config.llm_base_url else url),
        "model": config.llm_model or model,
        "effort": config.llm_effort if flavor in LLM_EFFORT_FLAVORS else None,
    }
    for iface in ("contexto", "traducao"):
        spec = out.get(iface)
        if spec is not None and spec.provider == "llm":
            out[iface] = spec.model_copy(update={"params": {**spec.params, "llm": llm}})
    # servidor de transcrição à parte (Settings → Integrations) passa por cima do motor local
    if config.asr_mode in REMOTE_IDS:
        params: dict[str, Any] = {"base_url": config.asr_url}
        if config.asr_model:
            params["model"] = config.asr_model
        out["transcricao"] = ComponentSpec(provider=REMOTE_IDS[config.asr_mode], params=params)
    return out


def effective_secrets(lib: Library) -> dict[str, str]:
    """Chaves do ambiente/.env com as digitadas em Settings por cima; a chave do provedor de LLM
    escolhido vira a chave que o cliente do LLM usa."""
    out = load_secrets()
    out.update(lib.secrets())
    flavor = lib.config().llm_provider
    key = out.get(LLM_KEY_ENV.get(flavor, "C3PARR_LLM_API_KEY"))
    if key:
        out["C3PARR_LLM_API_KEY"] = key
    else:
        out.pop("C3PARR_LLM_API_KEY", None)
    return out


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

    def refresh_secrets() -> None:
        secrets.clear()
        secrets.update(effective_secrets(lib))

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

    def setup_needed() -> list[dict[str, str]]:
        """O mínimo para funcionar e que ainda falta. Fica em System → Alerts e no contador até
        ser configurado: abrir a página não faz sumir."""
        cfg = lib.config()
        secrets = effective_secrets(lib)
        out = []
        arrs = [k for k in ("radarr", "sonarr") if getattr(cfg, k).enabled]
        if not arrs:
            out.append({"title": "Connect Radarr or Sonarr",
                        "detail": "c3parr reads its library from them, like Bazarr does.",
                        "href": "/settings/library/radarr"})  # fmt: skip
        for kind in arrs:
            if not getattr(cfg, kind).host or ARR_KEY_ENV[kind] not in secrets:
                out.append({"title": f"{kind.capitalize()} needs its address and API key",
                            "detail": f"{kind.capitalize()} → Settings → General → API Key.",
                            "href": f"/settings/library/{kind}"})  # fmt: skip
        flavor = cfg.llm_provider if cfg.llm_provider in LLM_PRESETS else "custom"
        url, model = llm_defaults(flavor)
        if flavor == "custom" and not ((cfg.llm_base_url or url) and (cfg.llm_model or model)):
            out.append({"title": "No LLM configured",
                        "detail": "Translations need an LLM: pick a provider and its key, or the URL "
                        "and model of your own server.", "href": "/settings/integrations"})  # fmt: skip
        elif flavor != "custom" and LLM_KEY_ENV[flavor] not in secrets:
            out.append({"title": f"No API key for the LLM ({flavor})",
                        "detail": "Translations need the key of the chosen provider.",
                        "href": "/settings/integrations"})  # fmt: skip
        if "TMDB_READ_ACCESS_TOKEN" not in secrets and not tmdb_meta.BUILTIN_READ_TOKEN:
            out.append({"title": "No TMDb key",
                        "detail": "Without metadata the original language and character names "
                        "are guessed.", "href": "/settings/integrations"})  # fmt: skip
        return out

    def render(name: str, **ctx: Any) -> HTMLResponse:
        config = lib.config()
        theme = config.theme if config.theme in themes else "robo"
        ctx.setdefault("pending", jobs.pending_count())
        ctx.setdefault("setup", setup_needed())
        ctx.setdefault("alerts_unseen", catalog.unseen_alerts() + len(ctx["setup"]))
        return HTMLResponse(env.get_template(name).render(**common, theme=theme, **ctx))

    def toast(message: str, error: bool = False, status: int = 200) -> HTMLResponse:
        html = env.get_template("toast.html").render(message=message, error=error)
        return HTMLResponse(html, status_code=status)

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

    app = FastAPI(title="c3parr", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.state.queue = jobs
    app.state.library = lib
    app.state.worker = worker
    app.state.scheduler = scheduler
    app.state.catalog = catalog
    app.state.pipeline = pipeline

    @app.get("/")
    def home() -> RedirectResponse:
        return RedirectResponse("/dashboard")

    @app.get("/fila")
    def old_queue_url() -> RedirectResponse:
        return RedirectResponse("/queue")

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard() -> HTMLResponse:
        cfg = lib.config()
        movies = _rows(lib.movies())
        episodes = _rows(lib.episodes())
        shows = len(lib.series_list())
        cards = [
            {"label": "Movies", "href": "/movies", "total": len(movies),
             "done": sum(1 for r in movies if not r["missing"])},
            {"label": f"Episodes ({shows} shows)", "href": "/tv", "total": len(episodes),
             "done": sum(1 for r in episodes if not r["missing"])},
        ]  # fmt: skip
        steps = catalog.step_counts()
        activity = [
            ("Translations", steps.get(("traduzir", "ok"), 0)),
            ("Transcriptions (ASR)", steps.get(("asr", "ok"), 0)),
            ("Embedded subtitles extracted", steps.get(("extrair", "ok"), 0)),
            ("Subtitles synced", steps.get(("sincronizar", "ok"), 0)),
            ("Steps in the queue", jobs.pending_count()),
            (
                "Steps waiting after a failure",
                sum(n for (_, st), n in steps.items() if st == "falhou"),
            ),
        ]
        made: dict[str, int] = {}
        for (lang, _), n in catalog.output_counts().items():
            made[lang] = made.get(lang, 0) + n
        by_lang: dict[str, dict[str, Any]] = {}
        for r in movies + episodes:
            for lang, kind in r["subs"]:
                row = by_lang.setdefault(lang, {"lang": lang, "embedded": 0, "human": 0, "ours": 0,
                                                "missing": 0, "target": False})  # fmt: skip
                row[kind] += 1
            for lang in r["missing"]:
                by_lang.setdefault(lang, {"lang": lang, "embedded": 0, "human": 0, "ours": 0,
                                          "missing": 0, "target": True})["missing"] += 1  # fmt: skip
        for lang in cfg.target_languages:
            if lang in by_lang:
                by_lang[lang]["target"] = True
        languages = sorted(by_lang.values(), key=lambda r: (not r["target"], -(
            r["embedded"] + r["human"] + r["ours"]), r["lang"]))  # fmt: skip
        return render("dashboard.html", section="dashboard", library_cards=cards,
                      activity=activity, made=sorted(made.items()), lang_rows=languages,
                      alerts_open=len(catalog.alerts(include_resolved=False)),
                      warnings=[w for w in _warnings() if w["level"] == "warn"])  # fmt: skip

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    # --- fila ---

    @app.get("/queue", response_class=HTMLResponse)
    def queue_page() -> HTMLResponse:
        target = lib.config().target_language
        return render("queue.html", section="queue", jobs=jobs.list(), target=target)

    @app.get("/queue/count", response_class=HTMLResponse)
    def pending_badge() -> HTMLResponse:
        return render("badge.html")

    @app.get("/queue/rows", response_class=HTMLResponse)
    def rows() -> HTMLResponse:
        return render("rows.html", jobs=jobs.list())

    def _job_or_404(job_id: int) -> Job:
        try:
            return jobs.get(job_id)
        except KeyError:
            raise HTTPException(404, "task not found") from None

    @app.get("/queue/{job_id}", response_class=HTMLResponse)
    def detail(job_id: int) -> HTMLResponse:
        job = _job_or_404(job_id)
        return render("detail.html", job=job, steps=_steps(job, planned))

    @app.post("/queue/{job_id}/cancel", response_class=HTMLResponse)
    def cancel(job_id: int) -> HTMLResponse:
        jobs.cancel(job_id)
        return detail(job_id)

    @app.post("/queue/{job_id}/retry", response_class=HTMLResponse)
    def retry(job_id: int) -> HTMLResponse:
        jobs.retry(job_id)
        return detail(job_id)

    @app.get("/queue/{job_id}/subtitle")
    def subtitle(job_id: int) -> FileResponse:
        job = _job_or_404(job_id)
        if not job.legenda or not Path(job.legenda).exists():
            raise HTTPException(404, "subtitle not available yet")
        return FileResponse(job.legenda, filename=Path(job.legenda).name)

    @app.get("/queue/{job_id}/report", response_class=PlainTextResponse)
    def report(job_id: int) -> str:
        job = _job_or_404(job_id)
        if not job.relatorio or not Path(job.relatorio).exists():
            raise HTTPException(404, "report not available yet")
        return Path(job.relatorio).read_text(encoding="utf-8")

    # --- biblioteca ---

    def _rows(items: list[Any]) -> list[dict[str, Any]]:
        """Por item: legendas por idioma (embutida, de outra fonte, nossa) e os idiomas alvo que
        faltam. Só lê o banco (o sync e a pipeline gravam o que acham no disco): com biblioteca
        grande, abrir a página não pode depender de listar pastas nem de acordar o NAS."""
        active = _active_jobs()
        videos = [i.video for i in items]
        subs = lib.subtitle_files(None if len(videos) > 500 else videos)
        targets = lib.config().target_languages
        out = []
        for item in items:
            present: dict[str, str] = {}
            files = sorted(subs.get(item.video, []), key=lambda f: f.kind != "embedded")
            for f in files:
                if not f.lang or f.forced:
                    continue
                if f.kind == "embedded":
                    present.setdefault(f.lang, "embedded")
                elif f.kind == "human" or f.lang not in present:
                    present[f.lang] = f.kind
            missing = [t for t in targets if not any(same_lang(t, lang) for lang in present)]
            out.append({"item": item, "name": Path(item.video).name,
                        "subs": sorted(present.items()), "missing": missing,
                        "job": active.get(item.video)})  # fmt: skip
        return out

    def _active_jobs() -> dict[str, str]:
        out: dict[str, str] = {}
        for j in jobs.list(2000):
            if j.status in ACTIVE:
                running = j.status == "processando"
                if running or j.video not in out:
                    out[j.video] = j.step_label if running else f"Queued: {j.step_label}"
        return out

    @app.get("/movies", response_class=HTMLResponse)
    def movies_page() -> HTMLResponse:
        return render("movies.html", section="movies", rows=_rows(lib.movies()))

    @app.get("/tv", response_class=HTMLResponse)
    def tv_page() -> HTMLResponse:
        by_show: dict[int, list[dict[str, Any]]] = {}
        for ep in _rows(lib.episodes()):
            by_show.setdefault(ep["item"].series_id, []).append(ep)
        rows = []
        for show in lib.series_list():
            eps = by_show.get(show.id, [])
            done = sum(1 for e in eps if not e["missing"])
            # "faltando" para o botão: sem alguma legenda e sem passo na fila
            missing = sum(1 for e in eps if e["missing"] and not e["job"])
            rows.append({"item": show, "total": len(eps), "done": done, "missing": missing})
        return render("tv.html", section="tv", rows=rows)

    @app.get("/tv/{series_id}", response_class=HTMLResponse)
    def series_page(series_id: int) -> HTMLResponse:
        try:
            show = lib.series(series_id)
        except KeyError:
            raise HTTPException(404, "show not found") from None
        seasons: dict[int | None, list[dict[str, Any]]] = {}
        for ep in _rows(lib.episodes(series_id)):
            seasons.setdefault(ep["item"].season, []).append(ep)
        ordered = sorted(seasons.items(), key=lambda kv: (kv[0] is None, kv[0] or 0))
        return render("series.html", section="tv", show=show, seasons=ordered)

    @app.post("/library/{kind}/{item_id}/monitor", response_class=HTMLResponse)
    def toggle_monitor(kind: str, item_id: int) -> HTMLResponse:
        if kind not in ("movie", "series"):
            raise HTTPException(404)
        try:
            item = lib.movie(item_id) if kind == "movie" else lib.series(item_id)
        except KeyError:
            raise HTTPException(404, "item not found") from None
        lib.set_monitored(kind, item_id, not item.monitored)
        item = lib.movie(item_id) if kind == "movie" else lib.series(item_id)
        return render("_monitor.html", kind=kind, item=item)

    def _videos_for(key: str) -> list[str]:
        """`movie:1`, `episode:7` ou `series:2` (todos os episódios: o decisor vê o que falta)."""
        kind, _, raw = key.partition(":")
        try:
            item_id = int(raw)
            if kind == "movie":
                return [lib.movie(item_id).video]
            if kind == "episode":
                return [lib.episode(item_id).video]
            if kind == "series":
                return [e.video for e in lib.episodes(item_id)]
        except (KeyError, ValueError):
            pass
        raise HTTPException(404, f"unknown item: {key}")

    def _process(videos: list[str]) -> tuple[int, int, int]:
        """Pedido manual: decide na hora, com prioridade e nova tentativa do que falhou.
        Devolve (passos novos, itens na fila, itens que falharam)."""
        steps = items = failed = 0
        for video in videos:
            d = reconcile(pipeline, video, "manual", retry_failed=True)
            if d is not None and "sondar" in d.blocked:
                failed += 1
            elif d is not None and (d.actions or jobs.active_steps_for(video)):
                items += 1
                steps += d.queued
        return steps, items, failed

    @app.post("/library/queue", response_class=HTMLResponse)
    async def queue_items(request: Request) -> HTMLResponse:
        form = await request.form()
        keys = [str(v) for v in form.getlist("item")]
        if not keys:
            return toast("Select at least one item.", error=True)
        videos = [v for key in keys for v in _videos_for(key)]
        steps, items, failed = _process(videos)
        if failed:
            return toast(f"{failed} item(s) could not be read (see System → Alerts).", error=True)
        if not items:
            return toast("Nothing to do: every target language is covered.")
        return toast(f"{items} item(s) in the queue ({steps} new step(s)).")

    # --- settings ---

    def _interfaces(config: AppConfig) -> list[dict[str, Any]]:
        # aqui é só o motor local; o servidor remoto se escolhe em Integrations
        chosen = components_for(settings, config.model_copy(update={"asr_mode": "local"}))
        remote = set(REMOTE_IDS.values())
        out = []
        for iface in ENGINE_INTERFACES:
            label = INTERFACE_LABELS[iface]
            if iface not in factories:
                continue
            providers = [p for p in factories[iface].providers()
                         if p.id != "falso" and p.id not in remote]  # fmt: skip
            selected = chosen[iface].provider if iface in chosen else ""
            out.append({"id": iface, "label": label, "providers": providers,
                        "selected": selected})  # fmt: skip
        return out

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
        if page not in dict(SETTINGS_PAGES):
            raise HTTPException(404)
        config = lib.config()
        stored = lib.secrets()
        env = load_secrets()
        secret_state = {n: ("settings" if n in stored else "environment" if n in env else "")
                        for n in INTEGRATION_SECRETS}  # fmt: skip
        secret_values = {n: stored.get(n) or env.get(n, "") for n in INTEGRATION_SECRETS}
        llm_env = load_env(LLM_ENV)
        return render("settings.html", section="settings", page=page, config=config,
                      interfaces=_interfaces(config), themes=themes, series_types=SERIES_TYPES,
                      arr_keys=ARR_KEY_ENV, tab=tab if tab in ARR_KEY_ENV else "radarr",
                      llm_providers=LLM_PROVIDERS, llm_keys=LLM_KEY_ENV, llm_sites=LLM_SITES,
                      llm_effort_flavors=LLM_EFFORT_FLAVORS,
                      tmdb_builtin=bool(tmdb_meta.BUILTIN_READ_TOKEN),
                      tmdb_attribution=tmdb_meta.ATTRIBUTION,
                      llm_defaults={v: llm_defaults(v) for v in LLM_PRESETS},
                      llm_url_from_env=not config.llm_base_url and "C3PARR_LLM_BASE_URL" in llm_env,
                      llm_model_from_env=(not config.llm_model and config.llm_provider == "custom"
                                          and "C3PARR_LLM_MODEL" in llm_env),
                      secret_state=secret_state, secret_values=secret_values)  # fmt: skip

    @app.post("/settings/test/{target}", response_class=HTMLResponse)
    async def test_connection(target: str, request: Request) -> HTMLResponse:
        """Botão "Test": usa o que está no formulário, mesmo antes de salvar (como nos *arr)."""
        form = {k: str(v).strip() for k, v in (await request.form()).items()}
        secrets = effective_secrets(lib)
        done = "connected."
        if target in ARR_KEY_ENV:
            kind: ArrKind = "radarr" if target == "radarr" else "sonarr"
            arr = arr_from_form(kind, await request.form())
            arr_api_key = form.get(ARR_KEY_ENV[kind]) or secrets.get(ARR_KEY_ENV[kind], "")

            def check() -> str:
                client = ArrClient(kind, arr, arr_api_key)
                try:
                    version = client.version()
                finally:
                    client.close()
                latest = latest_major(kind)
                found = major(version)
                note = f" Update it: the latest is {latest}." if found and found < latest else ""
                return f"{kind.capitalize()} {version}: connected.{note}"

            try:
                return toast(await run_in_threadpool(check))
            except ProviderError as e:
                return toast(e.message, error=True)
        if target == "tmdb":
            token = (form.get("TMDB_READ_ACCESS_TOKEN") or secrets.get("TMDB_READ_ACCESS_TOKEN")
                     or tmdb_meta.BUILTIN_READ_TOKEN)  # fmt: skip
            if not token:
                return toast("TMDb: no read access token.", error=True)
            tmdb = TMDbProvider(TMDbConfig(token=token, timeout_s=15))
            health = await run_in_threadpool(tmdb.health)
            ok, detail = health.ok, health.detail
        elif target == "llm":
            flavor = form.get("llm_provider") or lib.config().llm_provider
            if flavor not in LLM_PRESETS:
                return toast(f"LLM: unknown provider {flavor}.", error=True)
            url, model = llm_defaults(flavor)
            key = form.get(LLM_KEY_ENV[flavor]) or secrets.get(LLM_KEY_ENV[flavor])
            config = LLMConfig(
                flavor=flavor,
                base_url=(form.get("llm_base_url") or url) if flavor == "custom" else url,
                model=form.get("llm_model") or model,
                api_key=key or None,
                effort=(form.get("llm_effort") or lib.config().llm_effort)
                if flavor in LLM_EFFORT_FLAVORS
                else None,
                timeout_s=60,  # o servidor local pode estar acordando (wake-on-LAN)
            )
            try:
                client = make_llm_client(config)
            except ProviderError as e:
                return toast(f"LLM: {e.message}", error=True)
            ok, detail = await run_in_threadpool(client.health)
        elif target == "asr":
            mode = form.get("asr_mode") or lib.config().asr_mode
            if mode in REMOTE_IDS:
                key = form.get("C3PARR_ASR_API_KEY") or secrets.get("C3PARR_ASR_API_KEY")
                remote = RemoteTranscriber(
                    RemoteASRConfig(base_url=form.get("asr_url") or lib.config().asr_url,
                                    model=form.get("asr_model") or lib.config().asr_model
                                    or "whisper-1",
                                    api_key=key or None),
                    "openai" if mode == "openai" else "asr-webservice",
                )  # fmt: skip
                health = await run_in_threadpool(remote.health)
            else:
                local = components_for(
                    settings, lib.config().model_copy(update={"asr_mode": "local"})
                )
                try:
                    engine = factories["transcricao"].create(local["transcricao"], secrets)
                except ProviderError as e:
                    return toast(f"Transcription: {e.message}", error=True)
                health = await run_in_threadpool(engine.health)
                done = f"{engine.info.id} ready on this machine."
            ok, detail = health.ok, health.detail
        else:
            raise HTTPException(404)
        name = {"tmdb": "TMDb", "llm": "LLM", "asr": "Transcription"}[target]
        if ok:
            return toast(f"{name}: {done}" + (f" {detail}" if detail and target == "asr" else ""))
        return toast(f"{name}: {detail}", error=True)

    @app.post("/settings/{page}", response_class=HTMLResponse)
    async def save_settings(page: str, request: Request) -> HTMLResponse:
        form = await request.form()
        values = {k: str(v) for k, v in form.items()}
        changes: dict[str, Any] = {}
        if page == "library":
            for kind in ("radarr", "sonarr"):
                if f"{kind}_host" in values:  # cada aba só manda os campos dela
                    changes[kind] = arr_from_form(kind, form)
            if "sync_interval_minutes" in values:
                changes["monitor_new"] = "monitor_new" in values
                changes["sync_interval_minutes"] = values.get("sync_interval_minutes")
        elif page == "languages":
            targets = list(
                dict.fromkeys(norm_lang(str(v)) for v in form.getlist("target_languages"))
            )
            if not targets:
                return toast("Pick at least one target language.", error=True, status=422)
            changes["target_languages"] = targets
            fallback = (c.strip() for c in values.get("fallback_languages", "").split(","))
            changes["fallback_languages"] = list(dict.fromkeys(norm_lang(c) for c in fallback if c))
        elif page == "subtitles":
            changes["wait_minutes"] = values.get("wait_minutes")
            changes["search_interval_minutes"] = values.get("search_interval_minutes")
            for flag in SUBTITLE_FLAGS:
                changes[flag] = flag in values
            for choice in ("derived_on_main_change", "foreign_tag"):
                if choice in values:
                    changes[choice] = values[choice]
        elif page == "engines":
            valid = {i["id"]: {p.id for p in i["providers"]} for i in _interfaces(lib.config())}
            picked = {k: v for k, v in values.items() if v in valid.get(k, ())}
            changes["components"] = {**lib.config().components, **picked}
        elif page == "integrations":
            provider = values.get("llm_provider")
            if provider not in LLM_PRESETS:
                provider = lib.config().llm_provider
            changes["llm_provider"] = provider
            if values.get("llm_effort") in ("low", "medium"):
                changes["llm_effort"] = values["llm_effort"]
            # igual ao padrão do provedor grava vazio: segue o padrão se ele mudar numa versão nova
            model = values.get("llm_model", "").strip()
            changes["llm_model"] = "" if model == llm_defaults(provider)[1] else model
            url = values.get("llm_base_url", "").strip()
            changes["llm_base_url"] = "" if url == llm_defaults("custom")[0] else url
            mode = values.get("asr_mode", lib.config().asr_mode)
            if mode in REMOTE_IDS and not values.get("asr_url", "").strip():
                return toast("Transcription: put the server URL.", error=True, status=422)
            changes["asr_mode"] = mode
            changes["asr_url"] = values.get("asr_url", "").strip()
            changes["asr_model"] = values.get("asr_model", "").strip()
        else:
            raise HTTPException(404)
        env = load_secrets()
        for name in INTEGRATION_SECRETS:
            if name not in values:
                continue
            value = values[name].strip()
            # vazio apaga o salvo; igual ao .env não duplica: o .env continua sendo a fonte
            lib.set_secret(name, value if value and value != env.get(name) else None)
        try:
            lib.update_config(**changes)
            refresh_secrets()
        except ValidationError as e:
            fields = ", ".join(str(err["loc"][0]) for err in e.errors())
            return toast(f"Not saved, check: {fields}", error=True, status=422)
        if page == "library":  # como o Bazarr: gravou o endereço, já lê a biblioteca de novo
            for kind in ("radarr", "sonarr"):
                if kind in changes and changes[kind].enabled:
                    lib.request_run(f"sync_{kind}")
        return toast("Saved.")

    # --- system ---

    @app.get("/system")
    def system_home() -> RedirectResponse:
        return RedirectResponse("/system/tasks")

    def _task_rows() -> list[dict[str, Any]]:
        config = lib.config()
        out = []
        for t in scheduler.tasks.values():
            state = lib.task(t.name)
            out.append({"name": t.name, "label": t.label, "interval": _interval(t.interval(config)),
                        "last_run": state.last_run, "next_run": state.next_run,
                        "result": state.last_result, "running": t.name in scheduler.running,
                        "requested": state.run_now})  # fmt: skip
        return out

    @app.get("/system/tasks", response_class=HTMLResponse)
    def tasks_page() -> HTMLResponse:
        return render("system_tasks.html", section="system", page="tasks", tasks=_task_rows())

    @app.get("/system/tasks/rows", response_class=HTMLResponse)
    def task_rows() -> HTMLResponse:
        return render("_task_rows.html", tasks=_task_rows())

    @app.post("/system/tasks/{name}/run", response_class=HTMLResponse)
    def run_task(name: str) -> HTMLResponse:
        task = scheduler.tasks.get(name)
        if task is None:
            raise HTTPException(404, "unknown task")
        lib.request_run(name)
        return toast(f"{task.label}: starting.")

    @app.get("/system/alerts", response_class=HTMLResponse)
    def alerts_page() -> HTMLResponse:
        alerts = catalog.alerts()
        names: dict[str, list[str]] = {}
        for a in alerts:
            for mid in a.media_ids:
                with suppress(KeyError):
                    names.setdefault(a.key, []).append(Path(catalog.media(mid).video).name)
        setup = setup_needed()
        page = render("system_alerts.html", section="system", page="alerts", alerts=alerts,
                      names=names, step_names=ACTION_LABELS, setup=setup,
                      alerts_unseen=len(setup))  # fmt: skip
        catalog.mark_alerts_seen()  # abriu a página: viu (os de configuração só somem configurando)
        return page

    @app.post("/system/alerts/retry", response_class=HTMLResponse)
    async def retry_alert(request: Request) -> HTMLResponse:
        form = await request.form()
        key = str(form.get("key", ""))
        alert = next((a for a in catalog.alerts(include_resolved=False) if a.key == key), None)
        if alert is None:
            return toast("Alert already resolved.")
        videos = []
        for mid in alert.media_ids:
            with suppress(KeyError):
                videos.append(catalog.media(mid).video)
        steps, _, failed = _process(videos)
        if failed:
            return toast(f"Still failing for {failed} item(s).", error=True)
        return toast(f"Retrying: {steps} step(s) queued.")

    @app.get("/system/logs", response_class=HTMLResponse)
    def logs_page() -> HTMLResponse:
        records = list(reversed(logs.records))
        return render("system_logs.html", section="system", page="logs", records=records)

    @app.get("/system/logs/rows", response_class=HTMLResponse)
    def log_rows() -> HTMLResponse:
        return render("_log_rows.html", records=list(reversed(logs.records)))

    def _provider_status() -> list[dict[str, Any]]:
        chosen = components_for(settings, lib.config())
        out = []
        for iface, label in INTERFACE_LABELS.items():
            spec = chosen.get(iface)
            if spec is None or iface not in factories:
                continue
            try:
                factories[iface].create(spec, secrets)
                ok, detail = True, ""
            except ProviderError as e:
                ok, detail = False, str(e)
            out.append({"label": label, "provider": spec.provider, "ok": ok, "detail": detail})
        return out

    @app.get("/system/status", response_class=HTMLResponse)
    def status_page() -> HTMLResponse:
        about = [
            ("Version", __version__),
            ("Python", platform.python_version()),
            ("Database", str(lib.path)),
            ("Queue", str(jobs.path)),
            ("Cache", str(settings.cache_dir)),
        ]
        stored = lib.secrets()
        env = load_secrets()
        present = [(name, "Settings" if name in stored else ".env / environment" if name in env
                    else "") for name in SECRET_NAMES]  # fmt: skip
        about.append(("TMDb", tmdb_meta.ATTRIBUTION))
        return render("system_status.html", section="system", page="status", about=about,
                      providers=_provider_status(), secrets=present, warnings=_warnings())  # fmt: skip

    def _warnings() -> list[dict[str, str]]:
        """Avisos de configuração (não são falhas: não repetem nem viram alerta)."""
        out: list[dict[str, str]] = []
        cfg = lib.config()
        for kind in ("radarr", "sonarr"):  # *arr fora da versão major mais nova: aviso fixo
            found = major(lib.meta(f"{kind}_version") or "")
            latest = int(lib.meta(f"{kind}_latest_major") or 0)
            if getattr(cfg, kind).enabled and found and latest and found < latest:
                name = kind.capitalize()
                out.append({"level": "warn", "title": f"{name} {found} is out of date",
                            "detail": f"The latest is {name} {latest}. c3parr is made for it; "
                            f"update {name} to keep the library in sync."})  # fmt: skip
        asr = components_for(settings, cfg).get("transcricao")
        if asr is not None and asr.provider in REMOTE_IDS.values():
            out.append({"level": "info", "title": "Transcription on a separate server",
                        "detail": f"{cfg.asr_url} ({asr.provider})."})  # fmt: skip
        elif asr is not None and asr.provider == "whisper-cpp":
            speed = measured_speed()
            if speed and str(speed.get("backend", "")).upper() == "CPU":
                minutes = float(speed["seconds_per_minute"])
                out.append({"level": "warn", "title": "Transcription runs on the CPU",
                            "detail": f"Measured here: about {minutes:.0f} min per hour of video. "
                            "A GPU build of whisper.cpp (Metal, Vulkan, CUDA) is 5-8x faster."})  # fmt: skip
            elif speed:
                out.append({"level": "info", "title": f"Transcription on {speed['backend']}",
                            "detail": f"Measured here: about {float(speed['seconds_per_minute']):.1f}"
                            " min per hour of video."})  # fmt: skip
        elif asr is not None and asr.provider == "faster-whisper":
            out.append({"level": "warn", "title": "Transcription runs on the CPU (faster-whisper)",
                        "detail": "About 10 min per hour of video. Install whisper.cpp "
                        "(brew install whisper.cpp) to use the GPU."})  # fmt: skip
        images = sum(1 for m in catalog.all_media() if m.probe
                     and any(not t.text_based for t in m.probe.subtitles))  # fmt: skip
        if images:
            out.append({"level": "info", "title": f"{images} item(s) have image subtitles",
                        "detail": "PGS/VobSub tracks need OCR and are ignored; text tracks and "
                        "the audio are used instead."})  # fmt: skip
        return out

    return app


def serve() -> None:
    import uvicorn

    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt)
    host = os.environ.get("C3PARR_HOST", "127.0.0.1")
    port = int(os.environ.get("C3PARR_PORT", "8484"))
    uvicorn.run(create_app(), host=host, port=port)
