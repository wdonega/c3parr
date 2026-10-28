"""Motor genérico: monta os componentes, executa os estágios de uma receita e aplica os fallbacks.

Todo estágio devolve um resultado ou um motivo de falha; nenhuma exceção escapa do motor.
"""

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from c3parr.core.errors import InvalidInput, ProviderError
from c3parr.core.job import ComponentSpec, JobParams, RecipeName
from c3parr.core.models import Degradation, Model
from c3parr.engine.cache import ArtifactCache, canonical, digest, model_digest, video_fingerprint
from c3parr.providers.base import ProviderInfo
from c3parr.providers.factory import AbstractComponentFactoryFactory

log = logging.getLogger("c3parr.engine")

Progress = Callable[[str, float, str], None]
"""(estágio, fração 0..1, mensagem)"""


def _never(_: JobParams) -> str | None:
    return None


def _no_params(_: JobParams) -> dict[str, Any]:
    return {}


@dataclass(frozen=True)
class StepContext:
    step: str
    params: JobParams
    inputs: Mapping[str, BaseModel | None]
    component: Any
    workdir: Path
    progress: Callable[[float, str], None]
    cancelled: Callable[[], bool]

    def require[M: BaseModel](self, name: str, model: type[M]) -> M:
        value = self.inputs.get(name)
        if not isinstance(value, model):
            raise InvalidInput(f"stage {self.step} needs '{name}', which did not arrive")
        return value


@dataclass(frozen=True)
class Step:
    name: str
    run: Callable[[StepContext], BaseModel]
    output: type[BaseModel]
    version: str = "1"
    interface: str | None = None
    required: bool = True
    needs: tuple[str, ...] = ()
    skip_reason: Callable[[JobParams], str | None] = _never
    """Devolve o motivo para não rodar o estágio nesta tarefa, ou None para rodar."""
    key_params: Callable[[JobParams], dict[str, Any]] = _no_params
    """Opções da tarefa que mudam o resultado do estágio (entram na chave de cache)."""
    cacheable: bool = True
    failure_effect: str = ""


@dataclass(frozen=True)
class Recipe:
    name: RecipeName
    steps: tuple[Step, ...]


StepStatus = Literal["ok", "cache", "pulado", "falhou"]


class StepReport(Model):
    name: str
    status: StepStatus
    seconds: float = 0.0
    provider: str | None = None
    provider_version: str | None = None
    sends_data_off_machine: bool = False
    reason: str | None = None
    error_kind: str | None = None


@dataclass
class RunResult:
    recipe: RecipeName
    workdir: Path
    artifacts: dict[str, BaseModel] = field(default_factory=dict)
    steps: list[StepReport] = field(default_factory=list)
    degradations: list[Degradation] = field(default_factory=list)
    failed: StepReport | None = None

    @property
    def ok(self) -> bool:
        return self.failed is None


@dataclass(frozen=True)
class _Built:
    spec: ComponentSpec
    info: ProviderInfo
    component: Any


class Engine:
    def __init__(
        self,
        factories: AbstractComponentFactoryFactory,
        defaults: Mapping[str, ComponentSpec],
        secrets: Mapping[str, str],
        cache_root: Path,
    ) -> None:
        self.factories = factories
        self.defaults = defaults
        self.secrets = secrets
        self.cache_root = cache_root

    def run(
        self,
        recipe: Recipe,
        params: JobParams,
        progress: Progress | None = None,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> RunResult:
        workdir = self.cache_root / video_fingerprint(Path(params.video))
        workdir.mkdir(parents=True, exist_ok=True)
        result = RunResult(recipe=recipe.name, workdir=workdir)
        cache = ArtifactCache(workdir / "artifacts")
        report = progress or (lambda *_: None)

        built, build_errors = self._build_components(recipe, params)
        for step in recipe.steps:
            err = build_errors.get(step.name)
            if err is not None and step.required:
                result.failed = StepReport(
                    name=step.name, status="falhou", reason=err.message, error_kind=err.kind
                )
                result.steps.append(result.failed)
                log.error("%s: %s", step.name, err.message)
                return result

        hashes: dict[str, str] = {}
        for step in recipe.steps:
            if cancelled():
                result.failed = StepReport(name=step.name, status="falhou", reason="cancelada")
                return result
            skip = step.skip_reason(params)
            if skip is not None:
                result.steps.append(StepReport(name=step.name, status="pulado", reason=skip))
                continue
            if step.name in build_errors:
                err = build_errors[step.name]
                self._degrade(result, step, err.message, err.kind)
                continue

            b = built.get(step.name)
            inputs = {n: result.artifacts.get(n) for n in step.needs}
            key = digest(
                canonical(
                    {
                        "step": step.name,
                        "version": step.version,
                        "provider": b.info.id if b else None,
                        "provider_version": b.info.version if b else None,
                        "provider_params": b.spec.params if b else None,
                        "params": step.key_params(params),
                        "inputs": {n: hashes.get(n) for n in step.needs},
                    }
                )
            )
            base = StepReport(
                name=step.name,
                status="ok",
                provider=b.info.id if b else None,
                provider_version=b.info.version if b else None,
                sends_data_off_machine=b.info.requirements.sends_data_off_machine if b else False,
            )

            use_cache = step.cacheable and not params.no_cache and step.name not in params.force
            cached = cache.load(step.name, key, step.output) if use_cache else None
            if cached is not None:
                result.artifacts[step.name] = cached
                hashes[step.name] = model_digest(cached)
                result.steps.append(base.model_copy(update={"status": "cache"}))
                report(step.name, 1.0, "from cache")
                continue

            ctx = StepContext(
                step=step.name,
                params=params,
                inputs=inputs,
                component=b.component if b else None,
                workdir=workdir,
                progress=partial(report, step.name),
                cancelled=cancelled,
            )
            t0 = time.perf_counter()
            report(step.name, 0.0, "starting")
            try:
                output = step.run(ctx)
            except ProviderError as e:
                failure = (e.message, e.kind)
            except Exception as e:  # o motor nunca deixa uma exceção escapar
                log.exception("unexpected error in %s", step.name)
                failure = (f"unexpected error: {e}", "erro_inesperado")
            else:
                seconds = round(time.perf_counter() - t0, 2)
                result.artifacts[step.name] = output
                hashes[step.name] = model_digest(output)
                if step.cacheable:
                    cache.store(step.name, key, output)
                result.steps.append(base.model_copy(update={"seconds": seconds}))
                report(step.name, 1.0, "done")
                continue

            reason, kind = failure
            if step.required:
                result.failed = base.model_copy(
                    update={"status": "falhou", "reason": reason, "error_kind": kind}
                )
                result.steps.append(result.failed)
                log.error("%s: %s", step.name, reason)
                return result
            self._degrade(result, step, reason, kind)
        return result

    def _degrade(self, result: RunResult, step: Step, reason: str, kind: str) -> None:
        log.warning("%s: %s", step.name, reason)
        result.steps.append(
            StepReport(name=step.name, status="falhou", reason=reason, error_kind=kind)
        )
        result.degradations.append(
            Degradation(stage=step.name, reason=reason, effect=step.failure_effect)
        )

    def _build_components(
        self, recipe: Recipe, params: JobParams
    ) -> tuple[dict[str, _Built], dict[str, ProviderError]]:
        """Monta e valida tudo antes de a tarefa começar."""
        built: dict[str, _Built] = {}
        errors: dict[str, ProviderError] = {}
        for step in recipe.steps:
            if step.interface is None or step.skip_reason(params) is not None:
                continue
            spec = params.components.get(step.interface) or self.defaults.get(step.interface)
            try:
                if spec is None:
                    raise InvalidInput(f"no provider configured for {step.interface}")
                factory = self.factories.for_interface(step.interface)
                built[step.name] = _Built(
                    spec, factory.info(spec.provider), factory.create(spec, self.secrets)
                )
            except ProviderError as e:
                errors[step.name] = e
        return built, errors
