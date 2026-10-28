"""Memória de série em SQLite: obra, episódios, entidades, renderizações, falas e menções.

A tradução grava referências a entidades (menções), não só o texto final; é isso que permite trocar
um nome depois e propagar a mudança (F7). Falas travadas pelo usuário nunca são sobrescritas.
"""

import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from c3parr.core.bible import Bible
from c3parr.core.models import Model, Transcript
from c3parr.interfaces.translation import Translation

SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE IF NOT EXISTS obra (
    id INTEGER PRIMARY KEY, chave TEXT UNIQUE NOT NULL, titulo TEXT, tipo TEXT
);
CREATE TABLE IF NOT EXISTS episodio (
    id INTEGER PRIMARY KEY, obra_id INTEGER NOT NULL REFERENCES obra(id),
    temporada INTEGER NOT NULL DEFAULT 0, numero INTEGER NOT NULL DEFAULT 0, video TEXT,
    UNIQUE (obra_id, temporada, numero)
);
CREATE TABLE IF NOT EXISTS entidade (
    id INTEGER PRIMARY KEY, obra_id INTEGER NOT NULL REFERENCES obra(id), nome TEXT NOT NULL,
    tipo TEXT, genero TEXT, origem TEXT, confianca REAL, apelidos TEXT,
    UNIQUE (obra_id, nome)
);
CREATE TABLE IF NOT EXISTS renderizacao (
    id INTEGER PRIMARY KEY, entidade_id INTEGER NOT NULL REFERENCES entidade(id),
    texto TEXT NOT NULL, escopo TEXT NOT NULL DEFAULT 'obra', a_partir_de INTEGER,
    versao INTEGER NOT NULL, criada_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS relacao (
    id INTEGER PRIMARY KEY, obra_id INTEGER NOT NULL REFERENCES obra(id),
    a INTEGER NOT NULL REFERENCES entidade(id), b INTEGER NOT NULL REFERENCES entidade(id),
    tipo TEXT, tratamento_a_para_b TEXT, tratamento_b_para_a TEXT, confianca REAL,
    UNIQUE (obra_id, a, b)
);
CREATE TABLE IF NOT EXISTS fala (
    id INTEGER PRIMARY KEY, episodio_id INTEGER NOT NULL REFERENCES episodio(id),
    posicao INTEGER NOT NULL, inicio REAL, fim REAL, original TEXT, referencia TEXT,
    traducao TEXT, travada INTEGER NOT NULL DEFAULT 0, versao_glossario INTEGER,
    incerto INTEGER NOT NULL DEFAULT 0, motivo TEXT,
    UNIQUE (episodio_id, posicao)
);
CREATE TABLE IF NOT EXISTS mencao (
    id INTEGER PRIMARY KEY, fala_id INTEGER NOT NULL REFERENCES fala(id),
    entidade_id INTEGER NOT NULL REFERENCES entidade(id), trecho_original TEXT,
    trecho_traducao TEXT, rastreada INTEGER NOT NULL
);
"""

UPSERT_LINE = """
INSERT INTO fala (episodio_id, posicao, inicio, fim, original, referencia, traducao,
                  versao_glossario, incerto, motivo)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (episodio_id, posicao) DO UPDATE SET
    traducao = excluded.traducao, referencia = excluded.referencia,
    versao_glossario = excluded.versao_glossario, incerto = excluded.incerto,
    motivo = excluded.motivo
"""


class EpisodeKey(Model):
    work: str
    title: str = ""
    kind: str = "filme"
    season: int = 0
    episode: int = 0


class MemoryRecord(Model):
    lines: int = 0
    mentions: int = 0
    untracked: int = 0
    skipped: str | None = None


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("PRAGMA foreign_keys = ON")
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version < SCHEMA_VERSION:
        db.executescript(SCHEMA)
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return db


def _one(db: sqlite3.Connection, sql: str, args: tuple[object, ...]) -> int:
    return int(db.execute(sql, args).fetchone()[0])


def _entity(
    db: sqlite3.Connection,
    work_id: int,
    name: str,
    kind: str = "termo",
    gender: str | None = None,
    origin: str | None = None,
    confidence: float | None = None,
    aliases: str = "",
) -> int:
    db.execute(
        "INSERT INTO entidade (obra_id, nome, tipo, genero, origem, confianca, apelidos) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (obra_id, nome) DO UPDATE SET "
        "genero = COALESCE(entidade.genero, excluded.genero)",
        (work_id, name, kind, gender, origin, confidence, aliases),
    )
    return _one(db, "SELECT id FROM entidade WHERE obra_id = ? AND nome = ?", (work_id, name))


def _render(db: sqlite3.Connection, entity_id: int, text: str, episode: int) -> None:
    current = db.execute(
        "SELECT texto, versao FROM renderizacao WHERE entidade_id = ? ORDER BY versao DESC LIMIT 1",
        (entity_id,),
    ).fetchone()
    if current is not None and current[0] == text:
        return
    version = 1 if current is None else int(current[1]) + 1
    now = datetime.now(UTC).isoformat(timespec="seconds")
    db.execute(
        "INSERT INTO renderizacao (entidade_id, texto, escopo, a_partir_de, versao, criada_em) "
        "VALUES (?, ?, 'obra', ?, ?, ?)",
        (entity_id, text, episode, version, now),
    )


def record_translation(
    path: Path,
    key: EpisodeKey,
    video: str,
    transcript: Transcript,
    translation: Translation,
    bible: Bible,
    refs: Mapping[int, str],
) -> MemoryRecord:
    with connect(path) as db:
        db.execute(
            "INSERT INTO obra (chave, titulo, tipo) VALUES (?, ?, ?) "
            "ON CONFLICT (chave) DO NOTHING",
            (key.work, key.title, key.kind),
        )
        work_id = _one(db, "SELECT id FROM obra WHERE chave = ?", (key.work,))
        db.execute(
            "INSERT INTO episodio (obra_id, temporada, numero, video) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (obra_id, temporada, numero) DO UPDATE SET video = excluded.video",
            (work_id, key.season, key.episode, video),
        )
        episode_id = _one(
            db,
            "SELECT id FROM episodio WHERE obra_id = ? AND temporada = ? AND numero = ?",
            (work_id, key.season, key.episode),
        )
        entities: dict[str, int] = {}
        for c in bible.personagens:
            eid = _entity(db, work_id, c.nome, "personagem", c.genero, c.origem_genero,
                          c.confianca, ",".join(c.apelidos))  # fmt: skip
            entities[c.nome.lower()] = eid
            _render(db, eid, c.nome, key.episode)
        for g in bible.glossario:
            eid = _entity(db, work_id, g.termo, g.tipo, confidence=g.confianca)
            entities[g.termo.lower()] = eid
            _render(db, eid, g.traducao, key.episode)
        version = _one(
            db,
            "SELECT COALESCE(MAX(r.versao), 0) FROM renderizacao r "
            "JOIN entidade e ON e.id = r.entidade_id WHERE e.obra_id = ?",
            (work_id,),
        )

        segments = {s.id: s for s in transcript.segments}
        lines = mentions = untracked = 0
        for line in translation.lines:
            seg = segments.get(line.id)
            if seg is None:
                continue
            locked = db.execute(
                "SELECT travada FROM fala WHERE episodio_id = ? AND posicao = ?",
                (episode_id, line.id),
            ).fetchone()
            if locked is not None and locked[0]:
                continue
            args = (episode_id, line.id, seg.start, seg.end, seg.text, refs.get(line.id),
                    line.texto, version, int(line.incerto), line.motivo)  # fmt: skip
            db.execute(UPSERT_LINE, args)
            line_id = _one(
                db,
                "SELECT id FROM fala WHERE episodio_id = ? AND posicao = ?",
                (episode_id, line.id),
            )
            db.execute("DELETE FROM mencao WHERE fala_id = ?", (line_id,))
            for m in line.mencoes:
                key_name = m.entidade.lower()
                if key_name not in entities:
                    entities[key_name] = _entity(db, work_id, m.entidade, origin="llm")
                eid = entities[key_name]
                db.execute(
                    "INSERT INTO mencao (fala_id, entidade_id, trecho_original, trecho_traducao, "
                    "rastreada) VALUES (?, ?, ?, ?, ?)",
                    (line_id, eid, m.original, m.traducao, int(m.rastreada)),
                )
                mentions += 1
                untracked += 0 if m.rastreada else 1
            lines += 1
        return MemoryRecord(lines=lines, mentions=mentions, untracked=untracked)
