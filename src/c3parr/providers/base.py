"""O que todo provider declara: capacidades, requisitos, esquema de configuração, saúde e versão."""

from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import BaseModel

from c3parr.core.models import Model


class ModelDownload(Model):
    name: str
    approx_mb: int


class Requirements(Model):
    network: bool = False
    gpu: Literal["nao", "opcional", "obrigatoria"] = "nao"
    models: tuple[ModelDownload, ...] = ()
    extras: tuple[str, ...] = ()
    """Extras do pacote que precisam estar instalados (ex.: `asr`)."""
    sends_data_off_machine: bool = False


class Health(Model):
    ok: bool
    detail: str = ""


class NoConfig(BaseModel):
    pass


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    interface: str
    version: str
    """Entra na chave de cache: mudar a versão invalida só o que depende do provider."""
    config_schema: type[BaseModel] = NoConfig
    capabilities: frozenset[str] = field(default_factory=frozenset)
    requirements: Requirements = field(default_factory=Requirements)
    description: str = ""


class Provider(Protocol):
    @property
    def info(self) -> ProviderInfo: ...

    def health(self) -> Health: ...
