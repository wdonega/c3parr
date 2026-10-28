"""Pipeline de ponta a ponta com providers falsos: decisor + fila por passo + arquivos `.c3parr`."""

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from c3parr.asr.fake import FakeTranscriber
from c3parr.core.job import ComponentSpec
from c3parr.engine.engine import Engine
from c3parr.jobs.queue import Job, JobQueue, Worker
from c3parr.library.store import Library
from c3parr.pipeline.actions import run_step
from c3parr.pipeline.catalog import Catalog
from c3parr.pipeline.naming import output_file, work_file
from c3parr.pipeline.reconcile import PipelineContext, reconcile, reconcile_all
from c3parr.providers.registry import build_factories
from c3parr.subtitle.format import FormatRules

SRT_EN = """1
00:00:01,000 --> 00:00:02,500
Hello there.

2
00:00:04,000 --> 00:00:05,500
Are you tired?
"""


class Env:
    def __init__(self, root: Path, translator: dict[str, Any] | None = None) -> None:
        self.root = root
        self.library = Library(root / "c3parr.db")
        self.catalog = Catalog(root / "c3parr.db")
        self.queue = JobQueue(root / "fila.db")
        self.specs = {
            "transcricao": ComponentSpec(provider="falso"),
            "escrita_legenda": ComponentSpec(provider="srt"),
            "fonte_legenda": ComponentSpec(provider="arquivo"),
            "sincronizacao": ComponentSpec(provider="fuzzy"),
            "contexto": ComponentSpec(provider="falso"),
            "traducao": ComponentSpec(provider="falso", params=translator or {}),
        }
        factories = build_factories()
        self.ctx = PipelineContext(
            catalog=self.catalog, library=self.library, queue=self.queue, factories=factories,
            secrets={}, make_engine=lambda: Engine(factories, self.specs, {}, root / "cache"),
            components=lambda: self.specs, format_rules=FormatRules(),
        )  # fmt: skip
        self.worker = Worker(self.queue, lambda j, p, c: run_step(self.ctx, j, p, c))

    def drain(self, limit: int = 30) -> list[Job]:
        done = []
        for _ in range(limit):
            job = self.worker.run_once()
            if job is None:
                return done
            done.append(job)
        raise AssertionError("queue did not drain")


def place(src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dest)
    return dest


@pytest.fixture
def embedded_mkv(tiny_video: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Vídeo com uma legenda inglesa embutida (como o Criminal Minds)."""
    d = tmp_path_factory.mktemp("emb")
    srt = d / "en.srt"
    srt.write_text(SRT_EN, encoding="utf-8")
    out = d / "Show S01E01.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tiny_video), "-i", str(srt),
                    "-map", "0", "-map", "1", "-c", "copy", "-c:s", "srt",
                    "-metadata:s:s:0", "language=eng", str(out)], check=True)  # fmt: skip
    return out


def test_embedded_english_is_extracted_and_translated_without_asr(
    tmp_path: Path, embedded_mkv: Path
) -> None:
    env = Env(tmp_path)
    video = place(embedded_mkv, tmp_path / "tv" / "Show" / "Season 1" / "Show S01E01.mkv")
    FakeTranscriber.calls = 0
    reconcile(env.ctx, str(video), "manual")
    steps = [j.acao for j in env.drain()]
    assert steps == ["metadados", "extrair", "traduzir"]
    assert FakeTranscriber.calls == 0
    out = output_file(video, "pt-BR")
    assert out.name == "Show S01E01.pt-BR.c3parr.srt"
    assert "[pt-BR] Hello there." in out.read_text(encoding="utf-8")
    assert work_file(video, "embedded", "en").exists()
    media = env.catalog.media_by_video(str(video))
    assert media is not None and media.main_source == "embedded"
    assert reconcile(env.ctx, str(video), "manual") is not None and env.drain() == []


def test_no_subtitle_goes_through_asr_generate_and_translate(
    tmp_path: Path, tiny_video: Path
) -> None:
    env = Env(tmp_path)
    video = place(tiny_video, tmp_path / "movies" / "Filme (2001)" / "Filme.mkv")
    reconcile(env.ctx, str(video), "manual")
    assert [j.acao for j in env.drain()] == ["metadados", "asr", "gerar", "traduzir"]
    assert work_file(video, "asr").exists()
    assert work_file(video, "main", "fr").exists()
    assert "[pt-BR] Bonjour." in output_file(video, "pt-BR").read_text(encoding="utf-8")


def test_failure_raises_one_alert_and_the_routine_retries(
    tmp_path: Path, embedded_mkv: Path
) -> None:
    env = Env(tmp_path, translator={"fail": True})
    video = place(embedded_mkv, tmp_path / "Show S01E01.mkv")
    reconcile(env.ctx, str(video), "manual")
    jobs = env.drain()
    assert jobs[-1].acao == "traduzir" and jobs[-1].status == "falhou"
    assert env.catalog.unseen_alerts() == 1
    # a próxima rodada da rotina tenta de novo; o mesmo erro não vira outro alerta
    reconcile(env.ctx, str(video), "rotina", retry_failed=True, force=True)
    env.drain()
    alerts = env.catalog.alerts(include_resolved=False)
    assert len(alerts) == 1 and alerts[0].count == 2 and env.catalog.unseen_alerts() == 1
    env.catalog.mark_alerts_seen()
    assert env.catalog.unseen_alerts() == 0
    # consertou: a próxima tentativa passa e o alerta fecha sozinho
    env.specs["traducao"] = ComponentSpec(provider="falso")
    reconcile(env.ctx, str(video), "manual", retry_failed=True)
    env.drain()
    assert output_file(video, "pt-BR").exists()
    assert env.catalog.alerts(include_resolved=False) == []


def test_human_subtitle_replaces_ours(tmp_path: Path, embedded_mkv: Path) -> None:
    env = Env(tmp_path)
    video = place(embedded_mkv, tmp_path / "Show S01E01.mkv")
    reconcile(env.ctx, str(video), "manual")
    env.drain()
    ours = output_file(video, "pt-BR")
    assert ours.exists()
    human = video.with_name("Show S01E01.pt-BR.srt")
    human.write_text(SRT_EN.replace("Hello there.", "Olá.").replace("Are you", "Você"),
                     encoding="utf-8")  # fmt: skip
    reconcile(env.ctx, str(video), "manual")
    assert "remover" in [j.acao for j in env.drain()]
    assert not ours.exists() and human.exists()


def index_movies(library: Library, root: Path) -> None:
    """O que o sync com o Radarr grava: um filme por pasta."""
    for video in sorted(root.glob("*/*.mkv")):
        library.upsert_movie(str(video.parent), str(video), video.stem, 2001, None, True)


def test_manual_goes_ahead_of_the_routine_and_nothing_is_duplicated(
    tmp_path: Path, tiny_video: Path
) -> None:
    env = Env(tmp_path)
    for name in ("A", "B", "C"):
        place(tiny_video, tmp_path / "movies" / f"{name} (2001)" / f"{name}.mkv")
    env.library.update_config(wait_minutes=0)
    index_movies(env.library, tmp_path / "movies")
    reconcile_all(env.ctx)
    reconcile_all(env.ctx)  # rodar de novo não duplica
    assert env.queue.pending_count() == 3
    manual = str(tmp_path / "movies" / "C (2001)" / "C.mkv")
    reconcile(env.ctx, manual, "manual")  # já estava na fila: só sobe de prioridade
    assert env.queue.pending_count() == 3
    first = env.worker.run_once()
    assert first is not None and first.video == manual
    # o próximo passo do item manual herda a prioridade e passa na frente dos outros
    second = env.worker.run_once()
    assert second is not None and second.video == manual and second.acao == "asr"


def test_unmonitored_and_waiting_items_are_left_for_later(tmp_path: Path, tiny_video: Path) -> None:
    env = Env(tmp_path)
    place(tiny_video, tmp_path / "movies" / "A (2001)" / "A.mkv")
    env.library.update_config(wait_minutes=60)
    index_movies(env.library, tmp_path / "movies")
    reconcile_all(env.ctx)
    assert env.queue.pending_count() == 0  # ainda na espera
    env.library.update_config(wait_minutes=0)
    env.library.set_monitored("movie", env.library.movies()[0].id, False)
    reconcile_all(env.ctx)
    assert env.queue.pending_count() == 0  # não monitorado


def test_queued_step_that_no_longer_exists_is_skipped_without_alert(
    tmp_path: Path, tiny_video: Path
) -> None:
    env = Env(tmp_path)
    video = place(tiny_video, tmp_path / "Old (2001).mkv")
    media, _ = env.catalog.ensure_media(str(video))
    env.queue.submit_step(media.id, str(video), "buscar", "fr")  # fila de uma versão antiga
    job = env.worker.run_once()
    assert job is not None and job.status == "concluida"
    assert env.catalog.unseen_alerts() == 0
