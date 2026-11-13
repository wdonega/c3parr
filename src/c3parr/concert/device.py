"""Onde os modelos do show rodam: a GPU NVIDIA quando há, senão o processador.

O torch só é importado aqui dentro das funções: o c3parr sobe sem ele (o extra `concert` é
opcional), e quem pergunta pelo dispositivo sem o extra recebe "não instalado".
"""

import contextlib
import importlib.util
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SPEED_FILE = "concert-speed.json"


def default_cache_dir() -> Path:
    """O mesmo cache do `Settings` (variável `C3PARR_CACHE_DIR`), sem carregar o `Settings`."""
    env = os.environ.get("C3PARR_CACHE_DIR")
    return Path(env).expanduser() if env else Path.home() / ".cache" / "c3parr"


def default_model_dir() -> Path:
    return default_cache_dir() / "models" / "torch"


def installed() -> bool:
    """O extra `concert` está instalado?"""
    return all(importlib.util.find_spec(m) is not None for m in ("torch", "torchaudio", "demucs"))


def pick(prefer: str = "auto") -> str:
    """`cuda` quando há GPU NVIDIA e o torch veio com CUDA; senão `cpu`. No Mac fica no
    processador: a GPU dele (MPS) não roda o modelo de encaixe em trechos longos."""
    import torch

    return "cuda" if prefer == "auto" and torch.cuda.is_available() else "cpu"


def summary(model_dir: Path | None = None) -> str:
    """Para a tela System: se os modelos do show estão instalados e a velocidade medida aqui.
    Não carrega o torch (centenas de MB) só para responder: o dispositivo vem da última medição."""
    if not installed():
        return "not installed (the image or the 'concert' extra brings them)"
    speed = measured_speed(model_dir)
    if not speed:
        return "installed; they use the NVIDIA GPU when there is one, the CPU otherwise"
    names = {"separation": "voice separation", "fit": "lyric fitting"}
    return "; ".join(
        f"{label} on {'the GPU (CUDA)' if speed[key]['device'] == 'cuda' else 'the CPU'}: "
        f"{speed[key]['seconds_per_minute']:g} s per minute of concert"
        for key, label in names.items()
        if key in speed
    )


def use_model_dir(model_dir: Path) -> None:
    """Os pesos baixados ficam no cache do c3parr (no Docker, no volume `/config`)."""
    import torch

    model_dir.mkdir(parents=True, exist_ok=True)
    torch.hub.set_dir(str(model_dir / "hub"))


def record_speed(model_dir: Path, step: str, device: str, seconds: float, duration: float) -> None:
    """Guarda a velocidade medida aqui (segundos por minuto de show), por etapa."""
    if duration < 30:
        return
    path = model_dir / SPEED_FILE
    data = measured_speed(model_dir)
    data[step] = {
        "device": device,
        "seconds_per_minute": round(seconds / (duration / 60), 2),
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    with contextlib.suppress(OSError):
        path.write_text(json.dumps(data))


def measured_speed(model_dir: Path | None = None) -> dict[str, Any]:
    path = (model_dir or default_model_dir()) / SPEED_FILE
    try:
        return dict(json.loads(path.read_text()))
    except (OSError, ValueError):
        return {}
