"""Extração de áudio com ffmpeg/ffprobe, chamados como processos externos."""

import json
import shutil
import subprocess
from pathlib import Path

from c3parr.core.errors import InvalidInput, Unavailable
from c3parr.core.models import Model

SAMPLE_RATE = 16000

# ISO 639-1 (como no --src) -> códigos ISO 639-2 usados nas tags de faixa
_LANG_TAGS = {
    "ar": {"ara"},
    "de": {"ger", "deu"},
    "en": {"eng"},
    "es": {"spa"},
    "fr": {"fre", "fra"},
    "it": {"ita"},
    "ja": {"jpn"},
    "pt": {"por"},
}


class AudioStream(Model):
    index: int
    codec: str | None = None
    channels: int | None = None
    lang: str | None = None
    title: str | None = None


# legendas em texto (dá para extrair para SRT); o resto (PGS, VobSub, DVB) é imagem e pede OCR
TEXT_SUB_CODECS = frozenset({"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text"})


class SubtitleStream(Model):
    index: int
    """Índice absoluto da faixa no arquivo (o `-map 0:<index>` do ffmpeg)."""
    codec: str | None = None
    lang: str | None = None
    """Idioma normalizado (`en`, `fr`, `pt-BR`); None quando a faixa não diz."""
    raw_lang: str | None = None
    title: str | None = None
    forced: bool = False
    hearing_impaired: bool = False
    default: bool = False

    @property
    def text_based(self) -> bool:
        return (self.codec or "").lower() in TEXT_SUB_CODECS


class MediaInfo(Model):
    duration: float
    audio: tuple[AudioStream, ...]
    subtitles: tuple[SubtitleStream, ...] = ()


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise Unavailable(f"{name} not found on PATH")
    return path


def probe(path: Path) -> MediaInfo:
    if not path.exists():
        raise InvalidInput(f"file not found: {path}")
    proc = subprocess.run(
        [
            _tool("ffprobe"),
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise InvalidInput(f"ffprobe could not read {path.name}: {proc.stderr.strip()[:300]}")
    data = json.loads(proc.stdout)
    streams = [
        AudioStream(
            index=int(s["index"]),
            codec=s.get("codec_name"),
            channels=s.get("channels"),
            lang=(s.get("tags") or {}).get("language"),
            title=(s.get("tags") or {}).get("title"),
        )
        for s in data.get("streams", [])
        if s.get("codec_type") == "audio"
    ]
    subs = []
    for s in data.get("streams", []):
        if s.get("codec_type") != "subtitle":
            continue
        tags = s.get("tags") or {}
        disp = s.get("disposition") or {}
        title = tags.get("title")
        raw = tags.get("language")
        lowered = (title or "").lower()
        subs.append(SubtitleStream(
            index=int(s["index"]), codec=s.get("codec_name"), raw_lang=raw,
            lang=normalize_lang(raw, title), title=title,
            forced=bool(disp.get("forced")) or "forced" in lowered,
            hearing_impaired=bool(disp.get("hearing_impaired")) or "sdh" in lowered,
            default=bool(disp.get("default")),
        ))  # fmt: skip
    duration = float((data.get("format") or {}).get("duration") or 0.0)
    return MediaInfo(duration=duration, audio=tuple(streams), subtitles=tuple(subs))


def normalize_lang(code: str | None, title: str | None = None) -> str | None:
    """Código de faixa (`eng`, `fre`, `por`, `pt-BR`) para o curto usado no c3parr.

    `por` com "Brazil"/"Brasil" no título vira `pt-BR`; `und`/vazio vira None.
    """
    if not code or code.lower() in {"und", "unk", "mis", "zxx", "mul"}:
        return None
    c = code.strip().replace("_", "-")
    low = c.lower()
    if low in {"pob", "pb", "pt-br", "ptbr"}:
        return "pt-BR"
    base = low.split("-")[0]
    short = next((k for k, v in _LANG_TAGS.items() if base in v), base if len(base) == 2 else None)
    if short is None:
        short = _ISO3_EXTRA.get(base, base)
    if short == "pt":
        t = (title or "").lower()
        if "-br" in low or "brazil" in t or "brasil" in t:
            return "pt-BR"
    return short


_ISO3_EXTRA = {
    "rus": "ru", "chi": "zh", "zho": "zh", "jpn": "ja", "kor": "ko", "heb": "he", "tur": "tr",
    "pol": "pl", "dut": "nl", "nld": "nl", "swe": "sv", "nor": "no", "dan": "da", "fin": "fi",
    "gre": "el", "ell": "el", "hin": "hi", "hun": "hu", "cze": "cs", "ces": "cs", "rum": "ro",
    "ron": "ro", "ukr": "uk", "tha": "th", "vie": "vi", "ind": "id", "cat": "ca",
}  # fmt: skip


def pick_stream(info: MediaInfo, src: str | None) -> tuple[AudioStream, str]:
    """Escolhe a faixa do idioma `src`; sem match, a primeira. Devolve também a explicação."""
    if not info.audio:
        raise InvalidInput("the video has no audio track")
    first = info.audio[0]
    first_lang = first.lang or "no language"
    if src:
        tags = _LANG_TAGS.get(src.split("-")[0].lower(), {src.lower()})
        for s in info.audio:
            if s.lang and s.lang.lower() in tags:
                return s, f"track {s.index} ({s.lang}) chosen for language {src}"
        return first, f"no track tagged {src}; using the first one ({first_lang})"
    if len(info.audio) > 1:
        return first, f"{len(info.audio)} tracks and no --src; using the first one ({first_lang})"
    return first, f"single track ({first_lang})"


def extract_audio(path: Path, stream_index: int, dest: Path) -> None:
    """Grava a faixa como WAV mono 16 kHz."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp.wav")
    cmd = [
        _tool("ffmpeg"), "-v", "error", "-y", "-i", str(path),
        "-map", f"0:{stream_index}", "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-c:a", "pcm_s16le", str(tmp),
    ]  # fmt: skip
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise InvalidInput(f"ffmpeg failed to extract the audio: {proc.stderr.strip()[:300]}")
    tmp.replace(dest)
