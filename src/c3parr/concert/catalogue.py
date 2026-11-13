"""O que o artista pode tocar: as músicas e a letra de cada uma.

A letra é insumo: fica no cache e na legenda de quem roda o c3parr, nunca em log nem em mensagem.

Uma edição do álbum junta numa faixa só o que outra separa ("Suíte: I. Um / II. Dois / III.
Três"), e a letra só existe com os títulos separados: `parts` dá o título de cada parte.
"""

import re
import unicodedata

from c3parr.core.models import Model

TAG = re.compile(r"^\s*[\[(][^\])]{0,40}[\])]\s*$")  # linha que é só [Chorus], (x2)…
STAMP = re.compile(r"^\[(\d+):(\d+(?:\.\d+)?)\]\s*(.*)$")
NUMBER = re.compile(r"^\s*(?:[IVXLC]+|\d+)\s*[.:)]\s*")  # "II. ", "3) " na frente de uma parte
MIN_LINES = 4  # menos que isso não é letra (vinheta, faixa falada)


class Song(Model):
    artist: str
    title: str
    album: str = ""
    year: int | None = None
    length_s: float | None = None
    lines: tuple[str, ...] = ()
    synced: tuple[tuple[float, str], ...] = ()
    """O horário de cada linha na gravação de estúdio, quando a fonte tem."""
    instrumental: bool = False


class Catalogue(Model):
    artists: tuple[str, ...] = ()
    until: int | None = None
    """Só entra o que saiu até este ano (o do show)."""
    songs: tuple[Song, ...] = ()


def plain(text: str) -> str:
    """Para comparar nomes: sem acento, caixa nem pontuação."""
    folded = unicodedata.normalize("NFKD", text.replace("\u2010", "-"))
    return re.sub(r"[^a-z0-9]+", " ", folded.encode("ascii", "ignore").decode().lower()).strip()


def clean_lines(text: str) -> tuple[str, ...]:
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip().replace("\u2019", "'").replace("\u2018", "'")
        if line and not TAG.match(line):
            out.append(line)
    return tuple(out)


def parse_synced(text: str) -> tuple[tuple[float, str], ...]:
    out = []
    for raw in (text or "").splitlines():
        m = STAMP.match(raw.strip())
        if m and m.group(3).strip() and not TAG.match(m.group(3)):
            out.append((int(m.group(1)) * 60 + float(m.group(2)), m.group(3).strip()))
    return tuple(out)


def parts(title: str) -> list[str]:
    """As partes de uma faixa composta: "Suíte: I. Um / II. Dois" → ["Um", "Dois"]."""
    text = re.sub(r"\s*\([^)]*\)\s*$", "", title)  # "(versão tal)" no fim
    if " / " not in text:
        return []
    pieces = text.split(" / ")
    _, colon, rest = pieces[0].partition(": ")
    if colon and NUMBER.match(rest):
        pieces[0] = rest  # o nome da suíte fica de fora
    return [p for p in (NUMBER.sub("", piece).strip() for piece in pieces) if p]
