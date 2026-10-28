import subprocess
from pathlib import Path

import pytest
from hypothesis.configuration import set_hypothesis_home_dir

from c3parr.asr.fake import FakeTranscriber


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


@pytest.fixture(scope="session")
def tiny_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Vídeo de 4 s com uma faixa de áudio marcada como francês."""
    path = tmp_path_factory.mktemp("media") / "Filme Teste (2001).mkv"
    _ffmpeg(
        "-f", "lavfi", "-i", "color=c=black:s=64x64:d=4",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=4:sample_rate=44100",
        "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-c:a", "aac", "-ac", "2",
        "-metadata:s:a:0", "language=fra", "-shortest", str(path),
    )  # fmt: skip
    return path


@pytest.fixture(scope="session")
def silent_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Vídeo sem nenhuma faixa de áudio."""
    path = tmp_path_factory.mktemp("media") / "mudo.mkv"
    _ffmpeg("-f", "lavfi", "-i", "color=c=black:s=64x64:d=2", "-c:v", "libx264", str(path))
    return path


@pytest.fixture(autouse=True)
def reset_fake_calls() -> None:
    FakeTranscriber.calls = 0


@pytest.fixture(autouse=True)
def no_real_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Os testes não leem o `.env`, as chaves do ambiente nem a chave embutida do TMDb."""
    from c3parr.core import config
    from c3parr.metadata import tmdb

    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "no.env")
    monkeypatch.setattr(
        tmdb, "BUILTIN_READ_TOKEN", ""
    )  # cada teste liga a chave embutida se quiser
    for name in (*config.SECRET_NAMES, *config.LLM_ENV):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def no_github(monkeypatch: pytest.MonkeyPatch) -> None:
    """A versão mais nova do Radarr/Sonarr vem do GitHub; nos testes, a conhecida (6 e 4)."""
    from c3parr.library import arr, sync
    from c3parr.web import app

    def latest(kind: str, transport: object = None) -> int:
        return arr.LATEST_MAJOR_FALLBACK[kind]

    for module in (arr, sync, app):
        monkeypatch.setattr(module, "latest_major", latest)


# o diretório do Hypothesis (exemplos e caches) mora em .local, fora do git, e não na raiz
set_hypothesis_home_dir(Path(__file__).resolve().parents[1] / ".local" / "hypothesis")
