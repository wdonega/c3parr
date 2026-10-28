"""Rotinas agendadas (System → Tasks): sync com o Radarr/Sonarr e busca de legendas que faltam.

A busca só põe na fila o que está monitorado, ainda não tem legenda no idioma de destino e
apareceu há mais tempo que a espera configurada. Vídeo que já teve tarefa (mesmo falha ou
cancelada) não volta sozinho: repetir é com o botão da fila, para não ficar num laço caro.
"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from c3parr.core.models import Model
from c3parr.jobs.queue import JobQueue
from c3parr.library.sidecars import has_language, subtitle_languages
from c3parr.library.store import AppConfig, Library, now
from c3parr.library.sync import sync_radarr, sync_sonarr

log = logging.getLogger("c3parr.tasks")


class Wanted(Model):
    """Vídeo monitorado sem legenda no idioma de destino."""

    kind: str
    item_id: int
    title: str
    video: str
    added_at: str
    ready_at: str


def wanted(library: Library, config: AppConfig | None = None) -> list[Wanted]:
    config = config or library.config()
    wait = timedelta(minutes=config.wait_minutes)
    out: list[Wanted] = []

    def add(kind: str, item_id: int, title: str, video: str, added_at: str) -> None:
        if has_language(subtitle_languages(Path(video)), config.target_language):
            return
        ready = datetime.fromisoformat(added_at) + wait
        out.append(Wanted(kind=kind, item_id=item_id, title=title, video=video,
                          added_at=added_at, ready_at=ready.isoformat()))  # fmt: skip

    for m in library.movies():
        if m.monitored:
            add("movie", m.id, f"{m.title} ({m.year})" if m.year else m.title, m.video, m.added_at)
    shows = {s.id: s for s in library.series_list() if s.monitored}
    for ep in library.episodes():
        show = shows.get(ep.series_id)
        if show is None:
            continue
        has_code = ep.season is not None and ep.episode is not None  # temporada 0 = especiais
        code = f" S{ep.season:02d}E{ep.episode:02d}" if has_code else ""
        add("episode", ep.id, show.title + code, ep.video, ep.added_at)
    return out


def search_missing(library: Library, queue: JobQueue) -> str:
    config = library.config()
    tried = queue.videos_with_jobs(config.target_language)
    current = now()
    queued = waiting = 0
    for w in wanted(library, config):
        if w.video in tried:
            continue
        if datetime.fromisoformat(w.ready_at) > current:
            waiting += 1
            continue
        queue.submit(w.video, config.target_language)
        queued += 1
    return f"{queued} added to the queue, {waiting} still in the wait time"


@dataclass(frozen=True)
class TaskDef:
    name: str
    label: str
    interval: Callable[[AppConfig], timedelta]
    run: Callable[[], str]
    run_at_start: bool = False


def default_tasks(library: Library, queue: JobQueue, api_key: Callable[[str], str],
                  process: Callable[[], str] | None = None) -> list[TaskDef]:  # fmt: skip
    """Sync com o Radarr e o Sonarr (a biblioteca é a deles) e a rodada que decide e enfileira
    o que falta (a pipeline de legendas quando `process` é dado; senão a busca simples antiga,
    usada nos testes da biblioteca). `api_key("radarr")` dá a chave em uso."""
    run_missing = process or (lambda: search_missing(library, queue))
    label = "Process library (decide and queue what is missing)" if process else (
        "Search for missing subtitles")  # fmt: skip
    every = lambda c: timedelta(minutes=c.sync_interval_minutes)  # noqa: E731
    return [
        TaskDef("sync_radarr", "Sync with Radarr", every,
                lambda: sync_radarr(library, api_key("radarr")), run_at_start=True),
        TaskDef("sync_sonarr", "Sync with Sonarr", every,
                lambda: sync_sonarr(library, api_key("sonarr")), run_at_start=True),
        TaskDef("search_missing", label,
                lambda c: timedelta(minutes=c.search_interval_minutes), run_missing),
    ]  # fmt: skip


class Scheduler(threading.Thread):
    """Roda cada rotina no intervalo dela, ou na hora quando pedem pelo botão."""

    def __init__(self, library: Library, tasks: list[TaskDef], tick_s: float = 5.0) -> None:
        super().__init__(name="c3parr-scheduler", daemon=True)
        self.library = library
        self.tasks = {t.name: t for t in tasks}
        self.tick_s = tick_s
        self.running: set[str] = set()
        self._halt = threading.Event()
        config = library.config()
        for t in tasks:
            if library.task(t.name).next_run is None:
                first = now() if t.run_at_start else now() + t.interval(config)
                library.schedule(t.name, first)

    def stop(self) -> None:
        self._halt.set()

    def due(self) -> list[TaskDef]:
        current = now()
        out = []
        for t in self.tasks.values():
            state = self.library.task(t.name)
            next_run = datetime.fromisoformat(state.next_run) if state.next_run else current
            if state.run_now or next_run <= current:
                out.append(t)
        return out

    def run_task(self, task: TaskDef) -> None:
        self.running.add(task.name)
        start = time.monotonic()
        try:
            result = task.run()
        except Exception as e:  # rotina com erro não derruba o agendador
            log.exception("task %s failed", task.name)
            result = f"failed: {e}"
        finally:
            self.running.discard(task.name)
        seconds = time.monotonic() - start
        log.info("task %s: %s (%.1f s)", task.name, result, seconds)
        self.library.task_done(task.name, seconds, result, task.interval(self.library.config()))

    def run_once(self) -> int:
        due = self.due()
        for t in due:
            self.run_task(t)
        return len(due)

    def run(self) -> None:
        while not self._halt.is_set():
            self.run_once()
            self._halt.wait(self.tick_s)
