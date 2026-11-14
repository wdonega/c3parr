"""Provider `demucs` de separação: tira a banda e deixa a voz, e marca onde há voz.

Sem a voz isolada o resto não funciona. Medido em 10 min de um show: o encaixe da letra acerta 20
de 30 linhas com a voz isolada e 2 com a mistura, e o detector de voz descarta o canto na mistura.
Usa a GPU NVIDIA quando há (numa RTX 3060: 36x o tempo real, 550 MB de memória de vídeo); no
resto, o processador (num M3 Pro: 3,6x o tempo real). O áudio entra em pedaços, e o tamanho do
pedaço segue a memória que a máquina tem livre (`memory.plan`): de 15 min a 1 min.
"""

import gc
import logging
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from c3parr.audio import ffmpeg
from c3parr.concert import device as hw
from c3parr.concert import memory, worker
from c3parr.core.errors import InvalidInput
from c3parr.engine.cache import file_sha256
from c3parr.interfaces import separation
from c3parr.interfaces.separation import SeparationRequest, Voice
from c3parr.processor import client as remote
from c3parr.processor.client import ProcessorRef
from c3parr.providers.base import Health, ModelDownload, ProviderInfo, Requirements

log = logging.getLogger("c3parr.concert")
RATE = 16000
VAD_CHUNK_S = 1800.0


class DemucsConfig(BaseModel):
    model: str = "htdemucs"
    device: Literal["auto", "cpu"] = "auto"
    """`auto`: a GPU NVIDIA se houver, senão o processador; `cpu`: força o processador."""
    memory_limit: memory.Limit = "auto"
    """O teto de memória (Settings → Subtitles → Concerts): `2`, `3` ou `auto`."""
    isolate: bool = True
    """Roda num processo filho, que fecha no fim e devolve toda a memória (ver `worker`)."""
    model_dir: Path = Field(default_factory=hw.default_model_dir)
    """No cache do c3parr (`C3PARR_CACHE_DIR`): no Docker, fica no volume ao atualizar."""
    processor: ProcessorRef = Field(default_factory=ProcessorRef)
    """Com URL, separa no c3parr-cuda-processor (Settings → Integrations)."""


INFO = ProviderInfo(
    id="demucs",
    interface=separation.INTERFACE,
    version="2",
    config_schema=DemucsConfig,
    requirements=Requirements(
        gpu="opcional",
        models=(ModelDownload(name="htdemucs", approx_mb=80),),
        extras=("concert",),
    ),
    description="Demucs (htdemucs) on CUDA or CPU, plus Silero voice detection",
)


def voice_spans(path: Path) -> tuple[tuple[float, float], ...]:
    """Onde há voz na faixa isolada (canto ou fala), lida em pedaços de meia hora."""
    import soundfile as sf
    import torch
    from silero_vad import get_speech_timestamps, load_silero_vad

    model = load_silero_vad()
    out: list[tuple[float, float]] = []
    with sf.SoundFile(str(path)) as f:
        at = 0.0
        while True:
            data = f.read(int(VAD_CHUNK_S * RATE), dtype="float32")
            if len(data) == 0:
                break
            found = get_speech_timestamps(
                torch.from_numpy(data),
                model,
                return_seconds=True,
                min_silence_duration_ms=400,
                speech_pad_ms=150,
            )
            for s in found:
                a, b = at + float(s["start"]), at + float(s["end"])
                if out and a - out[-1][1] < 0.4:  # a voz que atravessa a emenda de dois pedaços
                    out[-1] = (out[-1][0], b)
                else:
                    out.append((a, b))
            at += len(data) / RATE
    return tuple((round(a, 2), round(b, 2)) for a, b in out)


class DemucsSeparator:
    info = INFO

    def __init__(self, config: DemucsConfig) -> None:
        self.config = config
        self._model: Any = None

    def health(self) -> Health:  # sem carregar o torch no processo de quem pergunta
        if self.config.processor.url:
            return Health(ok=True, detail="on the CUDA processor")
        if not hw.installed():
            return Health(ok=False, detail="needs the extra 'concert' (uv sync --extra concert)")
        return Health(ok=True, detail="NVIDIA GPU when there is one, the CPU otherwise")

    def _load(self) -> Any:
        if self._model is None:
            from demucs.pretrained import get_model

            hw.use_model_dir(self.config.model_dir)
            self._model = get_model(self.config.model).eval()
        return self._model

    def separate(self, request: SeparationRequest, progress: Callable[[float, str], None]) -> Voice:
        if self.config.processor.url:  # a memória que conta é a do processador
            params = self.config.model_dump(mode="json")
            return remote.separate(self.config.processor, params, request, progress)
        tier = memory.plan(self.config.memory_limit, memory.free_gb(), INFO.id)
        if self.config.isolate:
            params = self.config.model_dump(mode="json")
            args = {"request": request.model_dump(mode="json")}
            done = worker.call(separation.INTERFACE, INFO.id, params, "separate", args, progress)
            return Voice.model_validate(done)
        import soundfile as sf
        import torch
        from demucs.apply import apply_model
        from torchaudio.functional import resample

        video = Path(request.video)
        total = ffmpeg.probe(video).duration
        if total <= 0:
            raise InvalidInput(f"{video.name} has no duration", provider=INFO.id)
        where = hw.pick(self.config.device)
        progress(0.0, "preparing the voice separation model")
        model = self._load()
        model.to(where)
        voice = model.sources.index("vocals")
        dest = Path(request.dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(".part.wav")
        chunk_s = tier.piece_s
        t0 = time.monotonic()
        with (
            tempfile.TemporaryDirectory() as work,
            sf.SoundFile(str(part), "w", RATE, 1, "PCM_16") as joined,
        ):
            start = 0.0
            while start < total:
                piece = Path(work) / "chunk.wav"
                ffmpeg.extract_stereo(video, request.stream_index, start, chunk_s, piece,
                                      model.samplerate)  # fmt: skip
                data, _ = sf.read(str(piece), dtype="float32", always_2d=True)
                if len(data) < model.samplerate:  # menos de 1 s no fim: o modelo não aceita
                    joined.write(torch.zeros(int(len(data) * RATE / model.samplerate)).numpy())
                    break
                wav = torch.from_numpy(data.T.copy())
                ref = wav.mean(0)
                mean, std = ref.mean(), ref.std() + 1e-8
                with torch.no_grad():
                    sources = apply_model(model, ((wav - mean) / std)[None], device=where,
                                          split=True, overlap=0.25, progress=False)[0]  # fmt: skip
                vocals = sources[voice].cpu() * std + mean
                mono = resample(vocals.mean(0, keepdim=True), model.samplerate, RATE)
                joined.write(mono[0].clamp(-1, 1).numpy())
                # o pedaço sai da memória antes de o seguinte entrar (medido: 1,3 GB a menos)
                del data, wav, ref, sources, vocals, mono
                gc.collect()
                start += chunk_s
                progress(min(0.9, start / total * 0.9), f"separating the voice ({where})")
        part.replace(dest)
        seconds = time.monotonic() - t0
        progress(0.92, "finding where there is voice")
        spans = voice_spans(dest)
        with sf.SoundFile(str(dest)) as f:
            duration = round(f.frames / RATE, 3)
        hw.record_speed(self.config.model_dir, "separation", where, seconds, duration)
        log.info("voice separated in %.0f s on %s (%.0f min of audio)", seconds, where,
                 duration / 60)  # fmt: skip
        progress(1.0, f"voice separated ({where})")
        return Voice(path=str(dest), sha256=file_sha256(dest), duration=duration, spans=spans,
                     device=where)  # fmt: skip


def register() -> None:
    separation.FACTORY.register(INFO, DemucsSeparator)
