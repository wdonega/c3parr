"""Formatos da API JSON (`/api/v1`): o contrato com a interface.

Os nomes daqui são os da API, em inglês. Dentro do app a fila e a pipeline falam português
(`na_fila`, `pulado`); a tradução fica nas tabelas abaixo, num lugar só.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from c3parr.library.store import AppConfig

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


class Coded(Out):
    """Um texto que o servidor escreve em inglês e a interface reescreve no idioma dela."""

    code: str = ""
    """Qual frase é (`queue.cancelled`). Vazio: o texto veio de fora e fica como está."""
    params: dict[str, str | int | float] = {}
    """Os valores que entram na frase. `x_key` diz que `x` é um nome que a interface traduz."""


class ActionResult(Coded):
    """Resultado de uma ação, com a frase para mostrar ao usuário."""

    ok: bool
    message: str


class SetupItem(Coded):
    title: str
    detail: str
    href: str


class Notice(Coded):
    level: Literal["warn", "info"]
    title: str
    detail: str


class Sub(Out):
    lang: str
    kind: SubKind


class JobRef(Out):
    """A tarefa ativa de um item da biblioteca."""

    id: int
    action: str | None = None
    """Qual passo é (`traduzir`, `asr`…): a interface escreve o nome dele no idioma dela."""
    label: str
    language: str | None = None
    """O idioma que a tarefa está fazendo; `None` quando o passo não é de um idioma."""
    status: Literal["queued", "running"]
    progress: float
    position: int | None = None
    """Lugar na fila (1 é a próxima a rodar); `None` para a que já está rodando."""


class JobItem(Out):
    """O filme ou o episódio da tarefa, para mostrar título em vez de nome de arquivo."""

    kind: Literal["movie", "episode"]
    id: int
    title: str
    """Do filme, ou da série do episódio."""
    year: int | None
    show_id: int | None
    season: int | None
    episode: int | None
    episode_title: str | None


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
    item: JobItem | None = None
    """`None`: o vídeo não está (mais) na biblioteca."""


class Step(Out):
    name: str = ""
    """Qual etapa é (`traducao`, `sincronizacao`…)."""
    label: str
    state: StepState
    text: str
    """O estado em inglês, com quanto levou, o motor e o motivo: é o que `seconds`, `provider`
    e `reason` dizem em separado."""
    seconds: float | None = None
    provider: str | None = None
    reason: str | None = None


class Degraded(Out):
    """Etapa opcional que falhou ou foi pulada, sem derrubar a tarefa."""

    stage: str
    label: str
    reason: str
    effect: str


class PipelineStep(Out):
    """Um passo da pipeline já registrado para o vídeo (o último resultado de cada um)."""

    action: str
    label: str
    language: str | None
    state: Literal["done", "not_found", "failed"]
    detail: str | None
    attempts: int
    updated_at: str


PIPELINE_STATE: dict[str, Literal["done", "not_found", "failed"]] = {
    "ok": "done",
    "nao_encontrado": "not_found",
    "falhou": "failed",
}


class JobDetail(JobRow):
    stage: str | None
    """O que a tarefa está fazendo agora (Translation, Sync…), enquanto roda."""
    stage_name: str | None = None
    """A mesma etapa pelo nome (`traducao`), para a interface traduzir."""
    steps: list[Step]
    pipeline: list[PipelineStep]
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
    name: str
    """O nome desta instalação (Settings → General)."""
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
    """Um idioma alvo: quantos itens têm a legenda dele (por origem) e quantos não têm."""

    lang: str
    name: str
    embedded: int
    external: int
    ours: int
    missing: int


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
    """As legendas do idioma original e dos idiomas alvo."""
    others: int
    """Em quantos outros idiomas o item tem legenda (não entram em `subs`)."""
    missing: list[str]
    job: JobRef | None
    poster: str | None
    """Endereço do pôster nesta API; `None` se o Radarr não informou um."""


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
    poster: str | None


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
    title: str | None
    """Título do episódio no Sonarr."""
    name: str
    video: str
    subs: list[Sub]
    """As legendas do idioma original e dos idiomas alvo."""
    others: int
    """Em quantos outros idiomas o item tem legenda (não entram em `subs`)."""
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
    poster: str | None
    seasons: list[Season]


class SearchHit(Out):
    id: int
    title: str
    year: int | None
    poster: str | None


class SearchResults(Out):
    movies: list[SearchHit]
    shows: list[SearchHit]


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
    cuda: bool
    """Esta máquina tem GPU NVIDIA que o torch enxerga (a legenda de show precisa dela ou do
    processador CUDA)."""
    gpu: str | None
    concert_cpu_forced: bool
    """`C3PARR_CONCERT_ALLOW_CPU=true`: a legenda de show roda na CPU mesmo assim."""


class SettingsShown(Out):
    """O que os campos de LLM mostram quando nada foi escolhido: o padrão em uso."""

    llm_model: str
    llm_base_url: str
    llm_model_from_env: bool
    llm_url_from_env: bool


class TestRecord(Coded):
    """O último teste de conexão de um alvo, enquanto o que ele testou não mudar."""

    ok: bool
    message: str
    at: str
    ms: int


class TestResult(ActionResult):
    ms: int | None = None
    """Quanto o teste levou."""


class Listening(Out):
    """Onde o servidor escuta. Vem do ambiente (C3PARR_HOST, C3PARR_PORT), não de Settings."""

    address: str
    port: int
    url_base_from_env: str
    """A URL base que vem de C3PARR_URL_BASE e vale enquanto a de Settings estiver vazia."""


class SettingsView(Out):
    listening: Listening
    tests: dict[str, TestRecord]
    config: AppConfig
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
    id: str
    label: str
    value: str


class ProviderStatus(Out):
    id: str
    label: str
    provider: str
    ok: bool
    detail: str


class SecretSource(Out):
    name: str
    source: Literal["settings", "environment", ""]
    """De onde a chave vem: digitada em Settings, do ambiente (.env), ou vazio (não há)."""


class Status(Out):
    about: list[Fact]
    providers: list[ProviderStatus]
    secrets: list[SecretSource]
    warnings: list[Notice]
