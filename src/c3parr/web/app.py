"""O servidor web: monta o app, a API JSON (`api.py`) e serve a interface (o build do frontend).

O que cada tela mostra e o que cada ação faz está em `service.py`. Configs e biblioteca ficam no
SQLite (`c3parr.db`).
"""

import html
import json
import logging
import re
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from c3parr import __version__
from c3parr.asr.remote import RemoteASRConfig, RemoteTranscriber
from c3parr.core.config import Settings, load_secrets
from c3parr.core.errors import ProviderError
from c3parr.engine.engine import Engine, RunResult
from c3parr.jobs.queue import Job, JobQueue, ProgressFn, Runner, Worker, engine_runner
from c3parr.library.arr import API_KEY_ENV as ARR_KEY_ENV
from c3parr.library.arr import ArrClient, ArrConfig, ArrKind, latest_major
from c3parr.library.store import Library
from c3parr.library.tasks import Scheduler, default_tasks
from c3parr.llm.client import LLMConfig, make_llm_client
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
    Clients,
    LogBuffer,
    NotFound,
    Service,
    components_for,
    listening,
)

log = logging.getLogger("c3parr.web")
HERE = Path(__file__).parent
DIST = HERE / "dist"
"""O build do frontend (`npm run build` em frontend/). Não vai para o git."""
RunOutput = tuple[RunResult, str | None, str]
POSTER_MAX_BYTES = 5 * 1024 * 1024
NOT_BUILT = (
    "c3parr is running, but its interface is not built.\n"
    "From the source tree: cd frontend && npm ci && npm run build, then start c3parr-web again.\n"
    "The API is at /api/v1 (contract at /api/docs).\n"
)

__all__ = ["components_for", "create_app", "effective_secrets", "serve"]


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


def _fetch_poster(lib: Library, kind: ArrKind, url: str) -> bytes | None:
    """Baixa o pôster: da internet, ou a capa guardada no Radarr/Sonarr (pela API dele, que
    aceita a chave). O que não vier como imagem, ou vier grande demais, não serve."""
    try:
        if url.startswith(("http://", "https://")):
            got = httpx.get(url, timeout=15, follow_redirects=True)
            image = got.headers.get("content-type", "").startswith("image/")
            ok = got.status_code == 200 and image and len(got.content) <= POSTER_MAX_BYTES
            return got.content if ok else None
        key = effective_secrets(lib).get(ARR_KEY_ENV[kind], "")
        client = ArrClient(kind, getattr(lib.config(), kind), key)
        try:
            return client.image(url)
        finally:
            client.close()
    except (httpx.HTTPError, ProviderError) as e:
        log.info("poster %s: %s", url, e)
        return None


LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
"""Sempre aceitos: o healthcheck do contêiner e quem está na própria máquina."""


class HostMiddleware:
    """O que Settings → General → Host decide, aplicado a cada requisição, sem reiniciar:

    - URL base: o que chega com o prefixo (`/c3parr/movies`) é atendido como se não tivesse. O
      que chega sem ele também é: serve ao proxy que tira o prefixo antes de repassar, e a quem
      entra direto pela porta para consertar um prefixo errado.
    - Hosts aceitos: com a lista preenchida, um cabeçalho Host fora dela é recusado.
    """

    def __init__(self, app: Any, svc: Service) -> None:
        self.app = app
        self.svc = svc

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        host = self.svc.host()
        if host.allowed:
            header = dict(scope["headers"]).get(b"host", b"").decode("latin-1").lower()
            name = header[1:].split("]")[0] if header.startswith("[") else header.split(":")[0]
            if name not in host.allowed and name not in LOCAL_HOSTS:
                refused = PlainTextResponse(
                    f"c3parr does not answer as {name or 'this host'}. Add it to "
                    "Settings → General → Allowed hosts.\n", status_code=400)  # fmt: skip
                await refused(scope, receive, send)
                return
        base, path = host.url_base, scope["path"]
        if base and (path == base or path.startswith(base + "/")):
            scope = {**scope, "path": path[len(base) :] or "/"}
            scope["raw_path"] = scope["path"].encode()
        await self.app(scope, receive, send)


def serve_spa(app: FastAPI, dist: Path, svc: Service) -> None:
    """Serve a interface: os arquivos do build e, para qualquer outro endereço, a página dela
    (as rotas /movies, /tv/3… são resolvidas no navegador). A página sai com a URL base e o nome
    da instalação escritos nela: é dali que a interface sabe onde está."""
    root = dist.resolve()
    built = (root / "index.html").exists()
    template = (root / "index.html").read_text(encoding="utf-8") if built else ""
    if built:
        app.mount("/assets", StaticFiles(directory=root / "assets"), name="assets")
    fresh = {"Cache-Control": "no-cache"}  # a página e o service worker sempre na versão atual

    def index() -> HTMLResponse:
        host = svc.host()
        boot = json.dumps({"base": host.url_base, "name": host.name}).replace("<", "\\u003c")
        head = f'<head><base href="{host.url_base}/"><script>window.__C3PARR__={boot}</script>'
        page = template.replace("<head>", head, 1)
        title = f"<title>{html.escape(host.name)}</title>"
        return HTMLResponse(re.sub(r"<title>.*?</title>", lambda _: title, page, count=1),
                            headers=fresh)  # fmt: skip

    def manifest(file: Path) -> Response:
        data = json.loads(file.read_text(encoding="utf-8"))
        data["name"] = data["short_name"] = svc.host().name  # o app instalado leva o nome
        return Response(json.dumps(data), media_type="application/manifest+json", headers=fresh)

    @app.get("/{path:path}", include_in_schema=False, response_model=None)
    def page(path: str) -> Response:
        if path.startswith(("api/", "assets/")):
            raise HTTPException(404)
        if not built:
            return PlainTextResponse(NOT_BUILT, status_code=503)
        file = (root / path).resolve()
        if path and file.is_file() and root in file.parents:  # favicon, sw.js, ícones
            return manifest(file) if file.suffix == ".webmanifest" else FileResponse(
                file, headers=fresh)  # fmt: skip
        return index()


def create_app(
    settings: Settings | None = None,
    queue: JobQueue | None = None,
    runner: Runner | None = None,
    start_worker: bool = True,
    library: Library | None = None,
    dist: Path | None = None,
) -> FastAPI:
    """`dist`: onde está o build da interface (o padrão é o que vai no pacote)."""
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
        planned=[s.name for s in full_recipe.recipe().steps],
        themes=["robo"],
        clients=Clients(arr=_arr, tmdb=_tmdb, llm=_llm, remote_asr=_remote_asr,
                        env_secrets=_env_secrets, latest_major=_latest_major,
                        fetch_poster=lambda kind, url: _fetch_poster(lib, kind, url)),
    )  # fmt: skip

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
        """O contrato da API: só as rotas JSON entram."""
        if app.openapi_schema is None:
            schema = get_openapi(title="c3parr", version=__version__, routes=app.routes)
            schema["paths"] = {path: item for path, item in schema["paths"].items()
                               if path.startswith("/api/v1/")}  # fmt: skip
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = api_schema  # type: ignore[method-assign]
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

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    serve_spa(app, dist or DIST, svc)
    app.add_middleware(HostMiddleware, svc=svc)
    return app


def serve() -> None:
    import uvicorn

    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt)
    host, port = listening()
    uvicorn.run(create_app(), host=host, port=port)
