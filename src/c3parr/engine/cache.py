"""Cache de artefatos por estágio, endereçado por conteúdo.

A chave de um estágio junta nome, versão, provider, parâmetros e o hash das saídas de que ele
depende; trocar um provider invalida só o que vem depois dele.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

SAMPLE_BYTES = 1 << 20


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def model_digest(value: BaseModel) -> str:
    return digest(canonical(value.model_dump(mode="json")))


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def video_fingerprint(path: Path) -> str:
    """Impressão digital barata: tamanho + amostras do início, do meio e do fim do arquivo."""
    size = path.stat().st_size
    h = hashlib.sha256(str(size).encode())
    with path.open("rb") as f:
        for offset in (0, max(0, size // 2 - SAMPLE_BYTES // 2), max(0, size - SAMPLE_BYTES)):
            f.seek(offset)
            h.update(f.read(SAMPLE_BYTES))
    return h.hexdigest()[:32]


@runtime_checkable
class FileBacked(Protocol):
    """Artefato que aponta para arquivos no disco; o cache só vale se eles ainda existirem."""

    def missing_files(self) -> list[str]: ...


class ArtifactCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, step: str, key: str) -> Path:
        return self.root / step / f"{key}.json"

    def load[M: BaseModel](self, step: str, key: str, model: type[M]) -> M | None:
        path = self._path(step, key)
        if not path.exists():
            return None
        try:
            value = model.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError:
            return None
        if isinstance(value, FileBacked) and value.missing_files():
            return None
        return value

    def store(self, step: str, key: str, value: BaseModel) -> None:
        path = self._path(step, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(value.model_dump_json(), encoding="utf-8")
        tmp.replace(path)
