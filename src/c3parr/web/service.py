"""O que as telas mostram e o que as ações fazem, sem HTTP.

A API JSON (`api.py`) só traduz o que sai daqui: a regra mora no `Service`, uma vez só.
O que fala com o mundo de fora (Radarr/Sonarr, TMDb, LLM, servidor de transcrição) entra por
`Clients`, para os testes trocarem.
"""

import hashlib
import json
import logging
import os
import platform
import re
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, NamedTuple
from urllib.parse import urlencode, urlsplit

from pydantic import ValidationError

from c3parr import __version__
from c3parr.asr.remote import REMOTE_IDS, RemoteASRConfig
from c3parr.asr.whisper_cpp import measured_speed
from c3parr.concert import device as concert_device
from c3parr.core.config import LLM_ENV, SECRET_NAMES, Settings, load_env, load_secrets
from c3parr.core.errors import ProviderError
from c3parr.core.job import ComponentSpec
from c3parr.core.search import matches, words
from c3parr.jobs.queue import ACTION_LABELS, Job, JobQueue
from c3parr.library.arr import API_KEY_ENV as ARR_KEY_ENV
from c3parr.library.arr import SERIES_TYPES, ArrConfig, ArrKind, major
from c3parr.library.store import AppConfig, Library, LibraryItem
from c3parr.library.tasks import Scheduler
from c3parr.llm.client import LLM_KEY_ENV, LLM_PRESETS, LLMConfig, llm_defaults
from c3parr.metadata import tmdb as tmdb_meta
from c3parr.metadata.tmdb import TMDbConfig
from c3parr.pipeline.catalog import Catalog
from c3parr.pipeline.naming import norm_lang, same_base, same_lang
from c3parr.pipeline.reconcile import PipelineContext, reconcile
from c3parr.providers.factory import AbstractComponentFactoryFactory

STEP_LABELS = {
    "audio": "Audio",
    "metadados": "Metadata (TMDb)",
    "transcricao": "Transcription",
    "referencia": "Reference subtitle",
    "ponte": "Bridge subtitle",
    "sincronizacao": "Sync",
    "biblia": "User bible",
    "contexto": "Context (bible)",
    "nomes": "Name correction",
    "traducao": "Translation",
    "formatacao": "Formatting",
    "escrita": "Write subtitle",
    "escrita_original": "Original transcript",
    "memoria": "Series memory",
    # legenda de show
    "voz": "Voice separation",
    "letras": "Lyrics",
    "musicas": "Songs played",
    "encaixe": "Lyric fitting",
    "segunda_escuta": "Second listen",
    "repetidas": "Repeated lines",
    "fala": "Speech between songs",
    "montagem": "Assembly",
}
STEP_STATUS = {"ok": "done", "cache": "from cache", "pulado": "skipped", "falhou": "failed"}
LANGUAGES = [
    ("pt-BR", "Portuguese (Brazil)"), ("pt", "Portuguese (Portugal)"), ("en", "English"),
    ("es", "Spanish"), ("fr", "French"), ("it", "Italian"), ("de", "German"), ("nl", "Dutch"),
    ("ar", "Arabic"), ("he", "Hebrew"), ("tr", "Turkish"), ("ru", "Russian"),
    ("uk", "Ukrainian"), ("pl", "Polish"), ("cs", "Czech"), ("ro", "Romanian"),
    ("hu", "Hungarian"), ("el", "Greek"), ("sv", "Swedish"), ("no", "Norwegian"),
    ("da", "Danish"), ("fi", "Finnish"), ("ja", "Japanese"), ("ko", "Korean"),
    ("zh", "Chinese"), ("hi", "Hindi"), ("th", "Thai"), ("vi", "Vietnamese"),
    ("id", "Indonesian"), ("ca", "Catalan"),
]  # fmt: skip
LANGUAGE_NAMES = dict(LANGUAGES)
INTERFACE_LABELS = {
    "transcricao": "Transcription (ASR)",
    "sincronizacao": "Sync",
    "metadados": "Metadata",
    "contexto": "Context (bible)",
    "traducao": "Translation",
    "fonte_legenda": "Subtitle source",
    "escrita_legenda": "Subtitle format",
}
# o que roda dentro do app (Settings → Engines); o resto é serviço externo (Integrations)
ENGINE_INTERFACES = ("transcricao", "sincronizacao", "fonte_legenda", "escrita_legenda")
LLM_EFFORT_FLAVORS = ("anthropic", "gemini")
# onde pegar a chave (ou, no servidor próprio, um jeito fácil de subir um)
LLM_SITES = {
    "custom": "https://github.com/ollama/ollama#ollama",
    "openai": "https://platform.openai.com/",
    "anthropic": "https://platform.claude.com/",
    "gemini": "https://aistudio.google.com/",
    "deepseek": "https://platform.deepseek.com/",
}
LLM_PROVIDERS = [
    ("custom", "Local / custom (OpenAI-compatible URL)"),
    ("openai", "OpenAI (ChatGPT)"),
    ("anthropic", "Anthropic (Claude)"),
    ("gemini", "Google (Gemini)"),
    ("deepseek", "DeepSeek"),
]
INTEGRATION_SECRETS = (
    "TMDB_READ_ACCESS_TOKEN",
    *LLM_KEY_ENV.values(),
    "C3PARR_ASR_API_KEY",
    *ARR_KEY_ENV.values(),
)
SETTINGS_PAGES = [
    ("library", "Library"),
    ("integrations", "Integrations"),
    ("languages", "Languages"),
    ("engines", "Engines"),
    ("subtitles", "Subtitles"),
]
SYSTEM_PAGES = [("tasks", "Tasks"), ("alerts", "Alerts"), ("logs", "Logs"), ("status", "Status")]
ACTIVE = frozenset({"na_fila", "processando"})
PAGE_SIZE = 100
# chips da fila: valor na URL → (rótulo, status do banco)
QUEUE_FILTERS: dict[str, tuple[str, tuple[str, ...]]] = {
    "": ("All", ()),
    "running": ("Running", ("processando",)),
    "queued": ("Queued", ("na_fila",)),
    "done": ("Done", ("concluida", "concluida_avisos")),
    "failed": ("Failed", ("falhou",)),
    "cancelled": ("Cancelled", ("cancelada",)),
}
SUBTITLE_FLAGS = (
    "use_embedded", "embedded_resolves", "external_resolves", "forced_resolves",
    "sync_new_external", "fix_external_in_place", "delete_ours_on_human",
    "new_external_becomes_main", "asr_enabled", "asr_always",
    "concert_lyrics", "concert_publish_original",
)  # fmt: skip
SUBTITLE_CHOICES = ("derived_on_main_change", "foreign_tag", "concert_mode")
# campos da AppConfig que a API grava como vieram (a validação é a do próprio modelo)
PLAIN_SETTINGS = (*SUBTITLE_FLAGS, *SUBTITLE_CHOICES, "wait_minutes", "search_interval_minutes",
                  "monitor_new", "sync_interval_minutes", "theme")  # fmt: skip
PATCHABLE = frozenset({
    *PLAIN_SETTINGS, "radarr", "sonarr", "target_languages", "fallback_languages", "components",
    "url_base", "instance_name", "allowed_hosts", "concert_paths",
    "llm_provider", "llm_model", "llm_base_url", "llm_effort", "asr_mode", "asr_url", "asr_model",
})  # fmt: skip


SORTS = ("title", "added", "missing")
POSTER_TYPES = (".jpg", ".jpeg", ".png", ".webp")
POSTER_RETRY_S = 600.0
"""Pôster que não veio só é pedido de novo depois disto (a lista não martela quem está fora)."""
# o que cada teste de conexão usa: mexeu nisso, o resultado guardado não vale mais
TEST_INPUTS: dict[str, frozenset[str]] = {
    "radarr": frozenset({"radarr", "RADARR_API_KEY"}),
    "sonarr": frozenset({"sonarr", "SONARR_API_KEY"}),
    "llm": frozenset({"llm_provider", "llm_model", "llm_base_url", "llm_effort",
                      *LLM_KEY_ENV.values()}),
    "asr": frozenset({"asr_mode", "asr_url", "asr_model", "C3PARR_ASR_API_KEY", "components"}),
    "tmdb": frozenset({"TMDB_READ_ACCESS_TOKEN"}),
}  # fmt: skip


URL_BASE = re.compile(r"[A-Za-z0-9._~-]+(/[A-Za-z0-9._~-]+)*")
DEFAULT_NAME = "c3parr"


class Host(NamedTuple):
    """Como o c3parr é servido (Settings → General → Host)."""

    url_base: str
    """`/c3parr`, ou vazio na raiz."""
    name: str
    allowed: frozenset[str]
    """Nomes aceitos no cabeçalho Host; vazio = qualquer um."""


def clean_url_base(text: str) -> str | None:
    """`c3parr/`, ` /c3parr ` → `/c3parr`; vazio → vazio (na raiz). `None`: não serve de caminho."""
    path = text.strip().strip("/")
    if not path:
        return ""
    return f"/{path}" if URL_BASE.fullmatch(path) else None


def listening() -> tuple[str, int]:
    """Onde o servidor escuta. Vem do ambiente: no Docker, quem decide a porta é o compose."""
    port = os.environ.get("C3PARR_PORT", "")
    return os.environ.get("C3PARR_HOST", "127.0.0.1"), int(port) if port.isdigit() else 8484


class NotFound(Exception):
    """O item pedido não existe (vira 404 na rota)."""


Param = str | int | float
NO_PARAMS: Mapping[str, Param] = MappingProxyType({})

# O que o servidor diz ao usuário. A frase daqui é a de inglês; a API manda junto o código e os
# parâmetros, e a interface escreve no idioma dela (frontend/src/i18n, chaves `msg.<código>`).
# Um parâmetro `x_key` diz que `x` é um nome que a interface também sabe traduzir.
MESSAGES = {
    "queue.cancelled": "{n} task(s) cancelled.",
    "queue.none_waiting": "Nothing is waiting in the queue.",
    "queue.retried": "{n} task(s) back in the queue.",
    "queue.none_failed": "No failed task to run again.",
    "library.select_one": "Select at least one item.",
    "library.unreadable": "{n} item(s) could not be read (see System → Alerts).",
    "library.covered": "Nothing to do: every target language is covered.",
    "library.queued": "{n} item(s) in the queue ({steps} new step(s)).",
    "test.arr_connected": "{name} {version}: connected.",
    "test.arr_outdated": "{name} {version}: connected. Update it: the latest is {latest}.",
    "test.arr_fields": "{name}: check the fields.",
    "test.tmdb_no_token": "TMDb: no read access token.",
    "test.connected": "{name}: connected.",
    "test.error": "{name}: {detail}",
    "test.llm_unknown": "LLM: unknown provider {provider}.",
    "test.asr_remote": "Transcription: connected.{extra}",
    "test.asr_local": "Transcription: {engine} ready on this machine.{extra}",
    "test.asr_error": "Transcription: {detail}",
    "settings.saved": "Saved.",
    "settings.invalid": "Not saved, check: {fields}",
    "settings.unknown": "Not saved, unknown: {fields}",
    "settings.no_target": "Pick at least one target language.",
    "settings.asr_url": "Transcription: put the server URL.",
    "settings.url_base": "URL base: use a path like /c3parr (letters, digits, - and _).",
    "task.starting": "{task}: starting.",
    "alert.resolved": "Alert already resolved.",
    "alert.still_failing": "Still failing for {n} item(s).",
    "alert.retrying": "Retrying: {n} step(s) queued.",
}
# O mínimo que falta configurar: código → (título, detalhe). Chaves `setup.<código>.title|detail`.
SETUP = {
    "connect_arr": ("Connect Radarr or Sonarr",
                    "c3parr reads its library from them, like Bazarr does."),
    "arr_incomplete": ("{name} needs its address and API key",
                       "{name} → Settings → General → API Key."),
    "no_llm": ("No LLM configured",
               "Translations need an LLM: pick a provider and its key, or the URL and model of "
               "your own server."),
    "no_llm_key": ("No API key for the LLM ({provider})",
                   "Translations need the key of the chosen provider."),
    "no_tmdb": ("No TMDb key",
                "Without metadata the original language and character names are guessed."),
}  # fmt: skip
# Avisos de configuração: código → (título, detalhe). Chaves `notice.<código>.title|detail`.
NOTICES = {
    "arr_outdated": ("{name} {found} is out of date",
                     "The latest is {name} {latest}. c3parr is made for it; update {name} to "
                     "keep the library in sync."),
    "asr_remote": ("Transcription on a separate server", "{url} ({provider})."),
    "asr_cpu": ("Transcription runs on the CPU",
                "Measured here: about {minutes} min per hour of video. A GPU build of "
                "whisper.cpp (Metal, Vulkan, CUDA) is 5-8x faster."),
    "asr_backend": ("Transcription on {backend}",
                    "Measured here: about {minutes} min per hour of video."),
    "asr_faster_whisper": ("Transcription runs on the CPU (faster-whisper)",
                           "About 10 min per hour of video. Install whisper.cpp "
                           "(brew install whisper.cpp) to use the GPU."),
    "image_subs": ("{n} item(s) have image subtitles",
                   "PGS/VobSub tracks need OCR and are ignored; text tracks and the audio are "
                   "used instead."),
}  # fmt: skip


class Outcome(NamedTuple):
    """Resultado de uma ação, em palavras para o usuário."""

    message: str
    error: bool = False
    status: int = 200
    code: str = ""
    """Qual frase de `MESSAGES` é; vazio quando o texto veio de fora (erro do Radarr, do LLM)."""
    params: Mapping[str, Param] = NO_PARAMS


def say(code: str, *, error: bool = False, status: int = 200, **params: Param) -> Outcome:
    """A frase de `MESSAGES` com os parâmetros no lugar, e o código para a interface traduzir."""
    return Outcome(MESSAGES[code].format(**params), error, status, code, params)


def setup_item(code: str, href: str, **params: Param) -> dict[str, Any]:
    title, detail = SETUP[code]
    return {"title": title.format(**params), "detail": detail.format(**params), "href": href,
            "code": code, "params": params}  # fmt: skip


def notice(code: str, level: str, **params: Param) -> dict[str, Any]:
    title, detail = NOTICES[code]
    return {"level": level, "title": title.format(**params), "detail": detail.format(**params),
            "code": code, "params": params}  # fmt: skip


@dataclass(frozen=True)
class Clients:
    """Quem fala com o mundo de fora. As rotas passam os de verdade; os testes, os deles."""

    arr: Callable[[ArrKind, ArrConfig, str], Any]
    tmdb: Callable[[TMDbConfig], Any]
    llm: Callable[[LLMConfig], Any]
    remote_asr: Callable[[RemoteASRConfig, str], Any]
    env_secrets: Callable[[], dict[str, str]]
    latest_major: Callable[[ArrKind], int]
    fetch_poster: Callable[[ArrKind, str], bytes | None]
    """Baixa o pôster (endereço da internet ou capa no Radarr/Sonarr); None se não veio."""


class LogBuffer(logging.Handler):
    """Guarda as últimas linhas de log para System → Logs."""

    def __init__(self, size: int = 500) -> None:
        super().__init__(level=logging.INFO)
        self.records: deque[dict[str, str]] = deque(maxlen=size)

    def emit(self, record: logging.LogRecord) -> None:
        when = datetime.fromtimestamp(record.created).strftime("%b %d %H:%M:%S")
        self.records.append({"level": record.levelname, "time": when, "name": record.name,
                             "message": record.getMessage()})  # fmt: skip


def pager(path: str, params: dict[str, str], page: int, total: int) -> dict[str, Any]:
    """Página pedida (corrigida para o intervalo) e os links de anterior e próxima."""
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(max(page, 1), pages)

    def href(n: int) -> str:
        return f"{path}?{urlencode({**{k: v for k, v in params.items() if v}, 'page': n})}"

    return {"page": page, "pages": pages, "total": total, "href": href(page),
            "first": (page - 1) * PAGE_SIZE + 1 if total else 0,
            "last": min(page * PAGE_SIZE, total),
            "prev": href(page - 1) if page > 1 else None,
            "next": href(page + 1) if page < pages else None}  # fmt: skip


def _shown(show: str, missing: bool, queued: bool) -> bool:
    return {"missing": missing, "complete": not missing, "queued": queued}.get(show, True)


def poster_source(url: str) -> str:
    """O pôster num tamanho de lista: o TMDb e o Radarr/Sonarr guardam versões menores."""
    if "image.tmdb.org/t/p/original/" in url:
        return url.replace("/t/p/original/", "/t/p/w342/")
    if "/MediaCover/" in url and not url.startswith(("http://", "https://")):
        return re.sub(r"/poster(\.\w+)", r"/poster-500\1", url, count=1)
    return url


def interval_label(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes % 1440 == 0:
        return f"{minutes // 1440} d"
    if minutes % 60 == 0:
        return f"{minutes // 60} h"
    return f"{minutes} min"


def job_steps(job: Job, planned: list[str]) -> list[dict[str, Any]]:
    """Linha do tempo das etapas: as já rodadas (com motivo) e as que ainda vêm."""
    if job.etapas:
        out = []
        degraded = {d.get("stage") for d in job.degradacoes}
        for s in job.etapas:
            state: str = "aviso" if s.status == "falhou" and s.name in degraded else s.status
            text = "with warning" if state == "aviso" else STEP_STATUS.get(s.status, s.status)
            if s.seconds:
                text += f" · {s.seconds:.0f} s"
            if s.provider:
                text += f" · {s.provider}"
            if s.reason:
                text += f" · {s.reason}"
            label = STEP_LABELS.get(s.name) or ACTION_LABELS.get(s.name, s.name)
            out.append({"name": s.name, "label": label, "state": state, "text": text,
                        "seconds": s.seconds or None, "provider": s.provider or None,
                        "reason": s.reason or None})  # fmt: skip
        return out
    current = planned.index(job.etapa) if job.etapa in planned else -1
    out = []
    for i, name in enumerate(planned):
        reason = None
        if job.status == "processando" and i < current:
            state, text = "ok", "done"
        elif job.status == "processando" and i == current:
            state, text, reason = "running", job.mensagem or "running", job.mensagem or None
        else:
            state, text = "pending", "waiting"
        out.append({"name": name, "label": STEP_LABELS.get(name, name), "state": state,
                    "text": text, "seconds": None, "provider": None, "reason": reason})  # fmt: skip
    return out


def components_for(settings: Settings, config: AppConfig) -> dict[str, ComponentSpec]:
    """Padrões do `Settings` com o motor escolhido em Settings → Engines por cima, e o LLM de
    Settings → Integrations (provedor, endereço e modelo) no contexto e na tradução."""
    out = dict(settings.components)
    for iface, provider in config.components.items():
        base = out.get(iface)
        same = base is not None and base.provider == provider
        out[iface] = base if same and base else ComponentSpec(provider=provider)
    flavor = config.llm_provider if config.llm_provider in LLM_PRESETS else "custom"
    url, model = llm_defaults(flavor)
    llm = {
        "flavor": flavor,
        "base_url": (config.llm_base_url if flavor == "custom" and config.llm_base_url else url),
        "model": config.llm_model or model,
        "effort": config.llm_effort if flavor in LLM_EFFORT_FLAVORS else None,
    }
    for iface in ("contexto", "traducao"):
        spec = out.get(iface)
        if spec is not None and spec.provider == "llm":
            out[iface] = spec.model_copy(update={"params": {**spec.params, "llm": llm}})
    # servidor de transcrição à parte (Settings → Integrations) passa por cima do motor local
    if config.asr_mode in REMOTE_IDS:
        params: dict[str, Any] = {"base_url": config.asr_url}
        if config.asr_model:
            params["model"] = config.asr_model
        out["transcricao"] = ComponentSpec(provider=REMOTE_IDS[config.asr_mode], params=params)
    return out


def effective_secrets(lib: Library, env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Chaves do ambiente/.env com as digitadas em Settings por cima; a chave do provedor de LLM
    escolhido vira a chave que o cliente do LLM usa."""
    out = dict(load_secrets() if env is None else env)
    out.update(lib.secrets())
    flavor = lib.config().llm_provider
    key = out.get(LLM_KEY_ENV.get(flavor, "C3PARR_LLM_API_KEY"))
    if key:
        out["C3PARR_LLM_API_KEY"] = key
    else:
        out.pop("C3PARR_LLM_API_KEY", None)
    return out


class Service:
    def __init__(
        self,
        settings: Settings,
        library: Library,
        queue: JobQueue,
        catalog: Catalog,
        factories: AbstractComponentFactoryFactory,
        secrets: dict[str, str],
        pipeline: PipelineContext,
        scheduler: Scheduler,
        logs: LogBuffer,
        planned: list[str],
        themes: list[str],
        clients: Clients,
    ) -> None:
        self.settings = settings
        self.lib = library
        self.jobs = queue
        self.catalog = catalog
        self.factories = factories
        self.secrets = secrets
        """Dicionário vivo (o motor e a pipeline guardam o mesmo): Settings atualiza no lugar."""
        self.pipeline = pipeline
        self.scheduler = scheduler
        self.logs = logs
        self.planned = planned
        self.themes = themes
        self.clients = clients
        self._poster_misses: dict[str, float] = {}
        self._host: Host | None = None

    # --- como é servido ---

    def host(self) -> Host:
        """URL base, nome e hosts aceitos. Lido uma vez e de novo quando Settings grava: o
        servidor consulta isto a cada requisição."""
        if self._host is None:
            cfg = self.lib.config()
            base = cfg.url_base or clean_url_base(os.environ.get("C3PARR_URL_BASE", "")) or ""
            self._host = Host(base, cfg.instance_name or DEFAULT_NAME,
                              frozenset(h.lower() for h in cfg.allowed_hosts))  # fmt: skip
        return self._host

    # --- chaves ---

    def effective_secrets(self) -> dict[str, str]:
        return effective_secrets(self.lib, self.clients.env_secrets())

    def refresh_secrets(self) -> None:
        self.secrets.clear()
        self.secrets.update(self.effective_secrets())

    def components(self) -> dict[str, ComponentSpec]:
        return components_for(self.settings, self.lib.config())

    # --- o que aparece em toda tela ---

    def theme(self) -> str:
        theme = self.lib.config().theme
        return theme if theme in self.themes else "robo"

    def pending(self) -> int:
        return self.jobs.pending_count()

    def unseen_alerts(self) -> int:
        return self.catalog.unseen_alerts()

    def shell(self) -> dict[str, Any]:
        """O que aparece em toda tela: contadores do menu, o que falta configurar e a tarefa
        que está rodando."""
        setup = self.setup_needed()
        return {"theme": self.theme(), "name": self.host().name, "pending": self.pending(),
                "setup": setup,
                "alerts_unseen": self.unseen_alerts() + len(setup),
                "movies": len(self.lib.movies()), "shows": len(self.lib.series_list()),
                "running": self.running_job()}  # fmt: skip

    def setup_needed(self) -> list[dict[str, Any]]:
        """O mínimo para funcionar e que ainda falta. Fica em System → Alerts e no contador até
        ser configurado: abrir a página não faz sumir."""
        cfg = self.lib.config()
        secrets = self.effective_secrets()
        out = []
        arrs = [k for k in ("radarr", "sonarr") if getattr(cfg, k).enabled]
        if not arrs:
            out.append(setup_item("connect_arr", "/settings/library/radarr"))
        for kind in arrs:
            if not getattr(cfg, kind).host or ARR_KEY_ENV[kind] not in secrets:
                out.append(setup_item("arr_incomplete", f"/settings/library/{kind}",
                                      name=kind.capitalize()))  # fmt: skip
        flavor = cfg.llm_provider if cfg.llm_provider in LLM_PRESETS else "custom"
        url, model = llm_defaults(flavor)
        if flavor == "custom" and not ((cfg.llm_base_url or url) and (cfg.llm_model or model)):
            out.append(setup_item("no_llm", "/settings/integrations"))
        elif flavor != "custom" and LLM_KEY_ENV[flavor] not in secrets:
            out.append(setup_item("no_llm_key", "/settings/integrations", provider=flavor))
        if "TMDB_READ_ACCESS_TOKEN" not in secrets and not tmdb_meta.BUILTIN_READ_TOKEN:
            out.append(setup_item("no_tmdb", "/settings/integrations"))
        return out

    def warnings(self) -> list[dict[str, Any]]:
        """Avisos de configuração (não são falhas: não repetem nem viram alerta)."""
        out: list[dict[str, Any]] = []
        cfg = self.lib.config()
        for kind in ("radarr", "sonarr"):  # *arr fora da versão major mais nova: aviso fixo
            found = major(self.lib.meta(f"{kind}_version") or "")
            latest = int(self.lib.meta(f"{kind}_latest_major") or 0)
            if getattr(cfg, kind).enabled and found and latest and found < latest:
                out.append(notice("arr_outdated", "warn", name=kind.capitalize(), found=found,
                                  latest=latest))  # fmt: skip
        asr = components_for(self.settings, cfg).get("transcricao")
        if asr is not None and asr.provider in REMOTE_IDS.values():
            out.append(notice("asr_remote", "info", url=cfg.asr_url, provider=asr.provider))
        elif asr is not None and asr.provider == "whisper-cpp":
            speed = measured_speed()
            if speed and str(speed.get("backend", "")).upper() == "CPU":
                minutes = float(speed["seconds_per_minute"])
                out.append(notice("asr_cpu", "warn", minutes=f"{minutes:.0f}"))
            elif speed:
                minutes = float(speed["seconds_per_minute"])
                out.append(notice("asr_backend", "info", backend=str(speed["backend"]),
                                  minutes=f"{minutes:.1f}"))  # fmt: skip
        elif asr is not None and asr.provider == "faster-whisper":
            out.append(notice("asr_faster_whisper", "warn"))
        images = sum(1 for m in self.catalog.all_media() if m.probe
                     and any(not t.text_based for t in m.probe.subtitles))  # fmt: skip
        if images:
            out.append(notice("image_subs", "info", n=images))
        return out

    # --- dashboard ---

    def dashboard_data(self) -> dict[str, Any]:
        """Números da visão geral: biblioteca, o que a pipeline fez e legendas por idioma."""
        cfg = self.lib.config()
        movies = self.item_rows(self.lib.movies())
        episodes = self.item_rows(self.lib.episodes())
        steps = self.catalog.step_counts()
        made: dict[str, int] = {}
        for (lang, _), n in self.catalog.output_counts().items():
            made[lang] = made.get(lang, 0) + n
        targets = cfg.target_languages
        by_lang: dict[str, dict[str, Any]] = {}

        def row(lang: str) -> dict[str, Any]:
            return by_lang.setdefault(
                lang, {"lang": lang, "embedded": 0, "human": 0, "ours": 0, "missing": 0}
            )

        for r in movies + episodes:
            for lang, kind in r["subs"]:
                target = next((t for t in targets if same_lang(t, lang)), None)
                if target:
                    row(target)[kind] += 1
            for lang in r["missing"]:
                row(lang)["missing"] += 1
        # só os idiomas alvo, na ordem das configurações (o primeiro é o principal)
        languages = [by_lang[t] for t in targets if t in by_lang]
        return {
            "movies": {"total": len(movies), "done": sum(1 for r in movies if not r["missing"])},
            "episodes": {"total": len(episodes), "shows": len(self.lib.series_list()),
                         "done": sum(1 for r in episodes if not r["missing"])},
            "activity": {
                "translations": steps.get(("traduzir", "ok"), 0),
                "transcriptions": steps.get(("asr", "ok"), 0),
                "extracted": steps.get(("extrair", "ok"), 0),
                "synced": steps.get(("sincronizar", "ok"), 0),
                "queued": self.jobs.pending_count(),
                "failed": sum(n for (_, st), n in steps.items() if st == "falhou"),
            },
            "made": sorted(made.items()),
            "languages": languages,
            "alerts_open": len(self.catalog.alerts(include_resolved=False)),
            "warnings": [w for w in self.warnings() if w["level"] == "warn"],
        }  # fmt: skip

    # --- fila ---

    def queue(self, q: str, status: str, page: int) -> dict[str, Any]:
        statuses = QUEUE_FILTERS.get(status, QUEUE_FILTERS[""])[1]
        offset = (max(page, 1) - 1) * PAGE_SIZE
        found, total = self.jobs.search(q, statuses, PAGE_SIZE, offset)
        pages = pager("/queue", {"q": q, "status": status}, page, total)
        if total and pages["page"] != page:  # página além do fim (a fila encolheu): a última
            found, _ = self.jobs.search(q, statuses, PAGE_SIZE, (pages["page"] - 1) * PAGE_SIZE)
        options = [(k, label) for k, (label, _) in QUEUE_FILTERS.items()]
        return {"jobs": found, "pager": pages, "q": q, "status": status, "options": options}

    def queue_counts(self, q: str) -> dict[str, int]:
        """Quantas tarefas cada filtro da fila tem, para a busca dada."""
        by_status = self.jobs.counts(q)
        out = {"all": sum(by_status.values())}
        for key, (_, statuses) in QUEUE_FILTERS.items():
            if key:
                out[key] = sum(by_status.get(s, 0) for s in statuses)
        return out

    def running_job(self) -> Job | None:
        found, _ = self.jobs.search(statuses=("processando",), limit=1)
        return found[0] if found else None

    def upcoming(self, limit: int) -> list[Job]:
        return self.jobs.upcoming(limit)

    def recent(self, limit: int) -> list[Job]:
        return self.jobs.recent(limit)

    def queue_positions(self) -> dict[int, int]:
        """id da tarefa que espera → o lugar dela na fila (1 é a próxima a rodar)."""
        return {j.id: n for n, j in enumerate(self.jobs.upcoming(2000), start=1)}

    def items_for(self, jobs: Iterable[Job]) -> dict[str, LibraryItem]:
        """vídeo → o filme ou episódio dele, para a fila mostrar título em vez de arquivo."""
        return self.lib.items_for(j.video for j in jobs)

    def cancel_queued(self) -> Outcome:
        n = self.jobs.cancel_queued()
        return say("queue.cancelled", n=n) if n else say("queue.none_waiting")

    def retry_failed(self) -> Outcome:
        n = self.jobs.retry_failed()
        return say("queue.retried", n=n) if n else say("queue.none_failed")

    def move_to_top(self, job_id: int) -> dict[str, Any]:
        self.job(job_id)
        self.jobs.move_to_top(job_id)
        return self.job_detail(job_id)

    def job_extra(self, job: Job) -> dict[str, Any]:
        """O que a tarefa está fazendo agora e os passos da pipeline já registrados no vídeo."""
        stage_name = (job.etapa or None) if job.status == "processando" else None
        stage = STEP_LABELS.get(stage_name or "")
        steps = self.catalog.steps(job.media_id) if job.media_id is not None else {}
        order = list(ACTION_LABELS)
        done = sorted(steps.values(), key=lambda r: (
            order.index(r.action) if r.action in order else len(order), r.lang))  # fmt: skip
        pipeline = [{"action": r.action, "label": ACTION_LABELS.get(r.action, r.action),
                     "language": r.lang or None, "state": r.state, "detail": r.detail,
                     "attempts": r.attempts, "updated_at": r.updated_at} for r in done]  # fmt: skip
        return {"stage": stage, "stage_name": stage_name, "pipeline": pipeline}

    def job(self, job_id: int) -> Job:
        try:
            return self.jobs.get(job_id)
        except KeyError:
            raise NotFound("task not found") from None

    def job_detail(self, job_id: int) -> dict[str, Any]:
        job = self.job(job_id)
        return {"job": job, "steps": job_steps(job, self.planned)}

    def cancel(self, job_id: int) -> dict[str, Any]:
        self.jobs.cancel(job_id)
        return self.job_detail(job_id)

    def retry(self, job_id: int) -> dict[str, Any]:
        self.jobs.retry(job_id)
        return self.job_detail(job_id)

    def subtitle_file(self, job_id: int) -> Path:
        job = self.job(job_id)
        if not job.legenda or not Path(job.legenda).exists():
            raise NotFound("subtitle not available yet")
        return Path(job.legenda)

    def report_text(self, job_id: int) -> str:
        job = self.job(job_id)
        if not job.relatorio or not Path(job.relatorio).exists():
            raise NotFound("report not available yet")
        return Path(job.relatorio).read_text(encoding="utf-8")

    # --- biblioteca ---

    def item_rows(self, items: list[Any]) -> list[dict[str, Any]]:
        """Por item: as legendas que importam (a do idioma original e as dos idiomas alvo, cada
        uma com a origem: embutida, de outra fonte, nossa) e os idiomas alvo que faltam. Um
        vídeo traz dezenas de faixas em idiomas que ninguém pediu: essas ficam de fora.
        Só lê o banco (o sync e a pipeline gravam o que acham no disco): com biblioteca
        grande, abrir a página não pode depender de listar pastas nem de acordar o NAS."""
        active = self.active_jobs()
        videos = [i.video for i in items]
        subs = self.lib.subtitle_files(None if len(videos) > 500 else videos)
        targets = self.lib.config().target_languages
        originals = self.catalog.original_languages()
        out = []
        for item in items:
            present: dict[str, str] = {}
            files = sorted(subs.get(item.video, []), key=lambda f: f.kind != "embedded")
            for f in files:
                if not f.lang or f.forced:
                    continue
                if f.kind == "embedded":
                    present.setdefault(f.lang, "embedded")
                elif f.kind == "human" or f.lang not in present:
                    present[f.lang] = f.kind
            missing = [t for t in targets if not any(same_lang(t, lang) for lang in present)]
            original = originals.get(item.video)
            shown = [(lang, kind, same_base(original, lang)) for lang, kind in present.items()]
            shown = [s for s in shown if s[2] or any(same_lang(t, s[0]) for t in targets)]
            shown.sort(key=lambda s: (not s[2], s[0]))  # a original primeiro
            out.append({"item": item, "name": Path(item.video).name,
                        "subs": [(lang, kind) for lang, kind, _ in shown],
                        "others": len(present) - len(shown), "missing": missing,
                        "job": active.get(item.video)})  # fmt: skip
        return out

    def job_refs(self) -> dict[str, Job]:
        """vídeo → a tarefa ativa dele (a que está rodando ganha da que espera na fila)."""
        out: dict[str, Job] = {}
        for j in self.jobs.list(2000):
            if j.status in ACTIVE and (j.status == "processando" or j.video not in out):
                out[j.video] = j
        return out

    def active_jobs(self) -> dict[str, str]:
        return {video: j.step_label if j.status == "processando" else f"Queued: {j.step_label}"
                for video, j in self.job_refs().items()}  # fmt: skip

    @staticmethod
    def _page(path: str, found: list[dict[str, Any]], q: str, show: str, page: int,
              flags: Callable[[dict[str, Any]], tuple[bool, bool]],
              sort: str = "title") -> dict[str, Any]:  # fmt: skip
        """`found` (o que casa com a busca) filtrado pelo chip e cortado na página; e quantos
        itens cada chip tem. `flags` diz, de cada linha, se falta legenda e se está na fila.
        `sort`: `title` (como veio), `added` (mais novos primeiro) ou `missing` (o que falta
        legenda primeiro)."""
        if sort == "added":
            found = sorted(found, key=lambda r: r["item"].added_at, reverse=True)
        elif sort == "missing":
            found = sorted(found, key=lambda r: not flags(r)[0])
        marks = [flags(r) for r in found]
        rows = [r for r, (missing, queued) in zip(found, marks, strict=True)
                if _shown(show, missing, queued)]  # fmt: skip
        pages = pager(path, {"q": q, "show": show}, page, len(rows))
        start = (pages["page"] - 1) * PAGE_SIZE
        counts = {"all": len(found), "missing": sum(1 for m, _ in marks if m),
                  "complete": sum(1 for m, _ in marks if not m),
                  "queued": sum(1 for _, queued in marks if queued)}  # fmt: skip
        shown = rows[start : start + PAGE_SIZE]
        return {"rows": shown, "pager": pages, "counts": counts}

    def movies_data(self, q: str, show: str, page: int, sort: str = "title") -> dict[str, Any]:
        every = self.item_rows(self.lib.movies())
        found = [r for r in every
                 if matches(q, r["item"].title, r["item"].year, r["item"].folder)]  # fmt: skip
        return {"has_items": bool(every),
                **self._page("/movies", found, q, show, page,
                             lambda r: (bool(r["missing"]), bool(r["job"])), sort)}  # fmt: skip

    def shows_data(self, q: str, show: str, page: int, sort: str = "title") -> dict[str, Any]:
        by_show: dict[int, list[dict[str, Any]]] = {}
        for ep in self.item_rows(self.lib.episodes()):
            by_show.setdefault(ep["item"].series_id, []).append(ep)
        every: list[dict[str, Any]] = []
        for series in self.lib.series_list():
            eps = by_show.get(series.id, [])
            done = sum(1 for e in eps if not e["missing"])
            # "faltando" para o botão: sem alguma legenda e sem passo na fila
            missing = sum(1 for e in eps if e["missing"] and not e["job"])
            queued = any(e["job"] for e in eps)
            every.append({"item": series, "total": len(eps), "done": done, "missing": missing,
                          "queued": queued})  # fmt: skip
        found = [r for r in every
                 if matches(q, r["item"].title, r["item"].year, r["item"].folder)]  # fmt: skip
        return {"has_items": bool(every),
                **self._page("/tv", found, q, show, page,
                             lambda r: (r["done"] < r["total"], r["queued"]), sort)}  # fmt: skip

    def show(self, series_id: int) -> dict[str, Any]:
        try:
            show = self.lib.series(series_id)
        except KeyError:
            raise NotFound("show not found") from None
        seasons: dict[int | None, list[dict[str, Any]]] = {}
        for ep in self.item_rows(self.lib.episodes(series_id)):
            seasons.setdefault(ep["item"].season, []).append(ep)
        ordered = sorted(seasons.items(), key=lambda kv: (kv[0] is None, kv[0] or 0))
        return {"show": show, "seasons": ordered}

    def search(self, q: str, limit: int = 8) -> dict[str, list[Any]]:
        """Busca do topo: filmes e séries pelo título ou ano. Sem texto, nada."""
        if not words(q):
            return {"movies": [], "shows": []}
        movies = [m for m in self.lib.movies() if matches(q, m.title, m.year)]
        shows = [s for s in self.lib.series_list() if matches(q, s.title, s.year)]
        return {"movies": movies[:limit], "shows": shows[:limit]}

    def poster_file(self, kind: str, item_id: int) -> Path:
        """O pôster do filme ou da série, baixado na primeira vez e guardado no cache."""
        if kind not in ("movie", "series"):
            raise NotFound("Not Found")
        try:
            item = self.lib.movie(item_id) if kind == "movie" else self.lib.series(item_id)
        except KeyError:
            raise NotFound("item not found") from None
        if not item.poster:
            raise NotFound("no poster")
        url = poster_source(item.poster)
        suffix = Path(urlsplit(url).path).suffix.lower()
        name = hashlib.sha256(url.encode()).hexdigest()[:24]
        target = self.settings.cache_dir / "posters" / (
            name + (suffix if suffix in POSTER_TYPES else ".jpg"))  # fmt: skip
        if target.exists():
            return target
        if time.monotonic() - self._poster_misses.get(url, -POSTER_RETRY_S) < POSTER_RETRY_S:
            raise NotFound("poster not available")
        data = self.clients.fetch_poster("radarr" if kind == "movie" else "sonarr", url)
        if not data:
            self._poster_misses[url] = time.monotonic()
            raise NotFound("poster not available")
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(f"{target.name}.{uuid.uuid4().hex}.part")
        partial.write_bytes(data)
        partial.replace(target)
        return target

    def set_monitored(self, kind: str, item_id: int, monitored: bool | None = None) -> Any:
        """Marca ou desmarca o filme ou a série (`None` alterna) e devolve o item como ficou."""
        if kind not in ("movie", "series"):
            raise NotFound("Not Found")
        try:
            item = self.lib.movie(item_id) if kind == "movie" else self.lib.series(item_id)
        except KeyError:
            raise NotFound("item not found") from None
        self.lib.set_monitored(kind, item_id, not item.monitored if monitored is None
                               else monitored)  # fmt: skip
        return self.lib.movie(item_id) if kind == "movie" else self.lib.series(item_id)

    def _videos_for(self, key: str) -> list[str]:
        """`movie:1`, `episode:7` ou `series:2` (todos os episódios: o decisor vê o que falta)."""
        kind, _, raw = key.partition(":")
        try:
            item_id = int(raw)
            if kind == "movie":
                return [self.lib.movie(item_id).video]
            if kind == "episode":
                return [self.lib.episode(item_id).video]
            if kind == "series":
                return [e.video for e in self.lib.episodes(item_id)]
        except (KeyError, ValueError):
            pass
        raise NotFound(f"unknown item: {key}")

    def _process(self, videos: list[str]) -> tuple[int, int, int]:
        """Pedido manual: decide na hora, com prioridade e nova tentativa do que falhou.
        Devolve (passos novos, itens na fila, itens que falharam)."""
        steps = items = failed = 0
        for video in videos:
            d = reconcile(self.pipeline, video, "manual", retry_failed=True)
            if d is not None and "sondar" in d.blocked:
                failed += 1
            elif d is not None and (d.actions or self.jobs.active_steps_for(video)):
                items += 1
                steps += d.queued
        return steps, items, failed

    def add_to_queue(self, keys: list[str]) -> Outcome:
        if not keys:
            return say("library.select_one", error=True)
        videos = [v for key in keys for v in self._videos_for(key)]
        steps, items, failed = self._process(videos)
        if failed:
            return say("library.unreadable", error=True, n=failed)
        if not items:
            return say("library.covered")
        return say("library.queued", n=items, steps=steps)

    # --- settings ---

    def interfaces(self, config: AppConfig) -> list[dict[str, Any]]:
        # aqui é só o motor local; o servidor remoto se escolhe em Integrations
        local = config.model_copy(update={"asr_mode": "local"})
        chosen = components_for(self.settings, local)
        remote = set(REMOTE_IDS.values())
        out = []
        for iface in ENGINE_INTERFACES:
            label = INTERFACE_LABELS[iface]
            if iface not in self.factories:
                continue
            providers = [p for p in self.factories[iface].providers()
                         if p.id != "falso" and p.id not in remote]  # fmt: skip
            selected = chosen[iface].provider if iface in chosen else ""
            out.append({"id": iface, "label": label, "providers": providers,
                        "selected": selected})  # fmt: skip
        return out

    def settings_view(self, page: str, tab: str = "radarr") -> dict[str, Any]:
        if page not in dict(SETTINGS_PAGES):
            raise NotFound("Not Found")
        config = self.lib.config()
        stored = self.lib.secrets()
        env = self.clients.env_secrets()
        secret_state = {n: ("settings" if n in stored else "environment" if n in env else "")
                        for n in INTEGRATION_SECRETS}  # fmt: skip
        secret_values = {n: stored.get(n) or env.get(n, "") for n in INTEGRATION_SECRETS}
        llm_env = load_env(LLM_ENV)
        return {
            "page": page, "config": config, "interfaces": self.interfaces(config),
            "themes": self.themes, "series_types": SERIES_TYPES, "arr_keys": ARR_KEY_ENV,
            "tab": tab if tab in ARR_KEY_ENV else "radarr",
            "llm_providers": LLM_PROVIDERS, "llm_keys": LLM_KEY_ENV, "llm_sites": LLM_SITES,
            "llm_effort_flavors": LLM_EFFORT_FLAVORS,
            "tmdb_builtin": bool(tmdb_meta.BUILTIN_READ_TOKEN),
            "tmdb_attribution": tmdb_meta.ATTRIBUTION,
            "llm_defaults": {v: llm_defaults(v) for v in LLM_PRESETS},
            "llm_url_from_env": not config.llm_base_url and "C3PARR_LLM_BASE_URL" in llm_env,
            "llm_model_from_env": (not config.llm_model and config.llm_provider == "custom"
                                   and "C3PARR_LLM_MODEL" in llm_env),
            "secret_state": secret_state, "secret_values": secret_values,
        }  # fmt: skip

    # Os botões "Test" usam o que está na tela, mesmo antes de salvar (como nos *arr): o que
    # vier vazio cai no que está salvo.

    def test_arr(self, kind: ArrKind, arr: ArrConfig, api_key: str) -> Outcome:
        key = api_key or self.effective_secrets().get(ARR_KEY_ENV[kind], "")
        try:
            client = self.clients.arr(kind, arr, key)
            try:
                version = client.version()
            finally:
                client.close()
        except ProviderError as e:
            return Outcome(e.message, error=True)
        latest = self.clients.latest_major(kind)
        found = major(version)
        if found and found < latest:
            return say("test.arr_outdated", name=kind.capitalize(), version=version, latest=latest)
        return say("test.arr_connected", name=kind.capitalize(), version=version)

    def test_tmdb(self, token: str) -> Outcome:
        secrets = self.effective_secrets()
        token = (token or secrets.get("TMDB_READ_ACCESS_TOKEN", "")
                 or tmdb_meta.BUILTIN_READ_TOKEN)  # fmt: skip
        if not token:
            return say("test.tmdb_no_token", error=True)
        health = self.clients.tmdb(TMDbConfig(token=token, timeout_s=15)).health()
        return self._tested("TMDb", health.ok, health.detail)

    def test_llm(self, provider: str, base_url: str, model: str, effort: str,
                 api_key: str) -> Outcome:  # fmt: skip
        config = self.lib.config()
        flavor = provider or config.llm_provider
        if flavor not in LLM_PRESETS:
            return say("test.llm_unknown", error=True, provider=flavor)
        url, default_model = llm_defaults(flavor)
        key = api_key or self.effective_secrets().get(LLM_KEY_ENV[flavor])
        llm = LLMConfig(
            flavor=flavor,
            base_url=(base_url or url) if flavor == "custom" else url,
            model=model or default_model,
            api_key=key or None,
            effort=(effort or config.llm_effort) if flavor in LLM_EFFORT_FLAVORS else None,
            timeout_s=60,  # o servidor local pode estar acordando (wake-on-LAN)
        )
        try:
            client = self.clients.llm(llm)
        except ProviderError as e:
            return say("test.error", error=True, name="LLM", detail=e.message)
        ok, detail = client.health()
        return self._tested("LLM", ok, detail)

    def test_asr(self, mode: str, url: str, model: str, api_key: str) -> Outcome:
        config = self.lib.config()
        secrets = self.effective_secrets()
        mode = mode or config.asr_mode
        local_engine = ""
        if mode in REMOTE_IDS:
            key = api_key or secrets.get("C3PARR_ASR_API_KEY")
            remote = self.clients.remote_asr(
                RemoteASRConfig(base_url=url or config.asr_url,
                                model=model or config.asr_model or "whisper-1",
                                api_key=key or None),
                "openai" if mode == "openai" else "asr-webservice",
            )  # fmt: skip
            health = remote.health()
        else:
            local = components_for(self.settings, config.model_copy(update={"asr_mode": "local"}))
            try:
                engine = self.factories["transcricao"].create(local["transcricao"], secrets)
            except ProviderError as e:
                return say("test.asr_error", error=True, detail=e.message)
            health = engine.health()
            local_engine = engine.info.id
        if not health.ok:
            return say("test.asr_error", error=True, detail=health.detail)
        extra = f" {health.detail}" if health.detail else ""
        if local_engine:
            return say("test.asr_local", engine=local_engine, extra=extra)
        return say("test.asr_remote", extra=extra)

    @staticmethod
    def _tested(name: str, ok: bool, detail: str) -> Outcome:
        if ok:
            return say("test.connected", name=name)
        return say("test.error", error=True, name=name, detail=detail)

    def _save(self, changes: dict[str, Any], secrets: Mapping[str, str]) -> Outcome:
        """Grava as chaves que vieram e a configuração. Config inválida não é gravada."""
        env = self.clients.env_secrets()
        for name in INTEGRATION_SECRETS:
            if name not in secrets:
                continue
            value = secrets[name].strip()
            # vazio apaga o salvo; igual ao .env não duplica: o .env continua sendo a fonte
            self.lib.set_secret(name, value if value and value != env.get(name) else None)
        try:
            self.lib.update_config(**changes)
            self._host = None
            self.refresh_secrets()
        except ValidationError as e:
            fields = ", ".join(str(err["loc"][0]) for err in e.errors())
            return say("settings.invalid", error=True, status=422, fields=fields)
        return say("settings.saved")

    # A interface manda só o que mudou. As regras: o Radarr/Sonarr que veio é limpo e, se ligado,
    # relido; idiomas sem repetição; motor só entre os oferecidos; modelo e URL iguais ao padrão
    # ficam vazios; servidor de transcrição exige URL.

    def _arr_with(self, kind: str, given: Mapping[str, Any]) -> ArrConfig:
        """O Radarr/Sonarr salvo com os campos que vieram por cima, limpos."""
        data = {**getattr(self.lib.config(), kind).model_dump(), **given}
        for name in ("host", "base_url"):
            data[name] = str(data[name] or "").strip()
        data["excluded_tags"] = tuple(str(t).strip() for t in data["excluded_tags"]
                                      if str(t).strip())  # fmt: skip
        data["excluded_series_types"] = tuple(t for t in data["excluded_series_types"]
                                              if t in SERIES_TYPES)  # fmt: skip
        pairs = [(str(r).strip(), str(lo).strip()) for r, lo in data["path_mappings"]]
        data["path_mappings"] = tuple((r, lo) for r, lo in pairs if r and lo)
        return ArrConfig.model_validate(data)

    def patch_settings(self, changes: Mapping[str, Any],
                       secrets: Mapping[str, str | None]) -> Outcome:  # fmt: skip
        """Grava só os campos que vieram (nomes da `AppConfig`) e as chaves (vazio apaga)."""
        config = self.lib.config()
        unknown = changes.keys() - PATCHABLE
        if unknown:
            fields = ", ".join(sorted(unknown))
            return say("settings.unknown", error=True, status=422, fields=fields)
        out: dict[str, Any] = {}
        arrs: dict[str, ArrConfig] = {}
        for kind in ("radarr", "sonarr"):
            if kind in changes:
                try:
                    arrs[kind] = self._arr_with(kind, changes[kind])
                except (ValidationError, TypeError, ValueError):
                    return say("settings.invalid", error=True, status=422, fields=kind)
        if "target_languages" in changes:
            targets = list(dict.fromkeys(norm_lang(str(t)) for t in changes["target_languages"]))
            if not targets:
                return say("settings.no_target", error=True, status=422)
            out["target_languages"] = targets
        if "fallback_languages" in changes:
            given = (str(c).strip() for c in changes["fallback_languages"])
            out["fallback_languages"] = list(dict.fromkeys(norm_lang(c) for c in given if c))
        if "components" in changes:
            valid = {i["id"]: {p.id for p in i["providers"]} for i in self.interfaces(config)}
            picked = {k: v for k, v in dict(changes["components"]).items()
                      if v in valid.get(k, ())}  # fmt: skip
            out["components"] = {**config.components, **picked}
        provider = changes.get("llm_provider", config.llm_provider)
        if provider not in LLM_PRESETS:
            provider = config.llm_provider
        if "llm_provider" in changes:
            out["llm_provider"] = provider
        if changes.get("llm_effort") in ("low", "medium"):
            out["llm_effort"] = changes["llm_effort"]
        if "llm_model" in changes:
            model = str(changes["llm_model"] or "").strip()
            out["llm_model"] = "" if model == llm_defaults(provider)[1] else model
        if "llm_base_url" in changes:
            url = str(changes["llm_base_url"] or "").strip()
            out["llm_base_url"] = "" if url == llm_defaults("custom")[0] else url
        if changes.keys() & {"asr_mode", "asr_url", "asr_model"}:
            mode = changes.get("asr_mode", config.asr_mode)
            url = str(changes.get("asr_url", config.asr_url) or "").strip()
            if mode in REMOTE_IDS and not url:
                return say("settings.asr_url", error=True, status=422)
            out["asr_mode"], out["asr_url"] = mode, url
            out["asr_model"] = str(changes.get("asr_model", config.asr_model) or "").strip()
        if "url_base" in changes:
            base = clean_url_base(str(changes["url_base"] or ""))
            if base is None:
                return say("settings.url_base", error=True, status=422)
            out["url_base"] = base
        if "instance_name" in changes:
            out["instance_name"] = str(changes["instance_name"] or "").strip()[:60] or DEFAULT_NAME
        if "allowed_hosts" in changes:
            hosts = (str(h).strip().lower() for h in changes["allowed_hosts"])
            out["allowed_hosts"] = list(dict.fromkeys(h for h in hosts if h))
        if "concert_paths" in changes:
            folders = (str(f).strip().rstrip("/") or "/" for f in changes["concert_paths"]
                       if str(f).strip())  # fmt: skip
            out["concert_paths"] = list(dict.fromkeys(folders))
        out.update({name: changes[name] for name in PLAIN_SETTINGS if name in changes})
        outcome = self._save({**arrs, **out}, {k: v or "" for k, v in secrets.items()})
        if not outcome.error:
            touched = changes.keys() | secrets.keys()
            for target, inputs in TEST_INPUTS.items():
                if touched & inputs:
                    self.lib.set_meta(f"test_{target}", "")
            for kind, arr in arrs.items():
                if arr.enabled:
                    self.lib.request_run(f"sync_{kind}")
        return outcome

    def test_screen(self, target: str, changes: Mapping[str, Any],
                    secrets: Mapping[str, str | None]) -> Outcome:  # fmt: skip
        """Testa a conexão com o que a tela tem agora (o salvo, com o digitado por cima) e
        guarda o resultado, para a tela mostrar o último teste sem refazer."""
        started = time.perf_counter()
        outcome = self._test(target, changes, secrets)
        record = {"ok": not outcome.error, "message": outcome.message, "code": outcome.code,
                  "params": dict(outcome.params),
                  "at": datetime.now(UTC).isoformat(timespec="seconds"),
                  "ms": round((time.perf_counter() - started) * 1000)}  # fmt: skip
        self.lib.set_meta(f"test_{target}", json.dumps(record))
        return outcome

    def last_tests(self) -> dict[str, dict[str, Any]]:
        """Alvo → o último teste de conexão que ainda vale (ok, mensagem, quando, quanto levou)."""
        out = {}
        for target in TEST_INPUTS:
            raw = self.lib.meta(f"test_{target}")
            if raw:
                out[target] = dict(json.loads(raw))
        return out

    def _test(self, target: str, changes: Mapping[str, Any],
              secrets: Mapping[str, str | None]) -> Outcome:  # fmt: skip
        config = self.lib.config()
        typed = {k: (v or "").strip() for k, v in secrets.items()}

        def field(name: str) -> str:
            return str(changes.get(name, getattr(config, name)) or "").strip()

        if target in ARR_KEY_ENV:
            kind: ArrKind = "radarr" if target == "radarr" else "sonarr"
            try:
                arr = self._arr_with(kind, changes.get(kind, {}))
            except (ValidationError, TypeError, ValueError):
                return say("test.arr_fields", error=True, status=422, name=kind.capitalize())
            return self.test_arr(kind, arr, typed.get(ARR_KEY_ENV[kind], ""))
        if target == "tmdb":
            return self.test_tmdb(typed.get("TMDB_READ_ACCESS_TOKEN", ""))
        if target == "llm":
            provider = field("llm_provider")
            key = typed.get(LLM_KEY_ENV.get(provider, ""), "")
            return self.test_llm(provider, field("llm_base_url"), field("llm_model"),
                                 field("llm_effort"), key)  # fmt: skip
        if target == "asr":
            return self.test_asr(field("asr_mode"), field("asr_url"), field("asr_model"),
                                 typed.get("C3PARR_ASR_API_KEY", ""))  # fmt: skip
        raise NotFound("Not Found")

    # --- system ---

    def task_rows(self) -> list[dict[str, Any]]:
        config = self.lib.config()
        out = []
        for t in self.scheduler.tasks.values():
            state = self.lib.task(t.name)
            out.append({"name": t.name, "label": t.label,
                        "interval": interval_label(t.interval(config)),
                        "last_run": state.last_run, "next_run": state.next_run,
                        "result": state.last_result, "running": t.name in self.scheduler.running,
                        "requested": state.run_now})  # fmt: skip
        return out

    def run_task(self, name: str) -> Outcome:
        task = self.scheduler.tasks.get(name)
        if task is None:
            raise NotFound("unknown task")
        self.lib.request_run(name)
        return say("task.starting", task=task.label, task_key=f"routine.{task.name}")

    def alerts_data(self) -> dict[str, Any]:
        """Os alertas e o que falta configurar. Não marca nada como visto."""
        alerts = self.catalog.alerts()
        names: dict[str, list[str]] = {}
        for a in alerts:
            for mid in a.media_ids:
                with suppress(KeyError):
                    names.setdefault(a.key, []).append(Path(self.catalog.media(mid).video).name)
        return {"alerts": alerts, "names": names, "step_names": ACTION_LABELS,
                "setup": self.setup_needed()}  # fmt: skip

    def mark_alerts_seen(self) -> None:
        self.catalog.mark_alerts_seen()

    def retry_alert(self, key: str) -> Outcome:
        alert = next((a for a in self.catalog.alerts(include_resolved=False) if a.key == key), None)
        if alert is None:
            return say("alert.resolved")
        videos = []
        for mid in alert.media_ids:
            with suppress(KeyError):
                videos.append(self.catalog.media(mid).video)
        steps, _, failed = self._process(videos)
        if failed:
            return say("alert.still_failing", error=True, n=failed)
        return say("alert.retrying", n=steps)

    def log_records(self) -> list[dict[str, str]]:
        return list(reversed(self.logs.records))

    def provider_status(self) -> list[dict[str, Any]]:
        chosen = self.components()
        out = []
        for iface, label in INTERFACE_LABELS.items():
            spec = chosen.get(iface)
            if spec is None or iface not in self.factories:
                continue
            try:
                self.factories[iface].create(spec, self.secrets)
                ok, detail = True, ""
            except ProviderError as e:
                ok, detail = False, str(e)
            out.append({"id": iface, "label": label, "provider": spec.provider, "ok": ok,
                        "detail": detail})  # fmt: skip
        return out

    def status_view(self) -> dict[str, Any]:
        about = [
            ("version", "Version", __version__),
            ("python", "Python", platform.python_version()),
            ("database", "Database", str(self.lib.path)),
            ("queue", "Queue", str(self.jobs.path)),
            ("cache", "Cache", str(self.settings.cache_dir)),
            (
                "concert",
                "Concert models",
                concert_device.summary(self.settings.cache_dir / "models" / "torch"),
            ),
        ]
        stored = self.lib.secrets()
        env = self.clients.env_secrets()
        present = [(name, "settings" if name in stored else "environment" if name in env
                    else "") for name in SECRET_NAMES]  # fmt: skip
        about.append(("tmdb", "TMDb", tmdb_meta.ATTRIBUTION))
        return {"about": about, "providers": self.provider_status(), "secrets": present,
                "warnings": self.warnings()}  # fmt: skip
