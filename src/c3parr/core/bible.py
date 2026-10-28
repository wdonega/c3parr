"""A bíblia do filme: fatos que ajudam a transcrever e traduzir. Chaves em português, como na spec,
porque o usuário edita esse JSON e passa de volta com `--bible`."""

from typing import Literal

from pydantic import Field

from c3parr.core.models import GenderSource, Model

Gender = Literal["F", "M", "?"]


class Title(Model):
    original: str = ""
    pt: str = ""


class Character(Model):
    nome: str
    ator: str | None = None
    genero: Gender = "?"
    origem_genero: GenderSource | None = None
    confianca: float = 0.0
    apelidos: tuple[str, ...] = ()


class Relation(Model):
    a: str
    b: str
    tipo: str
    tratamento_a_para_b: str = ""
    """Como `a` se dirige a `b` (ex.: "você", "o senhor", "tu")."""
    tratamento_b_para_a: str = ""
    confianca: float = 0.0


class GlossaryEntry(Model):
    termo: str
    traducao: str
    tipo: str = "termo"
    confianca: float = 0.0


class SpeakerLink(Model):
    personagem: str
    confianca: float = 0.0


class Bible(Model):
    titulo: Title = Field(default_factory=Title)
    ano: int | None = None
    sinopse: str = ""
    generos: tuple[str, ...] = ()
    runtime_min: int | None = None
    personagens: tuple[Character, ...] = ()
    glossario: tuple[GlossaryEntry, ...] = ()
    tom: str = ""
    registro: str = ""
    """Época, país e nível de linguagem, para gírias e palavrões."""
    relacoes: tuple[Relation, ...] = ()
    falantes: dict[str, SpeakerLink] = Field(default_factory=dict)
    origem: tuple[str, ...] = ()
    """De onde veio cada parte (tmdb, llm, usuario), para o relatório."""

    def character(self, name: str) -> Character | None:
        low = name.lower()
        for c in self.personagens:
            if c.nome.lower() == low or low in (a.lower() for a in c.apelidos):
                return c
        return None
