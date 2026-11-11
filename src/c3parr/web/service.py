"""O que as telas mostram e o que as ações fazem, sem HTML nem HTTP.

As rotas HTML (`app.py`) e a API JSON usam o mesmo `Service`: a regra mora aqui uma vez só.
O que fala com o mundo de fora (Radarr/Sonarr, TMDb, LLM, servidor de transcrição) entra por
`Clients`, para os testes trocarem.
"""

import logging
import platform
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import urlencode

from pydantic import ValidationError

from c3parr import __version__
from c3parr.asr.remote import REMOTE_IDS, RemoteASRConfig
from c3parr.asr.whisper_cpp import measured_speed
from c3parr.core.config import LLM_ENV, SECRET_NAMES, Settings, load_env, load_secrets
from c3parr.core.errors import ProviderError
from c3parr.core.job import ComponentSpec
from c3parr.core.search import matches
from c3parr.jobs.queue import ACTION_LABELS, Job, JobQueue
from c3parr.library.arr import API_KEY_ENV as ARR_KEY_ENV
from c3parr.library.arr import SERIES_TYPES, ArrConfig, ArrKind, major
from c3parr.library.store import AppConfig, Library
from c3parr.library.tasks import Scheduler
from c3parr.llm.client import LLM_KEY_ENV, LLM_PRESETS, LLMConfig, llm_defaults
from c3parr.metadata import tmdb as tmdb_meta
from c3parr.metadata.tmdb import TMDbConfig
from c3parr.pipeline.catalog import Catalog
from c3parr.pipeline.naming import norm_lang, same_lang
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
# chips de Filmes e Séries: o que falta é algum idioma alvo
LIBRARY_FILTERS = {"": "All", "missing": "Missing subtitles", "complete": "Complete",
                   "queued": "In the queue"}  # fmt: skip
SUBTITLE_FLAGS = (
    "use_embedded", "embedded_resolves", "external_resolves", "forced_resolves",
    "sync_new_external", "fix_external_in_place", "delete_ours_on_human",
    "new_external_becomes_main", "asr_enabled", "asr_always",
)  # fmt: skip
SUBTITLE_CHOICES = ("derived_on_main_change", "foreign_tag")


class NotFound(Exception):
    """O item pedido não existe (vira 404 na rota)."""


class Outcome(NamedTuple):
    """Resultado de uma ação, em palavras para o usuário."""

    message: str
    error: bool = False
    status: int = 200


@dataclass(frozen=True)
class Clients:
    """Quem fala com o mundo de fora. As rotas passam os de verdade; os testes, os deles."""

    arr: Callable[[ArrKind, ArrConfig, str], Any]
    tmdb: Callable[[TMDbConfig], Any]
    llm: Callable[[LLMConfig], Any]
    remote_asr: Callable[[RemoteASRConfig, str], Any]
    env_secrets: Callable[[], dict[str, str]]
    latest_major: Callable[[ArrKind], int]


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


def interval_label(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes % 1440 == 0:
        return f"{minutes // 1440} d"
    if minutes % 60 == 0:
        return f"{minutes // 60} h"
    return f"{minutes} min"


def job_steps(job: Job, planned: list[str]) -> list[dict[str, str]]:
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
            out.append({"label": STEP_LABELS.get(s.name, s.name), "state": state, "text": text})
        return out
    current = planned.index(job.etapa) if job.etapa in planned else -1
    out = []
    for i, name in enumerate(planned):
        if job.status == "processando" and i < current:
            state, text = "ok", "done"
        elif job.status == "processando" and i == current:
            state, text = "running", job.mensagem or "running"
        else:
            state, text = "pending", "waiting"
        out.append({"label": STEP_LABELS.get(name, name), "state": state, "text": text})
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

    def setup_needed(self) -> list[dict[str, str]]:
        """O mínimo para funcionar e que ainda falta. Fica em System → Alerts e no contador até
        ser configurado: abrir a página não faz sumir."""
        cfg = self.lib.config()
        secrets = self.effective_secrets()
        out = []
        arrs = [k for k in ("radarr", "sonarr") if getattr(cfg, k).enabled]
        if not arrs:
            out.append({"title": "Connect Radarr or Sonarr",
                        "detail": "c3parr reads its library from them, like Bazarr does.",
                        "href": "/settings/library/radarr"})  # fmt: skip
        for kind in arrs:
            if not getattr(cfg, kind).host or ARR_KEY_ENV[kind] not in secrets:
                out.append({"title": f"{kind.capitalize()} needs its address and API key",
                            "detail": f"{kind.capitalize()} → Settings → General → API Key.",
                            "href": f"/settings/library/{kind}"})  # fmt: skip
        flavor = cfg.llm_provider if cfg.llm_provider in LLM_PRESETS else "custom"
        url, model = llm_defaults(flavor)
        if flavor == "custom" and not ((cfg.llm_base_url or url) and (cfg.llm_model or model)):
            out.append({"title": "No LLM configured",
                        "detail": "Translations need an LLM: pick a provider and its key, or "
                        "the URL and model of your own server.",
                        "href": "/settings/integrations"})  # fmt: skip
        elif flavor != "custom" and LLM_KEY_ENV[flavor] not in secrets:
            out.append({"title": f"No API key for the LLM ({flavor})",
                        "detail": "Translations need the key of the chosen provider.",
                        "href": "/settings/integrations"})  # fmt: skip
        if "TMDB_READ_ACCESS_TOKEN" not in secrets and not tmdb_meta.BUILTIN_READ_TOKEN:
            out.append({"title": "No TMDb key",
                        "detail": "Without metadata the original language and character names "
                        "are guessed.", "href": "/settings/integrations"})  # fmt: skip
        return out

    def warnings(self) -> list[dict[str, str]]:
        """Avisos de configuração (não são falhas: não repetem nem viram alerta)."""
        out: list[dict[str, str]] = []
        cfg = self.lib.config()
        for kind in ("radarr", "sonarr"):  # *arr fora da versão major mais nova: aviso fixo
            found = major(self.lib.meta(f"{kind}_version") or "")
            latest = int(self.lib.meta(f"{kind}_latest_major") or 0)
            if getattr(cfg, kind).enabled and found and latest and found < latest:
                name = kind.capitalize()
                out.append({"level": "warn", "title": f"{name} {found} is out of date",
                            "detail": f"The latest is {name} {latest}. c3parr is made for it; "
                            f"update {name} to keep the library in sync."})  # fmt: skip
        asr = components_for(self.settings, cfg).get("transcricao")
        if asr is not None and asr.provider in REMOTE_IDS.values():
            out.append({"level": "info", "title": "Transcription on a separate server",
                        "detail": f"{cfg.asr_url} ({asr.provider})."})  # fmt: skip
        elif asr is not None and asr.provider == "whisper-cpp":
            speed = measured_speed()
            if speed and str(speed.get("backend", "")).upper() == "CPU":
                minutes = float(speed["seconds_per_minute"])
                out.append({"level": "warn", "title": "Transcription runs on the CPU",
                            "detail": f"Measured here: about {minutes:.0f} min per hour of video."
                            " A GPU build of whisper.cpp (Metal, Vulkan, CUDA) is 5-8x faster."
                            })  # fmt: skip
            elif speed:
                minutes = float(speed["seconds_per_minute"])
                out.append({"level": "info", "title": f"Transcription on {speed['backend']}",
                            "detail": f"Measured here: about {minutes:.1f} min per hour of video."
                            })  # fmt: skip
        elif asr is not None and asr.provider == "faster-whisper":
            out.append({"level": "warn", "title": "Transcription runs on the CPU (faster-whisper)",
                        "detail": "About 10 min per hour of video. Install whisper.cpp "
                        "(brew install whisper.cpp) to use the GPU."})  # fmt: skip
        images = sum(1 for m in self.catalog.all_media() if m.probe
                     and any(not t.text_based for t in m.probe.subtitles))  # fmt: skip
        if images:
            out.append({"level": "info", "title": f"{images} item(s) have image subtitles",
                        "detail": "PGS/VobSub tracks need OCR and are ignored; text tracks and "
                        "the audio are used instead."})  # fmt: skip
        return out

    # --- dashboard ---

    def dashboard(self) -> dict[str, Any]:
        cfg = self.lib.config()
        movies = self.item_rows(self.lib.movies())
        episodes = self.item_rows(self.lib.episodes())
        shows = len(self.lib.series_list())
        cards = [
            {"label": "Movies", "href": "/movies", "total": len(movies),
             "done": sum(1 for r in movies if not r["missing"])},
            {"label": f"Episodes ({shows} shows)", "href": "/tv", "total": len(episodes),
             "done": sum(1 for r in episodes if not r["missing"])},
        ]  # fmt: skip
        steps = self.catalog.step_counts()
        activity = [
            ("Translations", steps.get(("traduzir", "ok"), 0)),
            ("Transcriptions (ASR)", steps.get(("asr", "ok"), 0)),
            ("Embedded subtitles extracted", steps.get(("extrair", "ok"), 0)),
            ("Subtitles synced", steps.get(("sincronizar", "ok"), 0)),
            ("Steps in the queue", self.jobs.pending_count()),
            (
                "Steps waiting after a failure",
                sum(n for (_, st), n in steps.items() if st == "falhou"),
            ),
        ]
        made: dict[str, int] = {}
        for (lang, _), n in self.catalog.output_counts().items():
            made[lang] = made.get(lang, 0) + n
        by_lang: dict[str, dict[str, Any]] = {}
        for r in movies + episodes:
            for lang, kind in r["subs"]:
                row = by_lang.setdefault(lang, {"lang": lang, "embedded": 0, "human": 0, "ours": 0,
                                                "missing": 0, "target": False})  # fmt: skip
                row[kind] += 1
            for lang in r["missing"]:
                row = by_lang.setdefault(lang, {"lang": lang, "embedded": 0, "human": 0, "ours": 0,
                                                "missing": 0, "target": True})  # fmt: skip
                row["missing"] += 1
        for lang in cfg.target_languages:
            if lang in by_lang:
                by_lang[lang]["target"] = True
        languages = sorted(by_lang.values(), key=lambda r: (not r["target"], -(
            r["embedded"] + r["human"] + r["ours"]), r["lang"]))  # fmt: skip
        return {"library_cards": cards, "activity": activity, "made": sorted(made.items()),
                "lang_rows": languages,
                "alerts_open": len(self.catalog.alerts(include_resolved=False)),
                "warnings": [w for w in self.warnings() if w["level"] == "warn"]}  # fmt: skip

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

    def target_language(self) -> str:
        return self.lib.config().target_language

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
        """Por item: legendas por idioma (embutida, de outra fonte, nossa) e os idiomas alvo que
        faltam. Só lê o banco (o sync e a pipeline gravam o que acham no disco): com biblioteca
        grande, abrir a página não pode depender de listar pastas nem de acordar o NAS."""
        active = self.active_jobs()
        videos = [i.video for i in items]
        subs = self.lib.subtitle_files(None if len(videos) > 500 else videos)
        targets = self.lib.config().target_languages
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
            out.append({"item": item, "name": Path(item.video).name,
                        "subs": sorted(present.items()), "missing": missing,
                        "job": active.get(item.video)})  # fmt: skip
        return out

    def active_jobs(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for j in self.jobs.list(2000):
            if j.status in ACTIVE:
                running = j.status == "processando"
                if running or j.video not in out:
                    out[j.video] = j.step_label if running else f"Queued: {j.step_label}"
        return out

    @staticmethod
    def _library(path: str, rows: list[dict[str, Any]], q: str, show: str,
                 page: int) -> dict[str, Any]:  # fmt: skip
        pages = pager(path, {"q": q, "show": show}, page, len(rows))
        start = (pages["page"] - 1) * PAGE_SIZE
        return {"rows": rows[start:start + PAGE_SIZE], "pager": pages, "q": q, "show": show,
                "options": list(LIBRARY_FILTERS.items())}  # fmt: skip

    def movies(self, q: str, show: str, page: int) -> dict[str, Any]:
        every = self.item_rows(self.lib.movies())
        rows = [r for r in every
                if matches(q, r["item"].title, r["item"].year, r["item"].folder)
                and _shown(show, bool(r["missing"]), bool(r["job"]))]  # fmt: skip
        return {"has_items": bool(every), **self._library("/movies", rows, q, show, page)}

    def shows(self, q: str, show: str, page: int) -> dict[str, Any]:
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
        rows = [r for r in every if matches(q, r["item"].title, r["item"].year, r["item"].folder)
                and _shown(show, r["done"] < r["total"], r["queued"])]  # fmt: skip
        return {"has_items": bool(every), **self._library("/tv", rows, q, show, page)}

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

    def toggle_monitor(self, kind: str, item_id: int) -> Any:
        if kind not in ("movie", "series"):
            raise NotFound("Not Found")
        try:
            item = self.lib.movie(item_id) if kind == "movie" else self.lib.series(item_id)
        except KeyError:
            raise NotFound("item not found") from None
        self.lib.set_monitored(kind, item_id, not item.monitored)
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
            return Outcome("Select at least one item.", error=True)
        videos = [v for key in keys for v in self._videos_for(key)]
        steps, items, failed = self._process(videos)
        if failed:
            return Outcome(f"{failed} item(s) could not be read (see System → Alerts).", error=True)
        if not items:
            return Outcome("Nothing to do: every target language is covered.")
        return Outcome(f"{items} item(s) in the queue ({steps} new step(s)).")

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
        note = f" Update it: the latest is {latest}." if found and found < latest else ""
        return Outcome(f"{kind.capitalize()} {version}: connected.{note}")

    def test_tmdb(self, token: str) -> Outcome:
        secrets = self.effective_secrets()
        token = (token or secrets.get("TMDB_READ_ACCESS_TOKEN", "")
                 or tmdb_meta.BUILTIN_READ_TOKEN)  # fmt: skip
        if not token:
            return Outcome("TMDb: no read access token.", error=True)
        health = self.clients.tmdb(TMDbConfig(token=token, timeout_s=15)).health()
        return self._tested("TMDb", health.ok, health.detail)

    def test_llm(self, provider: str, base_url: str, model: str, effort: str,
                 api_key: str) -> Outcome:  # fmt: skip
        config = self.lib.config()
        flavor = provider or config.llm_provider
        if flavor not in LLM_PRESETS:
            return Outcome(f"LLM: unknown provider {flavor}.", error=True)
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
            return Outcome(f"LLM: {e.message}", error=True)
        ok, detail = client.health()
        return self._tested("LLM", ok, detail)

    def test_asr(self, mode: str, url: str, model: str, api_key: str) -> Outcome:
        config = self.lib.config()
        secrets = self.effective_secrets()
        mode = mode or config.asr_mode
        done = "connected."
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
                return Outcome(f"Transcription: {e.message}", error=True)
            health = engine.health()
            done = f"{engine.info.id} ready on this machine."
        if health.ok:
            return Outcome(
                f"Transcription: {done}" + (f" {health.detail}" if health.detail else "")
            )
        return Outcome(f"Transcription: {health.detail}", error=True)

    @staticmethod
    def _tested(name: str, ok: bool, detail: str) -> Outcome:
        return Outcome(f"{name}: connected.") if ok else Outcome(f"{name}: {detail}", error=True)

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
            self.refresh_secrets()
        except ValidationError as e:
            fields = ", ".join(str(err["loc"][0]) for err in e.errors())
            return Outcome(f"Not saved, check: {fields}", error=True, status=422)
        return Outcome("Saved.")

    def save_library(self, arrs: Mapping[str, ArrConfig], shared: Mapping[str, Any],
                     secrets: Mapping[str, str]) -> Outcome:  # fmt: skip
        """`arrs`: só o Radarr ou o Sonarr que a tela mandou. `shared`: monitor_new e
        sync_interval_minutes, quando vieram."""
        outcome = self._save({**arrs, **shared}, secrets)
        if not outcome.error:  # como o Bazarr: gravou o endereço, já lê a biblioteca de novo
            for kind, arr in arrs.items():
                if arr.enabled:
                    self.lib.request_run(f"sync_{kind}")
        return outcome

    def save_languages(self, targets: Iterable[str], fallback: Iterable[str],
                       secrets: Mapping[str, str]) -> Outcome:  # fmt: skip
        chosen = list(dict.fromkeys(norm_lang(t) for t in targets))
        if not chosen:
            return Outcome("Pick at least one target language.", error=True, status=422)
        order = list(dict.fromkeys(norm_lang(c) for c in fallback if c))
        return self._save({"target_languages": chosen, "fallback_languages": order}, secrets)

    def save_subtitles(self, values: Mapping[str, Any], secrets: Mapping[str, str]) -> Outcome:
        """`values`: os liga/desliga presentes estão ligados; escolhas e minutos como vieram."""
        changes: dict[str, Any] = {
            "wait_minutes": values.get("wait_minutes"),
            "search_interval_minutes": values.get("search_interval_minutes"),
        }
        for flag in SUBTITLE_FLAGS:
            changes[flag] = bool(values.get(flag))
        for choice in SUBTITLE_CHOICES:
            if choice in values:
                changes[choice] = values[choice]
        return self._save(changes, secrets)

    def save_engines(self, choices: Mapping[str, str], secrets: Mapping[str, str]) -> Outcome:
        config = self.lib.config()
        valid = {i["id"]: {p.id for p in i["providers"]} for i in self.interfaces(config)}
        picked = {k: v for k, v in choices.items() if v in valid.get(k, ())}
        return self._save({"components": {**config.components, **picked}}, secrets)

    def save_integrations(self, values: Mapping[str, str],
                          secrets: Mapping[str, str]) -> Outcome:  # fmt: skip
        config = self.lib.config()
        changes: dict[str, Any] = {}
        provider = values.get("llm_provider")
        if provider not in LLM_PRESETS:
            provider = config.llm_provider
        changes["llm_provider"] = provider
        if values.get("llm_effort") in ("low", "medium"):
            changes["llm_effort"] = values["llm_effort"]
        # igual ao padrão do provedor grava vazio: segue o padrão se ele mudar numa versão nova
        model = values.get("llm_model", "").strip()
        changes["llm_model"] = "" if model == llm_defaults(provider)[1] else model
        url = values.get("llm_base_url", "").strip()
        changes["llm_base_url"] = "" if url == llm_defaults("custom")[0] else url
        mode = values.get("asr_mode", config.asr_mode)
        if mode in REMOTE_IDS and not values.get("asr_url", "").strip():
            return Outcome("Transcription: put the server URL.", error=True, status=422)
        changes["asr_mode"] = mode
        changes["asr_url"] = values.get("asr_url", "").strip()
        changes["asr_model"] = values.get("asr_model", "").strip()
        return self._save(changes, secrets)

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
        return Outcome(f"{task.label}: starting.")

    def alerts_view(self) -> dict[str, Any]:
        """Os alertas como estavam antes de abrir a página; abrir marca os abertos como vistos
        (os de configuração só somem configurando)."""
        alerts = self.catalog.alerts()
        names: dict[str, list[str]] = {}
        for a in alerts:
            for mid in a.media_ids:
                with suppress(KeyError):
                    names.setdefault(a.key, []).append(Path(self.catalog.media(mid).video).name)
        setup = self.setup_needed()
        self.catalog.mark_alerts_seen()
        return {"alerts": alerts, "names": names, "step_names": ACTION_LABELS, "setup": setup,
                "alerts_unseen": len(setup)}  # fmt: skip

    def retry_alert(self, key: str) -> Outcome:
        alert = next((a for a in self.catalog.alerts(include_resolved=False) if a.key == key), None)
        if alert is None:
            return Outcome("Alert already resolved.")
        videos = []
        for mid in alert.media_ids:
            with suppress(KeyError):
                videos.append(self.catalog.media(mid).video)
        steps, _, failed = self._process(videos)
        if failed:
            return Outcome(f"Still failing for {failed} item(s).", error=True)
        return Outcome(f"Retrying: {steps} step(s) queued.")

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
            out.append({"label": label, "provider": spec.provider, "ok": ok, "detail": detail})
        return out

    def status_view(self) -> dict[str, Any]:
        about = [
            ("Version", __version__),
            ("Python", platform.python_version()),
            ("Database", str(self.lib.path)),
            ("Queue", str(self.jobs.path)),
            ("Cache", str(self.settings.cache_dir)),
        ]
        stored = self.lib.secrets()
        env = self.clients.env_secrets()
        present = [(name, "Settings" if name in stored else ".env / environment" if name in env
                    else "") for name in SECRET_NAMES]  # fmt: skip
        about.append(("TMDb", tmdb_meta.ATTRIBUTION))
        return {"about": about, "providers": self.provider_status(), "secrets": present,
                "warnings": self.warnings()}  # fmt: skip
