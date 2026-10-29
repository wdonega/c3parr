"""Provider `whisper-cpp` de transcrição: o binário `whisper-cli` do whisper.cpp.

Um só motor para qualquer máquina (decisão 0005): Metal no Mac, Vulkan/CUDA onde o binário foi
feito com eles, CPU no resto. Configuração fixa medida no Incendies: `-mc 0` (sem o texto anterior
como contexto) e VAD Silero com pausa de 500 ms — sem isso o whisper.cpp alucina em trecho com
música. Threads = núcleos físicos; prioridade baixa (`nice`) para não atrapalhar a máquina.
"""

import contextlib
import json
import logging
import math
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
import zlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from c3parr.asr.faster_whisper import FasterWhisperConfig, flags_for, merge_tokens
from c3parr.audio.ffmpeg import normalize_lang
from c3parr.core.errors import TransientFailure, Unavailable
from c3parr.core.models import Segment, Transcript
from c3parr.interfaces import transcription
from c3parr.interfaces.transcription import AudioRef, TranscriptionRequest
from c3parr.providers.base import Health, ModelDownload, ProviderInfo, Requirements

log = logging.getLogger("c3parr.asr")


def default_cache_dir() -> Path:
    """O mesmo cache do `Settings` (variável `C3PARR_CACHE_DIR`), sem carregar o `Settings`."""
    env = os.environ.get("C3PARR_CACHE_DIR")
    return Path(env).expanduser() if env else Path.home() / ".cache" / "c3parr"


MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{name}.bin"
VAD_URL = "https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v5.1.2.bin"
SPEED_FILE = "whisper-cpp-speed.json"
_BACKEND = re.compile(r"backend_init_gpu: using (\S+?)\d* backend")
_PROGRESS = re.compile(r"progress\s*=\s*(\d+)%")


class WhisperCppConfig(BaseModel):
    binary: str = "whisper-cli"
    model: str = "large-v3-turbo-q8_0"
    model_dir: Path = Field(default_factory=lambda: default_cache_dir() / "models")
    """No cache do c3parr (`C3PARR_CACHE_DIR`): no Docker, fica no volume ao atualizar."""
    device: Literal["auto", "cpu"] = "auto"
    """`auto`: o acelerador com que o binário foi feito (Metal, Vulkan, CUDA); `cpu`: força CPU."""
    threads: int = 0
    """0 = um por núcleo físico (contar hyperthread piorou 2,6x no i7)."""
    low_priority: bool = True
    beam_size: int = 5
    vad_min_silence_ms: int = 500
    vad_pad_ms: int = 300


INFO = ProviderInfo(
    id="whisper-cpp",
    interface=transcription.INTERFACE,
    version="1",
    config_schema=WhisperCppConfig,
    capabilities=frozenset({"word_timestamps", "language_detection"}),
    requirements=Requirements(
        gpu="opcional", models=(ModelDownload(name="ggml-large-v3-turbo-q8_0", approx_mb=874),)
    ),
    description="whisper.cpp (Metal / Vulkan / CUDA / CPU), large-v3-turbo",
)


def physical_cores() -> int:
    """Núcleos físicos (sem hyperthread). Em ARM grande+pequeno conta todos: somam ~4%."""
    try:
        if platform.system() == "Darwin":
            out = subprocess.run(
                ["sysctl", "-n", "hw.physicalcpu"], capture_output=True, text=True, check=True
            )
            return max(1, int(out.stdout.strip()))
        cores = set()
        phys = None
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("physical id"):
                phys = line.split(":")[1].strip()
            elif line.startswith("core id"):
                cores.add((phys, line.split(":")[1].strip()))
        if cores:
            return len(cores)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return max(1, os.cpu_count() or 1)


def _fetch(url: str, dest: Path) -> Path:
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    log.info("downloading %s", url)
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
            r.raise_for_status()
            with tmp.open("wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
    except httpx.HTTPError as e:
        raise TransientFailure(f"could not download {dest.name}: {e}", provider=INFO.id) from e
    tmp.replace(dest)
    return dest


def choose_language(request: TranscriptionRequest, audio: AudioRef) -> str:
    """`--src` manda; senão o original do TMDb, a menos que a faixa diga outra língua (dublado)."""
    if request.language:
        return request.language.split("-")[0].lower()
    hint = (request.language_hint or "").split("-")[0].lower() or None
    tag = (normalize_lang(audio.stream_lang) or "").split("-")[0].lower() or None
    if hint and tag and tag != hint:
        return tag
    return hint or tag or "auto"


def _clock(ms: float) -> float:
    return round(float(ms) / 1000.0, 3)


def parse_output(data: dict[str, Any], lang: str, flags_cfg: FasterWhisperConfig) -> list[Segment]:
    segments: list[Segment] = []
    for entry in data.get("transcription", []):
        text = (entry.get("text") or "").strip()
        if not text:
            continue
        start, end = _clock(entry["offsets"]["from"]), _clock(entry["offsets"]["to"])
        tokens = [
            (
                t["text"],
                _clock(t["offsets"]["from"]),
                _clock(t["offsets"]["to"]),
                float(t.get("p", 1)),
            )
            for t in entry.get("tokens", [])
            if t.get("text") and not t["text"].strip().startswith("[_")
        ]
        if tokens:
            # com VAD, o tempo dos tokens não conta o que o VAD cortou (ex.: música no começo);
            # o da fala conta. Desloca os tokens para o tempo real da fala.
            shift = start - tokens[0][1]
            if abs(shift) > 0.5:
                tokens = [
                    (txt, min(end, max(start, a + shift)), min(end, max(start, b + shift)), p)
                    for txt, a, b, p in tokens
                ]
        probs = [max(1e-6, p) for *_, p in tokens] or [1.0]
        avg_logprob = sum(math.log(p) for p in probs) / len(probs)
        raw = text.encode("utf-8")
        compression = len(raw) / max(1, len(zlib.compress(raw)))
        segments.append(
            Segment(
                id=len(segments),
                start=start,
                end=max(end, start + 0.1),
                text=text,
                lang=lang,
                words=merge_tokens(tokens),
                flags=flags_for(text, start, end, avg_logprob, compression, flags_cfg),
            )
        )
    return segments


class WhisperCppTranscriber:
    info = INFO

    def __init__(self, config: WhisperCppConfig) -> None:
        self.config = config
        self.last_backend: str | None = None

    def _binary(self) -> str:
        found = shutil.which(self.config.binary)
        if found is None and Path(self.config.binary).exists():
            found = self.config.binary
        if found is None:
            raise Unavailable(
                f"{self.config.binary} not found (brew install whisper.cpp, or the Docker image "
                "with whisper.cpp)",
                provider=INFO.id,
            )
        return found

    def model_path(self) -> Path:
        return self.config.model_dir / f"ggml-{self.config.model}.bin"

    def health(self) -> Health:
        try:
            self._binary()
        except Unavailable as e:
            return Health(ok=False, detail=e.message)
        if not self.model_path().exists():
            return Health(ok=True, detail="model downloads on first use (~870 MB)")
        return Health(ok=True)

    def transcribe(
        self, audio: AudioRef, request: TranscriptionRequest, progress: Callable[[float, str], None]
    ) -> Transcript:
        c = self.config
        binary = self._binary()
        progress(0.0, "preparing the model")
        model = _fetch(MODEL_URL.format(name=c.model), self.model_path())
        vad = _fetch(VAD_URL, c.model_dir / "ggml-silero-v5.1.2.bin")
        lang = choose_language(request, audio)
        threads = c.threads or physical_cores()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "out"
            cmd = [
                binary, "-m", str(model), "-f", audio.path, "-l", lang, "-t", str(threads),
                "-bs", str(c.beam_size), "-mc", "0", "--vad", "-vm", str(vad),
                "-vsd", str(c.vad_min_silence_ms), "-vp", str(c.vad_pad_ms),
                "-ojf", "-of", str(base), "-pp",
            ]  # fmt: skip
            if c.device == "cpu":
                cmd.append("-ng")
            t0 = time.monotonic()
            stderr_tail = self._run(cmd, progress)
            seconds = time.monotonic() - t0
            out = base.with_suffix(".json")
            if not out.exists():
                msg = f"whisper-cli produced no output: {stderr_tail[-400:]}"
                raise TransientFailure(msg, provider=INFO.id)
            data = json.loads(out.read_text(encoding="utf-8", errors="replace"))
        detected = (data.get("result") or {}).get("language") or lang
        segments = parse_output(data, detected, FasterWhisperConfig())
        self._record_speed(seconds, audio.duration)
        progress(1.0, f"{len(segments)} lines in {detected} ({self.last_backend or 'CPU'})")
        return Transcript(
            segments=tuple(segments),
            language=detected,
            language_prob=1.0,
            model=f"whisper.cpp {c.model}",
            audio_duration=audio.duration,
        )

    def _run(self, cmd: list[str], progress: Callable[[float, str], None]) -> str:
        nice = self.config.low_priority and os.name == "posix"
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            preexec_fn=(lambda: os.nice(10)) if nice else None,
        )
        tail: list[str] = []
        assert proc.stderr is not None
        for line in proc.stderr:
            tail = [*tail, line][-40:]
            if m := _BACKEND.search(line):
                self.last_backend = m.group(1)
            elif "no GPU found" in line and self.last_backend is None:
                self.last_backend = "CPU"
            elif m := _PROGRESS.search(line):
                progress(min(0.99, int(m.group(1)) / 100), f"transcribing ({self.last_backend})")
        if proc.wait() != 0:
            msg = f"whisper-cli exited with {proc.returncode}: {''.join(tail)[-400:]}"
            raise TransientFailure(msg, provider=INFO.id)
        return "".join(tail)

    def _record_speed(self, seconds: float, duration: float) -> None:
        """Guarda a velocidade medida aqui, para o aviso "ASR na CPU: ~X min por hora de vídeo"."""
        if duration < 30:
            return
        info = {
            "backend": self.last_backend or "CPU",
            "seconds_per_minute": round(seconds / (duration / 60), 2),
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        with contextlib.suppress(OSError):
            (self.config.model_dir / SPEED_FILE).write_text(json.dumps(info))


def measured_speed(model_dir: Path | None = None) -> dict[str, Any] | None:
    path = (model_dir or WhisperCppConfig().model_dir) / SPEED_FILE
    try:
        return dict(json.loads(path.read_text()))
    except (OSError, ValueError):
        return None


def register() -> None:
    transcription.FACTORY.register(INFO, WhisperCppTranscriber)
