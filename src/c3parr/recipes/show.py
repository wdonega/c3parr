"""Receita `show`: a legenda de um show, com a letra oficial encaixada no canto (♪) e a fala
entre as músicas.

voz (separa a voz da banda) → metadados → letras (o que o artista pode tocar) → transcricao (a
voz, sem detector de voz) → musicas (quais tocam e onde) → encaixe (cada linha no seu instante,
com prova) → segunda_escuta (ouve de novo com a letra como dica) → repetidas (o que se canta de
novo) → fala (o que se diz fora das músicas) → montagem → formatacao → escrita.

Só a voz e a transcrição impedem a legenda. Sem letra, sem encaixe ou sem a segunda escuta ela
sai com o que foi ouvido naquele trecho.
"""

import hashlib
import json
import tempfile
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

from c3parr.audio import ffmpeg
from c3parr.concert import again, build, speech
from c3parr.concert import fit as rules
from c3parr.concert import songs as match
from c3parr.concert.catalogue import Catalogue
from c3parr.concert.fit import Fit, Fitted, Played
from c3parr.concert.models import Asked, Assembled, Line, SecondListen, SongReport, Spoken
from c3parr.concert.words import Phrase, phrases, words
from c3parr.core.errors import Unavailable
from c3parr.core.models import Cue, Subtitle, Transcript
from c3parr.engine.cache import file_sha256
from c3parr.engine.engine import Recipe, Step, StepContext
from c3parr.interfaces import (
    lyric_fit,
    lyrics,
    metadata,
    separation,
    subtitle_writer,
    transcription,
)
from c3parr.interfaces.lyric_fit import PlaceRequest, Span
from c3parr.interfaces.lyrics import LyricsRequest
from c3parr.interfaces.metadata import MetadataRequest, MetadataResult
from c3parr.interfaces.separation import SeparationRequest, Voice
from c3parr.interfaces.transcription import AudioRef, TranscriptionRequest
from c3parr.pipeline.naming import guess_lang
from c3parr.recipes.transcribe import WrittenFile, write
from c3parr.subtitle.format import FormatRules, wrap_lines

MIN_LINES = 5  # menos linhas com indício que isso é um trecho solto (vinheta): não encaixa a letra
MIN_SONG_S = 20.0
HEARING = frozenset({"no_vad", "prompt"})
"""O que o motor de transcrição precisa saber fazer: ouvir sem detector de voz e com dica."""
EMISSIONS = "emissions"
MEMO = "again.json"


def _quiet(_fraction: float, _message: str) -> None:
    return None


def _can_hear(component: Any) -> None:
    info = component.info
    if not info.capabilities >= HEARING:
        raise Unavailable(
            f"{info.id} cannot hear a concert (it needs to run without the voice detector and "
            "take a hint): use whisper.cpp, here or on a whisper.cpp server",
            provider=info.id,
        )


def _base(lang: str | None) -> str | None:
    low = (lang or "").split("-")[0].lower()
    return low if low and low not in ("auto", "und") else None


def cut(voice: Voice, start: float, end: float, dest: Path) -> AudioRef:
    """O trecho [start, end] da voz isolada, num arquivo à parte."""
    with wave.open(voice.path, "rb") as src:
        rate = src.getframerate()
        a = max(0, int(start * rate))
        b = min(src.getnframes(), int(end * rate))
        src.setpos(min(a, src.getnframes()))
        frames = src.readframes(max(0, b - a))
    return _write(frames, rate, dest)


def _write(frames: bytes, rate: int, dest: Path) -> AudioRef:
    with wave.open(str(dest), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(frames)
    return AudioRef(path=str(dest), sha256=file_sha256(dest),
                    duration=round(len(frames) / 2 / rate, 3), sample_rate=rate)  # fmt: skip


def _fitted(ctx: StepContext) -> Fitted:
    """As músicas com o encaixe mais completo que houver (uma etapa opcional pode ter falhado)."""
    for name in ("repetidas", "encaixe", "musicas"):
        value = ctx.inputs.get(name)
        if isinstance(value, Fitted):
            return value
    return Fitted()


def _windows(fitted: Fitted) -> list[tuple[float, float]]:
    """Onde vale a primeira transcrição (dentro de uma música com a letra encaixada)."""
    return [(s.first - 10, s.last + 10) for s in fitted.songs if s.fits]


def _phrase(start: float, end: float, text: str, confs: list[float | None]) -> Phrase:
    known = [c for c in confs if c is not None]
    return (start, end, text, sum(known) / len(known) if known else 1.0)


# --- as etapas ---


def separate(ctx: StepContext) -> Voice:
    video = Path(ctx.params.video)
    stream, note = ffmpeg.pick_stream(ffmpeg.probe(video), ctx.params.src)
    ctx.progress(0.0, note)
    request = SeparationRequest(video=str(video), stream_index=stream.index,
                                dest=str(ctx.workdir / f"voice-{stream.index}.wav"))  # fmt: skip
    result: Voice = ctx.component.separate(request, ctx.progress)
    return result


def identify(ctx: StepContext) -> MetadataResult:
    p = ctx.params
    request = MetadataRequest(filename=Path(p.video).name, path=p.video, title=p.title,
                              year=p.year, tmdb_id=p.tmdb_id, language=p.src)  # fmt: skip
    result: MetadataResult = ctx.component.identify(request)
    return result


def find_lyrics(ctx: StepContext) -> Catalogue:
    p = ctx.params
    meta = ctx.inputs.get("metadados")
    bible = meta.bible if isinstance(meta, MetadataResult) else None
    names = [p.title, bible.titulo.original if bible else None, bible.titulo.pt if bible else None,
             Path(p.video).stem]  # fmt: skip
    request = LyricsRequest(
        titles=tuple(dict.fromkeys(n for n in names if n)),
        year=p.year or (bible.ano if bible else None),
        overview=bible.sinopse if bible else "",
        artist=p.artist,
    )
    result: Catalogue = ctx.component.catalogue(request, ctx.progress)
    return result


def lyrics_language(catalogue: Catalogue | None) -> str | None:
    """O idioma em que o artista canta, pelas letras: é nele que a voz é transcrita (o idioma
    original no TMDb é o do país do show, que pode ser outro)."""
    if catalogue is None or not catalogue.songs:
        return None
    sample = " ".join(line for song in catalogue.songs for line in song.lines[:12])
    return _base(guess_lang(sample[:40000]))


def hear(ctx: StepContext) -> Transcript:
    _can_hear(ctx.component)
    voice = ctx.require("voz", Voice)
    meta = ctx.inputs.get("metadados")
    cat = ctx.inputs.get("letras")
    sung = lyrics_language(cat if isinstance(cat, Catalogue) else None)
    request = TranscriptionRequest(
        language=ctx.params.src or sung,
        language_hint=meta.original_language if isinstance(meta, MetadataResult) else None,
        vad=False,
    )
    result: Transcript = ctx.component.transcribe(voice.audio(), request, ctx.progress)
    return result


def set_list(ctx: StepContext) -> Fitted:
    transcript = ctx.require("transcricao", Transcript)
    cat = ctx.inputs.get("letras")
    if not isinstance(cat, Catalogue) or not cat.songs:
        return Fitted()
    listed = match.listed(cat)
    found = match.find(words(transcript), listed)
    return Fitted(songs=tuple(
        Played(title=f.title, artist=f.artist, first=f.first, last=f.last, start=f.start,
               end=f.end, matched=f.lines, lines=listed[f.song].lines,
               synced=listed[f.song].synced)
        for f in found
    ))  # fmt: skip


def fit_lines(ctx: StepContext) -> Fitted:
    voice = ctx.require("voz", Voice)
    transcript = ctx.require("transcricao", Transcript)
    played = ctx.require("musicas", Fitted)
    lang = _base(transcript.language)
    if lang not in ctx.component.info.capabilities:
        ctx.progress(1.0, f"lyrics in {lang or 'an unknown language'}: not fitted")
        return played
    heard = words(transcript)
    wanted = [n for n, s in enumerate(played.songs)
              if s.matched >= MIN_LINES and s.end - s.start >= MIN_SONG_S]  # fmt: skip
    out = list(played.songs)
    for k, n in enumerate(wanted):
        song = out[n]
        request = PlaceRequest(
            window=(song.start, song.end),
            spans=(Span(start=song.start, end=song.end, lines=song.lines),),
            cache_dir=str(ctx.workdir / EMISSIONS),
        )
        (placed,) = ctx.component.place(voice.audio(), request, _quiet)
        fits = rules.accept(
            [Fit(line=p.line, text=song.lines[p.line], start=p.start, end=p.end, score=p.score,
                 steady=p.steady) for p in placed],
            heard,
        )  # fmt: skip
        good, total = rules.studio_check(fits, song.synced)  # antes de o disco servir de prova
        fits = rules.studio(fits, song.synced)
        out[n] = song.model_copy(update={"fits": tuple(fits), "studio_pairs": total,
                                         "studio_consistent": good})  # fmt: skip
        ctx.progress((k + 1) / len(wanted), f"fitting the lyrics ({k + 1} of {len(wanted)})")
    return Fitted(songs=tuple(out))


def hear_again(ctx: StepContext) -> SecondListen:
    _can_hear(ctx.component)
    voice = ctx.require("voz", Voice)
    transcript = ctx.require("transcricao", Transcript)
    fitted = ctx.require("encaixe", Fitted)
    heard = phrases(words(transcript))
    lang = _base(transcript.language)
    jobs: list[tuple[int, again.Ask]] = []
    for n, song in enumerate(fitted.songs):
        if song.fits:
            window = (max(song.start, song.first - 15), min(song.end, song.last + 30))
            jobs += [(n, ask) for ask in again.asks(song.lines, heard, song.fits, window)]
    memo_file = ctx.workdir / MEMO  # o que o Whisper já ouviu com cada dica: não pergunta de novo
    memo: dict[str, str] = {}
    if memo_file.exists():
        memo = dict(json.loads(memo_file.read_text(encoding="utf-8")))
    out: list[Asked] = []
    with tempfile.TemporaryDirectory() as tmp:
        for k, (n, ask) in enumerate(jobs):
            a, b = ask.region
            said = hashlib.sha1(ask.prompt.encode()).hexdigest()[:12]
            key = f"{voice.sha256[:12]}|{a:.2f}|{b:.2f}|{said}|{lang}"
            if key not in memo:
                clip = cut(voice, a, b, Path(tmp) / "again.wav")
                request = TranscriptionRequest(language=lang, vad=False, prompt=ask.prompt)
                got: Transcript = ctx.component.transcribe(clip, request, _quiet)
                memo[key] = " ".join(s.text.strip() for s in got.segments)
                if k % 10 == 9:
                    memo_file.write_text(json.dumps(memo), encoding="utf-8")
            out.append(Asked(song=n, start=a, end=b, among=ask.among, first=ask.first,
                             text=memo[key]))  # fmt: skip
            ctx.progress((k + 1) / len(jobs), f"hearing again ({k + 1} of {len(jobs)})")
    memo_file.write_text(json.dumps(memo), encoding="utf-8")
    return SecondListen(asked=tuple(out))


def fit_again(ctx: StepContext) -> Fitted:
    """As linhas que a segunda escuta ouviu, encaixadas no trecho, com a prova de cada uma."""
    voice = ctx.require("voz", Voice)
    fitted = ctx.require("encaixe", Fitted)
    second = ctx.require("segunda_escuta", SecondListen)
    cache = str(ctx.workdir / EMISSIONS)
    out = list(fitted.songs)
    for n, song in enumerate(fitted.songs):
        spotted = [(asked, found) for asked in second.asked if asked.song == n
                   if (found := again.spot(asked.text, song.lines, asked.among))]  # fmt: skip
        if not spotted:
            continue
        window = (song.start, song.end)
        spans = tuple(Span(start=asked.start, end=asked.end,
                           lines=tuple(song.lines[i] for i in found))
                      for asked, found in spotted)  # fmt: skip
        placed = ctx.component.place(
            voice.audio(), PlaceRequest(window=window, spans=spans, cache_dir=cache), _quiet
        )
        # a nota "firme" de cada linha é medida de novo, só em volta de onde ela coube
        refs = [
            (asked, found[p.line], p)
            for (asked, found), group in zip(spotted, placed, strict=True)
            for p in group
        ]
        tight = tuple(Span(start=max(song.start, p.start - again.TIGHT_S),
                           end=p.end + again.TIGHT_S, lines=(song.lines[line],))
                      for _, line, p in refs)  # fmt: skip
        alone = ctx.component.place(
            voice.audio(), PlaceRequest(window=window, spans=tight, cache_dir=cache), _quiet
        )
        seconds = [
            again.Second(line, Fit(line=line, text=song.lines[line], start=p.start, end=p.end,
                                   score=p.score, steady=one[0].steady if one else 0.0),
                         again.run(song.lines[line], asked.first), (asked.start, asked.end))
            for (asked, line, p), one in zip(refs, alone, strict=True)
        ]  # fmt: skip
        taken = [(f.start, f.end) for other in out for f in other.fits if f.accepted]
        new = [f for f in again.accept(seconds)
               if not any(min(f.end, b) - max(f.start, a) > 0.5 * (f.end - f.start)
                          for a, b in taken)]  # fmt: skip
        out[n] = song.model_copy(update={"fits": (*song.fits, *new)})
    return Fitted(songs=tuple(out))


def hear_speech(ctx: StepContext) -> Spoken:
    """Fora das músicas: só onde há voz, com o idioma de cada trecho."""
    _can_hear(ctx.component)
    voice = ctx.require("voz", Voice)
    transcript = ctx.require("transcricao", Transcript)
    fitted = _fitted(ctx)
    fallback = _base(transcript.language)
    taken = [(f.start, f.end) for s in fitted.songs for f in s.fits if f.accepted]
    chunks = speech.outside(speech.free_chunks(voice.spans, taken), _windows(fitted))
    long = [c for c in chunks if c[1] - c[0] >= speech.DETECT_MIN_S]
    short = [c for c in chunks if c[1] - c[0] < speech.DETECT_MIN_S]
    known: dict[speech.Chunk, str] = {}
    found: list[tuple[Phrase, str]] = []
    listen: Callable[[AudioRef, str | None], Transcript] = lambda clip, lang: (  # noqa: E731
        ctx.component.transcribe(clip, TranscriptionRequest(language=lang, vad=False), _quiet)
    )
    with tempfile.TemporaryDirectory() as tmp:
        for k, chunk in enumerate(long):  # o idioma é detectado no próprio trecho
            origin = max(0.0, chunk[0] - speech.EDGE_S)
            got = listen(
                cut(voice, origin, chunk[1] + speech.EDGE_S, Path(tmp) / "speech.wav"), None
            )
            lang = _base(got.language) or fallback or "und"
            known[chunk] = lang
            found += [(_phrase(origin + s.start, min(origin + s.end, chunk[1] + speech.EDGE_S),
                               s.text.strip(), [w.conf for w in s.words]), lang)
                      for s in got.segments if s.text.strip() and s.words]  # fmt: skip
            ctx.progress(0.9 * (k + 1) / len(long), f"hearing the speech ({k + 1} of {len(long)})")
        # os trechos curtos vão juntos, com silêncio no meio, no idioma do vizinho
        for lang, group in speech.by_language(short, known, fallback or "und").items():
            found += [(p, lang) for p in _together(voice, group, _base(lang), Path(tmp), listen)]
    found.sort(key=lambda item: item[0][0])
    languages: dict[str, int] = {}
    for _, lang in found:
        languages[lang] = languages.get(lang, 0) + 1
    return Spoken(phrases=tuple(p for p, _ in found), languages=languages)


def _together(voice: Voice, chunks: list[speech.Chunk], lang: str | None, tmp: Path,
              listen: Callable[[AudioRef, str | None], Transcript]) -> list[Phrase]:  # fmt: skip
    """Os trechos, um atrás do outro com silêncio no meio, numa passada só do Whisper; cada frase
    volta para o horário do trecho de onde veio."""
    if not chunks:
        return []
    frames = b""
    where: list[tuple[float, float, float]] = []  # no áudio emendado: início, fim; e o horário real
    at = 0.0
    with wave.open(voice.path, "rb") as src:
        rate = src.getframerate()
        for start, end in chunks:
            a = max(0, int((start - speech.EDGE_S) * rate))
            src.setpos(min(a, src.getnframes()))
            piece = src.readframes(max(0, int((end + speech.EDGE_S) * rate) - a))
            length = len(piece) / 2 / rate
            where.append((at, at + length, a / rate))
            frames += piece + b"\0\0" * int(speech.GAP_S * rate)
            at += length + speech.GAP_S
    got = listen(_write(frames, rate, tmp / "speech-short.wav"), lang)
    out: list[Phrase] = []
    for s in got.segments:
        middle = (s.start + s.end) / 2
        home = next((w for w in where if w[0] - 0.2 <= middle <= w[1] + 0.2), None)
        if home is None or not s.words or not s.text.strip():
            continue  # caiu no silêncio entre dois trechos: inventado
        shift = home[2] - home[0]
        out.append(_phrase(max(s.start, home[0]) + shift, min(s.end, home[1]) + shift,
                           s.text.strip(), [w.conf for w in s.words]))  # fmt: skip
    return out


def assemble(ctx: StepContext) -> Assembled:
    transcript = ctx.require("transcricao", Transcript)
    fitted = _fitted(ctx)
    second = ctx.inputs.get("segunda_escuta")
    spoken = ctx.inputs.get("fala")
    heard_words = words(transcript)
    first = phrases(heard_words)
    if isinstance(spoken, Spoken):
        # dentro de uma música o detector de voz perde canto sustentado: ali vale a primeira
        # transcrição (há voz de verdade); fora, o que foi ouvido só onde há voz
        windows = _windows(fitted)
        inside = [p for p in first if any(a <= p[0] <= b for a, b in windows)]
        heard = sorted([*inside, *spoken.phrases])
    else:
        heard = first
    texts = ({(a.start, a.end): a.text for a in second.asked}
             if isinstance(second, SecondListen) else {})  # fmt: skip
    total = max([transcript.audio_duration, *(w.end for w in heard_words[-1:])])
    rows = build.build(
        fitted.songs, heard, total, lambda s, e, text: again.stable(text, texts, s, e) is False
    )
    undoubted = build.count(build.build(fitted.songs, heard, total))
    counts = build.count(rows)
    cat = ctx.inputs.get("letras")
    sung = lyrics_language(cat if isinstance(cat, Catalogue) else None)
    return Assembled(
        rows=tuple(Line(start=round(r.start, 3), end=round(r.end, 3), text=r.text, kind=r.kind)
                   for r in rows),
        counts=counts,
        songs=tuple(_report(s) for s in fitted.songs),
        languages=spoken.languages if isinstance(spoken, Spoken) else {},
        dropped_unstable=undoubted["heard"] - counts["heard"],
        language=ctx.params.src or sung or _base(transcript.language) or "und",
    )  # fmt: skip


def _report(song: Played) -> SongReport:
    proofs: dict[str, int] = {}
    for f in song.fits:
        if f.accepted:
            proofs[f.why] = proofs.get(f.why, 0) + 1
    return SongReport(
        title=song.title,
        artist=song.artist,
        start=round(song.first, 1),
        end=round(song.last, 1),
        lines=len(song.lines),
        accepted=sum(proofs.values()),
        proofs=proofs,
        studio_pairs=song.studio_pairs,
        studio_consistent=song.studio_consistent,
    )


def split_long(row: Line, limit: int) -> list[tuple[float, float, str]]:
    """Uma fala ouvida longa demais para a tela vira falas seguidas, com o tempo dividido pelo
    tamanho de cada pedaço. Linha de letra não se divide (são curtas, e o ♪ abre e fecha nela)."""
    if row.kind != "heard" or len(row.text) <= limit:
        return [(row.start, row.end, row.text)]
    pieces: list[str] = []
    for word in row.text.split():
        if pieces and len(pieces[-1]) + 1 + len(word) <= limit:
            pieces[-1] += " " + word
        else:
            pieces.append(word)
    total = sum(len(p) for p in pieces)
    out, at = [], row.start
    for piece in pieces:
        end = at + (row.end - row.start) * len(piece) / total
        out.append((round(at, 3), round(end, 3), piece))
        at = end
    return out


def make_format(rules: FormatRules) -> Step:
    def run(ctx: StepContext) -> Subtitle:
        done = ctx.require("montagem", Assembled)
        limit = rules.max_chars_per_line * rules.max_lines - 4  # folga para a quebra em linhas
        pieces = [piece for row in done.rows for piece in split_long(row, limit)]
        cues = tuple(
            Cue(index=n, start=start, end=end,
                text=wrap_lines(text, rules.max_chars_per_line, rules.max_lines))
            for n, (start, end, text) in enumerate(pieces, 1)
        )  # fmt: skip
        return Subtitle(cues=cues, lang=done.language)

    return Step(name="formatacao", run=run, output=Subtitle, needs=("montagem",),
                key_params=lambda _p: {"chars": rules.max_chars_per_line,
                                       "lines": rules.max_lines})  # fmt: skip


def steps(rules: FormatRules, metadata_step: Step) -> tuple[Step, ...]:
    """As etapas até a legenda formatada (sem gravar): a pipeline da biblioteca grava a dela."""
    return (
        Step(name="voz", run=separate, output=Voice, interface=separation.INTERFACE,
             key_params=lambda p: {"src": p.src}),
        metadata_step,
        Step(name="letras", run=find_lyrics, output=Catalogue, interface=lyrics.INTERFACE,
             required=False, needs=("metadados",),
             skip_reason=lambda p: "lyrics lookup is off" if p.no_lyrics else None,
             key_params=lambda p: {"title": p.title, "year": p.year, "artist": p.artist,
                                   "video": Path(p.video).name},
             failure_effect="no official lyrics: the subtitle has only what was heard"),
        Step(name="transcricao", run=hear, output=Transcript, interface=transcription.INTERFACE,
             needs=("voz", "metadados", "letras"), key_params=lambda p: {"src": p.src}),
        Step(name="musicas", run=set_list, output=Fitted, needs=("transcricao", "letras")),
        Step(name="encaixe", run=fit_lines, output=Fitted, interface=lyric_fit.INTERFACE,
             required=False, needs=("voz", "transcricao", "musicas"),
             failure_effect="lyrics not fitted to the singing: only lines the transcript heard "
                            "show the official text"),
        Step(name="segunda_escuta", run=hear_again, output=SecondListen,
             interface=transcription.INTERFACE, required=False,
             needs=("voz", "transcricao", "encaixe"),
             failure_effect="repeated choruses keep what the first listen heard"),
        Step(name="repetidas", run=fit_again, output=Fitted, interface=lyric_fit.INTERFACE,
             required=False, needs=("voz", "encaixe", "segunda_escuta"),
             failure_effect="repeated choruses keep what the first listen heard"),
        Step(name="fala", run=hear_speech, output=Spoken, interface=transcription.INTERFACE,
             required=False, needs=("voz", "transcricao", "musicas", "encaixe", "repetidas"),
             failure_effect="speech between songs comes from the first listen, less reliable"),
        Step(name="montagem", run=assemble, output=Assembled,
             needs=("transcricao", "letras", "musicas", "encaixe", "segunda_escuta", "repetidas",
                    "fala"),
             key_params=lambda p: {"src": p.src}),
        make_format(rules),
    )  # fmt: skip


def cli_metadata_step() -> Step:
    """Fora da biblioteca: o TMDb identifica o show pelo nome do arquivo (ou `--title`)."""
    return Step(name="metadados", run=identify, output=MetadataResult,
                interface=metadata.INTERFACE, required=False,
                skip_reason=lambda p: "no TMDb (--no-tmdb)" if p.no_tmdb else None,
                key_params=lambda p: {"title": p.title, "year": p.year, "tmdb": p.tmdb_id,
                                      "video": Path(p.video).name},
                failure_effect="the artist comes only from the file name")  # fmt: skip


def recipe(rules: FormatRules | None = None) -> Recipe:
    """A receita da linha de comando: grava `<vídeo>.<idioma>.srt`."""
    return Recipe(name="show", steps=(
        *steps(rules or FormatRules(), cli_metadata_step()),
        Step(name="escrita", run=write, output=WrittenFile, interface=subtitle_writer.INTERFACE,
             needs=("formatacao",), cacheable=False),
    ))  # fmt: skip
