"""Provider `lrclib` de letras: quem toca (MusicBrainz), o que já tinha lançado até o ano do show
(MusicBrainz) e a letra de cada faixa (LRCLIB).

Do título do show para o artista, conferido no MusicBrainz:
A. "Artista: Nome do show" / "Artista - Nome": o começo do título;
A2. título sem separador ("U2 3D"): as primeiras palavras, se a sinopse também cita o artista;
B. título sem artista: um álbum (ao vivo, vídeo) com o mesmo nome; o artista do álbum é o do show.

Fica de fora o que não é do artista (covers) e o que não tem letra no LRCLIB: nesses trechos a
legenda fica com o que foi ouvido. Cada resposta fica em disco por 30 dias, e os pedidos respeitam
o ritmo que cada serviço pede (o MusicBrainz, 1 por segundo). Só o nome do artista e os títulos
das faixas saem da máquina.
"""

import hashlib
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from c3parr.concert.catalogue import Catalogue, Song, clean_lines, parse_synced, parts, plain
from c3parr.concert.device import default_cache_dir
from c3parr.core.errors import TransientFailure
from c3parr.interfaces import lyrics
from c3parr.interfaces.lyrics import LyricsRequest
from c3parr.providers.base import Health, ProviderInfo, Requirements

log = logging.getLogger("c3parr.concert")
MB = "https://musicbrainz.org/ws/2"
LRCLIB = "https://lrclib.net/api"
PAUSE = {"musicbrainz.org": 1.1, "lrclib.net": 0.15}
SKIP_TYPES = frozenset({"Live", "Compilation", "Remix", "Demo", "DJ-mix", "Soundtrack", "Interview",
                        "Audiobook", "Spokenword", "Mixtape/Street"})  # fmt: skip
SPLIT_TITLE = re.compile(r"\s*(?::|\s[-–—|]\s|\s\|\s|：)\s*")  # noqa: RUF001
SPLIT_ARTISTS = re.compile(
    r"\s+(?:feat\.?|ft\.?|featuring|&|and|with|vs\.?|x|/)\s+|\s*,\s*", re.IGNORECASE
)
MAX_AGE_S = 30 * 24 * 3600.0

Fetch = Callable[[str, dict[str, Any]], Any]
"""(endereço, parâmetros) → JSON da resposta, ou None quando não existe (404)."""


class LrclibConfig(BaseModel):
    cache_dir: Path = Field(default_factory=lambda: default_cache_dir() / "lyrics")
    user_agent: str = "lyrics-subtitles/1.0"
    """Como o c3parr se apresenta aos dois serviços. O MusicBrainz pede um nome de programa com
    um contato; o padrão não leva nada de quem roda."""
    timeout_s: float = 30.0


INFO = ProviderInfo(
    id="lrclib",
    interface=lyrics.INTERFACE,
    version="1",
    config_schema=LrclibConfig,
    requirements=Requirements(network=True, sends_data_off_machine=True),
    description="MusicBrainz (artist and discography) + LRCLIB (lyrics)",
)


@dataclass(frozen=True)
class Artist:
    name: str
    mbid: str
    how: str = ""


class Http:
    """GET com cache em disco, pausa por serviço e nova tentativa quando o serviço pede calma."""

    def __init__(self, config: LrclibConfig) -> None:
        self.config = config
        self._last: dict[str, float] = {}

    def __call__(self, url: str, params: dict[str, Any]) -> Any:
        params = {k: v for k, v in params.items() if v is not None}
        host = urlsplit(url).netloc
        raw = json.dumps([url, sorted(params.items())], ensure_ascii=False)
        path = (
            self.config.cache_dir / "http" / host / f"{hashlib.sha1(raw.encode()).hexdigest()}.json"
        )
        if path.exists() and time.time() - path.stat().st_mtime < MAX_AGE_S:
            return json.loads(path.read_text(encoding="utf-8"))["body"]
        headers = {"User-Agent": self.config.user_agent, "Accept": "application/json"}
        got: httpx.Response | None = None
        for attempt in range(4):
            wait = PAUSE.get(host, 0.2) - (time.monotonic() - self._last.get(host, 0.0))
            if wait > 0:
                time.sleep(wait)
            try:
                got = httpx.get(url, params=params, headers=headers,
                                timeout=self.config.timeout_s, follow_redirects=True)  # fmt: skip
            except httpx.HTTPError as e:
                raise TransientFailure(f"{host} did not answer: {e}", provider=INFO.id) from e
            self._last[host] = time.monotonic()
            if got.status_code not in (429, 503):
                break
            time.sleep(2.0 * (attempt + 1))
        assert got is not None
        if got.status_code == 404:
            body = None
        elif got.status_code >= 400:
            msg = f"{host} answered HTTP {got.status_code}"
            raise TransientFailure(msg, provider=INFO.id)
        else:
            body = got.json()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"body": body}, ensure_ascii=False), encoding="utf-8")
        return body


# --- quem toca ---


def lookup(fetch: Fetch, name: str) -> Artist | None:
    """O artista com exatamente esse nome no MusicBrainz (o mais conhecido, se houver vários)."""
    if len(plain(name)) < 2:
        return None
    found = fetch(f"{MB}/artist", {"query": f'artist:"{name}"', "fmt": "json", "limit": 5})
    for a in (found or {}).get("artists", []):
        names = [a["name"], *[x["name"] for x in a.get("aliases", [])]]
        if a.get("score", 0) >= 90 and plain(name) in {plain(n) for n in names}:
            return Artist(a["name"], a["id"])
    return None


def from_title(fetch: Fetch, title: str) -> list[Artist]:
    """A: o trecho antes de ':' ou ' - '; inteiro primeiro, depois dividido (feat., &, vs…)."""
    head = SPLIT_TITLE.split(title.strip(), maxsplit=1)[0]
    if not head or plain(head) == plain(title):
        return []
    whole = lookup(fetch, head)
    if whole:
        return [Artist(whole.name, whole.mbid, "title")]
    pieces = [p for p in SPLIT_ARTISTS.split(head) if p]
    found = [a for a in (lookup(fetch, p) for p in pieces) if a] if len(pieces) > 1 else []
    return [Artist(a.name, a.mbid, "title") for a in found]


def from_title_start(fetch: Fetch, title: str, overview: str) -> list[Artist]:
    """A2: título sem separador ("U2 3D"): as primeiras palavras, se forem um artista que a
    sinopse também cita (sem a sinopse, "Stop" de "Stop Making Sense" passaria por artista)."""
    words = title.split()
    text = f" {plain(overview)} ".replace(f" {plain(title)} ", " ")  # o próprio título não conta
    for n in (3, 2, 1):
        head = " ".join(words[:n])
        if len(words) > n and plain(head) and f" {plain(head)} " in text:
            found = lookup(fetch, head)
            if found:
                return [Artist(found.name, found.mbid, "start of the title + overview")]
    return []


def from_release(fetch: Fetch, title: str, year: int | None, overview: str) -> list[Artist]:
    """B: um álbum (ao vivo, vídeo) com o nome do show; os artistas dele. Entre álbuns de mesmo
    nome, ganha o de artistas citados na sinopse."""
    query = {"query": f'releasegroup:"{title}"', "fmt": "json", "limit": 100}
    found = fetch(f"{MB}/release-group", query)
    best: tuple[float, list[Artist]] = (0.0, [])
    for group in (found or {}).get("release-groups", []):
        same = fuzz.ratio(plain(group["title"]), plain(title))
        if same < 92:
            continue
        when = (group.get("first-release-date") or "")[:4]
        near = 1.0 if not (year and when.isdigit()) or abs(int(when) - year) <= 2 else 0.0
        credit = [c["artist"] for c in group.get("artist-credit", []) if isinstance(c, dict)]
        if not credit or any(c["name"] == "Various Artists" for c in credit):
            continue
        live = 1.0 if "Live" in group.get("secondary-types", []) else 0.0
        text = f" {plain(overview)} "
        cited = sum(1 for c in credit if f" {plain(c['name'])} " in text) / len(credit)
        rank = same / 100 + near + 0.5 * live + 2.0 * cited + group.get("score", 0) / 1000
        if rank > best[0]:
            best = (rank, [Artist(c["name"], c["id"], "album with the same name") for c in credit])
    return best[1]


def find_artists(fetch: Fetch, request: LyricsRequest) -> list[Artist]:
    """Tenta o artista informado; senão cada título por A, A2 e depois B."""
    if request.artist:
        pieces = [request.artist, *[p for p in SPLIT_ARTISTS.split(request.artist) if p]]
        whole = lookup(fetch, pieces[0])
        found = [whole] if whole else [a for a in (lookup(fetch, p) for p in pieces[1:]) if a]
        return [Artist(a.name, a.mbid, "given") for a in found]
    for title in request.titles:
        hit = from_title(fetch, title) or from_title_start(fetch, title, request.overview)
        if hit:
            return hit
    for title in request.titles:
        tail = SPLIT_TITLE.split(title.strip(), maxsplit=1)
        hit = from_release(fetch, title, request.year, request.overview) or (
            from_release(fetch, tail[1], request.year, request.overview) if len(tail) > 1 else []
        )
        if hit:
            return hit
    return []


# --- o que o artista já tinha lançado, e as letras ---


def albums(fetch: Fetch, artist: Artist, until_year: int | None) -> list[dict[str, Any]]:
    """Álbuns e EPs de estúdio lançados até `until_year` (inclui os divididos com outro artista)."""
    out: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = fetch(
            f"{MB}/release-group",
            {
                "artist": artist.mbid,
                "type": "album|ep",
                "limit": 100,
                "offset": offset,
                "fmt": "json",
            },
        )
        groups = (page or {}).get("release-groups", [])
        for g in groups:
            year = (g.get("first-release-date") or "")[:4]
            if set(g.get("secondary-types", [])) & SKIP_TYPES or not year.isdigit():
                continue
            if until_year is None or int(year) <= until_year:
                out.append({"id": g["id"], "title": g["title"], "year": int(year)})
        offset += len(groups)
        if not groups or offset >= (page or {}).get("release-group-count", 0):
            break
    return sorted(out, key=lambda g: (g["year"], g["title"]))


def tracks(fetch: Fetch, group: dict[str, Any]) -> list[tuple[str, float | None]]:
    """As faixas da primeira edição oficial do álbum (a que tem menos extras): título e duração."""
    page = fetch(f"{MB}/release", {"release-group": group["id"], "inc": "recordings",
                                   "status": "official", "limit": 50, "fmt": "json"})  # fmt: skip
    releases = (page or {}).get("releases", [])
    if not releases:
        return []

    def order(release: dict[str, Any]) -> tuple[str, int]:
        count = sum(m.get("track-count", 0) for m in release.get("media", []))
        return (release.get("date") or "9999", count)

    first = min(releases, key=order)
    return [(t["title"], (t.get("length") or 0) / 1000 or None)
            for medium in first.get("media", []) for t in medium.get("tracks", [])]  # fmt: skip


def lyrics_of(fetch: Fetch, artist: str, title: str, album: str,
              length_s: float | None) -> dict[str, Any] | None:  # fmt: skip
    """A melhor entrada do LRCLIB para a faixa: mesmo título e artista; depois álbum e duração."""
    found = fetch(f"{LRCLIB}/search", {"track_name": title, "artist_name": artist}) or []
    best: tuple[float, dict[str, Any] | None] = (0.0, None)
    for hit in found:
        same = fuzz.ratio(plain(hit.get("trackName") or ""), plain(title))
        who = fuzz.partial_ratio(plain(artist), plain(hit.get("artistName") or ""))
        if same < 88 or who < 80 or not (hit.get("plainLyrics") or hit.get("instrumental")):
            continue
        rank = same / 100 + who / 200
        rank += 0.3 * (fuzz.ratio(plain(hit.get("albumName") or ""), plain(album)) >= 85)
        if length_s and hit.get("duration"):
            rank += 0.3 * (abs(hit["duration"] - length_s) <= 5)
        rank += 0.2 * bool(hit.get("syncedLyrics"))
        if rank > best[0]:
            best = (rank, hit)
    return best[1]


def _song(artist: str, title: str, group: dict[str, Any], length_s: float | None,
          hit: dict[str, Any] | None) -> Song:  # fmt: skip
    hit = hit or {}
    return Song(
        artist=artist, title=title, album=group["title"], year=group["year"], length_s=length_s,
        lines=clean_lines(hit.get("plainLyrics") or ""),
        synced=parse_synced(hit.get("syncedLyrics") or ""),
        instrumental=bool(hit.get("instrumental")),
    )  # fmt: skip


def build(fetch: Fetch, artists: list[Artist], until_year: int | None,
          progress: Callable[[float, str], None]) -> Catalogue:  # fmt: skip
    """O catálogo do show. Uma edição do álbum junta numa faixa só o que outra separa ("Suíte:
    I. Um / II. Dois"): a faixa composta sem letra é procurada parte por parte, e a parte só entra
    se nenhuma música com letra já tem aquele título."""
    songs: dict[str, Song] = {}
    composite: list[tuple[str, str, dict[str, Any]]] = []
    listed = [(artist, group) for artist in artists for group in albums(fetch, artist, until_year)]
    for n, (artist, group) in enumerate(listed):
        progress(0.1 + 0.8 * n / max(1, len(listed)), f"lyrics: {group['title']}")
        for title, length_s in tracks(fetch, group):
            key = f"{plain(artist.name)}|{plain(title)}"
            if key in songs:
                continue
            hit = lyrics_of(fetch, artist.name, title, group["title"], length_s)
            songs[key] = _song(artist.name, title, group, length_s, hit)
            if hit is None and parts(title):
                composite.append((artist.name, title, group))
    have = {plain(s.title) for s in songs.values() if s.lines}
    for artist_name, title, group in composite:
        for part in parts(title):
            if plain(part) in have:
                continue
            hit = lyrics_of(fetch, artist_name, part, group["title"], None)
            if hit:
                songs[f"{plain(artist_name)}|{plain(part)}"] = _song(artist_name, part, group,
                                                                     None, hit)  # fmt: skip
                have.add(plain(part))
    return Catalogue(artists=tuple(a.name for a in artists), until=until_year,
                     songs=tuple(songs.values()))  # fmt: skip


class LrclibLyrics:
    info = INFO

    def __init__(self, config: LrclibConfig, fetch: Fetch | None = None) -> None:
        self.config = config
        self.fetch: Fetch = fetch or Http(config)

    def health(self) -> Health:
        return Health(ok=True, detail="asks musicbrainz.org and lrclib.net")

    def catalogue(
        self, request: LyricsRequest, progress: Callable[[float, str], None]
    ) -> Catalogue:
        progress(0.0, "finding the artist")
        artists = find_artists(self.fetch, request)
        if not artists:
            log.info("no artist found for %s", ", ".join(request.titles))
            progress(1.0, "no artist found: no official lyrics")
            return Catalogue(until=request.year)
        progress(0.1, f"artist: {', '.join(a.name for a in artists)}")
        got = build(self.fetch, artists, request.year, progress)
        with_lyrics = sum(1 for s in got.songs if s.lines)
        progress(1.0, f"{with_lyrics} songs with lyrics")
        return got


def register() -> None:
    lyrics.FACTORY.register(INFO, LrclibLyrics)
