"""Divide a fala em blocos por cena, para o contexto de uma cena não vazar para outra."""

from collections.abc import Mapping, Sequence

from c3parr.core.bible import Bible, Gender
from c3parr.core.models import Model, Segment
from c3parr.interfaces.translation import Block, BlockLine


class BlockRules(Model):
    scene_pause: float = 3.0
    max_lines: int = 40
    """Blocos muito longos são cortados mesmo sem pausa, para caber bem no prompt."""
    context_lines: int = 3
    max_cps: float = 17.0
    max_chars: int = 84


class SongSpan(Model):
    """Uma música do show e de onde até onde ela é cantada (segundos)."""

    title: str
    artist: str = ""
    start: float
    end: float

    @property
    def label(self) -> str:
        return f"{self.title}, de {self.artist}" if self.artist else self.title


def _speaker_gender(seg: Segment, bible: Bible) -> Gender:
    if seg.speaker:
        link = bible.falantes.get(seg.speaker)
        if link is not None:
            ch = bible.character(link.personagem)
            if ch is not None and ch.genero != "?":
                return ch.genero
    if seg.speaker_gender is not None and seg.speaker_gender.conf >= 0.8:
        return seg.speaker_gender.value
    return "?"


def _speaker_name(seg: Segment, bible: Bible) -> str | None:
    if not seg.speaker:
        return None
    link = bible.falantes.get(seg.speaker)
    return link.personagem if link else seg.speaker


def build_blocks(
    segments: Sequence[Segment],
    bible: Bible,
    refs: Mapping[int, str] | None = None,
    rules: BlockRules | None = None,
    songs: Sequence[SongSpan] = (),
) -> list[Block]:
    """`refs`: texto da referência sincronizada por id de segmento, quando houver.

    `songs` (legenda de show): cada música é um contexto só. As falas dentro dela ficam juntas,
    sem cortar nas pausas (um solo não é outra cena), para o verso quebrado em duas linhas e o
    refrão repetido saírem com o mesmo sentido; música longa vai em partes de `max_lines`."""
    rules = rules or BlockRules()
    refs = refs or {}
    lines = [
        BlockLine(
            id=seg.id,
            text=seg.text,
            lang=seg.lang,
            start=seg.start,
            end=seg.end,
            speaker=_speaker_name(seg, bible),
            speaker_gender=_speaker_gender(seg, bible),
            ref=refs.get(seg.id),
            max_chars=min(
                rules.max_chars, max(12, int((seg.end - seg.start) * rules.max_cps * 1.2))
            ),
        )
        for seg in segments
    ]

    def song_of(line: BlockLine) -> int | None:
        return next((n for n, s in enumerate(songs) if s.start <= line.start <= s.end), None)

    blocks: list[Block] = []
    current: list[BlockLine] = []
    pause_before: float | None = None
    owner: int | None = None  # a música das falas em `current`
    part = 0

    def close() -> None:
        block = _block(len(blocks), current, blocks, pause_before, rules)
        if owner is not None:
            block = block.model_copy(update={"song": songs[owner].label, "song_part": part})
        blocks.append(block)

    for line in lines:
        mine = song_of(line)
        if current:
            pause = line.start - current[-1].end
            full = len(current) >= rules.max_lines
            if mine is not None or owner is not None:
                cut = mine != owner or full  # a música inteira junta: a pausa é só um solo
            else:
                last_two = {ln.speaker for ln in current[-2:]}
                speakers_changed = (
                    line.speaker is not None
                    and None not in last_two
                    and len(current) >= 2
                    and line.speaker not in last_two
                    and pause > rules.scene_pause / 2
                )
                cut = pause > rules.scene_pause or speakers_changed or full
            if cut:
                close()
                part = part + 1 if mine == owner and owner is not None else 0
                pause_before = pause
                current = []
        owner = mine
        current.append(line)
    if current:
        close()
    return _with_addressees(blocks)


def _block(
    index: int, lines: list[BlockLine], done: list[Block], pause: float | None, rules: BlockRules
) -> Block:
    previous = done[-1].lines[-rules.context_lines :] if done else ()
    return Block(index=index, lines=tuple(lines), previous=tuple(previous), pause_before=pause)


def _with_addressees(blocks: list[Block]) -> list[Block]:
    """Numa cena com exatamente dois falantes conhecidos, cada um fala com o outro."""
    out = []
    for b in blocks:
        speakers = {ln.speaker for ln in b.lines if ln.speaker}
        if len(speakers) != 2:
            out.append(b)
            continue
        gender = {ln.speaker: ln.speaker_gender for ln in b.lines if ln.speaker}
        lines = []
        for ln in b.lines:
            other = next((s for s in speakers if s != ln.speaker), None) if ln.speaker else None
            if other:
                ln = ln.model_copy(
                    update={"addressee": other, "addressee_gender": gender.get(other, "?")}
                )
            lines.append(ln)
        out.append(b.model_copy(update={"lines": tuple(lines)}))
    return out
