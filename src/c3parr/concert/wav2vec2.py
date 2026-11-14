"""Provider `wav2vec2` de encaixe: acha onde cada linha de um texto é cantada na voz isolada.

Um modelo de fonemas (wav2vec2, inglês) dá, a cada 20 ms, a probabilidade de cada letra; o
alinhamento forçado acha o caminho das letras da linha por essas probabilidades. Entre uma linha
e outra entra uma coluna "qualquer som" (solo, plateia, fala), para a linha não ser esticada sobre
o que não é dela. Só inglês: as letras do modelo são as do inglês.

A escuta de uma música inteira (a "emissão") leva 1 minuto a cada 15 de música no processador e
fica guardada: o encaixe das linhas repetidas usa a mesma. O modelo escuta em janelas, e o
tamanho da janela segue a memória livre (`memory.plan`): 30 s ou 10 s. Os pesos (1,3 GB) são lidos
direto do arquivo, sem a cópia que dobrava a memória ao carregar (2,7 GB → 1,5 GB, escuta idêntica).
"""

import hashlib
import logging
import re
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from c3parr.concert import device as hw
from c3parr.concert import memory, worker
from c3parr.interfaces import lyric_fit
from c3parr.interfaces.lyric_fit import Placed, PlaceRequest
from c3parr.interfaces.transcription import AudioRef
from c3parr.processor import client as remote
from c3parr.processor.client import ProcessorRef
from c3parr.providers.base import Health, ModelDownload, ProviderInfo, Requirements

log = logging.getLogger("c3parr.concert")
RATE = 16000
CONTEXT_S = 1.0
WEIGHTS_URL = "https://download.pytorch.org/torchaudio/models/"
LANGUAGES = frozenset({"en"})


class Wav2Vec2Config(BaseModel):
    device: Literal["auto", "cpu"] = "auto"
    """`auto`: a GPU NVIDIA se houver, senão o processador; `cpu`: força o processador."""
    model_dir: Path = Field(default_factory=hw.default_model_dir)
    memory_limit: memory.Limit = "auto"
    """O teto de memória (Settings → Subtitles → Concerts): `2`, `3` ou `auto`."""
    isolate: bool = True
    """Roda num processo filho, que fecha no fim e devolve toda a memória (ver `worker`)."""
    processor: ProcessorRef = Field(default_factory=ProcessorRef)
    """Com URL, encaixa no c3parr-cuda-processor (Settings → Integrations)."""


INFO = ProviderInfo(
    id="wav2vec2",
    interface=lyric_fit.INTERFACE,
    version="2",
    config_schema=Wav2Vec2Config,
    capabilities=LANGUAGES,
    requirements=Requirements(
        gpu="opcional",
        models=(ModelDownload(name="wav2vec2-large-960h-lv60-self", approx_mb=1200),),
        extras=("concert",),
    ),
    description="wav2vec2 forced alignment (English) on CUDA or CPU",
)


def tokens() -> dict[str, int]:
    import torchaudio

    labels = torchaudio.pipelines.WAV2VEC2_ASR_LARGE_LV60K_960H.get_labels()
    return {c: i for i, c in enumerate(labels)}


def place(emission: Any, per_s: float, start: float, lines: Sequence[str]) -> list[Placed]:
    """Acha onde cada linha (na ordem dada) cabe melhor em `emission`, que começa em `start`.
    A última coluna de `emission` é a "qualquer som"."""
    import numpy as np
    import torch
    import torchaudio.functional as F

    table = tokens()
    star = emission.shape[1] - 1
    seq, owner, word_of = [star], [-1], [-1]
    for n, line in enumerate(lines):
        words = [w for w in re.sub(r"[^a-z' ]", " ", line.lower().replace("-", " ")).split()
                 if w.strip("'")]  # fmt: skip
        for k, word in enumerate(words):
            if k:
                seq.append(table["|"])
                owner.append(n)
                word_of.append(-1)
            for c in word.upper():
                if c in table:
                    seq.append(table[c])
                    owner.append(n)
                    word_of.append(k)
        seq.append(star)
        owner.append(-1)
        word_of.append(-1)
    if emission.shape[0] < len(seq) + 10:
        return []
    aligned, scores = F.forced_align(
        emission.unsqueeze(0), torch.tensor([seq], dtype=torch.int32), blank=0
    )
    spans = F.merge_tokens(aligned[0], scores[0].exp())
    out: list[Placed] = []
    for n in range(len(lines)):
        mine = [s for s, o in zip(spans, owner, strict=True) if o == n]
        if not mine:
            continue
        frames = sum(s.end - s.start for s in mine)
        score = sum(s.score * (s.end - s.start) for s in mine) / max(1, frames)
        by_word: dict[int, list[float]] = {}
        for s, o, w in zip(spans, owner, word_of, strict=True):
            if o == n and w >= 0:
                by_word.setdefault(w, []).append(float(s.score))
        steady = float(np.median([np.mean(v) for v in by_word.values()])) if by_word else 0.0
        out.append(
            Placed(
                line=n,
                start=start + mine[0].start / per_s,
                end=start + mine[-1].end / per_s,
                score=float(score),
                steady=steady,
            )
        )
    return out


def load_model(model_dir: Path, where: str) -> Any:
    """O modelo com os pesos lidos direto do arquivo (mapeado), em vez de carregados e copiados.
    Usa partes internas do torchaudio; se elas mudarem, vale o carregamento comum."""
    import torch
    import torchaudio

    bundle = torchaudio.pipelines.WAV2VEC2_ASR_LARGE_LV60K_960H
    try:
        path = model_dir / "hub" / "checkpoints" / bundle._path
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            torch.hub.download_url_to_file(WEIGHTS_URL + bundle._path, str(path))
        state = torch.load(path, mmap=True, weights_only=True)
        for key in ("aux.weight", "aux.bias"):  # as saídas que o próprio torchaudio descarta
            rows = state[key]
            keep = [i for i in range(rows.size(0)) if i not in bundle._remove_aux_axis]
            state[key] = torch.stack([rows[i] for i in keep])
        inner = torchaudio.models.wav2vec2_model(**bundle._params)
        inner.load_state_dict(state, assign=True)
        normalize = bool(bundle._normalize_waveform)
    except Exception:
        log.warning("wav2vec2: mapped loading failed, loading the usual way", exc_info=True)
        return bundle.get_model().to(where).eval()

    class Normalized(torch.nn.Module):  # type: ignore[misc]
        """Como o torchaudio faz: a onda normalizada antes de entrar no modelo."""

        def __init__(self) -> None:
            super().__init__()
            self.inner = inner

        def forward(self, wave: Any) -> Any:
            if normalize:
                wave = torch.nn.functional.layer_norm(wave, wave.shape)
            return self.inner(wave)

    return Normalized().to(where).eval()


class Wav2Vec2Placer:
    info = INFO

    def __init__(self, config: Wav2Vec2Config) -> None:
        self.config = config
        self._model: Any = None
        self._where = "cpu"

    def health(self) -> Health:  # sem carregar o torch no processo de quem pergunta
        if self.config.processor.url:
            return Health(ok=True, detail="on the CUDA processor")
        if not hw.installed():
            return Health(ok=False, detail="needs the extra 'concert' (uv sync --extra concert)")
        return Health(ok=True, detail="NVIDIA GPU when there is one, the CPU otherwise")

    def _load(self) -> Any:
        if self._model is None:  # 1,2 GB: só quando falta calcular a escuta de algum trecho
            hw.use_model_dir(self.config.model_dir)
            self._where = hw.pick(self.config.device)
            self._model = load_model(self.config.model_dir, self._where)
        return self._model

    def _emission(self, audio: AudioRef, start: float, end: float, cache: Path,
                  progress: Callable[[float, str], None]) -> tuple[Any, float]:  # fmt: skip
        """A probabilidade de cada letra a cada 20 ms de [start, end], mais a coluna "qualquer
        som". Fica guardada em `cache`."""
        import numpy as np
        import soundfile as sf
        import torch

        # a janela entra no nome: a escuta com outra janela não é a mesma nas emendas
        window_s = memory.plan(self.config.memory_limit, memory.free_gb(), INFO.id).window_s
        key = f"{audio.sha256}|{start:.2f}|{end:.2f}|{window_s:g}"
        path = cache / f"emission-{hashlib.sha1(key.encode()).hexdigest()[:16]}.npy"
        if path.exists():
            saved = torch.from_numpy(np.load(path).astype("float32"))
            return saved, saved.shape[0] / (end - start)
        model = self._load()
        t0 = time.monotonic()
        pieces = []
        at = start
        with sf.SoundFile(audio.path) as f:
            if f.samplerate != RATE:
                raise ValueError(f"expected {RATE} Hz audio, got {f.samplerate}")
            while at < end:
                lead = min(CONTEXT_S, at - start) if at > start else 0.0
                stop = min(end, at + window_s)
                tail = min(CONTEXT_S, end - stop)
                f.seek(int((at - lead) * RATE))
                data = f.read(int((stop + tail) * RATE) - int((at - lead) * RATE), dtype="float32")
                if len(data) < RATE // 10:
                    break
                wave = torch.from_numpy(data).unsqueeze(0).to(self._where)
                with torch.inference_mode():
                    out, _ = model(wave)
                out = torch.log_softmax(out[0], dim=-1).cpu()
                per_s = out.shape[0] / (wave.shape[1] / RATE)
                keep_from, keep_to = int(lead * per_s), out.shape[0] - int(tail * per_s)
                pieces.append(out[keep_from:keep_to])
                at = stop
                progress(min(0.9, (at - start) / max(1.0, end - start)), "listening for the lyrics")
        if not pieces:
            empty = torch.zeros(0, len(tokens()) + 1)
            return empty, 50.0
        joined = torch.cat(pieces)
        joined = torch.cat((joined, torch.zeros(joined.shape[0], 1)), dim=1)
        cache.mkdir(parents=True, exist_ok=True)
        np.save(path, joined.numpy().astype("float16"))
        hw.record_speed(self.config.model_dir, "fit", self._where, time.monotonic() - t0,
                            end - start)  # fmt: skip
        return joined, joined.shape[0] / (end - start)

    def place(
        self, audio: AudioRef, request: PlaceRequest, progress: Callable[[float, str], None]
    ) -> tuple[tuple[Placed, ...], ...]:
        if self.config.processor.url:  # a memória que conta é a do processador
            params = self.config.model_dump(mode="json")
            return remote.place(self.config.processor, params, audio, request, progress)
        if self.config.isolate:
            memory.plan(
                self.config.memory_limit, memory.free_gb(), INFO.id
            )  # recusa antes de abrir
            params = self.config.model_dump(mode="json")
            args = {
                "audio": audio.model_dump(mode="json"),
                "request": request.model_dump(mode="json"),
            }
            done = worker.call(lyric_fit.INTERFACE, INFO.id, params, "place", args, progress)
            return tuple(tuple(Placed.model_validate(p) for p in group) for group in done)
        origin, end = request.window
        emission, per_s = self._emission(audio, origin, end, Path(request.cache_dir), progress)
        out = []
        for span in request.spans:
            lo = max(0, int((span.start - origin) * per_s))
            piece = emission[lo : int((span.end - origin) * per_s)]
            out.append(tuple(place(piece, per_s, span.start, span.lines)))
        return tuple(out)


def register() -> None:
    lyric_fit.FACTORY.register(INFO, Wav2Vec2Placer)
