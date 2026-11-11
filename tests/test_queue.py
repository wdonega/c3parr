"""A fila por dentro: ordem em que roda, contagens e as ações em lote."""

from pathlib import Path

import pytest

from c3parr.engine.engine import RunResult, StepReport
from c3parr.jobs.queue import JobQueue


@pytest.fixture
def queue(tmp_path: Path) -> JobQueue:
    return JobQueue(tmp_path / "fila.db")


def _finish(queue: JobQueue, job_id: int) -> None:
    result = RunResult(recipe="completa", workdir=Path("."))
    result.steps.append(StepReport(name="audio", status="ok"))
    queue.finish(job_id, result, None, "")


def test_upcoming_is_the_order_the_queue_will_run(queue: JobQueue) -> None:
    routine, _ = queue.submit_step(1, "/m/a.mkv", "traduzir", "pt-BR")
    manual, _ = queue.submit_step(2, "/m/b.mkv", "traduzir", "pt-BR", origem="manual")
    late, _ = queue.submit_step(3, "/m/c.mkv", "traduzir", "pt-BR")
    assert [j.id for j in queue.upcoming()] == [manual.id, routine.id, late.id]
    assert [j.id for j in queue.upcoming(limit=1)] == [manual.id]

    first = queue.take_next()  # o que roda é o primeiro da lista, e sai dela
    assert first is not None and first.id == manual.id
    assert [j.id for j in queue.upcoming()] == [routine.id, late.id]


def test_move_to_top_passes_ahead_of_every_waiting_task(queue: JobQueue) -> None:
    manual, _ = queue.submit_step(1, "/m/a.mkv", "traduzir", "pt-BR", origem="manual")
    routine, _ = queue.submit_step(2, "/m/b.mkv", "traduzir", "pt-BR")
    last, _ = queue.submit_step(3, "/m/c.mkv", "traduzir", "pt-BR")
    queue.move_to_top(last.id)
    assert [j.id for j in queue.upcoming()] == [last.id, manual.id, routine.id]
    queue.move_to_top(routine.id)  # o topo de agora perde o lugar para o novo
    assert [j.id for j in queue.upcoming()] == [routine.id, last.id, manual.id]

    running = queue.take_next()
    assert running is not None and running.id == routine.id
    before = queue.get(running.id).prioridade
    queue.move_to_top(running.id)  # só mexe em quem espera
    assert queue.get(running.id).prioridade == before
    queue.move_to_top(999)  # tarefa que não existe: nada acontece


def test_cancel_queued_leaves_the_running_and_the_finished_alone(queue: JobQueue) -> None:
    done = queue.submit("/m/done.mkv", "pt-BR")
    queue.take_next()
    _finish(queue, done.id)
    running = queue.submit("/m/running.mkv", "pt-BR")
    queue.take_next()
    waiting = [queue.submit(f"/m/{n}.mkv", "pt-BR") for n in range(3)]

    assert queue.cancel_queued() == 3
    assert {queue.get(j.id).status for j in waiting} == {"cancelada"}
    assert queue.get(running.id).status == "processando"
    assert queue.get(done.id).status == "concluida"
    assert queue.cancel_queued() == 0 and queue.take_next() is None


def test_retry_failed_brings_back_one_attempt_per_piece_of_work(queue: JobQueue) -> None:
    def fail(media: int, video: str, lang: str = "pt-BR") -> int:
        job, _ = queue.submit_step(media, video, "traduzir", lang)
        queue.take_next()
        queue.fail(job.id, "LLM fora do ar")
        return job.id

    old = fail(1, "/m/a.mkv")
    new = fail(1, "/m/a.mkv")  # o mesmo trabalho falhou duas vezes
    other_lang = fail(1, "/m/a.mkv", "es")
    covered = fail(2, "/m/b.mkv")
    again, _ = queue.submit_step(2, "/m/b.mkv", "traduzir", "pt-BR")  # já voltou para a fila
    legacy = queue.submit("/m/c.mkv", "pt-BR")
    queue.fail(legacy.id, "erro")

    assert queue.retry_failed() == 3
    assert queue.get(old).status == "falhou"  # só a tentativa mais recente volta
    brought_back = queue.get(new)
    assert (brought_back.status, brought_back.erro, brought_back.progresso) == ("na_fila", None, 0)
    assert queue.get(other_lang).status == "na_fila"  # outro idioma é outro trabalho
    assert queue.get(covered).status == "falhou"  # já havia uma na fila para ele
    assert queue.get(again.id).status == "na_fila" and queue.get(legacy.id).status == "na_fila"
    assert queue.retry_failed() == 0


def test_recent_lists_what_finished_last(queue: JobQueue) -> None:
    ids = []
    for n in range(3):
        job = queue.submit(f"/m/{n}.mkv", "pt-BR")
        queue.take_next()
        ids.append(job.id)
    _finish(queue, ids[0])
    queue.fail(ids[2], "erro")
    waiting = queue.submit("/m/waiting.mkv", "pt-BR")
    queue.cancel(waiting.id)  # cancelada antes de rodar: nunca terminou

    recent = [j.id for j in queue.recent()]
    assert set(recent) == {ids[0], ids[2]}  # a que roda e a que nem rodou ficam fora
    assert len(queue.recent(limit=1)) == 1


def test_counts_follow_the_text_search(queue: JobQueue) -> None:
    for n in range(3):
        queue.submit(f"/tv/Show/Show S01E0{n}.mkv", "pt-BR")
    movie = queue.submit("/movies/Quarantième.mkv", "pt-BR")
    queue.fail(movie.id, "LLM timeout")
    queue.take_next()

    assert queue.counts() == {"na_fila": 2, "processando": 1, "falhou": 1}
    assert queue.counts("show") == {"na_fila": 2, "processando": 1}
    assert queue.counts("quarantieme") == {"falhou": 1}
    assert queue.counts("timeout") == {"falhou": 1}  # acha pelo erro também
    assert queue.counts("nada") == {}
