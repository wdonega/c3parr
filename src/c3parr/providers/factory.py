"""Uma fábrica por interface, e o registro de interface para fábrica.

Segredos entram por `secrets` (vindos da configuração), nunca pelos parâmetros do DTO. Um campo do
esquema é segredo quando declara `json_schema_extra={"secret_env": "NOME_DA_VARIAVEL"}`.
"""

import importlib.util
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from c3parr.core.errors import InvalidInput, Unavailable
from c3parr.core.job import ComponentSpec
from c3parr.providers.base import ProviderInfo

EXTRA_PROBES = {"asr": "faster_whisper", "embed": "sentence_transformers"}


def secret_fields(schema: type[BaseModel], prefix: str = "") -> dict[str, str]:
    """Campo do esquema -> variável de onde vem o segredo. Entra em sub-blocos: o caminho fica
    com ponto (`llm.api_key` para a chave do LLM dentro da config do tradutor)."""
    out = {}
    for name, f in schema.model_fields.items():
        extra = f.json_schema_extra
        if isinstance(extra, dict) and isinstance(extra.get("secret_env"), str):
            out[prefix + name] = str(extra["secret_env"])
        elif isinstance(f.annotation, type) and issubclass(f.annotation, BaseModel):
            out.update(secret_fields(f.annotation, f"{prefix}{name}."))
    return out


def _has_path(values: Mapping[str, Any], path: str) -> bool:
    head, _, rest = path.partition(".")
    if head not in values:
        return False
    inner = values[head]
    return not rest or (isinstance(inner, Mapping) and _has_path(inner, rest))


def _set_path(values: dict[str, Any], path: str, value: str) -> None:
    head, _, rest = path.partition(".")
    if not rest:
        values[head] = value
        return
    inner = dict(values.get(head) or {})
    _set_path(inner, rest, value)
    values[head] = inner


@dataclass(frozen=True)
class Registration[P]:
    info: ProviderInfo
    build: Callable[[Any], P]


class ComponentFactory[P]:
    def __init__(self, interface: str) -> None:
        self.interface = interface
        self._registry: dict[str, Registration[P]] = {}

    def register(self, info: ProviderInfo, build: Callable[[Any], P]) -> None:
        if info.interface != self.interface:
            raise ValueError(f"provider {info.id} belongs to interface {info.interface}")
        self._registry[info.id] = Registration(info, build)

    def providers(self) -> list[ProviderInfo]:
        return [r.info for r in self._registry.values()]

    def info(self, provider_id: str) -> ProviderInfo:
        return self._lookup(provider_id).info

    def create(self, spec: ComponentSpec, secrets: Mapping[str, str]) -> P:
        reg = self._lookup(spec.provider)
        schema = reg.info.config_schema
        secrets_map = secret_fields(schema)
        leaked = sorted(path for path in secrets_map if _has_path(spec.params, path))
        if leaked:
            raise InvalidInput(
                f"secrets cannot come in the task parameters: {', '.join(leaked)}",
                provider=spec.provider,
            )
        for extra in reg.info.requirements.extras:
            module = EXTRA_PROBES.get(extra, extra)
            if importlib.util.find_spec(module) is None:
                raise Unavailable(
                    f"{spec.provider} needs the extra '{extra}' (uv sync --extra {extra})",
                    provider=spec.provider,
                )
        values = dict(spec.params)
        for path, env in secrets_map.items():
            if env in secrets:
                _set_path(values, path, secrets[env])
        try:
            config = schema.model_validate(values)
        except ValidationError as e:
            missing_secret = [
                secrets_map[".".join(str(x) for x in err["loc"])]
                for err in e.errors()
                if err["type"] == "missing" and ".".join(str(x) for x in err["loc"]) in secrets_map
            ]
            if missing_secret:
                raise Unavailable(
                    f"missing key: {', '.join(missing_secret)}", provider=spec.provider
                ) from e
            raise InvalidInput(
                f"invalid parameters for {spec.provider}: {e}", provider=spec.provider
            ) from e
        return reg.build(config)

    def _lookup(self, provider_id: str) -> Registration[P]:
        try:
            return self._registry[provider_id]
        except KeyError:
            known = ", ".join(sorted(self._registry)) or "none"
            raise InvalidInput(
                f"unknown provider for {self.interface}: {provider_id} (known: {known})"
            ) from None


class AbstractComponentFactoryFactory(dict[str, ComponentFactory[Any]]):
    """Por tradição de engenharia: é só um registro de interface para fábrica."""

    def for_interface(self, interface: str) -> ComponentFactory[Any]:
        try:
            return self[interface]
        except KeyError:
            raise InvalidInput(f"unknown interface: {interface}") from None
