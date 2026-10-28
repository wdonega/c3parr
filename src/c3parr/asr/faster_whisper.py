"""Provider `faster-whisper` (ADR 0001): Whisper large-v3-turbo int8 em CPU, VAD Silero e idioma
detectado por trecho de fala. Importa as dependências pesadas só ao transcrever.
"""

import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from c3parr.core.errors import InvalidInput, Unavailable
from c3parr.core.models import LanguageSpan, Segment, SegmentFlag, Transcript, Word
from c3parr.interfaces import transcription
from c3parr.interfaces.transcription import AudioRef, TranscriptionRequest
from c3parr.providers.base import Health, ModelDownload, ProviderInfo, Requirements

SR = 16000
KNOWN_HALLUCINATIONS = re.compile(
    r"sous-titr|amara\.org|merci d'avoir regard|subt[ií]tulos|legendas pela comunidade|"
    r"thanks for watching|subtitles by",
    re.IGNORECASE,
)


class FasterWhisperConfig(BaseModel):
    model: str = "large-v3-turbo"
    device: str = "cpu"
    compute_type: str = "int8"
    cpu_threads: int = 0
    beam_size: int = 5
    vad_min_silence_ms: int = Field(500, ge=100)
    lid_min_prob: float = Field(0.8, ge=0.0, le=1.0)
    """Confiança mínima para transcrever um trecho num idioma diferente do principal."""
    lid_region_max_s: float = 28.0
    low_logprob: float = -1.0
    max_compression_ratio: float = 2.4
    max_chars_per_s: float = 38.0
    """Diálogo rápido real chega a ~31 caracteres/s (medido no Incendies); acima disso, suspeita."""
    use_hotwords: bool = False
    """Desligado: no Incendies os nomes do elenco como hotwords juntaram 48 falas em 15 (mesmas
    palavras, sem pontuação nem cortes) e não melhoraram os nomes. A correção de nomes depois do
    ASR (`metadata.names`) faz esse papel."""


INFO = ProviderInfo(
    id="faster-whisper",
    interface=transcription.INTERFACE,
    version="5",
    config_schema=FasterWhisperConfig,
    capabilities=frozenset({"word_timestamps", "language_detection", "per_span_language"}),
    requirements=Requirements(
        extras=("asr",),
        models=(
            ModelDownload(name="mobiuslabsgmbh/faster-whisper-large-v3-turbo", approx_mb=1600),
        ),
    ),
    description="Whisper via CTranslate2 on CPU, with VAD and per-span language",
)


def _whisper_lang(lang: str) -> str:
    return lang.split("-")[0].lower()


def merge_regions(
    regions: list[tuple[float, float]], max_len: float, max_gap: float = 1.5
) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for s, e in regions:
        if out and s - out[-1][1] <= max_gap and e - out[-1][0] <= max_len:
            out[-1][1] = e
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def sample_regions(
    regions: list[tuple[float, float]], n: int = 15, min_len: float = 2.0
) -> list[tuple[float, float]]:
    """O trecho de fala mais longo de cada uma de `n` fatias do áudio (início, meio, fim...)."""
    if not regions:
        return []
    t0, t1 = regions[0][0], regions[-1][1]
    width = (t1 - t0) / n or 1.0
    picked = []
    for k in range(n):
        lo, hi = t0 + k * width, t0 + (k + 1) * width
        inside = [r for r in regions if lo <= r[0] < hi and r[1] - r[0] >= min_len]
        if inside:
            picked.append(max(inside, key=lambda r: r[1] - r[0]))
    return picked


def majority_language(
    spans: list[LanguageSpan], hint: str | None = None, hint_share: float = 0.25
) -> str:
    """Idioma com mais fala (duração x confiança) nos trechos amostrados.

    Com palpite (idioma original no TMDb), ele vence se tiver pelo menos `hint_share` do total:
    filme com cenas longas em outra língua não troca o idioma principal, mas áudio dublado sim.
    """
    by_lang: dict[str, float] = {}
    for sp in spans:
        by_lang[sp.lang] = by_lang.get(sp.lang, 0.0) + (sp.end - sp.start) * sp.prob
    hint = _whisper_lang(hint) if hint else None
    total = sum(by_lang.values()) or 1.0
    if hint and by_lang.get(hint, 0.0) / total >= hint_share:
        return hint
    return max(by_lang, key=lambda k: by_lang[k])


SUSPECT_FLAGS = frozenset({"alucinacao_conhecida", "baixa_confianca"})


def uncovered_regions(
    regions: list[tuple[float, float]],
    segments: list[Segment],
    min_cover: float = 0.5,
    min_len: float = 1.0,
) -> list[tuple[float, float]]:
    """Trechos com fala (VAD) que o ASR não cobriu com texto confiável."""
    good = [(s.start, s.end) for s in segments if not s.flags & SUSPECT_FLAGS]
    out = []
    for start, end in regions:
        length = end - start
        if length < min_len:
            continue
        covered = sum(max(0.0, min(end, e) - max(start, s)) for s, e in good)
        if covered / length < min_cover:
            out.append((start, end))
    return out


def merge_tokens(tokens: list[tuple[str, float, float, float]]) -> tuple[Word, ...]:
    """Cola no anterior o token que não começa com espaço ("J" + "'avoue" -> "J'avoue")."""
    words: list[Word] = []
    for raw, start, end, prob in tokens:
        text = raw.strip()
        if not text:
            continue
        if words and not raw[:1].isspace():
            prev = words[-1]
            conf = round(min(prev.conf, prob) if prev.conf is not None else prob, 3)
            words[-1] = Word(w=prev.w + text, start=prev.start, end=round(end, 3), conf=conf)
        else:
            start, end, conf = round(start, 3), round(end, 3), round(prob, 3)
            words.append(Word(w=text, start=start, end=end, conf=conf))
    return tuple(words)


def flags_for(
    text: str,
    start: float,
    end: float,
    avg_logprob: float,
    compression: float,
    cfg: FasterWhisperConfig,
) -> frozenset[SegmentFlag]:
    flags: set[SegmentFlag] = set()
    if avg_logprob < cfg.low_logprob:
        flags.add("baixa_confianca")
    if compression > cfg.max_compression_ratio:
        flags.add("repeticao")
    if end > start and len(text) / (end - start) > cfg.max_chars_per_s:
        flags.add("fala_rapida")
    if KNOWN_HALLUCINATIONS.search(text):
        flags.add("alucinacao_conhecida")
    return frozenset(flags)


class FasterWhisperTranscriber:
    info = INFO

    def __init__(self, config: FasterWhisperConfig) -> None:
        self.config = config
        self._model: Any = None

    def health(self) -> Health:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return Health(ok=False, detail="extra 'asr' not installed")
        return Health(ok=True)

    def _load(self) -> Any:
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as e:
                raise Unavailable("extra 'asr' not installed", provider=INFO.id) from e
            c = self.config
            self._model = WhisperModel(
                c.model, device=c.device, compute_type=c.compute_type, cpu_threads=c.cpu_threads
            )
        return self._model

    def transcribe(
        self, audio: AudioRef, request: TranscriptionRequest, progress: Callable[[float, str], None]
    ) -> Transcript:
        import soundfile as sf
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        c = self.config
        model = self._load()
        samples, sr = sf.read(audio.path, dtype="float32")
        if sr != SR:
            raise InvalidInput(f"audio must be {SR} Hz, got {sr}")
        duration = len(samples) / SR

        vad = get_speech_timestamps(
            samples, VadOptions(min_silence_duration_ms=c.vad_min_silence_ms)
        )
        regions = merge_regions([(t["start"] / SR, t["end"] / SR) for t in vad], c.lid_region_max_s)
        spans: list[LanguageSpan] = []

        def detect(start: float, end: float) -> LanguageSpan:
            lang, prob, _ = model.detect_language(samples[int(start * SR) : int(end * SR)])
            span = LanguageSpan(start=round(start, 2), end=round(end, 2), lang=lang,
                                prob=round(prob, 3))  # fmt: skip
            spans.append(span)
            return span

        if request.language:
            main = _whisper_lang(request.language)
        else:
            progress(0.0, "detecting the language at several points")
            for s, e in sample_regions(regions):
                detect(s, e)
            if not spans:
                return Transcript(segments=(), language="und", model=c.model,
                                  audio_duration=duration)  # fmt: skip
            main = majority_language(spans, request.language_hint)

        def report(end: float, count: int) -> None:
            progress(min(0.9, 0.9 * end / duration), f"{count} lines in {main}")

        segments = self._segments(samples, 0.0, duration, main, request, report)

        # Trechos com fala que o idioma principal não cobriu: conferir o idioma e refazer.
        suspicious = uncovered_regions(regions, segments)
        for n, (s, e) in enumerate(suspicious):
            progress(0.9 + 0.09 * n / max(1, len(suspicious)), "checking spans in another language")
            span = detect(s, e)
            if span.lang == main or span.prob < c.lid_min_prob:
                continue
            lo, hi = max(0.0, s - 0.3), min(duration, e + 0.3)
            redone = self._segments(samples, lo, hi, span.lang, request, lambda *_: None)
            if redone:
                segments = [x for x in segments if x.end <= s or x.start >= e] + redone

        segments.sort(key=lambda x: x.start)
        segments = [x.model_copy(update={"id": i}) for i, x in enumerate(segments)]
        speech = sum(x.end - x.start for x in segments) or 1.0
        main_prob = sum(x.end - x.start for x in segments if x.lang == main) / speech
        return Transcript(
            segments=tuple(segments),
            language=main,
            language_prob=round(main_prob, 3),
            language_spans=tuple(sorted(spans, key=lambda sp: sp.start)),
            model=c.model,
            audio_duration=round(duration, 3),
        )

    def _segments(
        self,
        samples: Any,
        lo: float,
        hi: float,
        lang: str,
        request: TranscriptionRequest,
        report: Callable[[float, int], None],
    ) -> list[Segment]:
        c = self.config
        segs, _ = self._load().transcribe(
            samples[int(lo * SR) : int(hi * SR)],
            language=lang,
            beam_size=c.beam_size,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": c.vad_min_silence_ms},
            condition_on_previous_text=False,
            hotwords=(" ".join(request.hotwords) or None) if c.use_hotwords else None,
        )
        out: list[Segment] = []
        for s in segs:
            text = s.text.strip()
            if not text:
                continue
            start, end = lo + s.start, lo + s.end
            tokens = [(w.word, lo + w.start, lo + w.end, w.probability) for w in s.words or []]
            out.append(
                Segment(
                    id=len(out),
                    start=round(start, 3),
                    end=round(end, 3),
                    text=text,
                    lang=lang,
                    words=merge_tokens(tokens),
                    flags=flags_for(text, start, end, s.avg_logprob, s.compression_ratio, c),
                )
            )
            report(end, len(out))
        return out


def register() -> None:
    transcription.FACTORY.register(INFO, FasterWhisperTranscriber)
