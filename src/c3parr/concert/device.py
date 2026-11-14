"""Onde os modelos do show rodam: a GPU NVIDIA quando há, senão o processador.

O torch só é importado aqui dentro das funções: o c3parr sobe sem ele (o extra `concert` é
opcional), e quem pergunta pelo dispositivo sem o extra recebe "não instalado".
"""

import contextlib
import importlib.util
import json
import os
import subprocess
import sys
import threading
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


_PROBE = """
import json
try:
    import torch
    ok = torch.cuda.is_available()
    props = torch.cuda.get_device_properties(0) if ok else None
    print(json.dumps({"cuda": ok, "name": props.name if props else None,
                      "memory_gb": round(props.total_memory / 2**30, 1) if props else None}))
except Exception as e:
    print(json.dumps({"cuda": False, "name": None, "memory_gb": None, "error": str(e)[:200]}))
"""
_gpu: dict[str, Any] | None = None
_gpu_lock = threading.Lock()


def gpu() -> dict[str, Any]:
    """A GPU NVIDIA que o torch enxerga: `{"cuda": bool, "name": ..., "memory_gb": ...}`.

    Pergunta num processo filho, uma vez por processo: quem pergunta não carrega o torch (centenas
    de MB que não voltariam ao sistema). Sem o extra `concert`, não há CUDA."""
    global _gpu
    with _gpu_lock:
        if _gpu is None:
            if not installed():
                _gpu = {"cuda": False, "name": None, "memory_gb": None}
            else:
                try:
                    out = subprocess.run([sys.executable, "-c", _PROBE], capture_output=True,
                                         text=True, timeout=180)  # fmt: skip
                    _gpu = dict(json.loads(out.stdout.strip().splitlines()[-1]))
                except (OSError, ValueError, IndexError, subprocess.SubprocessError):
                    _gpu = {"cuda": False, "name": None, "memory_gb": None}
        return dict(_gpu)


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
