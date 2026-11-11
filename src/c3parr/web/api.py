"""API JSON (`/api/v1`): o que as telas mostram e fazem, para a interface que roda no navegador.

As regras são as do `Service`, as mesmas das rotas HTML. Aqui só se traduz para os formatos de
`schemas.py`. Ação que dá errado por regra (nada escolhido, conexão recusada) responde
`{"ok": false, "message": …}`; o que não existe responde 404.
"""

from collections.abc import Iterable
from typing import Any

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from c3parr import REPO_URL, __version__
from c3parr.jobs.queue import FINISHED, Job
from c3parr.library.store import LibraryItem
from c3parr.web import schemas as s
from c3parr.web.service import (
    ACTIVE,
    LANGUAGE_NAMES,
    LANGUAGES,
    PAGE_SIZE,
    SORTS,
    STEP_LABELS,
    Outcome,
    Service,
)

POSTER_MAX_AGE = 7 * 24 * 3600


def release_url() -> str:
    """A página da versão no GitHub; versão de desenvolvimento aponta para a lista."""
    released = __version__.count(".") == 2 and __version__.startswith("1.")
    return f"{REPO_URL}/releases/tag/v{__version__}" if released else f"{REPO_URL}/releases"


def job_row(job: Job, item: LibraryItem | None = None) -> s.JobRow:
    return s.JobRow(
        id=job.id,
        name=job.name,
        video=job.video,
        status=s.JOB_STATUS[job.status],
        action=job.acao,
        language=job.idioma,
        step=job.step_label,
        progress=job.progresso,
        message=job.mensagem,
        error=job.erro,
        priority=bool(job.prioridade),
        origin=job.origem,
        created_at=job.criada_em,
        started_at=job.iniciada_em,
        finished_at=job.terminada_em,
        item=s.JobItem(**item.model_dump()) if item else None,
    )


def job_ref(job: Job | None, positions: dict[int, int]) -> s.JobRef | None:
    if job is None:
        return None
    status = "running" if job.status == "processando" else "queued"
    return s.JobRef(id=job.id, label=job.step_label, language=job.idioma, status=status,
                    progress=job.progresso, position=positions.get(job.id))  # fmt: skip


def subs(row: dict[str, Any]) -> list[s.Sub]:
    return [s.Sub(lang=lang, kind=s.SUB_KIND[kind]) for lang, kind in row["subs"]]


def poster(kind: str, item: Any) -> str | None:
    return f"/api/v1/poster/{kind}/{item.id}" if item.poster else None


def result(outcome: Outcome) -> JSONResponse:
    body = s.ActionResult(ok=not outcome.error, message=outcome.message)
    return JSONResponse(body.model_dump(), status_code=outcome.status)


def build_api(svc: Service) -> APIRouter:
    api = APIRouter(prefix="/api/v1")

    def job_rows(jobs: Iterable[Job]) -> list[s.JobRow]:
        found = list(jobs)
        items = svc.items_for(found)
        return [job_row(j, items.get(j.video)) for j in found]

    def job_detail(detail: dict[str, Any]) -> s.JobDetail:
        job: Job = detail["job"]
        extra = svc.job_extra(job)
        steps = [s.Step(label=st["label"], state=s.STEP_STATE[st["state"]], text=st["text"])
                 for st in detail["steps"]]  # fmt: skip
        pipeline = [s.PipelineStep(**{**p, "state": s.PIPELINE_STATE[p["state"]]})
                    for p in extra["pipeline"]]  # fmt: skip
        warnings = [s.Degraded(stage=d.get("stage", ""), reason=d.get("reason", ""),
                               label=STEP_LABELS.get(d.get("stage", ""), d.get("stage", "")),
                               effect=d.get("effect", ""))
                    for d in job.degradacoes]  # fmt: skip
        return s.JobDetail(
            **job_rows([job])[0].model_dump(),
            stage=extra["stage"],
            steps=steps,
            pipeline=pipeline,
            warnings=warnings,
            has_subtitle=bool(job.legenda),
            has_report=bool(job.relatorio),
            can_cancel=job.status in ACTIVE,
            can_retry=job.status in FINISHED,
        )

    # --- casca e visão geral ---

    @api.get("/shell")
    def shell() -> s.Shell:
        data = svc.shell()
        running = data["running"]
        return s.Shell(
            version=__version__,
            repo_url=REPO_URL,
            release_url=release_url(),
            theme=data["theme"],
            pending=data["pending"],
            alerts_unseen=data["alerts_unseen"],
            setup=[s.SetupItem(**item) for item in data["setup"]],
            movies=data["movies"],
            shows=data["shows"],
            running=job_rows([running])[0] if running else None,
        )

    @api.get("/dashboard")
    def dashboard() -> s.Dashboard:
        data = svc.dashboard_data()
        languages = [
            s.LanguageRow(lang=r["lang"], name=LANGUAGE_NAMES.get(r["lang"], r["lang"]),
                          embedded=r["embedded"], external=r["human"], ours=r["ours"],
                          missing=r["missing"], target=r["target"])
            for r in data["languages"]
        ]  # fmt: skip
        return s.Dashboard(
            movies=s.LibraryCount(**data["movies"]),
            episodes=s.EpisodeCount(**data["episodes"]),
            activity=s.Activity(**data["activity"]),
            made=[s.LanguageCount(lang=lang, count=n) for lang, n in data["made"]],
            languages=languages,
            alerts_open=data["alerts_open"],
            warnings=[s.Notice(**w) for w in data["warnings"]],
        )

    @api.get("/search")
    def search(q: str = "") -> s.SearchResults:
        found = svc.search(q)
        return s.SearchResults(
            movies=[s.SearchHit(id=m.id, title=m.title, year=m.year, poster=poster("movie", m))
                    for m in found["movies"]],
            shows=[s.SearchHit(id=sh.id, title=sh.title, year=sh.year, poster=poster("series", sh))
                   for sh in found["shows"]],
        )  # fmt: skip

    # --- biblioteca ---

    @api.get("/movies")
    def movies(q: str = "", show: str = "", page: int = 1, sort: str = "title") -> s.MoviePage:
        """`sort`: `title`, `added` (mais novos primeiro) ou `missing` (o que falta primeiro)."""
        data = svc.movies_data(q, show, page, sort if sort in SORTS else "title")
        refs, positions = svc.job_refs(), svc.queue_positions()
        rows = [
            s.MovieRow(id=r["item"].id, title=r["item"].title, year=r["item"].year,
                       folder=r["item"].folder, video=r["item"].video,
                       monitored=r["item"].monitored, added_at=r["item"].added_at,
                       subs=subs(r), missing=r["missing"],
                       job=job_ref(refs.get(r["item"].video), positions),
                       poster=poster("movie", r["item"]))
            for r in data["rows"]
        ]  # fmt: skip
        pager = data["pager"]
        return s.MoviePage(rows=rows, total=pager["total"], page=pager["page"],
                           pages=pager["pages"], counts=data["counts"])  # fmt: skip

    @api.get("/shows")
    def shows(q: str = "", show: str = "", page: int = 1, sort: str = "title") -> s.ShowPage:
        data = svc.shows_data(q, show, page, sort if sort in SORTS else "title")
        rows = [
            s.ShowRow(id=r["item"].id, title=r["item"].title, year=r["item"].year,
                      folder=r["item"].folder, monitored=r["item"].monitored,
                      added_at=r["item"].added_at, total=r["total"], done=r["done"],
                      missing=r["missing"], queued=r["queued"],
                      poster=poster("series", r["item"]))
            for r in data["rows"]
        ]  # fmt: skip
        pager = data["pager"]
        return s.ShowPage(rows=rows, total=pager["total"], page=pager["page"],
                          pages=pager["pages"], counts=data["counts"])  # fmt: skip

    @api.get("/shows/{show_id}")
    def show(show_id: int) -> s.ShowDetail:
        data = svc.show(show_id)
        refs, positions = svc.job_refs(), svc.queue_positions()
        seasons = [
            s.Season(season=season, episodes=[
                s.EpisodeRow(id=r["item"].id, season=r["item"].season,
                             episode=r["item"].episode, title=r["item"].title, name=r["name"],
                             video=r["item"].video, subs=subs(r), missing=r["missing"],
                             job=job_ref(refs.get(r["item"].video), positions))
                for r in rows])
            for season, rows in data["seasons"]
        ]  # fmt: skip
        item = data["show"]
        return s.ShowDetail(id=item.id, title=item.title, year=item.year, folder=item.folder,
                            monitored=item.monitored, poster=poster("series", item),
                            seasons=seasons)  # fmt: skip

    @api.get("/poster/{kind}/{item_id}", response_class=FileResponse)
    def poster_image(kind: str, item_id: int) -> FileResponse:
        """O pôster do filme (`movie`) ou da série (`series`), guardado no cache daqui."""
        path = svc.poster_file(kind, item_id)
        return FileResponse(path, headers={"Cache-Control": f"max-age={POSTER_MAX_AGE}"})

    @api.post("/library/{kind}/{item_id}/monitor")
    def monitor(kind: str, item_id: int, body: s.MonitorRequest | None = None) -> s.Monitored:
        wanted = body.monitored if body else None
        return s.Monitored(monitored=svc.set_monitored(kind, item_id, wanted).monitored)

    @api.post("/library/queue", response_model=s.ActionResult)
    def add_to_queue(body: s.QueueRequest) -> JSONResponse:
        return result(svc.add_to_queue(body.items))

    # --- fila ---

    @api.get("/queue")
    def queue(q: str = "", status: str = "", page: int = 1) -> s.JobPage:
        data = svc.queue(q, status, page)
        pager = data["pager"]
        return s.JobPage(rows=job_rows(data["jobs"]), total=pager["total"],
                         page=pager["page"], pages=pager["pages"],
                         counts=svc.queue_counts(q))  # fmt: skip

    @api.get("/queue/upcoming")
    def upcoming(limit: int = PAGE_SIZE) -> list[s.JobRow]:
        """As que esperam, na ordem em que vão rodar."""
        return job_rows(svc.upcoming(min(max(limit, 1), PAGE_SIZE)))

    @api.get("/queue/recent")
    def recent(limit: int = 10) -> list[s.JobRow]:
        """As que terminaram por último."""
        return job_rows(svc.recent(min(max(limit, 1), PAGE_SIZE)))

    @api.post("/queue/cancel-queued", response_model=s.ActionResult)
    def cancel_queued() -> JSONResponse:
        return result(svc.cancel_queued())

    @api.post("/queue/retry-failed", response_model=s.ActionResult)
    def retry_failed() -> JSONResponse:
        return result(svc.retry_failed())

    @api.get("/queue/{job_id}")
    def job(job_id: int) -> s.JobDetail:
        return job_detail(svc.job_detail(job_id))

    @api.post("/queue/{job_id}/cancel")
    def cancel(job_id: int) -> s.JobDetail:
        return job_detail(svc.cancel(job_id))

    @api.post("/queue/{job_id}/retry")
    def retry(job_id: int) -> s.JobDetail:
        return job_detail(svc.retry(job_id))

    @api.post("/queue/{job_id}/top")
    def move_to_top(job_id: int) -> s.JobDetail:
        """Passa a tarefa que espera na frente de todas as outras."""
        return job_detail(svc.move_to_top(job_id))

    @api.get("/queue/{job_id}/subtitle", response_class=FileResponse)
    def subtitle(job_id: int) -> FileResponse:
        path = svc.subtitle_file(job_id)
        return FileResponse(path, filename=path.name)

    @api.get("/queue/{job_id}/report", response_class=PlainTextResponse)
    def report(job_id: int) -> str:
        return svc.report_text(job_id)

    # --- settings ---

    @api.get("/settings")
    def settings() -> s.SettingsView:
        view = svc.settings_view("library")
        config, defaults = view["config"], view["llm_defaults"]
        providers = [
            s.LlmProvider(id=key, label=label, site=view["llm_sites"].get(key, ""),
                          default_url=defaults[key][0], default_model=defaults[key][1],
                          key=view["llm_keys"][key], effort=key in view["llm_effort_flavors"])
            for key, label in view["llm_providers"]
        ]  # fmt: skip
        engines = [
            s.EngineInterface(id=i["id"], label=i["label"], selected=i["selected"],
                              providers=[s.EngineChoice(id=p.id, description=p.description)
                                         for p in i["providers"]])
            for i in view["interfaces"]
        ]  # fmt: skip
        return s.SettingsView(
            config=config,
            shown=s.SettingsShown(
                llm_model=config.llm_model or defaults[config.llm_provider][1],
                llm_base_url=config.llm_base_url or defaults["custom"][0],
                llm_model_from_env=view["llm_model_from_env"],
                llm_url_from_env=view["llm_url_from_env"],
            ),
            secrets=view["secret_state"],
            tests={target: s.TestRecord(**record) for target, record in svc.last_tests().items()},
            options=s.SettingsOptions(
                languages=[s.Language(code=code, name=name) for code, name in LANGUAGES],
                llm_providers=providers,
                series_types=list(view["series_types"]),
                engines=engines,
                themes=view["themes"],
                arr_keys=dict(view["arr_keys"]),
                tmdb_builtin=view["tmdb_builtin"],
                tmdb_attribution=view["tmdb_attribution"],
            ),
        )

    @api.patch("/settings", response_model=s.ActionResult)
    def save_settings(body: s.SettingsPatch) -> JSONResponse:
        return result(svc.patch_settings(body.changes, body.secrets))

    @api.post("/settings/test/{target}", response_model=s.TestResult)
    def test_connection(target: str, body: s.SettingsPatch | None = None) -> JSONResponse:
        """Testa com o que está na tela (o salvo com `changes` por cima), sem salvar."""
        screen = body or s.SettingsPatch()
        outcome = svc.test_screen(target, screen.changes, screen.secrets)
        record = svc.last_tests().get(target, {})
        tested = s.TestResult(ok=not outcome.error, message=outcome.message, ms=record.get("ms"))
        return JSONResponse(tested.model_dump(), status_code=outcome.status)

    # --- system ---

    @api.get("/system/tasks")
    def tasks() -> list[s.TaskRow]:
        return [s.TaskRow(**row) for row in svc.task_rows()]

    @api.post("/system/tasks/{name}/run", response_model=s.ActionResult)
    def run_task(name: str) -> JSONResponse:
        return result(svc.run_task(name))

    @api.get("/system/alerts")
    def alerts() -> s.Alerts:
        data = svc.alerts_data()
        steps = {**data["step_names"], "sondar": "Read the video"}  # alerta que não é passo
        rows = [
            s.AlertRow(key=a.key, kind=a.kind, step=steps.get(a.kind, a.kind),
                       message=a.message, target=a.target, count=a.count, first_at=a.first_at,
                       last_at=a.last_at, seen=a.seen, resolved_at=a.resolved_at,
                       items=data["names"].get(a.key, []))
            for a in data["alerts"]
        ]  # fmt: skip
        return s.Alerts(setup=[s.SetupItem(**item) for item in data["setup"]], alerts=rows)

    @api.post("/system/alerts/seen")
    def alerts_seen() -> s.ActionResult:
        """A tela de alertas chama ao abrir: os abertos passam a contar como vistos."""
        svc.mark_alerts_seen()
        return s.ActionResult(ok=True, message="")

    @api.post("/system/alerts/retry", response_model=s.ActionResult)
    def retry_alert(body: s.AlertRetry) -> JSONResponse:
        return result(svc.retry_alert(body.key))

    @api.get("/system/logs")
    def logs() -> list[s.LogRow]:
        return [s.LogRow(**record) for record in svc.log_records()]

    @api.get("/system/status")
    def status() -> s.Status:
        data = svc.status_view()
        return s.Status(
            about=[s.Fact(label=label, value=value) for label, value in data["about"]],
            providers=[s.ProviderStatus(**p) for p in data["providers"]],
            secrets=[s.SecretSource(name=name, source=source) for name, source in data["secrets"]],
            warnings=[s.Notice(**w) for w in data["warnings"]],
        )

    return api
