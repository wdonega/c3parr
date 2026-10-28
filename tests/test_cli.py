import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from c3parr.cli.app import EXIT_NO_TRANSCRIPT, EXIT_NO_TRANSLATION, EXIT_OK, EXIT_USAGE, app

runner = CliRunner()


def components(transcriber: dict[str, object], translator: dict[str, object] | None = None) -> str:
    return json.dumps(
        {
            "transcricao": transcriber,
            "escrita_legenda": {"provider": "srt"},
            "contexto": {"provider": "falso"},
            "traducao": translator or {"provider": "falso"},
        }
    )


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("C3PARR_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("C3PARR_COMPONENTS", components({"provider": "falso"}))
    return tmp_path


@pytest.fixture
def video(env: Path, tiny_video: Path) -> Path:
    dest = env / tiny_video.name
    shutil.copy(tiny_video, dest)
    return dest


def test_transcribe_only_writes_srt_and_review(env: Path, video: Path) -> None:
    result = runner.invoke(app, [str(video), "--src", "fr", "--transcribe-only"])
    assert result.exit_code == EXIT_OK, result.output
    assert (env / "Filme Teste (2001).fr.srt").exists()
    review = (env / "Filme Teste (2001).review.md").read_text(encoding="utf-8")
    assert "Main language: fr" in review


def test_full_recipe_writes_translated_srt(env: Path, video: Path) -> None:
    result = runner.invoke(app, [str(video), "--src", "fr", "--keep-transcript"])
    assert result.exit_code == EXIT_OK, result.output
    assert "[pt-BR] Tu es fatigué ?" in (env / "Filme Teste (2001).pt-BR.srt").read_text()
    assert (env / "Filme Teste (2001).fr.srt").exists()
    review = (env / "Filme Teste (2001).review.md").read_text(encoding="utf-8")
    assert "## Translation" in review
    assert "Marie" in review


def test_llm_down_fails_the_job(env: Path, video: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    down = components({"provider": "falso"}, {"provider": "falso", "params": {"fail": True}})
    monkeypatch.setenv("C3PARR_COMPONENTS", down)
    result = runner.invoke(app, [str(video), "--src", "fr"])
    assert result.exit_code == EXIT_NO_TRANSLATION
    assert not (env / "Filme Teste (2001).pt-BR.srt").exists()
    review = (env / "Filme Teste (2001).review.md").read_text(encoding="utf-8")
    assert "Failed" in review


def test_sync_only_needs_ref(video: Path) -> None:
    assert runner.invoke(app, [str(video), "--sync-only"]).exit_code == EXIT_USAGE


def test_failed_transcription_exits_non_zero(video: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    failing = components({"provider": "falso", "params": {"fail": True}})
    monkeypatch.setenv("C3PARR_COMPONENTS", failing)
    result = runner.invoke(app, [str(video), "--transcribe-only"])
    assert result.exit_code == EXIT_NO_TRANSCRIPT
