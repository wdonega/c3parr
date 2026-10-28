"""Provider whisper.cpp: leitura da saída JSON e escolha do idioma (sem rodar o modelo)."""

from c3parr.asr.faster_whisper import FasterWhisperConfig
from c3parr.asr.whisper_cpp import choose_language, parse_output, physical_cores
from c3parr.interfaces.transcription import AudioRef, TranscriptionRequest

# saída real do whisper-cli com VAD: a fala está em 130 s, os tokens não contam o corte do VAD
OUTPUT = {
    "result": {"language": "fr"},
    "transcription": [
        {"offsets": {"from": 130000, "to": 132100}, "text": " Ouverture du testament.",
         "tokens": [{"text": "[_BEG_]", "offsets": {"from": 0, "to": 0}, "p": 1.0},
                    {"text": " Ouver", "offsets": {"from": 0, "to": 300}, "p": 0.6},
                    {"text": "ture", "offsets": {"from": 300, "to": 450}, "p": 0.9},
                    {"text": " du", "offsets": {"from": 450, "to": 550}, "p": 0.99},
                    {"text": " testament.", "offsets": {"from": 550, "to": 1100}, "p": 0.97}]},
        {"offsets": {"from": 140000, "to": 141000}, "text": " ", "tokens": []},
    ],
}  # fmt: skip


def test_tokens_are_shifted_to_the_real_time_and_merged_into_words() -> None:
    segs = parse_output(OUTPUT, "fr", FasterWhisperConfig())
    assert len(segs) == 1
    s = segs[0]
    assert (s.start, s.end, s.text) == (130.0, 132.1, "Ouverture du testament.")
    assert [(w.w, w.start) for w in s.words] == [
        ("Ouverture", 130.0), ("du", 130.45), ("testament.", 130.55)]  # fmt: skip


def audio(tag: str | None) -> AudioRef:
    return AudioRef(path="/a.wav", sha256="x", duration=60, stream_lang=tag)


def test_language_comes_from_src_then_tmdb_unless_the_track_says_otherwise() -> None:
    assert choose_language(TranscriptionRequest(language="pt-BR"), audio("eng")) == "pt"
    assert choose_language(TranscriptionRequest(language_hint="fr"), audio("fre")) == "fr"
    # filme francês com áudio dublado em inglês: vale a faixa
    assert choose_language(TranscriptionRequest(language_hint="fr"), audio("eng")) == "en"
    assert choose_language(TranscriptionRequest(language_hint="fr"), audio(None)) == "fr"
    assert choose_language(TranscriptionRequest(), audio(None)) == "auto"


def test_physical_cores_is_positive() -> None:
    assert physical_cores() >= 1
