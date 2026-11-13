"""Legenda de show, a lógica sem modelos: palavras, músicas, provas, segunda escuta, fala e
montagem. Tudo com letra inventada (`concertkit`)."""

from itertools import pairwise

from concertkit import catalogue, fit, played, song, sung, talk

from c3parr.concert import again, build, songs, speech
from c3parr.concert import fit as rules
from c3parr.concert.catalogue import clean_lines, parse_synced, parts
from c3parr.concert.words import norm, phrases, words
from c3parr.core.models import Segment, Transcript, Word

# --- palavras ---


def test_text_is_compared_without_case_or_punctuation() -> None:
    assert norm("Don\u2019t  STOP — now!") == "dont stop now"
    assert norm("São Paulo, 2004") == "são paulo 2004"


def test_a_transcript_becomes_words_with_time_and_confidence() -> None:
    seg = Segment(id=3, start=1.0, end=2.5, text="River of glass, twenty-two", lang="en", words=(
        Word(w="River", start=1.0, end=1.4, conf=0.4), Word(w="of", start=1.4, end=1.5, conf=0.9),
        Word(w="glass,", start=1.5, end=2.0), Word(w="twenty-two", start=2.1, end=2.5, conf=0.8),
    ))  # fmt: skip
    got = words(Transcript(segments=(seg,), language="en", model="x", audio_duration=3.0))
    assert [w.norm for w in got] == ["river", "of", "glass", "twenty", "two"]
    assert (got[0].start, got[0].end, got[0].p, got[0].segment) == (1.0, 1.4, 0.4, 3)
    assert got[2].p == 1.0  # sem confiança informada
    assert (got[3].start, got[4].start) == (
        2.1,
        2.1,
    )  # a palavra com hífen vira duas, mesmo horário
    assert phrases(got) == [(1.0, 2.5, "River of glass, twenty-two", (0.4 + 0.9 + 1 + 0.8 * 2) / 5)]


# --- quais músicas tocam ---


def test_finds_each_song_in_the_order_it_is_played_even_with_wrong_words() -> None:
    a, b, c = song("Alfa", 1), song("Beta", 2), song("Gama", 3)
    heard = (talk("good evening everybody thank you for coming", 5, 900)
             + sung(b.lines, 60, wrong_every=5) + sung(a.lines[:12], 400, wrong_every=4, segment=100)
             + talk("this next one is very special to us", 480, 901))  # fmt: skip
    found = songs.find(sorted(heard, key=lambda w: w.start), [a, b, c])
    assert [f.title for f in found] == ["Beta", "Alfa"]
    assert 55 <= found[0].first <= 70 and found[1].first >= 395
    assert found[0].end <= found[1].first  # as janelas para o encaixe não se atropelam


def test_talk_between_songs_is_not_a_song() -> None:
    heard = talk("hello sao paulo how are you feeling tonight we love playing here", 10, 0)
    assert songs.find(heard, [song("Alfa", 1), song("Beta", 2)]) == []


def test_a_long_solo_does_not_split_a_song_in_two() -> None:
    a = song("Alfa", 1, lines=30)
    heard = sung(a.lines[:15], 0) + sung(a.lines[15:], 15 * 4 + 300, segment=50)  # 5 min de solo
    found = songs.find(heard, [a, song("Beta", 2)])
    assert [f.title for f in found] == ["Alfa"]
    assert found[0].last - found[0].first > 350


def test_a_mashup_wins_over_the_two_songs_it_is_made_of() -> None:
    a, b = song("Alfa", 1), song("Beta", 2)
    mash = song("Alfa / Beta", 9).model_copy(update={"lines": a.lines[:12] + b.lines[:12]})
    found = songs.find(sung(mash.lines, 30, wrong_every=6), [a, b, mash])
    assert [f.title for f in found] == ["Alfa / Beta"]


def test_the_same_track_listed_twice_counts_once() -> None:
    a = song("Alfa", 1)
    twin = a.model_copy(update={"title": "Alfa (remastered)", "artist": "Outro Nome",
                                "lines": a.lines[:-2]})  # fmt: skip
    short = song("Vinheta", 5, lines=2)
    listed = songs.listed(catalogue(a, twin, song("Beta", 2), short))
    assert [s.title for s in listed] == ["Alfa", "Beta"]


# --- as provas de uma linha encaixada ---


def test_a_strong_fit_stands_alone_and_an_impossible_one_never_does() -> None:
    got = rules.accept([fit(0, 10, 13, 0.8), fit(1, 20, 20.2, 0.9), fit(2, 30, 60, 0.9)], [])
    assert [(f.accepted, f.why) for f in got] == [(True, "forte"), (False, "duracao"),
                                                  (False, "duracao")]  # fmt: skip


def test_a_weak_fit_needs_the_transcript_to_have_heard_the_line() -> None:
    heard = talk("amber river cold lantern window", 40.5, 0)
    got = rules.accept([fit(0, 40, 43, 0.15), fit(1, 80, 83, 0.15)], heard)
    assert [(f.accepted, f.why) for f in got] == [(True, "ouvida"), (False, "")]


def test_a_line_between_two_proven_ones_is_accepted() -> None:
    got = rules.accept([fit(0, 10, 13, 0.8), fit(1, 14, 17, 0.3), fit(2, 18, 21, 0.8),
                        fit(3, 60, 63, 0.3)], [])  # fmt: skip
    assert [f.accepted for f in got] == [True, True, True, False]
    assert got[1].why == "vizinhas"


TEXTS = ("amber river cold lantern", "window paper harbor silver", "engine morning quiet thunder",
         "garden shadow copper winter", "letter mountain candle ocean", "ribbon static velvet orbit")  # fmt: skip
STUDIO = tuple((10.0 + 20.0 * n, text) for n, text in enumerate(TEXTS))  # horário no disco


def live(scores: list[float], late: dict[int, float] | None = None) -> list[rules.Fit]:
    """As seis linhas no show, 100 s depois do disco; `late`: {linha: segundos a mais}."""
    at = [STUDIO[n][0] + 100.0 + (late or {}).get(n, 0.0) for n in range(len(TEXTS))]
    return rules.accept([fit(n, at[n], at[n] + 3.0, s, TEXTS[n]) for n, s in enumerate(scores)], [])


def test_a_weak_line_is_accepted_where_the_studio_recording_has_it_and_not_elsewhere() -> None:
    got = rules.studio(live([0.8, 0.8, 0.3, 0.3, 0.8, 0.8], late={3: 4.0}), STUDIO)
    assert [f.accepted for f in got] == [True, True, True, False, True, True]
    assert got[2].why == "disco"


def test_the_studio_timing_proves_nothing_where_the_band_plays_another_arrangement() -> None:
    """Antes da linha o show está 100 s depois do disco; depois dela, 110 s (um trecho a mais)."""
    late = {3: 10.0, 4: 10.0, 5: 10.0}
    assert not rules.studio(live([0.8, 0.8, 0.3, 0.8, 0.8, 0.8], late=late), STUDIO)[2].accepted


def test_the_studio_timing_needs_a_fit_and_the_same_lines() -> None:
    weak = [0.8, 0.8, 0.3, 0.8, 0.8, 0.8]
    assert not rules.studio(live([0.8, 0.8, 0.1, 0.8, 0.8, 0.8]), STUDIO)[2].accepted  # nada soa
    assert not rules.studio(live(weak), STUDIO[:-1])[2].accepted  # outra divisão em linhas
    other = tuple((stamp, "hollow ember signal marble") for stamp, _ in STUDIO)
    assert not rules.studio(live(weak), other)[2].accepted  # outro texto
    assert rules.studio(live(weak), STUDIO)[2].accepted


def test_the_studio_check_counts_pairs_of_lines_with_the_interval_of_the_record() -> None:
    got = live([0.8, 0.8, 0.8, 0.1, 0.8, 0.8], late={1: 25.0})  # a linha 1 atrasou: 2 pares ruins
    assert rules.studio_check(got, STUDIO) == (1, 3)
    assert rules.studio_check(got, STUDIO[:-1]) == (0, 0)


# --- ouvir de novo com a letra como dica ---

LINES = song("Teste", 7, lines=10).lines


def test_looks_only_where_something_was_heard_and_no_fitted_line_covers() -> None:
    heard = [(41.0, 43.5, "já coberto pela linha", 0.9), (50.0, 56.0, "refrão outra vez", 0.4)]
    assert again.regions(heard, [(40.0, 44.0), (60.0, 64.0)], (30.0, 80.0)) == [(49.0, 57.0)]


def test_a_phrase_that_overlaps_a_fitted_line_keeps_only_the_part_outside() -> None:
    heard = [(42.0, 50.0, "metade na linha", 0.9)]
    assert again.regions(heard, [(40.0, 44.0)], (30.0, 80.0)) == [(44.0, 51.0)]


def test_neighbours_go_together_and_no_region_passes_one_whisper_window() -> None:
    heard = [(10.0, 14.0, "um", 0.9), (15.5, 20.0, "dois", 0.9), (40.0, 44.0, "três", 0.9),
             (100.0, 160.0, "longa", 0.9)]  # fmt: skip
    got = again.regions(heard, [], (0.0, 200.0))
    assert got[:2] == [(9.0, 21.0), (39.0, 45.0)]
    assert all(b - a <= again.MAX_S for a, b in got)


def test_the_hint_has_the_lines_like_what_was_heard_and_the_ones_due_next() -> None:
    heard = LINES[7].replace(LINES[7].split()[1], "zzz")  # ouviu a linha 7 com uma palavra errada
    got = again.hint(LINES, heard, before=2, after=None)
    assert 7 in got and {2, 3, 4} <= set(got)
    assert len(got) <= again.HINT_LINES and got == sorted(got)


def test_each_region_to_hear_again_carries_its_hint_and_what_was_heard_first() -> None:
    heard = [(19.5, 24.0, "zzz zzz zzz zzz", 0.8)]
    proven = [fit(9, 10.0, 13.0, 0.8, LINES[9], accepted=True)]
    (ask,) = again.asks(LINES, heard, proven, (0.0, 40.0))
    assert ask.region == (18.5, 25.0) and ask.first == "zzz zzz zzz zzz"
    assert 9 in ask.among and LINES[9] in ask.prompt
    assert again.asks(LINES, [], proven, (0.0, 40.0)) == []  # nada foi ouvido: nada a perguntar


def test_a_line_sung_twice_is_found_twice_and_in_order() -> None:
    text = f"{LINES[3]} hey {LINES[5]}, {LINES[3]}!"
    assert again.spot(text, LINES, [3, 4, 5]) == [3, 5, 3]


def test_a_line_is_found_with_a_small_slip_but_not_from_a_few_shared_words() -> None:
    lines = ["amber river cold lantern window paper"]
    assert again.spot("oh amber river cold lantern windows paper", lines, [0]) == [0]
    assert again.spot("amber river and something else entirely", lines, [0]) == []


def test_very_short_lines_are_not_looked_for() -> None:
    assert again.spot("yeah yeah come on", ["yeah", "come on"], [0, 1]) == []


def test_run_is_the_longest_stretch_of_the_line_the_first_listen_heard_in_a_row() -> None:
    assert again.run("amber river cold lantern window", "so cold LANTERN, window... amber") == 3
    assert again.run("amber river cold", "amber zzz river zzz cold") == 1  # soltas não contam


def second(candidate: rules.Fit, run: int) -> list[rules.Fit]:
    return again.accept([again.Second(candidate.line, candidate, run, (18.0, 25.0))])


def test_three_words_in_a_row_from_the_first_listen_prove_a_line_the_sound_only_half_shows() -> (
    None
):
    heard = " ".join(LINES[9].split()[1:4]) + " zzz"
    got = second(fit(9, 20.0, 23.0, 0.3, LINES[9], steady=0.1), again.run(LINES[9], heard))
    assert [(f.accepted, f.why) for f in got] == [(True, "primeira_escuta")]
    assert not second(fit(9, 20.0, 23.0, 0.3, LINES[9], steady=0.1), 2)
    # e nem três palavras salvam uma linha que não cabe no som do trecho
    assert not second(fit(9, 20.0, 23.0, 0.1, LINES[9], steady=0.1), 4)


def test_the_sound_proves_a_line_of_five_words_but_not_a_short_one() -> None:
    assert not second(fit(0, 20.0, 22.0, 0.4, "amber river cold", steady=0.4), 0)  # bate por acaso
    got = second(fit(0, 20.0, 23.0, 0.4, "amber river cold lantern window", steady=0.4), 0)
    assert [f.why for f in got] == ["som"]


def test_a_phrase_both_listens_heard_is_stable_and_one_only_the_first_heard_is_not() -> None:
    texts = {(18.0, 26.0): "hello tokyo how are you tonight"}
    assert again.stable("Hello Tokyo, how are you?", texts, 19.0, 22.0) is True
    assert again.stable("if you bake a tiny moon", texts, 19.0, 22.0) is False
    assert again.stable("anything", texts, 60.0, 62.0) is None  # ali não houve segunda escuta


def test_the_hint_stays_within_what_whisper_reads() -> None:
    assert len(again.prompt(LINES * 5, list(range(50))).split()) <= again.HINT_WORDS


# --- a fala fora das músicas ---


def test_voice_already_covered_by_a_fitted_line_is_not_heard_again() -> None:
    spans = [(10.0, 20.0), (30.0, 31.0)]
    taken = [(12.0, 15.0)]  # uma linha da letra encaixada
    assert speech.free_chunks(spans, taken) == [(10.0, 11.7), (15.3, 20.0), (30.0, 31.0)]


def test_a_leftover_too_short_to_be_a_word_is_dropped() -> None:
    assert speech.free_chunks([(10.0, 13.0)], [(10.2, 12.9)]) == []


def test_close_stretches_go_together_but_never_past_one_whisper_window() -> None:
    spans = [(0.0, 10.0), (10.5, 20.0), (20.4, 29.5), (60.0, 61.0)]
    assert speech.free_chunks(spans, []) == [(0.0, 20.0), (20.4, 29.5), (60.0, 61.0)]


def test_only_what_starts_outside_every_song_is_speech() -> None:
    chunks = [(5.0, 9.0), (50.0, 55.0), (99.0, 104.0), (130.0, 133.0)]
    assert speech.outside(chunks, [(40.0, 100.0)]) == [(5.0, 9.0), (130.0, 133.0)]


def test_a_short_stretch_takes_the_language_of_the_nearest_one_that_has_it() -> None:
    known = {(10.0, 20.0): "ja", (200.0, 210.0): "en"}
    got = speech.by_language([(22.0, 24.0), (195.0, 197.0), (100.0, 102.0)], known, "en")
    assert got == {"ja": [(22.0, 24.0)], "en": [(195.0, 197.0), (100.0, 102.0)]}
    assert speech.by_language([(1.0, 2.0)], {}, "pt") == {"pt": [(1.0, 2.0)]}


# --- a legenda ---


def test_lyrics_speech_and_a_repeated_chorus_come_out_in_order() -> None:
    lines = song("Alfa", 1).lines
    fits = [
        fit(0, 20.0, 23.0, 0.9, lines[0], accepted=True),
        fit(1, 24.0, 27.0, 0.9, lines[1], accepted=True),
        fit(2, 28.0, 31.0, 0.1, lines[2]),
    ]  # sem prova: não entra
    heard = (talk("Good evening everybody", 5, 0) + talk(lines[0], 20.2, 1)  # coberta
             + talk(lines[1].lower(), 40.0, 2)  # refrão a mais, ouvido de novo
             + talk("Come on people", 50.0, 3) + talk("Thank you", 70.0, 4)
             + talk("mumble mumble mumble mumble", 80.0, 5, p=0.2))  # fmt: skip
    rows = build.build([played(lines, fits, 15, 60)], phrases(heard), 100.0)
    assert [(r.kind, r.text) for r in rows] == [
        ("heard", "Good evening everybody"),
        ("lyric", f"♪ {lines[0]} ♪"),
        ("lyric", f"♪ {lines[1]} ♪"),
        ("lyric-heard", f"♪ {lines[1]} ♪"),
        ("heard", "Come on people"),
    ]  # "Thank you" sozinho e a frase de baixa confiança ficam de fora
    assert all(a.end <= b.start for a, b in pairwise(rows))
    assert build.count(rows) == {"lyric": 2, "lyric-heard": 1, "heard": 2}


def test_a_heard_phrase_in_doubt_stays_out() -> None:
    heard = phrases(talk("Good evening everybody", 5, 0) + talk("Come on people", 50.0, 3))
    rows = build.build([], heard, 100.0, doubt=lambda start, end, text: start < 10)
    assert [r.text for r in rows] == ["Come on people"]


# --- o catálogo ---


def test_lines_that_are_only_a_tag_are_dropped() -> None:
    text = "[Chorus]\nRiver of glass\n\n(x2)\n  Lantern in the window  \n[Verse 2]"
    assert clean_lines(text) == ("River of glass", "Lantern in the window")


def test_timed_lyrics_are_read_with_their_seconds() -> None:
    text = "[00:12.50] River of glass\n[01:02.00] Lantern in the window\n[01:10.00] \nsem horário"
    assert parse_synced(text) == ((12.5, "River of glass"), (62.0, "Lantern in the window"))


def test_a_suite_in_one_track_is_split_into_its_parts() -> None:
    assert parts("Minha Suíte: I. Abertura / II. Vozes / III. O Homem Quieto") == [
        "Abertura", "Vozes", "O Homem Quieto"]  # fmt: skip
    assert parts("Um / Dois / Três (ao vivo)") == ["Um", "Dois", "Três"]
    assert parts("Título: com dois pontos / Outra") == ["Título: com dois pontos", "Outra"]
    assert parts("Uma Música Só") == [] and parts("Civil. Guerra") == []
