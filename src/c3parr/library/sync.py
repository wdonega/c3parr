"""Rotinas "Sync with Radarr" e "Sync with Sonarr": a biblioteca do c3parr é a deles.

Como o Bazarr: lê os filmes e episódios com arquivo, aplica os filtros de Settings → Library
(monitorado, tags, tipo de série, temporada 0), traduz o caminho e grava. O que saiu do
Radarr/Sonarr sai daqui também, mas só quando tudo o que eles listam foi achado neste disco
(caminho que não abre = mapeamento errado ou disco desmontado, e aí nada é apagado).
"""

import logging
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from c3parr.library.arr import ArrClient, latest_major, map_path
from c3parr.library.store import Library

log = logging.getLogger("c3parr.arr")


def _file_path(folder: str | None, file: dict[str, Any]) -> str | None:
    if file.get("path"):
        return str(file["path"])
    if folder and file.get("relativePath"):
        return str(PurePosixPath(folder) / str(file["relativePath"]))
    return None


def _summary(kind: str, counts: dict[str, int], removed: int | None) -> str:
    parts = [f"{counts['items']} {kind}", f"{counts['new']} new"]
    if removed:
        parts.append(f"{removed} removed")
    if counts["filtered"]:
        parts.append(f"{counts['filtered']} left out by the filters")
    if counts["missing"]:
        parts.append(f"{counts['missing']} file(s) not found here: check the Path Mappings")
        if removed is None:
            parts.append("nothing was removed")
    return ", ".join(parts)


def sync_radarr(
    library: Library, api_key: str, transport: httpx.BaseTransport | None = None
) -> str:
    config = library.config()
    cfg = config.radarr
    if not cfg.enabled:
        return "Radarr is off (Settings → Library)"
    client = ArrClient("radarr", cfg, api_key, transport)
    try:
        library.set_meta("radarr_version", client.version())
        library.set_meta("radarr_latest_major", str(latest_major("radarr")))
        excluded = {i for i, label in client.tags().items() if label in cfg.excluded_tags}
        movies = client.movies()
    finally:
        client.close()
    counts = {"items": 0, "new": 0, "filtered": 0, "missing": 0}
    folders: set[str] = set()
    for m in movies:
        file = m.get("movieFile")
        if not m.get("hasFile") or not isinstance(file, dict):
            continue
        if (cfg.only_monitored and not m.get("monitored")) or excluded & set(m.get("tags") or []):
            counts["filtered"] += 1
            continue
        remote = _file_path(m.get("path"), file)
        video = map_path(remote, cfg.path_mappings) if remote else None
        if video is None or not video.is_file():
            counts["missing"] += 1
            continue
        folder = str(video.parent)
        folders.add(folder)
        counts["items"] += 1
        counts["new"] += library.upsert_movie(
            folder, str(video), str(m.get("title") or video.stem), m.get("year") or None,
            m.get("tmdbId") or None, config.monitor_new,
        )  # fmt: skip
    removed = None if counts["missing"] else library.remove_movies_except(folders)
    return _summary("movies", counts, removed)


def sync_sonarr(
    library: Library, api_key: str, transport: httpx.BaseTransport | None = None
) -> str:
    config = library.config()
    cfg = config.sonarr
    if not cfg.enabled:
        return "Sonarr is off (Settings → Library)"
    client = ArrClient("sonarr", cfg, api_key, transport)
    counts = {"items": 0, "new": 0, "filtered": 0, "missing": 0}
    folders: set[str] = set()
    videos: set[str] = set()
    try:
        library.set_meta("sonarr_version", client.version())
        library.set_meta("sonarr_latest_major", str(latest_major("sonarr")))
        excluded = {i for i, label in client.tags().items() if label in cfg.excluded_tags}
        for s in client.series():
            stats = s.get("statistics") or {}
            if stats and not stats.get("episodeFileCount"):
                continue  # série sem arquivo: nem pergunta pelos episódios
            if (
                (cfg.only_monitored and not s.get("monitored"))
                or excluded & set(s.get("tags") or [])
                or s.get("seriesType") in cfg.excluded_series_types
            ):
                counts["filtered"] += 1
                continue
            numbers: dict[int, tuple[int | None, int | None, bool]] = {}
            for ep in client.episodes(int(s["id"])):
                fid = ep.get("episodeFileId")
                if fid and fid not in numbers:  # arquivo com vários episódios: fica o primeiro
                    numbers[fid] = (ep.get("seasonNumber"), ep.get("episodeNumber"),
                                    bool(ep.get("monitored")))  # fmt: skip
            series_id: int | None = None
            for f in client.episode_files(int(s["id"])):
                default = (f.get("seasonNumber"), None, True)
                season, number, monitored = numbers.get(int(f["id"]), default)
                skip_zero = cfg.exclude_season_zero and season == 0
                if skip_zero or (cfg.only_monitored and not monitored):
                    counts["filtered"] += 1
                    continue
                remote = _file_path(s.get("path"), f)
                video = map_path(remote, cfg.path_mappings) if remote else None
                if video is None or not video.is_file():
                    counts["missing"] += 1
                    continue
                if series_id is None:
                    folder = str(map_path(str(s.get("path") or video.parent), cfg.path_mappings))
                    series_id = library.upsert_series(
                        folder, str(s.get("title") or Path(folder).name), s.get("year") or None,
                        s.get("tmdbId") or None, config.monitor_new,
                    )  # fmt: skip
                    folders.add(folder)
                videos.add(str(video))
                counts["items"] += 1
                counts["new"] += library.upsert_episode(series_id, str(video), season, number)
    finally:
        client.close()
    removed = None if counts["missing"] else library.remove_series_except(folders, videos)
    return _summary("episodes", counts, removed)
