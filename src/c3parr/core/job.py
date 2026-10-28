"""DTO de parâmetros de uma tarefa: o que a tarefa traz para o motor. Nunca carrega segredos."""

from typing import Any, Literal

from pydantic import Field

from c3parr.core.models import Model

RecipeName = Literal["completa", "sincronizacao", "transcricao", "asr", "legenda"]


class ComponentSpec(Model):
    """Provider desejado para uma interface e seus parâmetros (sem segredos)."""

    provider: str
    params: dict[str, Any] = Field(default_factory=dict)


class JobParams(Model):
    recipe: RecipeName = "completa"
    video: str
    src: str | None = None
    dst: str = "pt-BR"
    ref: str | None = None
    ref_bridge: str | None = None
    """Legenda no idioma do áudio usada como ponte para sincronizar `ref`."""
    title: str | None = None
    year: int | None = None
    tmdb_id: int | None = None
    bible: str | None = None
    no_tmdb: bool = False
    out_dir: str | None = None
    keep_transcript: bool = False
    interactive: bool = False
    force: tuple[str, ...] = ()
    no_cache: bool = False
    verbose: bool = False
    components: dict[str, ComponentSpec] = Field(default_factory=dict)
