"""Tradução sem rede: blocos por cena, prompt, nova tentativa, fallback e checagem de gênero."""

from typing import Any, cast

from c3parr.core.bible import Bible, Character, SpeakerLink
from c3parr.core.errors import InvalidInput
from c3parr.core.models import Segment
from c3parr.interfaces.translation import TranslationRequest
from c3parr.llm.client import ChatUsage, OpenAICompatibleClient
from c3parr.translate.blocks import build_blocks
from c3parr.translate.llm_context import LLMContextConfig, merge
from c3parr.translate.llm_translator import LLMTranslator, LLMTranslatorConfig, format_block


def seg(i: int, start: float, end: float, text: str, speaker: str | None = None) -> Segment:
    return Segment(id=i, start=start, end=end, text=text, lang="fr", speaker=speaker)


BIBLE = Bible(
    personagens=(Character(nome="Marie", genero="F"), Character(nome="Paul", genero="M")),
    falantes={
        "SPEAKER_00": SpeakerLink(personagem="Marie"),
        "SPEAKER_01": SpeakerLink(personagem="Paul"),
    },
)


def test_scene_pause_starts_new_block_with_previous_context() -> None:
    segments = [seg(0, 0, 2, "Bonjour."), seg(1, 2.5, 4, "Ça va ?"), seg(2, 10, 12, "Plus tard.")]
    blocks = build_blocks(segments, Bible())
    assert [[ln.id for ln in b.lines] for b in blocks] == [[0, 1], [2]]
    assert [ln.id for ln in blocks[1].previous] == [0, 1]
    assert blocks[1].pause_before == 6


def test_two_speakers_get_each_other_as_addressee() -> None:
    segments = [seg(0, 0, 2, "Tu es fatigué ?", "SPEAKER_00"), seg(1, 2.2, 4, "Non.", "SPEAKER_01")]
    block = build_blocks(segments, BIBLE)[0]
    first = block.lines[0]
    labels = (first.speaker, first.speaker_gender, first.addressee, first.addressee_gender)
    assert labels == ("Marie", "F", "Paul", "M")
    assert '#0 Marie(F)→Paul(M) : "Tu es fatigué ?"' in format_block(block, "fr")


class FakeClient:
    def __init__(self, replies: list[Any]) -> None:
        self.replies = replies
        self.calls = 0

    def chat_json(self, system: str, user: str, schema: dict[str, Any], name: str = "") -> Any:
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply, ChatUsage(prompt_tokens=100, completion_tokens=10)


def line(i: int, texto: str, gf: str = "?", go: str = "?", **kw: Any) -> dict[str, Any]:
    base = {
        "id": i,
        "texto": texto,
        "incerto": False,
        "motivo": None,
        "genero_falante": gf,
        "genero_ouvinte": go,
        "mencoes": [],
    }
    return {**base, **kw}


def translator(replies: list[Any]) -> tuple[LLMTranslator, FakeClient]:
    client = FakeClient(replies)
    return LLMTranslator(LLMTranslatorConfig(), cast(OpenAICompatibleClient, client)), client


REQ = TranslationRequest(dst="pt-BR", src="fr")


def test_invalid_answer_is_retried_then_falls_back() -> None:
    blocks = build_blocks([seg(0, 0, 2, "Bonjour.")], Bible())
    tr, client = translator([InvalidInput("json"), {"falas": []}, InvalidInput("json")])
    result = tr.translate(blocks, Bible(), REQ, lambda *_: None)
    assert client.calls == 3
    assert result.lines[0].fallback
    assert result.lines[0].texto == "Bonjour."


def test_failed_block_is_retried_line_by_line() -> None:
    blocks = build_blocks([seg(0, 0, 2, "Bonjour."), seg(1, 2.2, 4, "Ça va ?")], Bible())
    bad = [InvalidInput("json")] * 3
    tr, client = translator([*bad, {"falas": [line(0, "Bom dia.")]}, *bad])
    result = tr.translate(blocks, Bible(), REQ, lambda *_: None)
    assert client.calls == 7
    assert [ln.texto for ln in result.lines] == ["Bom dia.", "Ça va ?"]
    assert [ln.fallback for ln in result.lines] == [False, True]


def test_gender_conflict_with_label_becomes_uncertain() -> None:
    segments = [seg(0, 0, 3, "Tu es fatigué ?", "SPEAKER_00"), seg(1, 3.2, 5, "Non.", "SPEAKER_01")]
    blocks = build_blocks(segments, BIBLE)
    reply = {"falas": [line(0, "Você está cansada?", "F", "F"), line(1, "Não.", "M", "F")]}
    tr, _ = translator([reply])
    result = tr.translate(blocks, BIBLE, REQ, lambda *_: None)
    first = result.lines[0]
    assert first.incerto
    assert "listener" in (first.motivo or "")
    assert not result.lines[1].incerto


def test_mentions_are_checked_against_the_translation() -> None:
    blocks = build_blocks([seg(0, 0, 3, "Marie, viens !")], Bible())
    mentions = [
        {"entidade": "Marie", "original": "Marie", "traducao": "Maria"},
        {"entidade": "Paris", "original": "Paris", "traducao": "Paris"},
    ]
    tr, _ = translator([{"falas": [line(0, "Maria, venha!", mencoes=mentions)]}])
    result = tr.translate(blocks, Bible(), REQ, lambda *_: None)
    assert [m.rastreada for m in result.lines[0].mencoes] == [True, False]


def test_too_long_translation_is_condensed_by_the_llm() -> None:
    blocks = build_blocks([seg(0, 0, 1.0, "Non.")], Bible())
    long = "Não, de jeito nenhum, eu jamais faria uma coisa dessas na minha vida inteira."
    tr, client = translator([{"falas": [line(0, long)]}, {"falas": [{"id": 0, "texto": "Não."}]}])
    result = tr.translate(blocks, Bible(), REQ, lambda *_: None)
    assert client.calls == 2
    assert result.lines[0].texto == "Não."
    assert result.usage.calls == 2


def test_bible_merge_keeps_known_data() -> None:
    base = Bible(personagens=(Character(nome="Nawal", genero="F", origem_genero="tmdb"),))
    data = {
        "personagens": [
            {"nome": "Nawal", "genero": "M", "confianca": 0.4, "apelidos": [], "evidencia": ""},
            {"nome": "Simon", "genero": "M", "confianca": 0.9, "apelidos": [], "evidencia": "il"},
        ],
        "falantes": [],
        "relacoes": [],
        "glossario": [],
        "tom": "drama",
        "registro": "",
        "sinopse": "",
    }
    data["glossario"] = [{"termo": "BAU", "traducao": "BAU", "tipo": "sigla", "confianca": 0.9,
                          "evidencia": "campo que o LLM inventou"}]  # fmt: skip
    merged = merge(base, data)
    got = [(c.nome, c.genero, c.origem_genero) for c in merged.personagens]
    assert got == [("Nawal", "F", "tmdb"), ("Simon", "M", "llm")]
    assert merged.tom == "drama"
    assert merged.glossario[0].termo == "BAU"
    assert LLMContextConfig().enable_thinking is False
