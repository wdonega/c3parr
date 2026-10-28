"""Provider `tmdb`: identifica filme ou episódio e monta a bíblia a partir do elenco.

Lê a biblioteca como Radarr, Sonarr e Bazarr a organizam:
  movies/Título (Ano)/Título (Ano) qualidade.mkv
  tv/Série/Season N/Série - S01E01 - Episódio.mkv
Ordem de identificação: ID do TMDb (`--tmdb`) > `[tmdbid-…]`/`[imdbid-…]` no caminho > busca por
título e ano (pasta antes do nome do arquivo). Vários candidatos são desempatados pela duração e
pelo idioma original; empate ou nenhum que passe faz o estágio falhar (aviso) em vez de adivinhar.

This product uses the TMDB API but is not endorsed or certified by TMDB.
"""

import re
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, SecretStr

from c3parr.core.bible import Bible, Character, Gender, Title
from c3parr.core.errors import InvalidInput, RateLimited, TransientFailure, Unavailable
from c3parr.core.models import Model
from c3parr.interfaces import metadata
from c3parr.interfaces.metadata import Candidate, MetadataRequest, MetadataResult
from c3parr.providers.base import Health, ProviderInfo, Requirements

GENDERS: dict[int, Gender] = {1: "F", 2: "M"}
GENERIC = re.compile(
    r"^(waiter|waitress|garçom|garçonete|voice|voz|himself|herself|narrator|narrador|guard|"
    r"guarda|soldier|soldado|nurse|enfermeira|doctor|médico|police|policial|man|woman|"
    r"homem|mulher|boy|girl|menino|menina)\b|\(voice\)|\(uncredited\)|#\d",
    re.IGNORECASE,
)
_YEAR = re.compile(r"[\s._(\[-]((?:19|20)\d{2})(?:[\s._)\]-]|$)")
_JUNK = re.compile(
    r"\b(1080p|720p|2160p|4k|bluray|blu-ray|web-?dl|webrip|hdtv|x26[45]|h\.?26[45]|aac|dts|"
    r"remux|proper|repack)\b.*$",
    re.IGNORECASE,
)
_TITLE_YEAR = re.compile(r"^(?P<title>.+?)\s*\((?P<year>(?:19|20)\d{2})\)")
_TMDB_ID = re.compile(r"[\[{]tmdb(?:id)?-(\d+)[\]}]", re.IGNORECASE)
_IMDB_ID = re.compile(r"[\[{]imdb(?:id)?-(tt\d+)[\]}]", re.IGNORECASE)
_EPISODE = re.compile(r"\bS(\d{1,2})E(\d{1,3})\b", re.IGNORECASE)
_SEASON_DIR = re.compile(r"^(season|temporada)\s*(\d+)$", re.IGNORECASE)
_TV_ROOTS = {"tv", "series", "séries", "shows", "tv shows"}
_MOVIE_ROOTS = {"movies", "filmes", "films"}


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", text.lower()).split())


def parse_filename(name: str) -> tuple[str, int | None]:
    """`Incendies (2010) Bluray-1080p.mp4` -> ("Incendies", 2010)."""
    stem = re.sub(r"\.[a-z0-9]{2,4}$", "", name, flags=re.IGNORECASE)
    m = _YEAR.search(stem)
    year = int(m.group(1)) if m else None
    title = stem[: m.start()] if m else _JUNK.sub("", stem)
    title = re.sub(r"[._]+", " ", title)
    return " ".join(title.replace("(", " ").split()).strip(" -"), year


class MediaHint(Model):
    """O que dá para saber só pelo caminho do arquivo."""

    kind: Literal["filme", "serie"]
    title: str
    year: int | None = None
    tmdb_id: int | None = None
    imdb_id: str | None = None
    season: int | None = None
    episode: int | None = None
    source: str = ""


def _clean_dir(name: str) -> tuple[str, int | None]:
    name = _TMDB_ID.sub("", _IMDB_ID.sub("", name)).strip()
    m = _TITLE_YEAR.match(name)
    if m:
        return m.group("title").strip(), int(m.group("year"))
    return name, None


def parse_path(path: Path) -> MediaHint:
    """Lê filme ou série do caminho, no layout do Radarr/Sonarr/Bazarr."""
    parents = [p.name for p in path.parents if p.name]
    chain = [path.name, *parents]
    tmdb = next((int(m.group(1)) for n in chain if (m := _TMDB_ID.search(n))), None)
    imdb = next((m.group(1) for n in chain if (m := _IMDB_ID.search(n))), None)
    ep = _EPISODE.search(path.name)
    season_dir = next((i for i, n in enumerate(parents) if _SEASON_DIR.match(n)), None)
    under_tv = any(n.lower() in _TV_ROOTS for n in parents)
    under_movies = any(n.lower() in _MOVIE_ROOTS for n in parents)

    if ep or season_dir is not None or (under_tv and not under_movies):
        show_dir = (
            parents[season_dir + 1]
            if season_dir is not None and len(parents) > season_dir + 1
            else None
        )
        if show_dir is None and parents and parents[0].lower() not in _TV_ROOTS:
            show_dir = parents[0]
        if show_dir:
            title, year = _clean_dir(show_dir)
            source = f"show folder '{show_dir}'"
        else:
            title = path.stem[: ep.start()].strip(" -._") if ep else parse_filename(path.name)[0]
            year, source = None, "file name"
        season = int(ep.group(1)) if ep else None
        if season is None and season_dir is not None:
            m = _SEASON_DIR.match(parents[season_dir])
            season = int(m.group(2)) if m else None
        return MediaHint(kind="serie", title=re.sub(r"[._]+", " ", title).strip(), year=year,
                         tmdb_id=tmdb, imdb_id=imdb, season=season,
                         episode=int(ep.group(2)) if ep else None, source=source)  # fmt: skip

    if parents and (m := _TITLE_YEAR.match(_TMDB_ID.sub("", _IMDB_ID.sub("", parents[0])))):
        return MediaHint(kind="filme", title=m.group("title").strip(), year=int(m.group("year")),
                         tmdb_id=tmdb, imdb_id=imdb, source=f"folder '{parents[0]}'")  # fmt: skip
    title, year = parse_filename(path.name)
    return MediaHint(kind="filme", title=title, year=year, tmdb_id=tmdb, imdb_id=imdb,
                     source="file name")  # fmt: skip


BUILTIN_READ_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJlZWJiNmI4OWM3ZjczZmFkZTFkNzQ1M2Y0ZDE3YTIyYyIsIm5iZiI6MTc4ODkwMDY0NS40NTQwMDAyLCJzdWIiOiI2YWEwNzUyNTUyMjU3ZGNjMmIyM2I1ZGIiLCJzY29wZXMiOlsiYXBpX3JlYWQiXSwidmVyc2lvbiI6MX0.Xd2ICEiOxltDmotoECQb7P1V56Lodnh42GzutHSXoJQ"  # noqa: E501
"""Read access token do app c3parr no TMDb, como o Jellyfin e o Kodi fazem: funciona sem
configurar nada. A chave de quem usa (.env ou Settings) tem prioridade. Os termos do TMDb pedem a
atribuição (ATTRIBUTION) visível no app."""
ATTRIBUTION = "This product uses the TMDB API but is not endorsed or certified by TMDB."


def _builtin_token() -> SecretStr | None:
    return SecretStr(BUILTIN_READ_TOKEN) if BUILTIN_READ_TOKEN else None


class TMDbConfig(BaseModel):
    token: SecretStr | None = Field(
        default_factory=_builtin_token, json_schema_extra={"secret_env": "TMDB_READ_ACCESS_TOKEN"}
    )
    base_url: str = "https://api.themoviedb.org/3"
    language: str = "pt-BR"
    max_candidates: int = 5
    runtime_tolerance_min: float = 8.0
    max_cast: int = 40
    timeout_s: float = 20.0


INFO = ProviderInfo(
    id="tmdb",
    interface=metadata.INTERFACE,
    version="3",
    config_schema=TMDbConfig,
    requirements=Requirements(network=True, sends_data_off_machine=True),
    description="The Movie Database (only title, year and IDs leave the machine)",
)


class TMDbProvider:
    info = INFO

    def __init__(self, config: TMDbConfig, transport: httpx.BaseTransport | None = None) -> None:
        if config.token is None:
            raise Unavailable("missing key: TMDB_READ_ACCESS_TOKEN", provider=INFO.id)
        self.config = config
        self._http = httpx.Client(
            base_url=config.base_url,
            headers={"Authorization": f"Bearer {config.token.get_secret_value()}"},
            timeout=config.timeout_s,
            transport=transport,
        )

    def health(self) -> Health:
        try:
            self._get("/configuration")
        except (Unavailable, TransientFailure, InvalidInput) as e:
            return Health(ok=False, detail=str(e))
        return Health(ok=True)

    def _get(self, path: str, **params: Any) -> Any:
        query = {k: v for k, v in params.items() if v is not None}
        try:
            r = self._http.get(path, params=query)
        except httpx.HTTPError as e:
            raise TransientFailure(f"TMDb did not respond: {e}", provider=INFO.id) from e
        if r.status_code == 401:
            raise Unavailable("TMDb rejected the key (401)", provider=INFO.id)
        if r.status_code == 429:
            raise RateLimited("TMDb rate-limited the calls", provider=INFO.id)
        if r.status_code == 404:
            raise InvalidInput(f"TMDb did not find {path}", provider=INFO.id)
        if r.status_code >= 400:
            raise TransientFailure(f"TMDb respondeu HTTP {r.status_code}", provider=INFO.id)
        return r.json()

    # --- identificação ---

    def identify(self, request: MetadataRequest) -> MetadataResult:
        hint = parse_path(Path(request.path or request.filename))
        if request.title:
            hint = hint.model_copy(update={"title": request.title, "year": request.year,
                                           "source": "given title"})  # fmt: skip
        tmdb_id, how = request.tmdb_id, "TMDb ID given"
        if tmdb_id is None and hint.tmdb_id is not None:
            tmdb_id, how = hint.tmdb_id, "TMDb ID in the path"
        if tmdb_id is None and hint.imdb_id is not None:
            tmdb_id, how = self._from_imdb(hint), f"IMDb ID in the path ({hint.imdb_id})"
        if hint.kind == "serie":
            return self._identify_show(hint, request, tmdb_id, how)
        return self._identify_movie(hint, request, tmdb_id, how)

    def _from_imdb(self, hint: MediaHint) -> int:
        found = self._get(f"/find/{hint.imdb_id}", external_source="imdb_id")
        key = "tv_results" if hint.kind == "serie" else "movie_results"
        results = found.get(key) or []
        if not results:
            raise InvalidInput(f"TMDb does not know IMDb {hint.imdb_id}", provider=INFO.id)
        return int(results[0]["id"])

    def _pick(
        self, results: list[dict[str, Any]], checker: Any, label: str, prefer: Any = None
    ) -> tuple[Any, list[Candidate]]:
        """Fica com o único candidato que passa nas checagens; havendo empate, desempata por
        `prefer` (nome exato, título do episódio no arquivo) se houver um só melhor."""
        candidates, passing = [], []
        for r in results[: self.config.max_candidates]:
            data, cand, ok = checker(int(r["id"]))
            candidates.append(cand)
            if ok:
                passing.append(data)
        if len(passing) > 1 and prefer is not None:
            scores = [prefer(d) for d in passing]
            best = max(scores)
            if best > 0 and scores.count(best) == 1:
                passing = [passing[scores.index(best)]]
        if len(passing) != 1:
            listing = "; ".join(f"{c.titulo} ({c.ano}, id {c.id}): {c.motivo}" for c in candidates)
            what = "no candidate passed" if not passing else f"{len(passing)} tied"
            raise InvalidInput(
                f"TMDb ({label}): {what} in the runtime and language checks. {listing}"
            )
        return passing[0], candidates

    def _identify_movie(self, hint: MediaHint, request: MetadataRequest, tmdb_id: int | None,
                        how: str) -> MetadataResult:  # fmt: skip
        if tmdb_id is not None:
            d = self._movie(tmdb_id)
            return MetadataResult(bible=self._movie_bible(d), tmdb_id=tmdb_id, chosen_reason=how,
                                  original_language=d.get("original_language"))  # fmt: skip
        if not hint.title:
            raise InvalidInput("no title to search on TMDb", provider=INFO.id)
        found = self._get("/search/movie", query=hint.title, year=hint.year,
                          language=self.config.language)  # fmt: skip
        results = found.get("results", [])
        if not results:
            raise InvalidInput(f"TMDb: no movie for '{hint.title}' ({hint.year or 'no year'})")

        def check(movie_id: int) -> tuple[dict[str, Any], Candidate, bool]:
            d = self._movie(movie_id)
            title = d.get("title") or d.get("original_title", "")
            cand, ok = self._check(d, d.get("runtime"), title, d.get("release_date"), request)
            return d, cand, ok

        chosen, candidates = self._pick(results, check, "movie")
        reason = next(c.motivo for c in candidates if c.id == chosen["id"])
        return MetadataResult(bible=self._movie_bible(chosen), tmdb_id=int(chosen["id"]),
                              candidates=tuple(candidates),
                              original_language=chosen.get("original_language"),
                              chosen_reason=f"search by {hint.source}: {reason}")  # fmt: skip

    def _identify_show(self, hint: MediaHint, request: MetadataRequest, tmdb_id: int | None,
                       how: str) -> MetadataResult:  # fmt: skip
        if tmdb_id is None:
            if not hint.title:
                raise InvalidInput("no show name to search on TMDb", provider=INFO.id)
            found = self._get("/search/tv", query=hint.title, first_air_date_year=hint.year,
                              language=self.config.language)  # fmt: skip
            results = found.get("results", [])
            if not results:
                raise InvalidInput(f"TMDb: no show for '{hint.title}'")

            def check(show_id: int) -> tuple[dict[str, Any], Candidate, bool]:
                show = self._show(show_id)
                ep = self._episode(show_id, hint) if hint.season and hint.episode else None
                runtime = (ep or {}).get("runtime") or next(
                    iter(show.get("episode_run_time") or []), None
                )
                cand, ok = self._check(show, runtime, show.get("name", ""),
                                       show.get("first_air_date"), request)  # fmt: skip
                return {**show, "_episode": ep}, cand, ok

            stem = _norm(Path(request.filename).stem)

            def prefer(show: dict[str, Any]) -> int:
                names = {_norm(show.get("name", "")), _norm(show.get("original_name", ""))}
                score = 2 if _norm(hint.title) in names else 0
                ep = show.get("_episode") or {}
                if ep.get("name") and _norm(ep["name"]) in stem:
                    score += 1
                return score

            chosen, candidates = self._pick(results, check, "show", prefer)
            reason = f"search by {hint.source}: " + next(
                c.motivo for c in candidates if c.id == chosen["id"]
            )
        else:
            chosen = self._show(tmdb_id)
            chosen["_episode"] = (
                self._episode(tmdb_id, hint) if hint.season and hint.episode else None
            )
            candidates, reason = [], how
        ep = chosen.get("_episode")
        return MetadataResult(
            bible=self._show_bible(chosen, ep),
            tmdb_id=int(chosen["id"]),
            kind="serie",
            season=hint.season,
            episode=hint.episode,
            episode_title=(ep or {}).get("name"),
            original_language=chosen.get("original_language"),
            candidates=tuple(candidates),
            chosen_reason=reason,
        )

    # --- chamadas ---

    def _movie(self, movie_id: int) -> dict[str, Any]:
        data: dict[str, Any] = self._get(f"/movie/{movie_id}", language=self.config.language,
                                         append_to_response="credits")  # fmt: skip
        return data

    def _show(self, show_id: int) -> dict[str, Any]:
        data: dict[str, Any] = self._get(f"/tv/{show_id}", language=self.config.language,
                                         append_to_response="aggregate_credits")  # fmt: skip
        return data

    def _episode(self, show_id: int, hint: MediaHint) -> dict[str, Any] | None:
        try:
            data: dict[str, Any] = self._get(
                f"/tv/{show_id}/season/{hint.season}/episode/{hint.episode}",
                language=self.config.language,
                append_to_response="credits",
            )
        except InvalidInput:
            return None
        return data

    def _check(self, d: dict[str, Any], runtime: int | None, title: str, date: str | None,
               request: MetadataRequest) -> tuple[Candidate, bool]:  # fmt: skip
        lang = d.get("original_language")
        year = int(date[:4]) if date else None
        reasons, ok = [], True
        if request.audio_duration and runtime:
            minutes = request.audio_duration / 60
            if abs(runtime - minutes) <= self.config.runtime_tolerance_min:
                reasons.append(f"runtime matches ({runtime} min)")
            else:
                ok = False
                reasons.append(f"runtime {runtime} min vs {minutes:.0f} min in the file")
        elif not runtime:
            reasons.append("no runtime on TMDb")
        if request.language and lang:
            if lang == request.language.split("-")[0]:
                reasons.append(f"original language {lang}")
            else:
                ok = False
                reasons.append(f"original language {lang}, audio in {request.language}")
        cand = Candidate(id=int(d["id"]), titulo=title, ano=year, runtime_min=runtime, idioma=lang,
                         motivo="; ".join(reasons))  # fmt: skip
        return cand, ok

    # --- bíblia ---

    def _characters(self, people: list[dict[str, Any]]) -> tuple[Character, ...]:
        chars: list[Character] = []
        seen: set[str] = set()
        for person in people[: self.config.max_cast]:
            role = person.get("character") or next(
                (r.get("character") for r in person.get("roles", []) if r.get("character")), ""
            )
            role = (role or "").split("/")[0].strip()
            if not role or GENERIC.search(role) or role.lower() in seen:
                continue
            seen.add(role.lower())
            gender = GENDERS.get(int(person.get("gender") or 0), "?")
            chars.append(Character(nome=role, ator=person.get("name"), genero=gender,
                                   origem_genero="tmdb" if gender != "?" else None,
                                   confianca=0.8 if gender != "?" else 0.0))  # fmt: skip
        return tuple(chars)

    def _movie_bible(self, d: dict[str, Any]) -> Bible:
        year = int(d["release_date"][:4]) if d.get("release_date") else None
        return Bible(
            titulo=Title(original=d.get("original_title", ""), pt=d.get("title", "")),
            ano=year,
            sinopse=d.get("overview", ""),
            generos=tuple(g["name"] for g in d.get("genres", [])),
            runtime_min=d.get("runtime") or None,
            personagens=self._characters((d.get("credits") or {}).get("cast", [])),
            origem=("tmdb",),
        )

    def _show_bible(self, show: dict[str, Any], ep: dict[str, Any] | None) -> Bible:
        cast = list((show.get("aggregate_credits") or {}).get("cast", []))
        if ep:
            cast += (ep.get("guest_stars") or []) + (
                (ep.get("credits") or {}).get("guest_stars") or []
            )
        year = int(show["first_air_date"][:4]) if show.get("first_air_date") else None
        synopsis = show.get("overview", "")
        if ep and ep.get("overview"):
            synopsis = f'{synopsis}\n\nEpisode "{ep.get("name", "")}": {ep["overview"]}'
        return Bible(
            titulo=Title(original=show.get("original_name", ""), pt=show.get("name", "")),
            ano=year,
            sinopse=synopsis,
            generos=tuple(g["name"] for g in show.get("genres", [])),
            runtime_min=(ep or {}).get("runtime") or None,
            personagens=self._characters(cast),
            origem=("tmdb",),
        )


def register() -> None:
    metadata.FACTORY.register(INFO, TMDbProvider)
