"""Formatos da API JSON (`/api/v1`): o contrato com a interface.

Os nomes daqui são os da API, em inglês. Dentro do app a fila e a pipeline falam português
(`na_fila`, `pulado`); a tradução fica nas tabelas abaixo, num lugar só.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

JobStatus = Literal["queued", "running", "done", "done_with_warnings", "failed", "cancelled"]
StepState = Literal["done", "cached", "skipped", "failed", "warning", "running", "pending"]
SubKind = Literal["embedded", "external", "ours"]

JOB_STATUS: dict[str, JobStatus] = {
    "na_fila": "queued",
    "processando": "running",
    "concluida": "done",
    "concluida_avisos": "done_with_warnings",
    "falhou": "failed",
    "cancelada": "cancelled",
}
STEP_STATE: dict[str, StepState] = {
    "ok": "done",
    "cache": "cached",
    "pulado": "skipped",
    "falhou": "failed",
    "aviso": "warning",
    "running": "running",
    "pending": "pending",
}
# no banco, legenda de outra fonte (Bazarr, humana) chama `human`
SUB_KIND: dict[str, SubKind] = {"embedded": "embedded", "human": "external", "ours": "ours"}


class Out(BaseModel):
    """O que a API devolve."""

    model_config = ConfigDict(extra="forbid")


class In(BaseModel):
    """O que a API recebe: campo desconhecido é erro, não é ignorado em silêncio."""

    model_config = ConfigDict(extra="forbid")


# --- comuns ---


class ActionResult(Out):
    """Resultado de uma ação, com a frase para mostrar ao usuário."""

    ok: bool
    message: str


class SetupItem(Out):
    title: str
    detail: str
    href: str


class Notice(Out):
    level: Literal["warn", "info"]
    title: str
    detail: str


class Sub(Out):
    lang: str
    kind: SubKind


class JobRef(Out):
    """A tarefa ativa de um item da biblioteca."""

    id: int
    label: str
    status: Literal["queued", "running"]
    progress: float


# --- fila ---


class JobRow(Out):
    id: int
    name: str
    video: str
    status: JobStatus
    action: str | None
    language: str | None
    step: str
    progress: float
    message: str | None
    error: str | None
    priority: bool
    origin: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None


class Step(Out):
    label: str
    state: StepState
    text: str


class Degraded(Out):
    """Etapa opcional que falhou ou foi pulada, sem derrubar a tarefa."""

    stage: str
    label: str
    reason: str
    effect: str


class JobDetail(JobRow):
    steps: list[Step]
    warnings: list[Degraded]
    has_subtitle: bool
    has_report: bool
    can_cancel: bool
    can_retry: bool


class JobPage(Out):
    rows: list[JobRow]
    total: int
    page: int
    pages: int
    counts: dict[str, int]
    """Quantas tarefas cada filtro tem: all, running, queued, done, failed, cancelled."""


# --- casca ---


class Shell(Out):
    """O que toda tela mostra: versão, contadores do menu e a tarefa rodando."""

    version: str
    repo_url: str
    release_url: str
    theme: str
    pending: int
    alerts_unseen: int
    setup: list[SetupItem]
    movies: int
    shows: int
    running: JobRow | None


# --- visão geral ---


class LibraryCount(Out):
    total: int
    done: int
    """Itens com legenda em todos os idiomas alvo."""


class EpisodeCount(LibraryCount):
    shows: int


class Activity(Out):
    translations: int
    transcriptions: int
    extracted: int
    synced: int
    queued: int
    failed: int


class LanguageCount(Out):
    lang: str
    count: int


class LanguageRow(Out):
    lang: str
    name: str
    embedded: int
    external: int
    ours: int
    missing: int
    target: bool


class Dashboard(Out):
    movies: LibraryCount
    episodes: EpisodeCount
    activity: Activity
    made: list[LanguageCount]
    languages: list[LanguageRow]
    alerts_open: int
    warnings: list[Notice]


# --- biblioteca ---


class MovieRow(Out):
    id: int
    title: str
    year: int | None
    folder: str
    video: str
    monitored: bool
    added_at: str
    subs: list[Sub]
    missing: list[str]
    job: JobRef | None


class MoviePage(Out):
    rows: list[MovieRow]
    total: int
    page: int
    pages: int
    counts: dict[str, int]
    """Quantos itens cada filtro tem: all, missing, complete, queued."""


class ShowRow(Out):
    id: int
    title: str
    year: int | None
    folder: str
    monitored: bool
    added_at: str
    total: int
    done: int
    missing: int
    """Episódios sem alguma legenda e sem tarefa na fila."""
    queued: bool


class ShowPage(Out):
    rows: list[ShowRow]
    total: int
    page: int
    pages: int
    counts: dict[str, int]


class EpisodeRow(Out):
    id: int
    season: int | None
    episode: int | None
    name: str
    video: str
    subs: list[Sub]
    missing: list[str]
    job: JobRef | None


class Season(Out):
    season: int | None
    """`None`: arquivos sem temporada."""
    episodes: list[EpisodeRow]


class ShowDetail(Out):
    id: int
    title: str
    year: int | None
    folder: str
    monitored: bool
    seasons: list[Season]


class Monitored(Out):
    monitored: bool


class MonitorRequest(In):
    monitored: bool | None = None
    """Sem valor: alterna."""


class QueueRequest(In):
    items: list[str]
    """`movie:1`, `episode:7` ou `series:2`."""


# --- settings ---


class SettingsPatch(In):
    changes: dict[str, Any] = Field(default_factory=dict)
    """Campos da configuração que mudaram. `radarr` e `sonarr` aceitam só os campos alterados."""
    secrets: dict[str, str | None] = Field(default_factory=dict)
    """Chave → valor novo. Vazio ou `null` apaga a salva; chave ausente fica como está."""


class LlmProvider(Out):
    id: str
    label: str
    site: str
    default_url: str
    default_model: str
    key: str
    """Nome da chave deste provedor em `secrets`."""
    effort: bool


class EngineChoice(Out):
    id: str
    description: str


class EngineInterface(Out):
    id: str
    label: str
    selected: str
    providers: list[EngineChoice]


class Language(Out):
    code: str
    name: str


class SettingsOptions(Out):
    languages: list[Language]
    llm_providers: list[LlmProvider]
    series_types: list[str]
    engines: list[EngineInterface]
    themes: list[str]
    arr_keys: dict[str, str]
    tmdb_builtin: bool
    tmdb_attribution: str


class SettingsShown(Out):
    """O que os campos de LLM mostram quando nada foi escolhido: o padrão em uso."""

    llm_model: str
    llm_base_url: str
    llm_model_from_env: bool
    llm_url_from_env: bool


class SettingsView(Out):
    config: dict[str, Any]
    shown: SettingsShown
    secrets: dict[str, Literal["settings", "environment", ""]]
    """De onde vem cada chave. O valor nunca sai do servidor."""
    options: SettingsOptions


# --- system ---


class TaskRow(Out):
    name: str
    label: str
    interval: str
    last_run: str | None
    next_run: str | None
    result: str | None
    running: bool
    requested: bool


class AlertRow(Out):
    key: str
    kind: str
    step: str
    message: str
    target: str
    count: int
    first_at: str
    last_at: str
    seen: bool
    resolved_at: str | None
    items: list[str]


class Alerts(Out):
    setup: list[SetupItem]
    alerts: list[AlertRow]


class AlertRetry(In):
    key: str


class LogRow(Out):
    level: str
    time: str
    name: str
    message: str


class Fact(Out):
    label: str
    value: str


class ProviderStatus(Out):
    label: str
    provider: str
    ok: bool
    detail: str


class SecretSource(Out):
    name: str
    source: str
    """`Settings`, `.env / environment` ou vazio (não há)."""


class Status(Out):
    about: list[Fact]
    providers: list[ProviderStatus]
    secrets: list[SecretSource]
    warnings: list[Notice]
