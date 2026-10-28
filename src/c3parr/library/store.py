"""Banco da biblioteca (SQLite): configs da interface, filmes, séries, episódios e rotinas.

Filme e série só entram aqui pelo sync com o Radarr/Sonarr; a interface nunca cadastra item na mão.
Segredos continuam fora daqui (só no ambiente ou no `.env`).
"""

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from c3parr.core.models import Model
from c3parr.library.arr import ArrConfig

SCHEMA = """
CREATE TABLE IF NOT EXISTS setting (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS movie (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    folder TEXT NOT NULL UNIQUE,
    video TEXT NOT NULL,
    title TEXT NOT NULL,
    year INTEGER,
    tmdb_id INTEGER,
    monitored INTEGER NOT NULL DEFAULT 1,
    added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS series (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    folder TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    year INTEGER,
    tmdb_id INTEGER,
    monitored INTEGER NOT NULL DEFAULT 1,
    added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS episode (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id INTEGER NOT NULL REFERENCES series(id) ON DELETE CASCADE,
    video TEXT NOT NULL UNIQUE,
    season INTEGER,
    episode INTEGER,
    added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS secret (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task (
    name TEXT PRIMARY KEY,
    last_run TEXT,
    last_seconds REAL,
    last_result TEXT,
    next_run TEXT,
    run_now INTEGER NOT NULL DEFAULT 0
);
"""


def now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


class AppConfig(Model):
    """O que se edita em Settings. Cada campo vira uma linha da tabela `setting`."""

    radarr: ArrConfig = Field(default_factory=lambda: ArrConfig(port=7878))
    sonarr: ArrConfig = Field(default_factory=lambda: ArrConfig(port=8989))
    sync_interval_minutes: int = Field(60, ge=5)
    """De quanto em quanto tempo as rotinas leem o Radarr e o Sonarr (60 min, como o Bazarr)."""
    target_languages: tuple[str, ...] = ("pt-BR",)
    """Idiomas que cada item deve ter; o primeiro é o principal da interface."""
    fallback_languages: tuple[str, ...] = ("en", "es", "fr", "it", "de", "pt")
    """Se não houver legenda no idioma original, a principal sai do primeiro destes que houver."""
    monitor_new: bool = True
    """Item novo achado no sync já entra monitorado."""
    wait_minutes: int = Field(60, ge=0)
    """Tempo desde que o arquivo apareceu até a rotina gerar a legenda que falta."""
    search_interval_minutes: int = Field(360, ge=5)
    theme: str = "robo"
    components: dict[str, str] = Field(default_factory=dict)
    """Provider escolhido por interface; o que faltar usa o padrão do `Settings`."""
    llm_provider: str = "custom"
    """custom (servidor local/compatível), openai, anthropic, gemini ou deepseek."""
    llm_base_url: str = ""
    """Endereço da API do LLM (compatível com OpenAI, termina em /v1); vazio = padrão."""
    llm_model: str = ""
    llm_effort: Literal["low", "medium"] = "low"
    """Raciocínio do LLM na Anthropic e no Gemini: low traduz igual e custa menos (decisão 0006)."""
    asr_mode: Literal["local", "openai", "asr-webservice"] = "local"
    """local: nesta máquina (whisper.cpp ou faster-whisper; CPU se não houver GPU). Os outros: um
    servidor de transcrição à parte (outro container), formato OpenAI ou whisper-asr-webservice."""
    asr_url: str = ""
    asr_model: str = ""
    """Só no formato OpenAI; vazio = whisper-1."""

    # --- fontes de legenda ---
    use_embedded: bool = True
    """Usa as legendas embutidas no vídeo (texto) como principal e para resolver idiomas."""
    embedded_resolves: bool = True
    external_resolves: bool = True
    """Legenda de outra fonte ao lado do vídeo (Bazarr, humana) já resolve aquele idioma."""
    forced_resolves: bool = False
    """Legenda forced só tem trechos (placas, falas estrangeiras): por padrão não resolve."""

    # --- quando aparece uma legenda nova ---
    sync_new_external: bool = True
    fix_external_in_place: bool = True
    """Grava o tempo corrigido na própria legenda externa (com cópia `.orig.c3parr`)."""
    delete_ours_on_human: bool = True
    """Apaga a nossa quando aparece uma de outra fonte no mesmo idioma (senão ficam as duas)."""
    new_external_becomes_main: bool = True
    derived_on_main_change: Literal["regenerate", "keep", "mark"] = "regenerate"
    """O que fazer com as traduções quando a principal muda."""

    # --- ASR e tradução ---
    asr_enabled: bool = True
    asr_always: bool = False
    """Roda o ASR mesmo quando já há legenda principal (mais contexto, bem mais lento)."""
    foreign_tag: Literal["first", "every", "off"] = "first"

    @property
    def target_language(self) -> str:
        """O idioma alvo principal (o primeiro da lista)."""
        return self.target_languages[0] if self.target_languages else "pt-BR"


class Movie(Model):
    id: int
    folder: str
    video: str
    title: str
    year: int | None = None
    tmdb_id: int | None = None
    monitored: bool = True
    added_at: str


class Series(Model):
    id: int
    folder: str
    title: str
    year: int | None = None
    tmdb_id: int | None = None
    monitored: bool = True
    added_at: str


class Episode(Model):
    id: int
    series_id: int
    video: str
    season: int | None = None
    episode: int | None = None
    added_at: str


class TaskState(Model):
    name: str
    last_run: str | None = None
    last_seconds: float | None = None
    last_result: str | None = None
    next_run: str | None = None
    run_now: bool = False


class Library:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        with self._db() as db:
            db.executescript(SCHEMA)

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    # --- configs ---

    def config(self) -> AppConfig:
        with self._db() as db:
            rows = db.execute("SELECT key, value FROM setting").fetchall()
        values = {r["key"]: json.loads(r["value"]) for r in rows}
        known = AppConfig.model_fields
        data = {k: v for k, v in values.items() if k in known}
        if "target_languages" not in data and values.get("target_language"):
            data["target_languages"] = [values["target_language"]]  # config antiga: um idioma só
        for kind in ("radarr", "sonarr"):  # config antiga: um mapeamento só, do tempo do webhook
            if kind not in data and values.get("path_mappings"):
                port = 7878 if kind == "radarr" else 8989
                data[kind] = {"port": port, "path_mappings": values["path_mappings"]}
        return AppConfig.model_validate(data)

    def save_config(self, config: AppConfig) -> None:
        values = config.model_dump(mode="json")
        with self._lock, self._db() as db:
            db.executemany(
                "INSERT INTO setting (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                [(k, json.dumps(v, ensure_ascii=False)) for k, v in values.items()],
            )

    def update_config(self, **changes: Any) -> AppConfig:
        if "target_language" in changes:  # atalho antigo: um idioma vira a lista
            changes["target_languages"] = [changes.pop("target_language")]
        config = AppConfig.model_validate({**self.config().model_dump(), **changes})
        self.save_config(config)
        return config

    # --- estado lido de fora (versão do Radarr/Sonarr); mora junto das configs, sem virar uma ---

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._db() as db:
            db.execute(
                "INSERT INTO setting (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (f"meta:{key}", json.dumps(value)),
            )

    def meta(self, key: str) -> str | None:
        with self._db() as db:
            row = db.execute("SELECT value FROM setting WHERE key = ?", (f"meta:{key}",)).fetchone()
        return json.loads(row["value"]) if row else None

    # --- segredos (chaves digitadas em Settings; ficam fora das configs e dos logs) ---

    def secrets(self) -> dict[str, str]:
        with self._db() as db:
            return {r["name"]: r["value"] for r in db.execute("SELECT name, value FROM secret")}

    def set_secret(self, name: str, value: str | None) -> None:
        with self._lock, self._db() as db:
            if value:
                db.execute(
                    "INSERT INTO secret (name, value) VALUES (?, ?) "
                    "ON CONFLICT(name) DO UPDATE SET value = excluded.value",
                    (name, value),
                )
            else:
                db.execute("DELETE FROM secret WHERE name = ?", (name,))

    # --- filmes ---

    def movies(self) -> list[Movie]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM movie ORDER BY title, year").fetchall()
        return [Movie.model_validate(dict(r)) for r in rows]

    def movie(self, movie_id: int) -> Movie:
        with self._db() as db:
            row = db.execute("SELECT * FROM movie WHERE id = ?", (movie_id,)).fetchone()
        if row is None:
            raise KeyError(movie_id)
        return Movie.model_validate(dict(row))

    def upsert_movie(self, folder: str, video: str, title: str, year: int | None,
                     tmdb_id: int | None, monitored: bool) -> bool:  # fmt: skip
        """Grava o filme achado no scan. Devolve True se ele é novo."""
        with self._lock, self._db() as db:
            cur = db.execute(
                "UPDATE movie SET video = ?, title = ?, year = ?, "
                "tmdb_id = COALESCE(?, tmdb_id) WHERE folder = ?",
                (video, title, year, tmdb_id, folder),
            )
            if cur.rowcount:
                return False
            db.execute(
                "INSERT INTO movie (folder, video, title, year, tmdb_id, monitored, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (folder, video, title, year, tmdb_id, int(monitored), now().isoformat()),
            )
            return True

    def movie_by_folder(self, folder: str) -> Movie | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM movie WHERE folder = ?", (folder,)).fetchone()
        return Movie.model_validate(dict(row)) if row else None

    def remove_movies_except(self, folders: set[str]) -> int:
        with self._lock, self._db() as db:
            gone = [r["id"] for r in db.execute("SELECT id, folder FROM movie")
                    if r["folder"] not in folders]  # fmt: skip
            db.executemany("DELETE FROM movie WHERE id = ?", [(i,) for i in gone])
        return len(gone)

    # --- séries ---

    def series_list(self) -> list[Series]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM series ORDER BY title, year").fetchall()
        return [Series.model_validate(dict(r)) for r in rows]

    def series(self, series_id: int) -> Series:
        with self._db() as db:
            row = db.execute("SELECT * FROM series WHERE id = ?", (series_id,)).fetchone()
        if row is None:
            raise KeyError(series_id)
        return Series.model_validate(dict(row))

    def upsert_series(self, folder: str, title: str, year: int | None, tmdb_id: int | None,
                      monitored: bool) -> int:  # fmt: skip
        with self._lock, self._db() as db:
            db.execute(
                "INSERT INTO series (folder, title, year, tmdb_id, monitored, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(folder) DO UPDATE SET "
                "title = excluded.title, year = excluded.year, "
                "tmdb_id = COALESCE(excluded.tmdb_id, series.tmdb_id)",
                (folder, title, year, tmdb_id, int(monitored), now().isoformat()),
            )
            row = db.execute("SELECT id FROM series WHERE folder = ?", (folder,)).fetchone()
        return int(row["id"])

    def upsert_episode(self, series_id: int, video: str, season: int | None,
                       episode: int | None) -> bool:  # fmt: skip
        with self._lock, self._db() as db:
            cur = db.execute(
                "UPDATE episode SET series_id = ?, season = ?, episode = ? WHERE video = ?",
                (series_id, season, episode, video),
            )
            if cur.rowcount:
                return False
            db.execute(
                "INSERT INTO episode (series_id, video, season, episode, added_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (series_id, video, season, episode, now().isoformat()),
            )
            return True

    def episodes(self, series_id: int | None = None) -> list[Episode]:
        sql = "SELECT * FROM episode"
        args: tuple[Any, ...] = ()
        if series_id is not None:
            sql, args = sql + " WHERE series_id = ?", (series_id,)
        with self._db() as db:
            rows = db.execute(sql + " ORDER BY series_id, season, episode, video", args)
            return [Episode.model_validate(dict(r)) for r in rows.fetchall()]

    def episode_by_video(self, video: str) -> Episode | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM episode WHERE video = ?", (video,)).fetchone()
        return Episode.model_validate(dict(row)) if row else None

    def episode(self, episode_id: int) -> Episode:
        with self._db() as db:
            row = db.execute("SELECT * FROM episode WHERE id = ?", (episode_id,)).fetchone()
        if row is None:
            raise KeyError(episode_id)
        return Episode.model_validate(dict(row))

    def remove_series_except(self, folders: set[str], videos: set[str]) -> int:
        with self._lock, self._db() as db:
            eps = [r["id"] for r in db.execute("SELECT id, video FROM episode")
                   if r["video"] not in videos]  # fmt: skip
            db.executemany("DELETE FROM episode WHERE id = ?", [(i,) for i in eps])
            shows = [r["id"] for r in db.execute("SELECT id, folder FROM series")
                     if r["folder"] not in folders]  # fmt: skip
            db.executemany("DELETE FROM series WHERE id = ?", [(i,) for i in shows])
        return len(eps) + len(shows)

    def watch_info(self, video: str) -> tuple[bool, str] | None:
        """(monitorado, quando entrou na biblioteca) do vídeo; None se não está na biblioteca."""
        with self._db() as db:
            row = db.execute("SELECT monitored, added_at FROM movie WHERE video = ?",
                             (video,)).fetchone()  # fmt: skip
            if row is None:
                row = db.execute(
                    "SELECT s.monitored AS monitored, e.added_at AS added_at FROM episode e "
                    "JOIN series s ON s.id = e.series_id WHERE e.video = ?",
                    (video,),
                ).fetchone()
        return (bool(row["monitored"]), str(row["added_at"])) if row else None

    def videos(self) -> list[str]:
        with self._db() as db:
            movies = [r["video"] for r in db.execute("SELECT video FROM movie")]
            eps = [r["video"] for r in db.execute("SELECT video FROM episode")]
        return movies + eps

    def set_monitored(self, kind: str, item_id: int, monitored: bool) -> None:
        table = {"movie": "movie", "series": "series"}[kind]
        with self._lock, self._db() as db:
            db.execute(f"UPDATE {table} SET monitored = ? WHERE id = ?", (int(monitored), item_id))

    # --- rotinas ---

    def task(self, name: str) -> TaskState:
        with self._db() as db:
            row = db.execute("SELECT * FROM task WHERE name = ?", (name,)).fetchone()
        return TaskState.model_validate(dict(row)) if row else TaskState(name=name)

    def schedule(self, name: str, next_run: datetime) -> None:
        with self._lock, self._db() as db:
            db.execute(
                "INSERT INTO task (name, next_run) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET next_run = excluded.next_run",
                (name, next_run.isoformat()),
            )

    def request_run(self, name: str) -> None:
        with self._lock, self._db() as db:
            db.execute(
                "INSERT INTO task (name, run_now) VALUES (?, 1) "
                "ON CONFLICT(name) DO UPDATE SET run_now = 1",
                (name,),
            )

    def task_done(self, name: str, seconds: float, result: str, interval: timedelta) -> None:
        finished = now()
        with self._lock, self._db() as db:
            db.execute("INSERT INTO task (name) VALUES (?) ON CONFLICT(name) DO NOTHING", (name,))
            db.execute(
                "UPDATE task SET last_run = ?, last_seconds = ?, last_result = ?, next_run = ?, "
                "run_now = 0 WHERE name = ?",
                (finished.isoformat(), round(seconds, 2), result,
                 (finished + interval).isoformat(), name),
            )  # fmt: skip
