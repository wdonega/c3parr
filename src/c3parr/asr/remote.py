"""Transcrição num servidor à parte (outro container, outra máquina com GPU).

Dois formatos de API, ambos com imagem Docker pronta:
- `remote-openai`: `POST /v1/audio/transcriptions` do OpenAI (whisper.cpp `whisper-server` com
  `--inference-path /v1/audio/transcriptions`, speaches, LocalAI);
- `asr-webservice`: `POST /asr` do whisper-asr-webservice, o mesmo que o Bazarr usa.

O áudio vai em FLAC (1 h de WAV 16 kHz tem ~115 MB; em FLAC, bem menos). O servidor não informa
progresso: a barra fica em "transcribing on the server" até a resposta chegar.
"""

import math
import shutil
import subprocess
import tempfile
import zlib
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import IO, Any, Literal

import httpx
from pydantic import BaseModel, Field, SecretStr

from c3parr.asr.faster_whisper import FasterWhisperConfig, flags_for, merge_tokens
from c3parr.asr.whisper_cpp import choose_language
from c3parr.audio.ffmpeg import normalize_lang
from c3parr.core.errors import TransientFailure, Unavailable
from c3parr.core.models import Segment, Transcript
from c3parr.interfaces import transcription
from c3parr.interfaces.transcription import AudioRef, TranscriptionRequest
from c3parr.providers.base import Health, ProviderInfo, Requirements
from c3parr.translate.languages import NAMES_EN

Flavor = Literal["openai", "asr-webservice"]
# os nomes que o Whisper devolve no formato OpenAI (tokenizer.LANGUAGES), mais os apelidos dele
_WHISPER_NAMES = {
    "english": "en", "chinese": "zh", "german": "de", "spanish": "es", "russian": "ru",
    "korean": "ko", "french": "fr", "japanese": "ja", "portuguese": "pt", "turkish": "tr",
    "polish": "pl", "catalan": "ca", "dutch": "nl", "arabic": "ar", "swedish": "sv",
    "italian": "it", "indonesian": "id", "hindi": "hi", "finnish": "fi", "vietnamese": "vi",
    "hebrew": "he", "ukrainian": "uk", "greek": "el", "malay": "ms", "czech": "cs",
    "romanian": "ro", "danish": "da", "hungarian": "hu", "tamil": "ta", "norwegian": "no",
    "thai": "th", "urdu": "ur", "croatian": "hr", "bulgarian": "bg", "lithuanian": "lt",
    "latin": "la", "maori": "mi", "malayalam": "ml", "welsh": "cy", "slovak": "sk",
    "telugu": "te", "persian": "fa", "latvian": "lv", "bengali": "bn", "serbian": "sr",
    "azerbaijani": "az", "slovenian": "sl", "kannada": "kn", "estonian": "et",
    "macedonian": "mk", "breton": "br", "basque": "eu", "icelandic": "is", "armenian": "hy",
    "nepali": "ne", "mongolian": "mn", "bosnian": "bs", "kazakh": "kk", "albanian": "sq",
    "swahili": "sw", "galician": "gl", "marathi": "mr", "punjabi": "pa", "sinhala": "si",
    "khmer": "km", "shona": "sn", "yoruba": "yo", "somali": "so", "afrikaans": "af",
    "occitan": "oc", "georgian": "ka", "belarusian": "be", "tajik": "tg", "sindhi": "sd",
    "gujarati": "gu", "amharic": "am", "yiddish": "yi", "lao": "lo", "uzbek": "uz",
    "faroese": "fo", "haitian creole": "ht", "pashto": "ps", "turkmen": "tk", "nynorsk": "nn",
    "maltese": "mt", "sanskrit": "sa", "luxembourgish": "lb", "myanmar": "my", "tibetan": "bo",
    "tagalog": "tl", "malagasy": "mg", "assamese": "as", "tatar": "tt", "hawaiian": "haw",
    "lingala": "ln", "hausa": "ha", "bashkir": "ba", "javanese": "jw", "sundanese": "su",
    "cantonese": "yue", "burmese": "my", "valencian": "ca", "flemish": "nl", "haitian": "ht",
    "letzeburgesch": "lb", "pushto": "ps", "panjabi": "pa", "moldavian": "ro", "moldovan": "ro",
    "sinhalese": "si", "castilian": "es", "mandarin": "zh",
}  # fmt: skip
_NAME_TO_CODE = _WHISPER_NAMES | {v.lower(): k for k, v in NAMES_EN.items()}


class RemoteASRConfig(BaseModel):
    base_url: str = ""
    """openai: termina em /v1; asr-webservice: a raiz do servidor (http://host:9000)."""
    model: str = "whisper-1"
    """Só no formato OpenAI; o whisper.cpp ignora (usa o modelo com que subiu)."""
    api_key: SecretStr | None = Field(None, json_schema_extra={"secret_env": "C3PARR_ASR_API_KEY"})
    timeout_s: float = 1800
    """Um filme inteiro numa GPU leva 2-3 min; numa CPU fraca, bem mais."""
    vad: bool = True
    """Sem VAD o Whisper alucina em trecho com música (decisão 0005)."""


INFO_OPENAI = ProviderInfo(
    id="remote-openai",
    interface=transcription.INTERFACE,
    version="1",
    config_schema=RemoteASRConfig,
    capabilities=frozenset({"word_timestamps", "language_detection"}),
    requirements=Requirements(network=True),
    description="OpenAI-compatible server (/v1/audio/transcriptions)",
)
INFO_WEBSERVICE = replace(
    INFO_OPENAI, id="asr-webservice", description="whisper-asr-webservice (/asr, as in Bazarr)"
)
REMOTE_IDS: dict[str, str] = {"openai": INFO_OPENAI.id, "asr-webservice": INFO_WEBSERVICE.id}


def _code(lang: str | None, fallback: str) -> str:
    """O OpenAI devolve o nome ("french"); o webservice, o código."""
    if not lang:
        return fallback
    low = lang.strip().lower()
    if low in _NAME_TO_CODE:
        return _NAME_TO_CODE[low]
    code = normalize_lang(low)
    # nome que não está na tabela não vira "código": o idioma pedido é melhor que "ukrainian"
    return code if code and len(code.split("-")[0]) <= 3 else fallback


def parse_response(data: dict[str, Any], lang: str, cfg: FasterWhisperConfig) -> list[Segment]:
    """Segmentos com tempo, nos dois formatos. Palavras vêm dentro do segmento (webservice,
    whisper.cpp) ou numa lista à parte (OpenAI), e aí entram no segmento pelo tempo."""
    loose = data.get("words") or []
    segments: list[Segment] = []
    for entry in data.get("segments") or []:
        text = (entry.get("text") or "").strip()
        if not text:
            continue
        start, end = round(float(entry["start"]), 3), round(float(entry["end"]), 3)
        words = entry.get("words") or [w for w in loose if start - 0.01 <= float(w["start"]) < end]
        # sem espaço no começo de nenhuma, são palavras inteiras (OpenAI); com espaço, tokens
        spaced = any(str(w.get("word", "")).startswith(" ") for w in words)
        tokens = [
            (
                w["word"] if spaced else " " + str(w["word"]).strip(),
                float(w["start"]),
                float(w["end"]),
                float(w.get("probability", 1)),
            )
            for w in words
            if str(w.get("word", "")).strip()
        ]
        if tokens:
            # whisper-server com VAD: o tempo das palavras não conta o que o VAD cortou (música
            # no começo), o do segmento conta. Desloca as palavras para o tempo do segmento.
            shift = start - tokens[0][1]
            if abs(shift) > 0.5:
                tokens = [
                    (txt, min(end, max(start, a + shift)), min(end, max(start, b + shift)), p)
                    for txt, a, b, p in tokens
                ]
        if "avg_logprob" in entry:
            avg_logprob = float(entry["avg_logprob"])
        else:
            probs = [max(1e-6, p) for *_, p in tokens] or [1.0]
            avg_logprob = sum(math.log(p) for p in probs) / len(probs)
        raw = text.encode("utf-8")
        compression = float(
            entry.get("compression_ratio") or len(raw) / max(1, len(zlib.compress(raw)))
        )
        segments.append(
            Segment(
                id=len(segments),
                start=start,
                end=max(end, start + 0.1),
                text=text,
                lang=lang,
                words=merge_tokens(tokens),
                flags=flags_for(text, start, end, avg_logprob, compression, cfg),
            )
        )
    return segments


def _flac(wav: Path, tmp: Path) -> Path:
    """FLAC para subir menos; sem ffmpeg vai o WAV mesmo."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return wav
    out = tmp / "audio.flac"
    cmd = [ffmpeg, "-v", "error", "-y", "-i", str(wav), "-c:a", "flac", str(out)]
    if subprocess.run(cmd, capture_output=True).returncode != 0:
        return wav
    return out


class RemoteTranscriber:
    def __init__(
        self,
        config: RemoteASRConfig,
        flavor: Flavor,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        self.flavor = flavor
        self.info = INFO_OPENAI if flavor == "openai" else INFO_WEBSERVICE
        self._transport = transport

    def _client(self, timeout: float) -> httpx.Client:
        if not self.config.base_url.strip():
            raise Unavailable("no transcription server URL", provider=self.info.id)
        headers = {}
        if self.config.api_key is not None:
            headers["Authorization"] = f"Bearer {self.config.api_key.get_secret_value()}"
        return httpx.Client(
            base_url=self.config.base_url.strip().rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(timeout, connect=15),
            transport=self._transport,
            follow_redirects=True,
        )

    def health(self) -> Health:
        try:
            with self._client(15) as http:
                if self.flavor == "openai":
                    r = http.get("/models")
                    if r.status_code == 200:
                        names = [m.get("id", "") for m in r.json().get("data", [])][:8]
                        return Health(ok=True, detail=", ".join(n for n in names if n))
                r = http.get("/")
        except Unavailable as e:
            return Health(ok=False, detail=e.message)
        except (httpx.HTTPError, ValueError) as e:
            return Health(ok=False, detail=f"no answer from {self.config.base_url}: {e}")
        if r.status_code >= 500 or r.status_code in (401, 403):
            return Health(ok=False, detail=f"server answered HTTP {r.status_code}")
        return Health(ok=True)

    def transcribe(
        self, audio: AudioRef, request: TranscriptionRequest, progress: Callable[[float, str], None]
    ) -> Transcript:
        lang = choose_language(request, audio)
        with tempfile.TemporaryDirectory() as tmp:
            progress(0.0, "preparing the audio")
            upload = _flac(Path(audio.path), Path(tmp))
            progress(0.05, "transcribing on the server")
            try:
                with self._client(self.config.timeout_s) as http, upload.open("rb") as f:
                    r = self._post(http, f, upload.name, lang)
            except httpx.HTTPError as e:
                msg = f"transcription server failed: {e}"
                raise TransientFailure(msg, provider=self.info.id) from e
        if r.status_code >= 400:
            msg = f"transcription server answered HTTP {r.status_code}: {r.text[:300]}"
            raise TransientFailure(msg, provider=self.info.id)
        try:
            data = r.json()
        except ValueError as e:
            msg = f"transcription server did not return JSON: {r.text[:200]}"
            raise TransientFailure(msg, provider=self.info.id) from e
        detected = _code(data.get("language"), lang if lang != "auto" else "und")
        segments = parse_response(data, detected, FasterWhisperConfig())
        progress(1.0, f"{len(segments)} lines in {detected} (server)")
        model = self.config.model if self.flavor == "openai" else "whisper-asr-webservice"
        return Transcript(
            segments=tuple(segments),
            language=detected,
            language_prob=1.0,
            model=f"remote {model}",
            audio_duration=audio.duration,
        )

    def _post(self, http: httpx.Client, f: IO[bytes], name: str, lang: str) -> httpx.Response:
        if self.flavor == "openai":
            data: dict[str, Any] = {
                "model": self.config.model,
                "response_format": "verbose_json",
                "timestamp_granularities[]": ["segment", "word"],
            }
            if lang != "auto":
                data["language"] = lang
            return http.post("/audio/transcriptions", data=data, files={"file": (name, f)})
        params = {
            "task": "transcribe",
            "output": "json",
            "encode": "true",
            "word_timestamps": "true",
            "vad_filter": str(self.config.vad).lower(),
        }
        if lang != "auto":
            params["language"] = lang
        return http.post("/asr", params=params, files={"audio_file": (name, f)})


def register() -> None:
    transcription.FACTORY.register(INFO_OPENAI, lambda c: RemoteTranscriber(c, "openai"))
    transcription.FACTORY.register(
        INFO_WEBSERVICE, lambda c: RemoteTranscriber(c, "asr-webservice")
    )
