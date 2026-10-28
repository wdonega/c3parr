"""Clientes do Radarr e do Sonarr (API v3, a mesma no Radarr 6 e no Sonarr 4).

O c3parr lê a biblioteca deles, como o Bazarr: eles já arrumam os metadados e os nomes dos
arquivos. Só GET; nada é alterado no Radarr/Sonarr.
"""

import logging
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import Field

from c3parr.core.errors import TransientFailure, Unavailable
from c3parr.core.models import Model

log = logging.getLogger("c3parr.arr")
ArrKind = Literal["radarr", "sonarr"]
API_KEY_ENV: dict[str, str] = {"radarr": "RADARR_API_KEY", "sonarr": "SONARR_API_KEY"}
# versão major mais nova quando não dá para perguntar ao GitHub (offline)
LATEST_MAJOR_FALLBACK: dict[str, int] = {"radarr": 6, "sonarr": 4}
RELEASES = {
    "radarr": "https://api.github.com/repos/Radarr/Radarr/releases/latest",
    "sonarr": "https://api.github.com/repos/Sonarr/Sonarr/releases/latest",
}
SERIES_TYPES = ("standard", "daily", "anime")


class ArrConfig(Model):
    """Endereço e opções de um Radarr ou Sonarr (Settings → Library)."""

    enabled: bool = False
    host: str = ""
    """Nome ou IP (sonarr, 192.168.1.10) ou endereço completo (https://sonarr.exemplo.com)."""
    port: int | None = None
    base_url: str = ""
    ssl: bool = False
    timeout_s: int = Field(60, ge=5, le=600)
    only_monitored: bool = False
    """Só o que está monitorado no Radarr/Sonarr (filmes, séries e episódios)."""
    excluded_tags: tuple[str, ...] = ()
    """Itens com estas tags no Radarr/Sonarr ficam de fora (o nome da tag, como no Bazarr)."""
    excluded_series_types: tuple[str, ...] = ()
    """Só no Sonarr: standard, daily ou anime."""
    exclude_season_zero: bool = False
    """Só no Sonarr: temporada 0 (extras)."""
    path_mappings: tuple[tuple[str, str], ...] = ()
    """Caminho no Radarr/Sonarr → caminho aqui (quando rodam em contêineres diferentes)."""

    def url(self) -> str:
        host = self.host.strip().rstrip("/")
        if "://" not in host:
            host = f"{'https' if self.ssl else 'http'}://{host}"
            if self.port:
                host += f":{self.port}"
        base = self.base_url.strip().strip("/")
        return f"{host}/{base}" if base else host


def map_path(path: str, mappings: Iterable[tuple[str, str]]) -> Path:
    """Troca o prefixo do caminho do Radarr/Sonarr pelo daqui (o mais longo que casar)."""
    for remote, local in sorted(mappings, key=lambda m: len(m[0]), reverse=True):
        r = remote.rstrip("/")
        if path == r or path.startswith(r + "/"):
            return Path(local.rstrip("/") + path[len(r) :])
    return Path(path)


def major(version: str) -> int | None:
    head = version.strip().lstrip("vV").split(".")[0]
    return int(head) if head.isdigit() else None


class ArrClient:
    def __init__(
        self,
        kind: ArrKind,
        config: ArrConfig,
        api_key: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.kind = kind
        self.name = kind.capitalize()
        if not config.host.strip():
            raise Unavailable(f"{self.name}: no address", provider=kind)
        if not api_key:
            raise Unavailable(f"{self.name}: no API key", provider=kind)
        self._http = httpx.Client(
            base_url=config.url() + "/api/v3",
            headers={"X-Api-Key": api_key},
            timeout=config.timeout_s,
            transport=transport,
            follow_redirects=True,
        )

    def get(self, path: str, **params: Any) -> Any:
        try:
            r = self._http.get(path, params=params or None)
        except httpx.HTTPError as e:
            raise TransientFailure(f"{self.name} did not respond: {e}", provider=self.kind) from e
        if r.status_code == 401:
            raise Unavailable(f"{self.name} rejected the API key", provider=self.kind)
        if r.status_code >= 400:
            msg = f"{self.name} answered HTTP {r.status_code} on {path}"
            raise TransientFailure(msg, provider=self.kind)
        return r.json()

    def version(self) -> str:
        return str(self.get("/system/status").get("version", ""))

    def tags(self) -> dict[int, str]:
        return {int(t["id"]): str(t["label"]) for t in self.get("/tag")}

    def movies(self) -> list[dict[str, Any]]:
        return list(self.get("/movie"))

    def series(self) -> list[dict[str, Any]]:
        return list(self.get("/series"))

    def episode_files(self, series_id: int) -> list[dict[str, Any]]:
        return list(self.get("/episodefile", seriesId=series_id))

    def episodes(self, series_id: int) -> list[dict[str, Any]]:
        return list(self.get("/episode", seriesId=series_id))

    def close(self) -> None:
        self._http.close()


_latest_cache: dict[str, tuple[float, int]] = {}


def latest_major(kind: ArrKind, transport: httpx.BaseTransport | None = None) -> int:
    """Versão major mais nova publicada no GitHub (uma consulta por dia); offline, a conhecida."""
    hit = _latest_cache.get(kind)
    if hit and time.monotonic() - hit[0] < 24 * 3600:
        return hit[1]
    value = LATEST_MAJOR_FALLBACK[kind]
    try:
        with httpx.Client(timeout=10, transport=transport) as http:
            tag = http.get(RELEASES[kind]).raise_for_status().json().get("tag_name", "")
        value = major(str(tag)) or value
    except (httpx.HTTPError, ValueError) as e:
        log.info("latest %s release unknown (%s); using %d", kind, e, value)
    _latest_cache[kind] = (time.monotonic(), value)
    return value
