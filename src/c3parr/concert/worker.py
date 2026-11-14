"""Roda um provider pesado do show num processo filho.

A separação de voz e o encaixe da letra carregam o torch e modelos de até 1,3 GB. Dentro do
processo do c3parr essa memória não volta toda ao sistema quando a tarefa termina (medido: 0,7 GB
presos depois de um show), e numa máquina pequena o show seguinte já não cabe. Aqui o trabalho roda
num processo que fecha no fim, como o `whisper-cli`: a memória volta inteira.

O pai manda um pedido em JSON pela entrada do filho; o filho escreve o andamento na saída de erro
(`progress <fração> <mensagem>`) e o resultado em JSON na saída padrão.
"""

import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from typing import Any

from c3parr.core.errors import (
    InvalidInput,
    ProviderError,
    RateLimited,
    TransientFailure,
    Unavailable,
)

ERRORS: dict[str, type[ProviderError]] = {
    cls.kind: cls
    for cls in (Unavailable, InvalidInput, TransientFailure, RateLimited, ProviderError)
}


LOCAL_PARAMS = frozenset({"processor", "isolate"})
"""Não vão para o filho: o filho roda aqui mesmo, e sem a chave do processador."""


def call(interface: str, provider: str, params: dict[str, Any], method: str,
         args: dict[str, Any], progress: Callable[[float, str], None],
         started: Callable[[subprocess.Popen[str]], None] | None = None) -> Any:  # fmt: skip
    """Chama `method` do provider num processo filho e devolve o resultado (dados de JSON).

    `started` recebe o processo assim que ele abre: quem precisar pode encerrá-lo (o processador
    CUDA, ao desligar)."""
    kept = {k: v for k, v in params.items() if k not in LOCAL_PARAMS}
    job = {"interface": interface, "provider": provider, "params": kept, "method": method,
           "args": args}  # fmt: skip
    nice = os.name == "posix"
    proc = subprocess.Popen(
        [sys.executable, "-m", "c3parr.concert.worker"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        errors="replace", preexec_fn=(lambda: os.nice(10)) if nice else None,
    )  # fmt: skip
    assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
    if started is not None:
        started(proc)
    proc.stdin.write(json.dumps(job))
    proc.stdin.close()
    # a resposta é lida ao mesmo tempo que o andamento: uma resposta grande (a transcrição de um
    # show inteiro, vários MB) enche o cano da saída, e o filho para esperando quem não lê
    stdout = proc.stdout
    answer_parts: list[str] = []
    reader = threading.Thread(target=lambda: answer_parts.append(stdout.read()), daemon=True)
    reader.start()
    tail: list[str] = []
    for line in proc.stderr:
        if line.startswith("progress "):
            _, fraction, message = line.rstrip("\n").split(" ", 2)
            progress(float(fraction), message)
        else:
            tail = [*tail, line][-30:]
    reader.join()
    out = "".join(answer_parts)
    if proc.wait() != 0 and not out.strip():
        raise TransientFailure(
            f"{provider} stopped with code {proc.returncode}: {''.join(tail)[-400:]}",
            provider=provider,
        )
    answer = json.loads(out)
    if "error" in answer:
        kind = ERRORS.get(answer["error"]["kind"], ProviderError)
        raise kind(answer["error"]["message"], provider=provider)
    return answer["result"]


def main() -> None:
    from c3parr.core.job import ComponentSpec
    from c3parr.interfaces.lyric_fit import PlaceRequest
    from c3parr.interfaces.separation import SeparationRequest
    from c3parr.interfaces.transcription import AudioRef, TranscriptionRequest
    from c3parr.providers.registry import build_factories

    job = json.loads(sys.stdin.read())

    def progress(fraction: float, message: str) -> None:
        print(f"progress {fraction:.4f} {message}", file=sys.stderr, flush=True)

    try:
        factory = build_factories().for_interface(job["interface"])
        spec = ComponentSpec(provider=job["provider"], params={**job["params"], "isolate": False})
        component = factory.create(spec, {})
        args = job["args"]
        if job["method"] == "separate":
            request = SeparationRequest.model_validate(args["request"])
            result: Any = component.separate(request, progress).model_dump(mode="json")
        elif job["method"] == "place":
            audio = AudioRef.model_validate(args["audio"])
            placed = component.place(audio, PlaceRequest.model_validate(args["request"]), progress)
            result = [[p.model_dump(mode="json") for p in group] for group in placed]
        elif job["method"] == "transcribe":
            audio = AudioRef.model_validate(args["audio"])
            asked = TranscriptionRequest.model_validate(args["request"])
            result = component.transcribe(audio, asked, progress).model_dump(mode="json")
        else:
            raise InvalidInput(f"unknown method {job['method']}")
        answer: dict[str, Any] = {"result": result}
    except ProviderError as e:
        answer = {"error": {"kind": e.kind, "message": e.message}}
    except Exception as e:  # o pai transforma em falha da etapa, com o motivo
        answer = {"error": {"kind": "erro", "message": f"{type(e).__name__}: {e}"}}
    print(json.dumps(answer), flush=True)


if __name__ == "__main__":
    main()
