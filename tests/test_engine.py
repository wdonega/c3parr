from pathlib import Path

from pydantic import BaseModel

from c3parr.asr.fake import FakeTranscriber
from c3parr.core.errors import TransientFailure
from c3parr.core.job import ComponentSpec, JobParams
from c3parr.core.models import Subtitle
from c3parr.engine.engine import Engine, Recipe, Step, StepContext
from c3parr.providers.registry import build_factories
from c3parr.recipes import transcribe

DEFAULTS = {
    "transcricao": ComponentSpec(provider="falso"),
    "escrita_legenda": ComponentSpec(provider="srt"),
}


def make_engine(tmp_path: Path) -> Engine:
    return Engine(build_factories(), DEFAULTS, {}, tmp_path / "cache")


def params(video: Path, out: Path, **kw: object) -> JobParams:
    return JobParams.model_validate(
        {"recipe": "transcricao", "video": str(video), "src": "fr", "out_dir": str(out), **kw}
    )


def with_transcriber(p: JobParams, spec: ComponentSpec) -> JobParams:
    return p.model_copy(update={"components": {"transcricao": spec}})


def test_transcribe_recipe_writes_srt(tiny_video: Path, tmp_path: Path) -> None:
    result = make_engine(tmp_path).run(transcribe.recipe(), params(tiny_video, tmp_path / "out"))
    assert result.ok, result.failed
    out = tmp_path / "out" / "Filme Teste (2001).fr.srt"
    assert "Tu es fatigué ?" in out.read_text(encoding="utf-8")


def test_second_run_comes_from_cache(tiny_video: Path, tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.run(transcribe.recipe(), params(tiny_video, tmp_path / "out"))
    assert FakeTranscriber.calls == 1
    again = engine.run(transcribe.recipe(), params(tiny_video, tmp_path / "out"))
    assert FakeTranscriber.calls == 1
    statuses = {s.name: s.status for s in again.steps}
    assert statuses == {
        "audio": "cache",
        "transcricao": "cache",
        "formatacao": "cache",
        "escrita": "ok",
    }


def test_force_reruns_only_that_stage(tiny_video: Path, tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.run(transcribe.recipe(), params(tiny_video, tmp_path / "out"))
    forced = params(tiny_video, tmp_path / "out", force=("transcricao",))
    again = engine.run(transcribe.recipe(), forced)
    assert FakeTranscriber.calls == 2
    statuses = {s.name: s.status for s in again.steps}
    assert statuses["audio"] == "cache"
    assert statuses["transcricao"] == "ok"
    assert statuses["formatacao"] == "cache"  # mesma saída, mesma chave


def test_required_failure_stops_with_reason(tiny_video: Path, tmp_path: Path) -> None:
    spec = ComponentSpec(provider="falso", params={"fail": True})
    p = with_transcriber(params(tiny_video, tmp_path / "out"), spec)
    result = make_engine(tmp_path).run(transcribe.recipe(), p)
    assert result.failed is not None
    assert result.failed.name == "transcricao"
    assert result.failed.error_kind == "indisponivel"
    assert "escrita" not in result.artifacts


def test_no_audio_is_a_clear_error(silent_video: Path, tmp_path: Path) -> None:
    result = make_engine(tmp_path).run(transcribe.recipe(), params(silent_video, tmp_path / "out"))
    assert result.failed is not None
    assert result.failed.name == "audio"
    assert "has no audio track" in (result.failed.reason or "")


def test_unknown_required_provider_fails_before_start(tiny_video: Path, tmp_path: Path) -> None:
    p = with_transcriber(params(tiny_video, tmp_path / "out"), ComponentSpec(provider="nada"))
    result = make_engine(tmp_path).run(transcribe.recipe(), p)
    assert result.failed is not None
    assert [s.name for s in result.steps] == ["transcricao"]
    assert FakeTranscriber.calls == 0


class Note(BaseModel):
    text: str


def test_optional_failure_degrades_and_pipeline_continues(tiny_video: Path, tmp_path: Path) -> None:
    def boom(_ctx: StepContext) -> Note:
        raise TransientFailure("serviço caiu")

    def after(ctx: StepContext) -> Note:
        return Note(text="ok" if ctx.inputs["extra"] is None else "inesperado")

    recipe = Recipe(
        name="completa",
        steps=(
            Step(name="extra", run=boom, output=Note, required=False, failure_effect="sem extra"),
            Step(name="final", run=after, output=Note, needs=("extra",)),
        ),
    )
    result = make_engine(tmp_path).run(recipe, params(tiny_video, tmp_path))
    assert result.ok
    assert result.artifacts["final"] == Note(text="ok")
    assert [(d.stage, d.reason) for d in result.degradations] == [("extra", "serviço caiu")]


def test_unexpected_exception_never_escapes(tiny_video: Path, tmp_path: Path) -> None:
    def bug(_ctx: StepContext) -> Subtitle:
        raise ZeroDivisionError("ops")

    recipe = Recipe(name="completa", steps=(Step(name="bug", run=bug, output=Subtitle),))
    result = make_engine(tmp_path).run(recipe, params(tiny_video, tmp_path))
    assert result.failed is not None
    assert result.failed.error_kind == "erro_inesperado"
