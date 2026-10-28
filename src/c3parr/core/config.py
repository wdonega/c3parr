"""Configuração. Precedência: CLI > variáveis C3PARR_* > .env > arquivo TOML do usuário > padrões.

Segredos (chaves de API, tokens) vêm só do ambiente ou do `.env`, nunca do TOML nem do DTO.
"""

import os
import shutil
from pathlib import Path

from dotenv import dotenv_values
from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from c3parr.core.job import ComponentSpec
from c3parr.subtitle.format import FormatRules

USER_CONFIG = Path.home() / ".config" / "c3parr" / "config.toml"
SECRET_NAMES = (
    "HF_TOKEN",
    "TMDB_API_KEY",
    "TMDB_READ_ACCESS_TOKEN",
    "ANTHROPIC_API_KEY",
    "FISH_AUDIO_API_KEY",
    "C3PARR_LLM_API_KEY",
    "C3PARR_ASR_API_KEY",
    "RADARR_API_KEY",
    "SONARR_API_KEY",
)


def _default_components() -> dict[str, ComponentSpec]:
    return {
        # whisper.cpp quando o binário existe (GPU em qualquer máquina, decisão 0005)
        "transcricao": ComponentSpec(
            provider="whisper-cpp" if shutil.which("whisper-cli") else "faster-whisper"
        ),
        "escrita_legenda": ComponentSpec(provider="srt"),
        "fonte_legenda": ComponentSpec(provider="arquivo"),
        "sincronizacao": ComponentSpec(provider="auto"),
        "metadados": ComponentSpec(provider="tmdb"),
        "contexto": ComponentSpec(provider="llm"),
        "traducao": ComponentSpec(provider="llm"),
    }


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="C3PARR_",
        env_file=".env",
        env_nested_delimiter="__",
        extra="ignore",
        toml_file=USER_CONFIG,
    )

    cache_dir: Path = Path.home() / ".cache" / "c3parr"
    config_dir: Path = USER_CONFIG.parent
    """Onde fica o `c3parr.db` (biblioteca e configs editadas na interface)."""
    theme: str = "robo"
    """Tema da interface web: arquivo web/static/themes/<nome>.css."""
    components: dict[str, ComponentSpec] = Field(default_factory=_default_components)
    format: FormatRules = Field(default_factory=FormatRules)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlConfigSettingsSource(settings_cls),
        )


ENV_FILE = Path(".env")
LLM_ENV = ("C3PARR_LLM_BASE_URL", "C3PARR_LLM_MODEL")
"""Servidor LLM local/compatível vindo do ambiente; Settings → Integrations tem prioridade."""


def load_env(names: tuple[str, ...], env_file: Path | None = None) -> dict[str, str]:
    """Os nomes pedidos, do `.env` e do ambiente (o ambiente ganha)."""
    path = env_file or ENV_FILE
    values = {k: v for k, v in dotenv_values(path).items() if v} if path.exists() else {}
    values.update({k: v for k, v in os.environ.items() if k in names and v})
    return {k: v for k, v in values.items() if k in names}


def load_secrets(env_file: Path | None = None) -> dict[str, str]:
    return load_env(SECRET_NAMES, env_file)
