"""Apoio dos testes da interface, feito para sobreviver à troca dela.

Três peças:
- `seed_world`: uma biblioteca, fila e catálogo de exemplo, sempre iguais;
- `ContextSpy`: guarda o que cada tela recebe para desenhar (os dados, não o HTML);
- `HtmlDriver`: faz as ações e as leituras pela interface de hoje (formulários e páginas).
  Os cenários só falam com o driver em termos do domínio, então a interface nova entra com
  outro driver e os mesmos cenários.
"""

import html
import re
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

import jinja2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from c3parr.core.config import Settings
from c3parr.core.job import ComponentSpec
from c3parr.core.models import Degradation
from c3parr.engine.engine import RunResult, StepReport
from c3parr.jobs.queue import Job, JobQueue
from c3parr.library.arr import ArrConfig
from c3parr.library.store import Library, SubtitleFile
from c3parr.llm.client import LLM_KEY_ENV
from c3parr.pipeline.catalog import Catalog
from c3parr.providers.base import ProviderInfo
from c3parr.web.app import create_app

Runner = Callable[..., tuple[RunResult, str | None, str]]
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:\+00:00|Z)?")
TOAST = re.compile(r'<div class="snackbar active ([a-z]*)\s*" id="toast">(.*?)</div>', re.S)
# o que só existe para desenhar o HTML de hoje (classes, ícones) ou muda a cada versão
PRESENTATION = frozenset({"icons", "step_icons", "status_classes", "step_classes", "level_classes",
                          "version", "repo_url", "release_url"})  # fmt: skip
SUBTITLE_FLAGS = (
    "use_embedded", "embedded_resolves", "external_resolves", "forced_resolves",
    "sync_new_external", "fix_external_in_place", "delete_ours_on_human",
    "new_external_becomes_main", "asr_enabled", "asr_always",
)  # fmt: skip


def fake_runner(outcome: str = "ok") -> Runner:
    """Roda uma tarefa sem modelo nenhum: `ok`, `aviso` (degradação) ou `falha`."""

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


def settings_for(root: Path) -> Settings:
    """Settings que não dependem da máquina: pastas do teste e a transcrição de mentira."""
    return Settings(
        cache_dir=root / "cache",
        config_dir=root,
        theme="robo",
        components={
            "transcricao": ComponentSpec(provider="falso"),
            "escrita_legenda": ComponentSpec(provider="srt"),
            "fonte_legenda": ComponentSpec(provider="arquivo"),
            "sincronizacao": ComponentSpec(provider="texto"),
            "metadados": ComponentSpec(provider="tmdb"),
            "contexto": ComponentSpec(provider="llm"),
            "traducao": ComponentSpec(provider="llm"),
        },
    )


class World(NamedTuple):
    root: Path
    app: FastAPI
    library: Library
    queue: JobQueue
    catalog: Catalog
    ids: dict[str, int]
    """Ids pelos nomes do exemplo: filmes e séries pelo título, tarefas pelo papel (`running`…)."""


def empty_world(root: Path) -> World:
    """Instalação nova: nada configurado, biblioteca e fila vazias."""
    library = Library(root / "c3parr.db")
    queue = JobQueue(root / "cache" / "fila.db")
    app = create_app(settings=settings_for(root), queue=queue, runner=fake_runner(),
                     start_worker=False, library=library)  # fmt: skip
    return World(root, app, library, queue, app.state.catalog, {})


def _video(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return str(path)


def _sub(video: str, lang: str, kind: str, forced: bool = False) -> SubtitleFile:
    path = f"{video}#{lang}" if kind == "embedded" else f"{video}.{lang}.{kind}.srt"
    return SubtitleFile.model_validate(
        {"path": path, "video": video, "lang": lang, "kind": kind, "forced": forced}
    )


def seed_world(root: Path) -> World:
    """O exemplo fixo das telas: 4 filmes, 2 séries, uma tarefa em cada estado, alertas e
    rotinas. Alvos pt-BR e es, para haver itens completos, pela metade e sem nada."""
    world = empty_world(root)
    lib, queue, catalog = world.library, world.queue, world.catalog
    ids = world.ids
    lib.update_config(
        target_languages=("pt-BR", "es"),
        radarr=ArrConfig(enabled=True, host="radarr.test", port=7878, excluded_tags=("skip",),
                         path_mappings=(("/data/movies", str(root / "movies")),)),
        sonarr=ArrConfig(enabled=True, host="", port=8989),
        llm_provider="custom", llm_base_url="http://llm.test:8080/v1", llm_model="modelo-x",
    )  # fmt: skip
    lib.set_secret("RADARR_API_KEY", "radarr-key")
    lib.set_meta("radarr_version", "5.2.0.1")
    lib.set_meta("radarr_latest_major", "6")

    movies: dict[str, tuple[int, bool, list[tuple[Any, ...]]]] = {
        "Incendies": (2010, True, [("pt-BR", "human"), ("es", "human"), ("en", "embedded"),
                                   ("fr", "human", True)]),
        "Parasite": (2019, True, [("pt-BR", "ours"), ("pt", "human")]),
        "Oldboy": (2003, True, []),
        "Roma": (2018, False, [("en", "embedded")]),
    }  # fmt: skip
    videos: dict[str, str] = {}
    for title, (year, _monitored, subs) in movies.items():
        video = _video(root / "movies" / f"{title} ({year})" / f"{title}.mkv")
        videos[title] = video
        lib.upsert_movie(str(Path(video).parent), video, title, year, None, True)
        lib.set_subtitle_files(video, [_sub(video, *s) for s in subs])
    for movie in lib.movies():
        ids[movie.title] = movie.id
        if not movies[movie.title][1]:
            lib.set_monitored("movie", movie.id, False)

    shows: dict[str, tuple[int, list[tuple[Any, ...]]]] = {
        "Supernatural": (2005, [
            (1, 1, "Supernatural - S01E01 - Pilot.mkv",
             [("pt-BR", "human"), ("es", "ours"), ("en", "embedded")]),
            (1, 2, "Supernatural - S01E02 - Wendigo.mkv", [("en", "embedded")]),
            (2, 1, "Supernatural - S02E01 - In My Time of Dying.mkv", []),
            (None, None, "Supernatural - Extra.mkv", [("pt-BR", "human")]),
        ]),
        "Dark": (2017, [(1, 1, "Dark - S01E01.mkv", [("pt-BR", "ours"), ("es", "human")])]),
    }  # fmt: skip
    for title, (year, episodes) in shows.items():
        folder = root / "tv" / title
        ids[title] = lib.upsert_series(str(folder), title, year, None, True)
        for season, number, name, subs in episodes:
            sub_folder = f"Season {season}" if season else "Extras"
            video = _video(folder / sub_folder / name)
            videos[name] = video
            lib.upsert_episode(ids[title], video, season, number)
            lib.set_subtitle_files(video, [_sub(video, *s) for s in subs])

    media = {name: catalog.ensure_media(video)[0].id for name, video in videos.items()}
    pilot, wendigo = "Supernatural - S01E01 - Pilot.mkv", "Supernatural - S01E02 - Wendigo.mkv"
    dying = "Supernatural - S02E01 - In My Time of Dying.mkv"

    # uma tarefa em cada estado; só fica uma na fila por vez, para a ordem ser sempre a mesma
    done, _ = queue.submit_step(media[pilot], videos[pilot], "extrair", "en")
    queue.take_next()
    ok = RunResult(recipe="completa", workdir=root)
    ok.steps += [StepReport(name="audio", status="ok", seconds=3.0),
                 StepReport(name="transcricao", status="pulado", reason="já há legenda"),
                 StepReport(name="traducao", status="cache", provider="llm")]  # fmt: skip
    subtitle = Path(videos[pilot]).with_suffix(".es.c3parr.srt")
    subtitle.write_text("1\n00:00:01,000 --> 00:00:02,000\nHola\n")
    report = Path(videos[pilot]).with_suffix(".review.md")
    report.write_text("# relatório\n")
    queue.finish(done.id, ok, str(subtitle), str(report))
    ids["done"] = done.id

    warned, _ = queue.submit_step(media["Parasite"], videos["Parasite"], "traduzir", "pt-BR")
    queue.take_next()
    partial = RunResult(recipe="completa", workdir=root)
    partial.steps.append(StepReport(name="metadados", status="falhou", reason="TMDb não achou"))
    partial.degradations.append(
        Degradation(stage="metadados", reason="TMDb não achou", effect="nomes sem conferir")
    )
    queue.finish(warned.id, partial, None, str(report))
    ids["warned"] = warned.id

    failed, _ = queue.submit_step(media[wendigo], videos[wendigo], "traduzir", "pt-BR")
    queue.take_next()
    queue.fail(failed.id, "LLM fora do ar")
    ids["failed"] = failed.id

    cancelled = queue.submit(videos["Roma"], "pt-BR")  # tarefa antiga, sem passo
    queue.cancel(cancelled.id)
    ids["cancelled"] = cancelled.id

    running, _ = queue.submit_step(media["Oldboy"], videos["Oldboy"], "traduzir", "pt-BR",
                                   origem="manual")  # fmt: skip
    queue.take_next()
    queue.progress(running.id, "traducao", 0.47, "bloco 44/94")
    ids["running"] = running.id

    queued, _ = queue.submit_step(media[dying], videos[dying], "traduzir", "pt-BR")
    ids["queued"] = queued.id

    catalog.record_step(media[pilot], "extrair", "en", "ok")
    catalog.record_step(media["Parasite"], "traduzir", "pt-BR", "ok")
    catalog.record_step(media[wendigo], "traduzir", "pt-BR", "falhou", "LLM fora do ar")
    catalog.record_output(media["Parasite"], "pt-BR", subtitle, 1, "translated")
    catalog.record_output(media[pilot], "es", subtitle, 1, "translated")
    for _ in range(2):  # o mesmo problema duas vezes: um alerta só, contando 2
        catalog.raise_alert("traduzir:pt-BR:llm", "traduzir", "LLM fora do ar", "pt-BR",
                            media[wendigo])  # fmt: skip
    catalog.raise_alert("probe:old", "sondar", "could not read old.mkv", "old.mkv", media["Roma"])
    catalog.resolve_alerts(key="probe:old")

    lib.task_done("sync_radarr", 1.5, "4 movies, 0 new", timedelta(minutes=60))
    lib.request_run("sync_sonarr")
    return world


class ContextSpy:
    """Guarda o contexto de cada página desenhada (nome do template e os dados)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.seen: list[tuple[str, dict[str, Any]]] = []
        original = jinja2.Template.render

        def render(template: jinja2.Template, *args: Any, **kwargs: Any) -> str:
            self.seen.append((template.name or "", dict(*args, **kwargs)))
            return original(template, *args, **kwargs)

        monkeypatch.setattr(jinja2.Template, "render", render)

    def last(self) -> tuple[str, dict[str, Any]]:
        return self.seen[-1]


def normalize(value: Any, root: Path) -> Any:
    """Os dados de uma tela em JSON estável: sem horários, sem a pasta do teste."""
    if isinstance(value, Job):
        extra = {"step_label": value.step_label, "label": value.label, "name": value.name}
        return normalize({**value.model_dump(), **extra}, root)
    if isinstance(value, BaseModel):
        return normalize(value.model_dump(), root)
    if isinstance(value, ProviderInfo):
        return {"id": value.id, "description": value.description}
    if isinstance(value, Mapping):
        items = sorted(value.items(), key=lambda kv: str(kv[0]))
        return {str(k): normalize(v, root) for k, v in items}
    if isinstance(value, set | frozenset):
        return sorted(normalize(v, root) for v in value)
    if isinstance(value, list | tuple | deque):
        return [normalize(v, root) for v in value]
    if isinstance(value, datetime):
        return "<ts>"
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, Path):
        value = str(value)
    if isinstance(value, str):
        return TIMESTAMP.sub("<ts>", value.replace(str(root), "<root>"))
    if value is None or isinstance(value, bool | int | float):
        return value
    return f"<{type(value).__name__}>"


def screen_data(context: Mapping[str, Any], root: Path) -> dict[str, Any]:
    """O que a tela recebeu, sem o que é só apresentação."""
    data = normalize({k: v for k, v in context.items() if k not in PRESENTATION}, root)
    assert isinstance(data, dict)
    return data


class Result(NamedTuple):
    ok: bool
    message: str
    status: int


class HtmlDriver:
    """A interface de hoje: formulários e páginas HTML. Mesmos métodos que o driver da nova."""

    def __init__(self, client: TestClient, spy: ContextSpy, root: Path) -> None:
        self.client = client
        self.spy = spy
        self.root = root

    # --- base ---

    def _toast(self, response: Any) -> Result:
        found = TOAST.search(response.text)
        if found is None:
            return Result(response.status_code < 400, response.text, response.status_code)
        return Result(found.group(1) != "error" and response.status_code < 400,
                      html.unescape(found.group(2)).strip(), response.status_code)  # fmt: skip

    def _context(self, path: str, **params: Any) -> dict[str, Any]:
        response = self.client.get(path, params={k: v for k, v in params.items() if v})
        assert response.status_code == 200, f"{path}: {response.status_code}"
        return self.spy.last()[1]

    def status_of(self, path: str) -> int:
        return int(self.client.get(path, follow_redirects=False).status_code)

    # --- leituras ---

    @staticmethod
    def _pager(context: Mapping[str, Any]) -> dict[str, int]:
        pager = context["pager"]
        return {"total": pager["total"], "page": pager["page"], "pages": pager["pages"]}

    @staticmethod
    def _item_row(row: Mapping[str, Any]) -> dict[str, Any]:
        return {"subs": [list(s) for s in row["subs"]], "missing": list(row["missing"]),
                "job": row["job"]}  # fmt: skip

    def movies(self, q: str = "", show: str = "", page: int = 0) -> dict[str, Any]:
        context = self._context("/movies", q=q, show=show, page=page)
        rows = [{"id": r["item"].id, "title": r["item"].title, "year": r["item"].year,
                 "monitored": r["item"].monitored, **self._item_row(r)}
                for r in context["rows"]]  # fmt: skip
        return {"rows": rows, **self._pager(context)}

    def shows(self, q: str = "", show: str = "", page: int = 0) -> dict[str, Any]:
        context = self._context("/tv", q=q, show=show, page=page)
        rows = [{"id": r["item"].id, "title": r["item"].title, "year": r["item"].year,
                 "monitored": r["item"].monitored, "total": r["total"], "done": r["done"],
                 "missing": r["missing"], "queued": r["queued"]}
                for r in context["rows"]]  # fmt: skip
        return {"rows": rows, **self._pager(context)}

    def show(self, show_id: int) -> dict[str, Any]:
        context = self._context(f"/tv/{show_id}")
        seasons = [{"season": season,
                    "episodes": [{"id": r["item"].id, "episode": r["item"].episode,
                                  "name": r["name"], **self._item_row(r)} for r in rows]}
                   for season, rows in context["seasons"]]  # fmt: skip
        item = context["show"]
        return {"title": item.title, "year": item.year, "monitored": item.monitored,
                "seasons": seasons}  # fmt: skip

    def queue(self, q: str = "", status: str = "", page: int = 0) -> dict[str, Any]:
        context = self._context("/queue/rows", q=q, status=status, page=page)
        rows = [{"id": j.id, "name": j.name, "status": j.status, "step": j.step_label,
                 "progress": j.progresso, "message": j.mensagem, "error": j.erro,
                 "priority": bool(j.prioridade)} for j in context["jobs"]]  # fmt: skip
        return {"rows": rows, **self._pager(context)}

    def job(self, job_id: int) -> dict[str, Any] | None:
        response = self.client.get(f"/queue/{job_id}")
        if response.status_code == 404:
            return None
        context = self.spy.last()[1]
        job: Job = context["job"]
        return {"status": job.status, "step": job.step_label, "error": job.erro,
                "steps": [dict(s) for s in context["steps"]],
                "warnings": [dict(d) for d in job.degradacoes],
                "has_subtitle": bool(job.legenda), "has_report": bool(job.relatorio)}  # fmt: skip

    def pending(self) -> int:
        return int(self._context("/queue/count")["pending"])

    def setup(self) -> list[str]:
        return [s["title"] for s in self._context("/dashboard")["setup"]]

    def alerts_badge(self) -> int:
        return int(self._context("/dashboard")["alerts_unseen"])

    def alerts(self) -> list[dict[str, Any]]:
        """Abrir a página de alertas marca os abertos como vistos."""
        context = self._context("/system/alerts")
        return [{"key": a.key, "message": a.message, "count": a.count, "seen": a.seen,
                 "resolved": a.resolved_at is not None,
                 "items": list(context["names"].get(a.key, []))}
                for a in context["alerts"]]  # fmt: skip

    def tasks(self) -> dict[str, dict[str, Any]]:
        context = self._context("/system/tasks")
        return {t["name"]: {"label": t["label"], "interval": t["interval"], "result": t["result"],
                            "running": t["running"], "requested": t["requested"]}
                for t in context["tasks"]}  # fmt: skip

    def warnings(self) -> list[str]:
        return [w["title"] for w in self._context("/system/status")["warnings"]]

    def secret_state(self) -> dict[str, str]:
        """De onde vem cada chave: `settings`, `environment` ou vazio (não há)."""
        return dict(self._context("/settings/integrations")["secret_state"])

    def download(self, job_id: int, what: str) -> tuple[int, str]:
        response = self.client.get(f"/queue/{job_id}/{what}")
        return response.status_code, response.text

    # --- ações ---
    # O navegador manda o formulário inteiro: o que a tela mostra, com o que a pessoa mudou por
    # cima. Os métodos fazem igual: leem a tela e trocam só os campos passados.

    def _arr_state(self, kind: str) -> dict[str, Any]:
        context = self._context(f"/settings/library/{kind}")
        config = context["config"]
        arr: ArrConfig = getattr(config, kind)
        return {
            "enabled": arr.enabled, "host": arr.host, "port": arr.port, "base_url": arr.base_url,
            "ssl": arr.ssl, "timeout_s": arr.timeout_s, "only_monitored": arr.only_monitored,
            "excluded_tags": list(arr.excluded_tags),
            "excluded_series_types": list(arr.excluded_series_types),
            "exclude_season_zero": arr.exclude_season_zero,
            "path_mappings": list(arr.path_mappings),
            "api_key": context["secret_values"].get(f"{kind.upper()}_API_KEY", ""),
            "monitor_new": config.monitor_new,
            "sync_interval_minutes": config.sync_interval_minutes,
        }  # fmt: skip

    @staticmethod
    def _arr_form(kind: str, state: Mapping[str, Any]) -> dict[str, Any]:
        data: dict[str, Any] = {
            f"{kind}_host": state["host"],
            f"{kind}_port": "" if state["port"] is None else str(state["port"]),
            f"{kind}_base_url": state["base_url"], f"{kind}_timeout_s": str(state["timeout_s"]),
            f"{kind}_excluded_tags": ", ".join(state["excluded_tags"]),
            f"{kind}_map_remote": [r for r, _ in state["path_mappings"]],
            f"{kind}_map_local": [lo for _, lo in state["path_mappings"]],
            f"{kind.upper()}_API_KEY": state["api_key"],
            "sync_interval_minutes": str(state["sync_interval_minutes"]),
        }  # fmt: skip
        flags = {f"{kind}_enabled": state["enabled"], f"{kind}_ssl": state["ssl"],
                 f"{kind}_only_monitored": state["only_monitored"],
                 "monitor_new": state["monitor_new"]}  # fmt: skip
        if kind == "sonarr":  # só a aba do Sonarr tem tipos de série e temporada zero
            data["sonarr_excluded_series_types"] = list(state["excluded_series_types"])
            flags["sonarr_exclude_season_zero"] = state["exclude_season_zero"]
        data.update({name: "1" for name, on in flags.items() if on})
        return data

    def save_library(self, kind: str, **changes: Any) -> Result:
        """Campos: enabled, host, port, base_url, ssl, timeout_s, api_key, only_monitored,
        excluded_tags, excluded_series_types, exclude_season_zero, path_mappings, monitor_new,
        sync_interval_minutes."""
        state = self._arr_state(kind)
        assert changes.keys() <= state.keys(), changes.keys() - state.keys()
        form = self._arr_form(kind, {**state, **changes})
        return self._toast(self.client.post("/settings/library", data=form))

    def _integrations_state(self) -> dict[str, Any]:
        context = self._context("/settings/integrations")
        config, defaults = context["config"], context["llm_defaults"]
        names = (*LLM_KEY_ENV.values(), "C3PARR_ASR_API_KEY", "TMDB_READ_ACCESS_TOKEN")
        return {
            "llm_provider": config.llm_provider,
            "llm_model": config.llm_model or defaults[config.llm_provider][1],
            "llm_base_url": config.llm_base_url or defaults["custom"][0],
            "llm_effort": config.llm_effort, "asr_mode": config.asr_mode,
            "asr_url": config.asr_url, "asr_model": config.asr_model,
            "secrets": {name: context["secret_values"].get(name, "") for name in names},
            "_defaults": defaults,
        }  # fmt: skip

    @staticmethod
    def _integrations_form(state: Mapping[str, Any], changes: Mapping[str, Any]) -> dict[str, Any]:
        merged = {**state, **{k: v for k, v in changes.items() if k != "secrets"}}
        # como o script da página: trocar de provedor leva o modelo padrão do novo, a não ser
        # que o modelo tenha sido escolhido à mão
        old, new = state["llm_provider"], merged["llm_provider"]
        if new != old and "llm_model" not in changes:
            defaults = state["_defaults"]
            if state["llm_model"] in ("", defaults[old][1]):
                merged["llm_model"] = defaults[new][1]
        data = {k: v for k, v in merged.items() if k not in ("secrets", "_defaults")}
        data.update({**state["secrets"], **changes.get("secrets", {})})
        return data

    def save_integrations(self, **changes: Any) -> Result:
        """Campos: llm_provider, llm_model, llm_base_url, llm_effort, asr_mode, asr_url,
        asr_model e `secrets` (nome → valor; vazio apaga)."""
        form = self._integrations_form(self._integrations_state(), changes)
        return self._toast(self.client.post("/settings/integrations", data=form))

    def save_languages(self, targets: Iterable[str] | None = None,
                       fallback: Iterable[str] | None = None) -> Result:  # fmt: skip
        config = self._context("/settings/languages")["config"]
        chosen = list(config.target_languages if targets is None else targets)
        order = list(config.fallback_languages if fallback is None else fallback)
        data = {"target_languages": chosen, "fallback_languages": ", ".join(order)}
        return self._toast(self.client.post("/settings/languages", data=data))

    def save_subtitles(self, **changes: Any) -> Result:
        """Campos: os liga/desliga de legendas, derived_on_main_change, foreign_tag,
        wait_minutes e search_interval_minutes."""
        config = self._context("/settings/subtitles")["config"]
        names = (*SUBTITLE_FLAGS, "derived_on_main_change", "foreign_tag", "wait_minutes",
                 "search_interval_minutes")  # fmt: skip
        assert changes.keys() <= set(names), changes.keys() - set(names)
        state = {**{name: getattr(config, name) for name in names}, **changes}
        data = {name: str(state[name]) for name in names if name not in SUBTITLE_FLAGS}
        data.update({name: "1" for name in SUBTITLE_FLAGS if state[name]})
        return self._toast(self.client.post("/settings/subtitles", data=data))

    def save_engines(self, **changes: str) -> Result:
        """Interface → motor (`sincronizacao="tempo"`)."""
        shown = self._context("/settings/engines")["interfaces"]
        data = {**{i["id"]: i["selected"] for i in shown}, **changes}
        return self._toast(self.client.post("/settings/engines", data=data))

    def test_connection(self, target: str, **changes: Any) -> Result:
        """Testa com o que está na tela, sem salvar. radarr/sonarr: os campos de `save_library`.
        llm, tmdb e asr: os de `save_integrations`."""
        if target in ("radarr", "sonarr"):
            form = self._arr_form(target, {**self._arr_state(target), **changes})
        else:
            form = self._integrations_form(self._integrations_state(), changes)
        return self._toast(self.client.post(f"/settings/test/{target}", data=form))

    def add_to_queue(self, *keys: str) -> Result:
        """`movie:1`, `episode:7` ou `series:2`."""
        return self._toast(self.client.post("/library/queue", data={"item": list(keys)}))

    def toggle_monitor(self, kind: str, item_id: int) -> bool | None:
        """O estado novo, ou None se o item não existe."""
        response = self.client.post(f"/library/{kind}/{item_id}/monitor")
        if response.status_code == 404:
            return None
        return bool(self.spy.last()[1]["item"].monitored)

    def cancel(self, job_id: int) -> int:
        return int(self.client.post(f"/queue/{job_id}/cancel").status_code)

    def retry(self, job_id: int) -> int:
        return int(self.client.post(f"/queue/{job_id}/retry").status_code)

    def run_task(self, name: str) -> Result:
        return self._toast(self.client.post(f"/system/tasks/{name}/run"))

    def retry_alert(self, key: str) -> Result:
        return self._toast(self.client.post("/system/alerts/retry", data={"key": key}))
