"""Fila de tarefas FIFO persistente (SQLite) e o worker que a consome.

Sobrevive a reinício: tarefa que estava processando volta para a fila e retoma pelo cache.
Uma tarefa pesada por vez.
"""

import json
import logging
import sqlite3
import threading
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from c3parr.core.job import JobParams
from c3parr.core.models import Model
from c3parr.core.search import fold, words
from c3parr.engine.engine import Engine, Recipe, RunResult, StepReport

log = logging.getLogger("c3parr.jobs")

Status = Literal["na_fila", "processando", "concluida", "concluida_avisos", "falhou", "cancelada"]
STATUS_LABELS: dict[str, str] = {
    "na_fila": "Queued",
    "processando": "Processing",
    "concluida": "Done",
    "concluida_avisos": "Done with warnings",
    "falhou": "Failed",
    "cancelada": "Cancelled",
}
FINISHED = frozenset({"concluida", "concluida_avisos", "falhou", "cancelada"})
CANCEL_REQUESTED = "cancelamento pedido"

# peso de cada estágio no progresso total (os outros dividem o que sobrar)
STEP_WEIGHTS = {"transcricao": 0.45, "traducao": 0.30, "contexto": 0.08, "sincronizacao": 0.05}

SCHEMA = """
CREATE TABLE IF NOT EXISTS tarefas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video TEXT NOT NULL,
    dst TEXT NOT NULL,
    status TEXT NOT NULL,
    criada_em TEXT NOT NULL,
    iniciada_em TEXT,
    terminada_em TEXT,
    etapa TEXT,
    progresso REAL NOT NULL DEFAULT 0,
    mensagem TEXT,
    etapas_json TEXT NOT NULL DEFAULT '[]',
    degradacoes_json TEXT NOT NULL DEFAULT '[]',
    legenda TEXT,
    relatorio TEXT,
    erro TEXT
);
"""
# colunas da fila por passo (pipeline de legendas); bancos antigos ganham as colunas no início
STEP_COLUMNS = {
    "media_id": "INTEGER",
    "acao": "TEXT",
    "idioma": "TEXT",
    "arg": "TEXT",
    "prioridade": "INTEGER NOT NULL DEFAULT 0",
    "origem": "TEXT",
}
PRIORITY = {"rotina": 0, "webhook": 1, "manual": 1}
ACTION_LABELS = {
    "metadados": "Metadata",
    "extrair": "Extract embedded subtitle",
    "asr": "Transcribe audio (ASR)",
    "sincronizar": "Sync subtitle",
    "gerar": "Subtitle from ASR",
    "traduzir": "Translate",
    "publicar": "Publish subtitle",
    "remover": "Remove our subtitle",
}


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Job(Model):
    id: int
    video: str
    dst: str
    status: Status
    criada_em: str
    iniciada_em: str | None = None
    terminada_em: str | None = None
    etapa: str | None = None
    progresso: float = 0.0
    mensagem: str | None = None
    etapas: tuple[StepReport, ...] = ()
    degradacoes: tuple[dict[str, str], ...] = ()
    legenda: str | None = None
    relatorio: str | None = None
    erro: str | None = None
    media_id: int | None = None
    acao: str | None = None
    """Passo da pipeline (metadados, asr, traduzir…); None = receita completa (tarefa antiga)."""
    idioma: str | None = None
    arg: str | None = None
    prioridade: int = 0
    origem: str | None = None

    @property
    def step_label(self) -> str:
        if self.acao is None:
            return f"Subtitle {self.dst}"
        label = ACTION_LABELS.get(self.acao, self.acao)
        return f"{label} → {self.idioma}" if self.idioma else label

    @property
    def label(self) -> str:
        return STATUS_LABELS[self.status]

    @property
    def name(self) -> str:
        return Path(self.video).name


class JobQueue:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        with self._db() as db:
            db.executescript(SCHEMA)
            have = {r["name"] for r in db.execute("PRAGMA table_info(tarefas)")}
            for col, decl in STEP_COLUMNS.items():
                if col not in have:
                    db.execute(f"ALTER TABLE tarefas ADD COLUMN {col} {decl}")
            # tarefa interrompida por reinício volta para a fila (o cache evita refazer trabalho)
            db.execute(
                "UPDATE tarefas SET status = 'na_fila', etapa = NULL WHERE status = 'processando'"
            )

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def submit(self, video: str, dst: str) -> Job:
        with self._lock, self._db() as db:
            cur = db.execute(
                "INSERT INTO tarefas (video, dst, status, criada_em) VALUES (?, ?, 'na_fila', ?)",
                (video, dst, now()),
            )
            job_id = int(cur.lastrowid or 0)
        return self.get(job_id)

    def submit_step(
        self,
        media_id: int,
        video: str,
        acao: str,
        idioma: str | None = None,
        arg: str | None = None,
        origem: str = "rotina",
    ) -> tuple[Job, bool]:
        """Enfileira um passo da pipeline, sem duplicar: se o mesmo (item, passo, idioma) já está
        na fila ou rodando, devolve esse (promovido, se o pedido novo tem prioridade maior).
        Devolve também se criou uma tarefa nova."""
        prio = PRIORITY.get(origem, 0)
        with self._lock, self._db() as db:
            row = db.execute(
                "SELECT id, status, prioridade FROM tarefas WHERE media_id = ? AND acao = ? "
                "AND COALESCE(idioma, '') = ? AND status IN ('na_fila', 'processando')",
                (media_id, acao, idioma or ""),
            ).fetchone()
            if row is not None:
                if row["status"] == "na_fila" and prio > row["prioridade"]:
                    db.execute("UPDATE tarefas SET prioridade = ?, origem = ? WHERE id = ?",
                               (prio, origem, row["id"]))  # fmt: skip
                job_id, created = int(row["id"]), False
            else:
                cur = db.execute(
                    "INSERT INTO tarefas (video, dst, status, criada_em, media_id, acao, idioma, "
                    "arg, prioridade, origem) VALUES (?, ?, 'na_fila', ?, ?, ?, ?, ?, ?, ?)",
                    (video, idioma or "", now(), media_id, acao, idioma, arg, prio, origem),
                )
                job_id, created = int(cur.lastrowid or 0), True
        return self.get(job_id), created

    def active_steps(self, media_id: int) -> list[Job]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM tarefas WHERE media_id = ? AND status IN ('na_fila', 'processando')",
                (media_id,),
            ).fetchall()
        return [_job(r) for r in rows]

    def active_steps_for(self, video: str) -> list[Job]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM tarefas WHERE video = ? AND status IN ('na_fila', 'processando')",
                (video,),
            ).fetchall()
        return [_job(r) for r in rows]

    def media_priority(self, media_id: int) -> int:
        """Maior prioridade entre as tarefas ativas do item (para os próximos passos herdarem)."""
        with self._db() as db:
            row = db.execute(
                "SELECT MAX(prioridade) FROM tarefas WHERE media_id = ? "
                "AND status IN ('na_fila', 'processando')", (media_id,)).fetchone()  # fmt: skip
        return int(row[0] or 0)

    def get(self, job_id: int) -> Job:
        with self._db() as db:
            row = db.execute("SELECT * FROM tarefas WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return _job(row)

    def search(
        self, query: str = "", statuses: Iterable[str] = (), limit: int = 100, offset: int = 0
    ) -> tuple[list[Job], int]:
        """Uma página da fila filtrada por texto (nome do vídeo ou erro) e status, e o total."""
        where, args = [], []
        for word in words(query):
            escaped = word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append("fold(video || ' ' || COALESCE(erro, '')) LIKE ? ESCAPE '\\'")
            args.append(f"%{escaped}%")
        wanted = list(statuses)
        if wanted:
            where.append(f"status IN ({', '.join('?' * len(wanted))})")
            args.extend(wanted)
        sql = " WHERE " + " AND ".join(where) if where else ""
        with self._db() as db:
            db.create_function("fold", 1, fold, deterministic=True)
            total = int(db.execute(f"SELECT COUNT(*) FROM tarefas{sql}", args).fetchone()[0])
            rows = db.execute(
                f"SELECT * FROM tarefas{sql} ORDER BY id DESC LIMIT ? OFFSET ?",
                [*args, limit, offset],
            ).fetchall()
        return [_job(r) for r in rows], total

    def list(self, limit: int = 200) -> list[Job]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM tarefas ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [_job(r) for r in rows]

    def pending_count(self) -> int:
        """Tarefas que ainda não terminaram (na fila ou processando)."""
        with self._db() as db:
            row = db.execute(
                "SELECT COUNT(*) FROM tarefas WHERE status IN ('na_fila', 'processando')"
            ).fetchone()
        return int(row[0])

    def videos_with_jobs(self, dst: str, statuses: frozenset[str] | None = None) -> set[str]:
        """Vídeos que já têm tarefa para esse idioma (de qualquer status, ou só dos pedidos)."""
        sql, args = "SELECT DISTINCT video FROM tarefas WHERE dst = ?", [dst]
        if statuses is not None:
            sql += f" AND status IN ({', '.join('?' * len(statuses))})"
            args += sorted(statuses)
        with self._db() as db:
            return {r["video"] for r in db.execute(sql, args).fetchall()}

    def take_next(self) -> Job | None:
        """Próxima da fila (maior prioridade, depois a mais antiga), já marcada como processando."""
        with self._lock, self._db() as db:
            row = db.execute(
                "SELECT id FROM tarefas WHERE status = 'na_fila' "
                "ORDER BY prioridade DESC, id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE tarefas SET status = 'processando', iniciada_em = ?, progresso = 0, "
                "erro = NULL, mensagem = NULL WHERE id = ?",
                (now(), row["id"]),
            )
        return self.get(int(row["id"]))

    def progress(self, job_id: int, step: str, fraction: float, message: str) -> None:
        with self._db() as db:
            db.execute(
                "UPDATE tarefas SET etapa = ?, progresso = ?, "
                "mensagem = CASE mensagem WHEN ? THEN mensagem ELSE ? END WHERE id = ?",
                (step, round(fraction, 4), CANCEL_REQUESTED, message, job_id),
            )

    def finish(self, job_id: int, result: RunResult, legenda: str | None, relatorio: str) -> Job:
        status: Status
        if result.failed is not None:
            status = "cancelada" if result.failed.reason == "cancelada" else "falhou"
        else:
            status = "concluida_avisos" if result.degradations else "concluida"
        steps = json.dumps([s.model_dump() for s in result.steps], ensure_ascii=False)
        degr = json.dumps([d.model_dump() for d in result.degradations], ensure_ascii=False)
        done = 1.0 if status.startswith("concluida") else None
        error = result.failed.reason if result.failed else None
        with self._db() as db:
            db.execute(
                "UPDATE tarefas SET status = ?, terminada_em = ?, "
                "progresso = COALESCE(?, progresso), etapa = NULL, etapas_json = ?, "
                "degradacoes_json = ?, legenda = ?, relatorio = ?, erro = ? WHERE id = ?",
                (status, now(), done, steps, degr, legenda, relatorio, error, job_id),
            )
        return self.get(job_id)

    def fail(self, job_id: int, error: str) -> None:
        with self._db() as db:
            db.execute(
                "UPDATE tarefas SET status = 'falhou', terminada_em = ?, erro = ? WHERE id = ?",
                (now(), error, job_id),
            )

    def cancel(self, job_id: int) -> None:
        """Na fila: sai na hora. Processando: o motor para entre um estágio e outro."""
        with self._db() as db:
            db.execute(
                "UPDATE tarefas SET status = CASE status WHEN 'na_fila' THEN 'cancelada' "
                "ELSE status END, mensagem = ? WHERE id = ? "
                "AND status IN ('na_fila', 'processando')",
                (CANCEL_REQUESTED, job_id),
            )

    def cancel_requested(self, job_id: int) -> bool:
        with self._db() as db:
            row = db.execute("SELECT mensagem FROM tarefas WHERE id = ?", (job_id,)).fetchone()
        return row is not None and row["mensagem"] == CANCEL_REQUESTED

    def retry(self, job_id: int) -> None:
        with self._db() as db:
            db.execute(
                "UPDATE tarefas SET status = 'na_fila', progresso = 0, etapa = NULL, erro = NULL, "
                "mensagem = NULL, terminada_em = NULL WHERE id = ? AND status IN "
                "('falhou', 'cancelada', 'concluida', 'concluida_avisos')",
                (job_id,),
            )


def _job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        video=row["video"],
        dst=row["dst"],
        status=row["status"],
        criada_em=row["criada_em"],
        iniciada_em=row["iniciada_em"],
        terminada_em=row["terminada_em"],
        etapa=row["etapa"],
        progresso=row["progresso"] or 0.0,
        mensagem=row["mensagem"],
        etapas=tuple(StepReport.model_validate(s) for s in json.loads(row["etapas_json"])),
        degradacoes=tuple(json.loads(row["degradacoes_json"])),
        legenda=row["legenda"],
        relatorio=row["relatorio"],
        erro=row["erro"],
        media_id=row["media_id"],
        acao=row["acao"],
        idioma=row["idioma"],
        arg=row["arg"],
        prioridade=row["prioridade"] or 0,
        origem=row["origem"],
    )


def overall_progress(recipe: Recipe, step: str, fraction: float) -> float:
    """Progresso total: soma os pesos dos estágios já passados + a fração do atual."""
    names = [s.name for s in recipe.steps]
    if step not in names:
        return 0.0
    rest = 1.0 - sum(STEP_WEIGHTS.get(n, 0.0) for n in names)
    light = rest / max(1, sum(1 for n in names if n not in STEP_WEIGHTS))
    weight = {n: STEP_WEIGHTS.get(n, light) for n in names}
    done = sum(weight[n] for n in names[: names.index(step)])
    return min(0.999, done + weight[step] * max(0.0, min(1.0, fraction)))


ProgressFn = Callable[[str, float, str], None]
Runner = Callable[[Job, ProgressFn, Callable[[], bool]], tuple[RunResult, str | None, str]]


class Worker(threading.Thread):
    """Consome a fila uma tarefa por vez."""

    def __init__(self, queue: JobQueue, run: Runner, idle_s: float = 1.0) -> None:
        super().__init__(name="c3parr-worker", daemon=True)
        self.queue = queue
        self.run_job = run
        self.idle_s = idle_s
        self._halt = threading.Event()

    def stop(self) -> None:
        self._halt.set()

    def run_once(self) -> Job | None:
        job = self.queue.take_next()
        if job is None:
            return None

        def on_progress(step: str, fraction: float, message: str) -> None:
            self.queue.progress(job.id, step, fraction, message)

        try:
            result, legenda, relatorio = self.run_job(
                job, on_progress, lambda: self.queue.cancel_requested(job.id)
            )
            return self.queue.finish(job.id, result, legenda, relatorio)
        except Exception as e:  # a fila nunca para por causa de uma tarefa
            log.exception("task %d failed", job.id)
            self.queue.fail(job.id, f"unexpected error: {e}")
            return self.queue.get(job.id)

    def run(self) -> None:
        while not self._halt.is_set():
            if self.run_once() is None:
                self._halt.wait(self.idle_s)


def engine_runner(engine: Engine | Callable[[], Engine],
                  recipe_factory: Callable[[], Recipe]) -> Runner:  # fmt: skip
    """Liga a fila ao motor: monta o DTO da tarefa, roda a receita e grava o relatório.

    Aceita uma função que monta o motor a cada tarefa, para valer o provider salvo em Settings.
    """
    from c3parr.library.sidecars import pick_reference
    from c3parr.recipes.transcribe import WrittenFile
    from c3parr.report.review import build_review, write_review

    def run(
        job: Job, progress: ProgressFn, cancelled: Callable[[], bool]
    ) -> tuple[RunResult, str | None, str]:
        recipe = recipe_factory()
        # legenda que já existe ao lado do vídeo vira referência (sincronizada contra o áudio)
        ref = pick_reference(Path(job.video), job.dst)
        params = JobParams(recipe=recipe.name, video=job.video, dst=job.dst,
                           ref=str(ref) if ref else None)  # fmt: skip

        def on_progress(step: str, fraction: float, message: str) -> None:
            progress(step, overall_progress(recipe, step, fraction), message)

        current = engine if isinstance(engine, Engine) else engine()
        result = current.run(recipe, params, on_progress, cancelled)
        written = result.artifacts.get("escrita")
        legenda = written.path if isinstance(written, WrittenFile) else None
        review_md = result.workdir / f"tarefa-{job.id}.review.md"
        review = build_review(result, job.video, legenda)
        write_review(review, result.workdir / "review.json", review_md)
        return result, legenda, str(review_md)

    return run
