"""Catálogo da pipeline de legendas (SQLite, mesmo arquivo da biblioteca).

Guarda só o estado e os caminhos; o conteúdo (metadados, transcrição, legendas) fica nos arquivos
`.c3parr` ao lado do vídeo. Tabelas:
- `media`: um vídeo, o que a sondagem achou, idioma original e a legenda principal (com versão);
- `step`: o último resultado de cada passo por item e idioma (ok / nao_encontrado / falhou);
- `output`: as nossas legendas (`.c3parr.srt`), com hash e a versão da principal de origem;
- `alert`: falhas, sem duplicar (chave = causa + alvo).
"""

import hashlib
import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from c3parr.audio.ffmpeg import MediaInfo
from c3parr.core.models import Model

SCHEMA = """
CREATE TABLE IF NOT EXISTS media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video TEXT NOT NULL UNIQUE,
    size INTEGER,
    mtime REAL,
    probe_json TEXT,
    original_language TEXT,
    main_path TEXT,
    main_lang TEXT,
    main_source TEXT,
    main_version INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS step (
    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    lang TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    detail TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (media_id, action, lang)
);
CREATE TABLE IF NOT EXISTS output (
    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    lang TEXT NOT NULL,
    path TEXT NOT NULL,
    sha TEXT NOT NULL,
    main_version INTEGER NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (media_id, lang)
);
CREATE TABLE IF NOT EXISTS alert (
    key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    target TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    first_at TEXT NOT NULL,
    last_at TEXT NOT NULL,
    seen INTEGER NOT NULL DEFAULT 0,
    resolved_at TEXT,
    media_ids TEXT NOT NULL DEFAULT '[]'
);
"""

StepState = Literal["ok", "nao_encontrado", "falhou"]
MainSource = Literal["embedded", "external", "asr"]


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sha_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Media(Model):
    id: int
    video: str
    size: int | None = None
    mtime: float | None = None
    probe: MediaInfo | None = None
    original_language: str | None = None
    main_path: str | None = None
    main_lang: str | None = None
    main_source: str | None = None
    main_version: int = 0


class StepRecord(Model):
    action: str
    lang: str = ""
    state: StepState
    detail: str | None = None
    attempts: int = 0
    updated_at: str


class OutputRecord(Model):
    lang: str
    path: str
    sha: str
    main_version: int
    source: str
    """`translated` (traduzida da principal) ou `published` (a própria principal no alvo)."""


class Alert(Model):
    key: str
    kind: str
    message: str
    target: str
    count: int
    first_at: str
    last_at: str
    seen: bool
    resolved_at: str | None = None
    media_ids: tuple[int, ...] = ()


class Catalog:
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

    # --- media ---

    def _media(self, row: sqlite3.Row | None) -> Media | None:
        if row is None:
            return None
        data = dict(row)
        probe = data.pop("probe_json")
        data.pop("updated_at", None)
        data["probe"] = MediaInfo.model_validate_json(probe) if probe else None
        return Media.model_validate(data)

    def media(self, media_id: int) -> Media:
        with self._db() as db:
            m = self._media(db.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone())
        if m is None:
            raise KeyError(media_id)
        return m

    def media_by_video(self, video: str) -> Media | None:
        with self._db() as db:
            return self._media(
                db.execute("SELECT * FROM media WHERE video = ?", (video,)).fetchone()
            )

    def all_media(self) -> list[Media]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM media ORDER BY id").fetchall()
        return [m for r in rows if (m := self._media(r)) is not None]

    def ensure_media(self, video: str) -> tuple[Media, bool]:
        """Registra o vídeo (ou o encontra). Devolve também se o arquivo mudou desde a última
        vez (tamanho/mtime): aí o trabalho anterior não vale e o item começa de novo."""
        p = Path(video)
        st = p.stat()
        with self._lock, self._db() as db:
            row = db.execute("SELECT * FROM media WHERE video = ?", (video,)).fetchone()
            if row is None:
                cur = db.execute(
                    "INSERT INTO media (video, size, mtime, updated_at) VALUES (?, ?, ?, ?)",
                    (video, st.st_size, st.st_mtime, now()),
                )
                media_id, changed = int(cur.lastrowid or 0), False
            else:
                media_id = int(row["id"])
                changed = row["size"] != st.st_size or abs((row["mtime"] or 0) - st.st_mtime) > 1
                if changed:
                    db.execute("DELETE FROM step WHERE media_id = ?", (media_id,))
                    db.execute("DELETE FROM output WHERE media_id = ?", (media_id,))
                    db.execute(
                        "UPDATE media SET size = ?, mtime = ?, probe_json = NULL, "
                        "main_path = NULL, main_lang = NULL, main_source = NULL, updated_at = ? "
                        "WHERE id = ?",
                        (st.st_size, st.st_mtime, now(), media_id),
                    )
        return self.media(media_id), changed

    def remove_media(self, video: str) -> None:
        with self._lock, self._db() as db:
            db.execute("DELETE FROM media WHERE video = ?", (video,))

    def set_probe(self, media_id: int, probe: MediaInfo) -> None:
        self._update(media_id, probe_json=probe.model_dump_json())

    def set_original_language(self, media_id: int, lang: str | None) -> None:
        self._update(media_id, original_language=lang)

    def set_main(self, media_id: int, path: str, lang: str, source: str) -> int:
        """Troca a principal e sobe a versão (as traduções antigas ficam desatualizadas)."""
        m = self.media(media_id)
        if m.main_path == path and m.main_lang == lang and m.main_source == source:
            return m.main_version
        version = m.main_version + 1
        self._update(media_id, main_path=path, main_lang=lang, main_source=source,
                     main_version=version)  # fmt: skip
        return version

    def _update(self, media_id: int, **fields: Any) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._lock, self._db() as db:
            db.execute(f"UPDATE media SET {cols}, updated_at = ? WHERE id = ?",
                       (*fields.values(), now(), media_id))  # fmt: skip

    # --- passos ---

    def steps(self, media_id: int) -> dict[tuple[str, str], StepRecord]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM step WHERE media_id = ?", (media_id,)).fetchall()
        fields = ("action", "lang", "state", "detail", "attempts", "updated_at")
        return {
            (r["action"], r["lang"]): StepRecord.model_validate({k: r[k] for k in fields})
            for r in rows
        }

    def record_step(self, media_id: int, action: str, lang: str | None, state: StepState,
                    detail: str | None = None) -> None:  # fmt: skip
        with self._lock, self._db() as db:
            db.execute(
                "INSERT INTO step (media_id, action, lang, state, detail, attempts, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?) ON CONFLICT(media_id, action, lang) DO UPDATE SET "
                "state = excluded.state, detail = excluded.detail, attempts = step.attempts + 1, "
                "updated_at = excluded.updated_at",
                (media_id, action, lang or "", state, detail, now()),
            )

    def clear_step(self, media_id: int, action: str, lang: str | None = None) -> None:
        with self._lock, self._db() as db:
            db.execute("DELETE FROM step WHERE media_id = ? AND action = ? AND lang = ?",
                       (media_id, action, lang or ""))  # fmt: skip

    # --- nossas legendas ---

    def outputs(self, media_id: int) -> dict[str, OutputRecord]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM output WHERE media_id = ?", (media_id,)).fetchall()
        return {r["lang"]: OutputRecord.model_validate(
                    {k: r[k] for k in ("lang", "path", "sha", "main_version", "source")})
                for r in rows}  # fmt: skip

    def record_output(self, media_id: int, lang: str, path: Path, main_version: int,
                      source: str) -> None:  # fmt: skip
        with self._lock, self._db() as db:
            db.execute(
                "INSERT INTO output (media_id, lang, path, sha, main_version, source) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(media_id, lang) DO UPDATE SET "
                "path = excluded.path, sha = excluded.sha, main_version = excluded.main_version, "
                "source = excluded.source",
                (media_id, lang, str(path), sha_of(path), main_version, source),
            )

    def forget_output(self, media_id: int, lang: str) -> None:
        with self._lock, self._db() as db:
            db.execute("DELETE FROM output WHERE media_id = ? AND lang = ?", (media_id, lang))

    # --- números para o dashboard ---

    def output_counts(self) -> dict[tuple[str, str], int]:
        """(idioma, origem: translated|published) → quantas legendas nossas."""
        with self._db() as db:
            rows = db.execute(
                "SELECT lang, source, COUNT(*) AS n FROM output GROUP BY lang, source"
            )
            return {(r["lang"], r["source"]): int(r["n"]) for r in rows}

    def step_counts(self) -> dict[tuple[str, str], int]:
        """(passo, estado) → quantos itens terminaram assim da última vez."""
        with self._db() as db:
            rows = db.execute(
                "SELECT action, state, COUNT(*) AS n FROM step GROUP BY action, state"
            )
            return {(r["action"], r["state"]): int(r["n"]) for r in rows}

    # --- alertas ---

    def raise_alert(self, key: str, kind: str, message: str, target: str,
                    media_id: int | None = None) -> None:  # fmt: skip
        """Mesmo problema de novo atualiza o alerta existente (conta, última vez, itens).
        Visto e repetido volta a contar como não visto, uma vez; resolvido e repetido reabre."""
        with self._lock, self._db() as db:
            row = db.execute("SELECT * FROM alert WHERE key = ?", (key,)).fetchone()
            t = now()
            if row is None:
                ids = [media_id] if media_id is not None else []
                db.execute(
                    "INSERT INTO alert (key, kind, message, target, count, first_at, last_at, "
                    "seen, media_ids) VALUES (?, ?, ?, ?, 1, ?, ?, 0, ?)",
                    (key, kind, message, target, t, t, json.dumps(ids)),
                )
                return
            ids = json.loads(row["media_ids"])
            if media_id is not None and media_id not in ids:
                ids.append(media_id)
            reopened = row["resolved_at"] is not None
            db.execute(
                "UPDATE alert SET message = ?, count = count + 1, last_at = ?, media_ids = ?, "
                "seen = CASE WHEN ? THEN 0 WHEN seen = 1 THEN 0 ELSE seen END, "
                "resolved_at = NULL WHERE key = ?",
                (message, t, json.dumps(ids), reopened, key),
            )

    def resolve_alerts(self, media_id: int | None = None, key: str | None = None,
                       kind: str | None = None) -> None:  # fmt: skip
        """Fecha o alerta (passo voltou a dar certo). Com `media_id`, tira o item dos alertas
        dele (só os do tipo `kind`, se dado) e fecha os que ficam sem item nenhum."""
        with self._lock, self._db() as db:
            if key is not None:
                db.execute("UPDATE alert SET resolved_at = ? WHERE key = ? AND resolved_at IS NULL",
                           (now(), key))  # fmt: skip
            if media_id is None:
                return
            sql = "SELECT key, media_ids FROM alert WHERE resolved_at IS NULL"
            args: tuple[str, ...] = ()
            if kind is not None:
                sql, args = sql + " AND kind = ?", (kind,)
            rows = db.execute(sql, args).fetchall()
            for r in rows:
                ids = json.loads(r["media_ids"])
                if media_id not in ids:
                    continue
                ids.remove(media_id)
                if ids:
                    db.execute("UPDATE alert SET media_ids = ? WHERE key = ?",
                               (json.dumps(ids), r["key"]))  # fmt: skip
                else:
                    db.execute("UPDATE alert SET media_ids = '[]', resolved_at = ? WHERE key = ?",
                               (now(), r["key"]))  # fmt: skip

    def mark_alerts_seen(self) -> None:
        with self._lock, self._db() as db:
            db.execute("UPDATE alert SET seen = 1 WHERE resolved_at IS NULL")

    def alerts(self, include_resolved: bool = True, limit: int = 200) -> list[Alert]:
        sql = "SELECT * FROM alert"
        if not include_resolved:
            sql += " WHERE resolved_at IS NULL"
        with self._db() as db:
            rows = db.execute(sql + " ORDER BY resolved_at IS NOT NULL, last_at DESC LIMIT ?",
                              (limit,)).fetchall()  # fmt: skip
        out = []
        for r in rows:
            d = dict(r)
            d["seen"] = bool(d["seen"])
            d["media_ids"] = tuple(json.loads(d["media_ids"]))
            out.append(Alert.model_validate(d))
        return out

    def unseen_alerts(self) -> int:
        with self._db() as db:
            row = db.execute(
                "SELECT COUNT(*) FROM alert WHERE seen = 0 AND resolved_at IS NULL"
            ).fetchone()
        return int(row[0])
