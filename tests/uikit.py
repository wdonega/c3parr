"""Apoio dos testes da interface.

- `seed_world`: uma biblioteca, fila e catálogo de exemplo, sempre iguais;
- `ApiDriver`: faz as ações e as leituras pela API JSON, em termos do domínio. Os cenários só
  falam com ele, então não dependem do formato exato das respostas.
"""

import re
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

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
from c3parr.pipeline.catalog import Catalog
from c3parr.providers.base import ProviderInfo
from c3parr.web.app import create_app

Runner = Callable[..., tuple[RunResult, str | None, str]]
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:\+00:00|Z)?")
# o pôster como o Radarr/Sonarr informa: na internet (TMDb) ou a capa guardada nele
POSTERS = {
    "Incendies": "https://image.tmdb.org/t/p/original/incendies.jpg",
    "Supernatural": "/MediaCover/7/poster.jpg?lastWrite=1",
}
EPISODE_TITLES = {
    "Supernatural - S01E01 - Pilot.mkv": "Pilot",
    "Supernatural - S02E01 - In My Time of Dying.mkv": "In My Time of Dying",
}


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


ORIGINALS = {
    "Incendies": "fr",
    "Parasite": "ko",
    "Oldboy": "ko",
    "Supernatural - S01E01 - Pilot.mkv": "en",
    "Supernatural - S01E02 - Wendigo.mkv": "en",
}


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
        lib.upsert_movie(str(Path(video).parent), video, title, year, None, True,
                         POSTERS.get(title))  # fmt: skip
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
        ids[title] = lib.upsert_series(str(folder), title, year, None, True, POSTERS.get(title))
        for season, number, name, subs in episodes:
            sub_folder = f"Season {season}" if season else "Extras"
            video = _video(folder / sub_folder / name)
            videos[name] = video
            lib.upsert_episode(ids[title], video, season, number, EPISODE_TITLES.get(name))
            lib.set_subtitle_files(video, [_sub(video, *s) for s in subs])

    media = {name: catalog.ensure_media(video)[0].id for name, video in videos.items()}
    # o idioma original de cada um: a legenda dele aparece na biblioteca, junto das dos alvos
    for name, lang in ORIGINALS.items():
        catalog.set_original_language(media[name], lang)
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
    queue.progress(running.id, "traducao", 0.47, "block 44 of 94")
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


class Result(NamedTuple):
    ok: bool
    message: str
    status: int


class ApiDriver:
    """A API JSON, que é o que a interface usa, dita em termos do domínio."""

    def __init__(self, client: TestClient) -> None:
        self.client = client

    # --- base ---

    def _get(self, path: str, **params: Any) -> Any:
        response = self.client.get(f"/api/v1{path}", params={k: v for k, v in params.items() if v})
        assert response.status_code == 200, f"{path}: {response.status_code}"
        return response.json()

    @staticmethod
    def _result(response: Any) -> Result:
        body = response.json()
        if "ok" in body:
            return Result(body["ok"] and response.status_code < 400, body["message"],
                          response.status_code)  # fmt: skip
        return Result(False, str(body.get("detail", "")), response.status_code)

    def _patch(self, changes: Mapping[str, Any], secrets: Mapping[str, Any] | None) -> Result:
        body = {"changes": dict(changes), "secrets": dict(secrets or {})}
        return self._result(self.client.patch("/api/v1/settings", json=body))

    # --- leituras ---

    @staticmethod
    def _paged(data: Mapping[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {"rows": rows, "total": data["total"], "page": data["page"], "pages": data["pages"]}

    @staticmethod
    def _item_row(row: Mapping[str, Any]) -> dict[str, Any]:
        job = row["job"] and {"label": row["job"]["label"], "status": row["job"]["status"]}
        return {"subs": [[sub["lang"], sub["kind"]] for sub in row["subs"]],
                "others": row["others"], "missing": row["missing"], "job": job}  # fmt: skip

    def dashboard(self) -> dict[str, Any]:
        return dict(self._get("/dashboard"))

    def movies(self, q: str = "", show: str = "", page: int = 0) -> dict[str, Any]:
        data = self._get("/movies", q=q, show=show, page=page)
        rows = [{"id": r["id"], "title": r["title"], "year": r["year"],
                 "monitored": r["monitored"], **self._item_row(r)} for r in data["rows"]]  # fmt: skip
        return self._paged(data, rows)

    def shows(self, q: str = "", show: str = "", page: int = 0) -> dict[str, Any]:
        data = self._get("/shows", q=q, show=show, page=page)
        keys = ("id", "title", "year", "monitored", "total", "done", "missing", "queued")
        return self._paged(data, [{k: r[k] for k in keys} for r in data["rows"]])

    def show(self, show_id: int) -> dict[str, Any] | None:
        response = self.client.get(f"/api/v1/shows/{show_id}")
        if response.status_code == 404:
            return None
        data = response.json()
        seasons = [{"season": season["season"],
                    "episodes": [{"id": e["id"], "episode": e["episode"], "name": e["name"],
                                  **self._item_row(e)} for e in season["episodes"]]}
                   for season in data["seasons"]]  # fmt: skip
        return {"title": data["title"], "year": data["year"], "monitored": data["monitored"],
                "seasons": seasons}  # fmt: skip

    def queue(self, q: str = "", status: str = "", page: int = 0) -> dict[str, Any]:
        data = self._get("/queue", q=q, status=status, page=page)
        keys = ("id", "name", "status", "step", "progress", "message", "error", "priority")
        return self._paged(data, [{k: j[k] for k in keys} for j in data["rows"]])

    def job(self, job_id: int) -> dict[str, Any] | None:
        response = self.client.get(f"/api/v1/queue/{job_id}")
        if response.status_code == 404:
            return None
        data = response.json()
        warnings = [{k: w[k] for k in ("stage", "reason", "effect") if w[k]}
                    for w in data["warnings"]]  # fmt: skip
        return {"status": data["status"], "step": data["step"], "error": data["error"],
                "steps": data["steps"], "warnings": warnings,
                "has_subtitle": data["has_subtitle"], "has_report": data["has_report"]}  # fmt: skip

    def pending(self) -> int:
        return int(self._get("/shell")["pending"])

    def setup(self) -> list[str]:
        return [item["title"] for item in self._get("/shell")["setup"]]

    def alerts_badge(self) -> int:
        return int(self._get("/shell")["alerts_unseen"])

    def alerts(self) -> list[dict[str, Any]]:
        """Como a tela de alertas faz: lê e avisa que foram vistos."""
        data = self._get("/system/alerts")
        assert self.client.post("/api/v1/system/alerts/seen").status_code == 200
        return [{"key": a["key"], "message": a["message"], "count": a["count"], "seen": a["seen"],
                 "resolved": a["resolved_at"] is not None, "items": a["items"]}
                for a in data["alerts"]]  # fmt: skip

    def tasks(self) -> dict[str, dict[str, Any]]:
        keys = ("label", "interval", "result", "running", "requested")
        return {t["name"]: {k: t[k] for k in keys} for t in self._get("/system/tasks")}

    def warnings(self) -> list[str]:
        return [w["title"] for w in self._get("/system/status")["warnings"]]

    def secret_state(self) -> dict[str, str]:
        return dict(self._get("/settings")["secrets"])

    def download(self, job_id: int, what: str) -> tuple[int, str]:
        response = self.client.get(f"/api/v1/queue/{job_id}/{what}")
        return response.status_code, response.text

    # --- ações: a API recebe só o que mudou ---

    @staticmethod
    def _arr_body(kind: str, changes: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Separa o que é do Radarr/Sonarr, o que vale para os dois e a chave."""
        shared = {k: changes.pop(k) for k in ("monitor_new", "sync_interval_minutes")
                  if k in changes}  # fmt: skip
        secrets = {}
        if "api_key" in changes:
            secrets[f"{kind.upper()}_API_KEY"] = changes.pop("api_key")
        return {kind: changes, **shared}, secrets

    def save_library(self, kind: str, **changes: Any) -> Result:
        return self._patch(*self._arr_body(kind, changes))

    def save_integrations(self, **changes: Any) -> Result:
        secrets = changes.pop("secrets", None)
        return self._patch(changes, secrets)

    def save_languages(self, targets: Iterable[str] | None = None,
                       fallback: Iterable[str] | None = None) -> Result:  # fmt: skip
        changes: dict[str, Any] = {}
        if targets is not None:
            changes["target_languages"] = list(targets)
        if fallback is not None:
            changes["fallback_languages"] = list(fallback)
        return self._patch(changes, None)

    def save_subtitles(self, **changes: Any) -> Result:
        return self._patch(changes, None)

    def save_engines(self, **changes: str) -> Result:
        return self._patch({"components": changes}, None)

    def test_connection(self, target: str, **changes: Any) -> Result:
        if target in ("radarr", "sonarr"):
            body, secrets = self._arr_body(target, changes)
        else:
            secrets = changes.pop("secrets", None) or {}
            body = changes
        response = self.client.post(f"/api/v1/settings/test/{target}",
                                    json={"changes": body, "secrets": secrets})  # fmt: skip
        return self._result(response)

    def queue_concerts(self) -> Result:
        return self._result(self.client.post("/api/v1/library/queue-concerts"))

    def add_to_queue(self, *keys: str) -> Result:
        return self._result(self.client.post("/api/v1/library/queue", json={"items": list(keys)}))

    def toggle_monitor(self, kind: str, item_id: int) -> bool | None:
        response = self.client.post(f"/api/v1/library/{kind}/{item_id}/monitor")
        if response.status_code == 404:
            return None
        return bool(response.json()["monitored"])

    def cancel(self, job_id: int) -> int:
        return int(self.client.post(f"/api/v1/queue/{job_id}/cancel").status_code)

    def retry(self, job_id: int) -> int:
        return int(self.client.post(f"/api/v1/queue/{job_id}/retry").status_code)

    def run_task(self, name: str) -> Result:
        return self._result(self.client.post(f"/api/v1/system/tasks/{name}/run"))

    def retry_alert(self, key: str) -> Result:
        return self._result(self.client.post("/api/v1/system/alerts/retry", json={"key": key}))
