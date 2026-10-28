"""Provider `llm` de contexto: o LLM lê o roteiro transcrito e monta ou completa a bíblia."""

import logging
from typing import Any

from pydantic import BaseModel, Field

from c3parr.core.bible import Bible, Character, GlossaryEntry, Relation, SpeakerLink
from c3parr.core.models import Transcript
from c3parr.interfaces import context
from c3parr.interfaces.context import ContextRequest
from c3parr.llm.client import LLMClient, LLMConfig, make_llm_client
from c3parr.providers.base import Health, ProviderInfo, Requirements
from c3parr.subtitle.srt import format_time
from c3parr.translate.languages import language_name

log = logging.getLogger("c3parr.context")


def _obj(**props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(props)}


def _list(item: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": item}


_S: dict[str, Any] = {"type": "string"}
_N: dict[str, Any] = {"type": "number"}
_G: dict[str, Any] = {"type": "string", "enum": ["F", "M", "?"]}

SCHEMA = _obj(
    personagens=_list(_obj(nome=_S, genero=_G, confianca=_N, apelidos=_list(_S), evidencia=_S)),
    falantes=_list(_obj(falante=_S, personagem=_S, confianca=_N)),
    relacoes=_list(
        _obj(a=_S, b=_S, tipo=_S, tratamento_a_para_b=_S, tratamento_b_para_a=_S, confianca=_N)
    ),
    glossario=_list(_obj(termo=_S, traducao=_S, tipo=_S, confianca=_N)),
    tom=_S,
    registro=_S,
    sinopse=_S,
)

SYSTEM = """Você prepara a "bíblia" de um filme para quem vai traduzir as legendas para {dst}.
Você recebe o roteiro transcrito do áudio (pode ter erros de reconhecimento) e, às vezes, dados do
filme já conhecidos. Extraia só o que o texto sustenta; não invente.

- personagens: nome, gênero (F, M ou ? se não houver base), apelidos e a evidência usada. Atenção:
  o texto veio de reconhecimento de fala; em francês, "fatigué" e "fatiguée" soam igual, então a
  grafia da concordância não prova gênero. Prefira vocativos, pronomes (il/elle), artigos e papéis.
- falantes: só se o roteiro tiver rótulos SPEAKER_XX; ligue cada um a um personagem quando der.
- relacoes: pares de personagens, tipo (mãe e filho, irmãos, notário e cliente...) e o tratamento
  em {dst} de um para o outro ("você", "o senhor", "a senhora", "tu").
- glossario: nomes próprios, lugares e termos recorrentes, com a tradução a usar em {dst}
  (nomes próprios normalmente ficam iguais).
- tom e registro: gênero do filme, época, país, nível de linguagem.
- confianca: 0 a 1 em cada item.
Responda só com o JSON pedido."""


class LLMContextConfig(BaseModel):
    llm: LLMConfig = Field(default_factory=lambda: LLMConfig())
    enable_thinking: bool = False
    """A bíblia roda uma vez por filme; pode valer ligar o raciocínio (mais lento)."""


INFO = ProviderInfo(
    id="llm",
    interface=context.INTERFACE,
    version="2",
    config_schema=LLMContextConfig,
    requirements=Requirements(network=True, sends_data_off_machine=True),
    description="LLM reads the script and builds characters, relations, formality and glossary",
)


def script_text(transcript: Transcript) -> str:
    lines = []
    for s in transcript.segments:
        speaker = s.speaker or "?"
        lang = f" [{s.lang}]" if s.lang != transcript.language else ""
        lines.append(f"#{s.id} [{format_time(s.start)[:8]}] {speaker}{lang}: {s.text}")
    return "\n".join(lines)


class LLMContextBuilder:
    info = INFO

    def __init__(self, config: LLMContextConfig, client: LLMClient | None = None) -> None:
        self.config = config
        llm = config.llm.model_copy(update={"enable_thinking": config.enable_thinking})
        if client is None:
            client = make_llm_client(llm)
            client.warm_up()
        self.client = client

    def health(self) -> Health:
        ok, detail = self.client.health()
        return Health(ok=ok, detail=detail)

    def build(self, transcript: Transcript, request: ContextRequest) -> Bible:
        base = request.base or Bible()
        known = base.model_dump_json(exclude_defaults=True) if request.base else "{}"
        user = f"Dados já conhecidos do filme (têm prioridade):\n{known}\n\nRoteiro:\n"
        user += script_text(transcript)
        system = SYSTEM.format(dst=language_name(request.dst))
        data, usage = self.client.chat_json(system, user, SCHEMA, name="biblia")
        log.info("bible: %d input tokens, %.0f s", usage.prompt_tokens, usage.seconds)
        return merge(base, data)


def _known(model: type[BaseModel], item: dict[str, Any]) -> dict[str, Any]:
    """Só os campos que o modelo conhece: o LLM às vezes inventa um a mais (ex.: `evidencia` no
    glossário) e isso não pode derrubar a bíblia inteira."""
    return {k: v for k, v in item.items() if k in model.model_fields}


def merge(base: Bible, data: dict[str, Any]) -> Bible:
    """Junta o que o LLM achou com a bíblia de partida; o que já existia tem prioridade."""
    chars = {c.nome.lower(): c for c in base.personagens}
    for p in data.get("personagens", []):
        key = p["nome"].lower()
        if key in chars:
            known = chars[key]
            if known.genero == "?" and p["genero"] != "?":
                update = {
                    "genero": p["genero"],
                    "origem_genero": "llm",
                    "confianca": p["confianca"],
                }
                chars[key] = known.model_copy(update=update)
            continue
        chars[key] = Character(
            nome=p["nome"],
            genero=p["genero"],
            origem_genero="llm" if p["genero"] != "?" else None,
            confianca=float(p["confianca"]),
            apelidos=tuple(p.get("apelidos", ())),
        )
    speakers = dict(base.falantes)
    for f in data.get("falantes", []):
        link = SpeakerLink(personagem=f["personagem"], confianca=float(f["confianca"]))
        speakers.setdefault(f["falante"], link)
    glossary = {g.termo.lower(): g for g in base.glossario}
    for g in data.get("glossario", []):
        glossary.setdefault(g["termo"].lower(), GlossaryEntry(**_known(GlossaryEntry, g)))
    relations = list(base.relacoes) or [
        Relation(**_known(Relation, r)) for r in data.get("relacoes", [])
    ]
    return base.model_copy(
        update={
            "personagens": tuple(chars.values()),
            "falantes": speakers,
            "glossario": tuple(glossary.values()),
            "relacoes": tuple(relations),
            "tom": base.tom or data.get("tom", ""),
            "registro": base.registro or data.get("registro", ""),
            "sinopse": base.sinopse or data.get("sinopse", ""),
            "origem": tuple(dict.fromkeys((*base.origem, "llm"))),
        }
    )


def register() -> None:
    context.FACTORY.register(INFO, LLMContextBuilder)
