"""CLI `c3parr`: argumentos, modos de uso e códigos de saída."""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import BarColumn, Progress, TaskID, TextColumn, TimeElapsedColumn

from c3parr.core.config import Settings, load_secrets
from c3parr.core.job import JobParams, RecipeName
from c3parr.engine.engine import Engine, Recipe, RunResult
from c3parr.providers.registry import build_factories
from c3parr.recipes import full as full_recipe
from c3parr.recipes import sync as sync_recipe
from c3parr.recipes import transcribe as transcribe_recipe
from c3parr.recipes.transcribe import WrittenFile
from c3parr.report.review import build_review, write_review

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_USAGE = 2
EXIT_NO_TRANSCRIPT = 3
EXIT_NO_TRANSLATION = 4

console = Console(stderr=True)
app = typer.Typer(add_completion=False, help="Generates and re-syncs subtitles from the audio.")


class SecretFilter(logging.Filter):
    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        self.secrets = [s for s in secrets if len(s) >= 6]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for s in self.secrets:
            msg = msg.replace(s, "***")
        record.msg, record.args = msg, None
        return True


def _setup_logging(verbose: bool, secrets: list[str]) -> None:
    handler = RichHandler(console=console, show_path=False, markup=False)
    handler.addFilter(SecretFilter(secrets))
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, handlers=[handler], format="%(message)s", force=True)
    for noisy in ("httpx", "huggingface_hub", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _warn_downloads(settings: Settings) -> None:
    hub = Path.home() / ".cache" / "huggingface" / "hub"
    factories = build_factories()
    for interface, spec in settings.components.items():
        if interface not in factories:
            continue
        info = factories[interface].info(spec.provider)
        for m in info.requirements.models:
            if not (hub / f"models--{m.name.replace('/', '--')}").exists():
                console.print(f"[yellow]Will download model {m.name} (~{m.approx_mb} MB).[/]")


@app.command()
def main(
    video: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Video or audio")],
    src: Annotated[str | None, typer.Option("--src", help="Spoken language (e.g. fr)")] = None,
    dst: Annotated[str, typer.Option("--dst", help="Output language")] = "pt-BR",
    ref: Annotated[Path | None, typer.Option("--ref", help="Reference subtitle")] = None,
    ref_bridge: Annotated[
        Path | None,
        typer.Option("--ref-bridge", help="Subtitle in the audio language, used as a bridge"),
    ] = None,
    title: Annotated[
        str | None, typer.Option("--title", help="Title (otherwise from the file name)")
    ] = None,
    year: Annotated[int | None, typer.Option("--year")] = None,
    tmdb: Annotated[int | None, typer.Option("--tmdb", help="ID do filme no TMDb")] = None,
    no_tmdb: Annotated[bool, typer.Option("--no-tmdb")] = False,
    bible: Annotated[
        Path | None, typer.Option("--bible", help="Bible JSON (replaces TMDb and LLM)")
    ] = None,
    transcribe_only: Annotated[bool, typer.Option("--transcribe-only")] = False,
    sync_only: Annotated[bool, typer.Option("--sync-only")] = False,
    keep_transcript: Annotated[bool, typer.Option("--keep-transcript")] = False,
    out_dir: Annotated[Path | None, typer.Option("--out-dir", help="Output folder")] = None,
    force: Annotated[list[str] | None, typer.Option("--force", help="Recompute this stage")] = None,
    no_cache: Annotated[bool, typer.Option("--no-cache")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Gera a legenda de VIDEO."""
    secrets = load_secrets()
    _setup_logging(verbose, list(secrets.values()))
    recipe_name: RecipeName
    if sync_only:
        if ref is None:
            console.print("[red]--sync-only needs --ref.[/]")
            raise typer.Exit(EXIT_USAGE)
        recipe_name = "sincronizacao"
    elif transcribe_only:
        recipe_name = "transcricao"
    else:
        recipe_name = "completa"

    settings = Settings()
    params = JobParams(
        recipe=recipe_name,
        video=str(video.resolve()),
        src=src,
        dst=dst,
        ref=str(ref) if ref else None,
        ref_bridge=str(ref_bridge) if ref_bridge else None,
        title=title,
        year=year,
        tmdb_id=tmdb,
        no_tmdb=no_tmdb,
        bible=str(bible) if bible else None,
        out_dir=str(out_dir) if out_dir else None,
        keep_transcript=keep_transcript,
        interactive=console.is_terminal,
        force=tuple(force or ()),
        no_cache=no_cache,
        verbose=verbose,
    )
    _warn_downloads(settings)
    engine = Engine(build_factories(), settings.components, secrets, settings.cache_dir)
    result = _run_with_progress(engine, params, settings)
    raise typer.Exit(_finish(result, params))


def _run_with_progress(engine: Engine, params: JobParams, settings: Settings) -> RunResult:
    columns = (
        TextColumn("{task.description:<12}"),
        BarColumn(),
        TextColumn("{task.fields[msg]}"),
        TimeElapsedColumn(),
    )
    with Progress(*columns, console=console) as bar:
        tasks: dict[str, TaskID] = {}

        def on_progress(step: str, fraction: float, message: str) -> None:
            if step not in tasks:
                tasks[step] = bar.add_task(step, total=1.0, msg="")
            bar.update(tasks[step], completed=fraction, msg=message)

        recipes: dict[str, Callable[[], Recipe]] = {
            "transcricao": lambda: transcribe_recipe.recipe(settings.format),
            "sincronizacao": sync_recipe.recipe,
            "completa": lambda: full_recipe.recipe(settings.format),
        }
        return engine.run(recipes[params.recipe](), params, on_progress)


def _finish(result: RunResult, params: JobParams) -> int:
    written = result.artifacts.get("escrita")
    output = written.path if isinstance(written, WrittenFile) else None
    video = Path(params.video)
    folder = Path(params.out_dir) if params.out_dir else video.parent
    md = folder / f"{video.stem}.review.md"
    write_review(build_review(result, params.video, output), result.workdir / "review.json", md)

    for d in result.degradations:
        console.print(f"[yellow]Degraded[/] {d.stage}: {d.reason}")
    if result.failed:
        console.print(f"[red]Failed at {result.failed.name}:[/] {result.failed.reason}")
        console.print(f"Report: {md}")
        if result.failed.name in ("audio", "transcricao"):
            return EXIT_NO_TRANSCRIPT
        if result.failed.name == "traducao":
            return EXIT_NO_TRANSLATION
        return EXIT_UNEXPECTED
    console.print(f"[green]Subtitle:[/] {output}")
    console.print(f"Report: {md}")
    return EXIT_OK
