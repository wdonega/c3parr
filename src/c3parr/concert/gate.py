"""Se a legenda de show pode rodar aqui.

Só com uma GPU NVIDIA (CUDA) nesta máquina ou com o c3parr-cuda-processor configurado: na CPU de
uma máquina pequena, um show de 3 horas ocupa a máquina por um dia inteiro (estimativa para um
NanoPi M5, pela primeira meia hora de um show de verdade: 14 h só para separar a voz). Quem quer
assim mesmo liga `C3PARR_CONCERT_ALLOW_CPU=true`.
"""

import os
from collections.abc import Callable
from typing import Any, Literal

from c3parr.concert import device as hw

ALLOW_ENV = "C3PARR_CONCERT_ALLOW_CPU"
Gate = Literal["processor", "cuda", "forced", "blocked"]
WHY = "concert subtitles need an NVIDIA GPU with CUDA on this machine, or the CUDA processor"


def forced() -> bool:
    return os.environ.get(ALLOW_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def gate(processor_url: str, gpu: Callable[[], dict[str, Any]] | None = None) -> Gate:
    """Onde o show roda: no processador (quando há um, mesmo com GPU aqui), na GPU daqui, na CPU
    porque alguém forçou, ou em lugar nenhum."""
    if processor_url.strip():
        return "processor"
    if (gpu or hw.gpu)().get("cuda"):
        return "cuda"
    return "forced" if forced() else "blocked"
