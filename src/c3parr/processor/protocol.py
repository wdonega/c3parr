"""O que o c3parr e o processador CUDA trocam. `PROTOCOL` muda quando um lado deixa de entender o
outro; o c3parr recusa um processador de outro protocolo."""

import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

SERVICE = "c3parr-cuda-processor"
PROTOCOL = 1
DEFAULT_PORT = 8485
KEY_ENV = "C3PARR_PROCESSOR_API_KEY"
SHA = re.compile(r"^[0-9a-f]{64}$")
SUFFIX = re.compile(r"^\.[a-z0-9]{1,5}$")

Method = Literal["separate", "place", "transcribe"]


@dataclass(frozen=True)
class Job:
    interface: str
    provider: str
    params: frozenset[str]
    """Os parâmetros que o c3parr pode mandar. Os que apontam para arquivos ou programas (pasta
    de modelos, binário) são os do processador e não vêm do pedido."""


JOBS: dict[str, Job] = {
    "separate": Job("separacao", "demucs", frozenset({"model", "device"})),
    "place": Job("encaixe", "wav2vec2", frozenset({"device"})),
    "transcribe": Job(
        "transcricao",
        "whisper-cpp",
        frozenset({"model", "device", "beam_size", "vad_min_silence_ms", "vad_pad_ms"}),
    ),
}
NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
"""Nome de modelo: vira nome de arquivo e parte de URL do lado do processador."""


class Info(BaseModel):
    service: str = SERVICE
    version: str
    protocol: int = PROTOCOL
    cuda: bool
    gpu: str | None = None
    gpu_memory_gb: float | None = None
    whisper: str = ""
    """O whisper.cpp do processador: pronto, ou o que falta."""
    busy: bool = False
    queued: int = 0


class JobIn(BaseModel):
    method: Method
    params: dict[str, Any] = Field(default_factory=dict)
    args: dict[str, Any] = Field(default_factory=dict)
    audio: str
    """sha256 do áudio já enviado (`PUT /v1/files/{sha}`)."""


class JobOut(BaseModel):
    id: str
    status: Literal["queued", "running", "done", "failed"]
    progress: float = 0.0
    message: str = ""
    ahead: int = 0
    """Quantas tarefas estão na frente desta."""
    result: Any = None
    error: dict[str, str] | None = None
