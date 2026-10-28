"""Provider `llm` de tradução: um bloco (cena) por chamada, com a bíblia num prefixo fixo do prompt.

Resposta inválida tem uma nova tentativa; se falhar de novo, o bloco sai no idioma original e vai
para o relatório. LLM fora do ar levanta `Unavailable` e o pipeline entrega a transcrição.
"""

import logging
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from c3parr.core.bible import Bible
from c3parr.core.errors import InvalidInput
from c3parr.interfaces import translation
from c3parr.interfaces.translation import (
    Block,
    BlockLine,
    Mention,
    TranslatedLine,
    Translation,
    TranslationRequest,
    TranslationUsage,
)
from c3parr.llm.client import ChatUsage, LLMClient, LLMConfig, make_llm_client
from c3parr.providers.base import Health, ProviderInfo, Requirements
from c3parr.translate.languages import language_name

log = logging.getLogger("c3parr.translate")


def _obj(**props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(props)}


def _list(item: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": item}


_S: dict[str, Any] = {"type": "string"}
_I: dict[str, Any] = {"type": "integer"}
_G: dict[str, Any] = {"type": "string", "enum": ["F", "M", "?"]}

SCHEMA = _obj(
    falas=_list(
        _obj(
            id=_I,
            texto=_S,
            incerto={"type": "boolean"},
            motivo={"type": ["string", "null"]},
            genero_falante=_G,
            genero_ouvinte=_G,
            mencoes=_list(_obj(entidade=_S, original=_S, traducao=_S)),
        )
    )
)
CONDENSE_SCHEMA = _obj(falas=_list(_obj(id=_I, texto=_S)))

SYSTEM = """Você traduz legendas de filme para {dst}, a partir do que foi falado no original.
A coluna "ref" (quando existe) é uma legenda em outro idioma: use só como desempate, sobretudo
quando o reconhecimento de fala errou; não traduza a referência.

Regras:
- Gênero: use os rótulos entre parênteses (F/M) de quem fala e de quem ouve. Em francês falado a
  concordância muitas vezes não se ouve ("fatigué" e "fatiguée" soam igual), então não confie na
  grafia do original. Sem base para escolher, marque "incerto": true, explique em "motivo" e use
  uma forma neutra quando existir em {dst}. Se a tradução não muda com o gênero (imperativos,
  frases sem adjetivo, particípio ou pronome marcado), NÃO marque incerto por causa de gênero.
- Tratamento: siga as relações da bíblia. Em português do Brasil, "você" é o padrão informal,
  "o senhor"/"a senhora" o formal, e "tu" só se o contexto pedir. Na dúvida, marque incerto.
- Glossário: nomes e termos do glossário nunca mudam de tradução.
- Registro: respeite época, país e tom do filme em gírias e palavrões.
- Tamanho: cada fala tem um máximo de caracteres; condense o sentido para caber, sem cortar
  informação importante.
- Música, avisos e créditos não são fala: devolva "texto" vazio para eles.
- Linhas marcadas como [CONTEXTO] são de outra cena: não as traduza e não assuma continuidade.
- "genero_falante" e "genero_ouvinte": o gênero que você usou na tradução (F, M ou ?).
- "mencoes": nomes próprios e termos do glossário que aparecem na fala, com o trecho no original e
  o trecho exato que você escreveu na tradução.
Responda só com o JSON pedido, uma entrada por fala do bloco, com o mesmo id.

Bíblia do filme:
{bible}"""

CONDENSE = """Encurte cada fala para caber no máximo de caracteres indicado, em {dst}, mantendo o
sentido, o gênero e o tratamento. Responda só com o JSON pedido."""


class LLMTranslatorConfig(BaseModel):
    llm: LLMConfig = Field(default_factory=lambda: LLMConfig())
    condense_margin: float = 1.15
    """Fala acima de max_chars x margem recebe um pedido para condensar."""


INFO = ProviderInfo(
    id="llm",
    interface=translation.INTERFACE,
    version="2",
    config_schema=LLMTranslatorConfig,
    capabilities=frozenset({"contexto", "glossario", "incerteza", "mencoes", "genero"}),
    requirements=Requirements(network=True, sends_data_off_machine=True),
    description="LLM with bible, scene blocks and structured output",
)


def _label(name: str | None, gender: str) -> str:
    return f"{name or '?'}({gender})"


def format_line(line: BlockLine, src: str) -> str:
    speaker = _label(line.speaker, line.speaker_gender)
    addressee = _label(line.addressee, line.addressee_gender)
    lang = f" [{line.lang}]" if line.lang != src else ""
    ref = f' / ref: "{line.ref}"' if line.ref else ""
    return f'#{line.id} {speaker}→{addressee}{lang} : "{line.text}"{ref} (máx. {line.max_chars})'


def format_block(block: Block, src: str) -> str:
    parts = []
    if block.previous:
        parts.append("[CONTEXTO: outra cena, não traduzir]")
        parts += [
            f'  {_label(ln.speaker, ln.speaker_gender)} : "{ln.text}"' for ln in block.previous
        ]
    if block.pause_before is not None:
        parts.append(f"[--- pausa de {block.pause_before:.0f} s: outra cena ---]")
    parts.append(f"[BLOCO {block.index}]")
    parts += [format_line(ln, src) for ln in block.lines]
    return "\n".join(parts)


class LLMTranslator:
    info = INFO

    def __init__(self, config: LLMTranslatorConfig, client: LLMClient | None = None) -> None:
        self.config = config
        if client is None:
            client = make_llm_client(config.llm)
            client.warm_up()
        self.client = client

    def health(self) -> Health:
        ok, detail = self.client.health()
        return Health(ok=ok, detail=detail)

    def translate(
        self,
        blocks: list[Block],
        bible: Bible,
        request: TranslationRequest,
        progress: Callable[[float, str], None],
    ) -> Translation:
        dst = language_name(request.dst)
        bible_json = bible.model_dump_json(exclude_defaults=True)
        system = SYSTEM.format(dst=dst, bible=bible_json)
        usage = TranslationUsage()
        out: list[TranslatedLine] = []
        for n, block in enumerate(blocks):
            lines, usage = self._block(system, block, request.src, dst, usage)
            out.extend(lines)
            progress((n + 1) / max(1, len(blocks)), f"bloco {n + 1}/{len(blocks)}")
        return Translation(lines=tuple(out), dst=request.dst, usage=usage)

    def _block(
        self, system: str, block: Block, src: str, dst: str, usage: TranslationUsage
    ) -> tuple[list[TranslatedLine], TranslationUsage]:
        user = format_block(block, src)
        expected = {ln.id for ln in block.lines}
        for attempt in (1, 2, 3):
            try:
                data, meta = self.client.chat_json(system, user, SCHEMA, name="traducao")
                usage = _add(usage, meta)
                items = {int(f["id"]): f for f in data["falas"]}
                if not expected <= items.keys():
                    raise InvalidInput(f"response missing lines {sorted(expected - items.keys())}")
            except (InvalidInput, KeyError, TypeError, ValueError) as e:
                log.warning("block %d, attempt %d: %s", block.index, attempt, e)
                continue
            lines = [self._line(ln, items[ln.id]) for ln in block.lines]
            return self._condense(block, lines, dst, usage)
        if len(block.lines) > 1:
            # pedido menor o modelo erra menos: tenta fala por fala antes de desistir
            log.warning("block %d: translating line by line", block.index)
            out: list[TranslatedLine] = []
            for ln in block.lines:
                single = block.model_copy(update={"lines": (ln,)})
                lines, usage = self._block(system, single, src, dst, usage)
                out.extend(lines)
            return out, usage
        reason = "invalid LLM response three times; kept the original"
        fallback = [
            TranslatedLine(id=ln.id, texto=ln.text, incerto=True, fallback=True, motivo=reason)
            for ln in block.lines
        ]
        return fallback, usage

    def _line(self, src: BlockLine, item: dict[str, Any]) -> TranslatedLine:
        texto = str(item["texto"]).strip()
        incerto, motivo = bool(item["incerto"]), item.get("motivo")
        gf, go = item["genero_falante"], item["genero_ouvinte"]
        conflicts = []
        if src.speaker_gender != "?" and gf not in ("?", src.speaker_gender):
            conflicts.append(f"used {gf} for the speaker, label is {src.speaker_gender}")
        if src.addressee_gender != "?" and go not in ("?", src.addressee_gender):
            conflicts.append(f"used {go} for the listener, label is {src.addressee_gender}")
        if conflicts:
            incerto = True
            motivo = "; ".join([*conflicts, *([motivo] if motivo else [])])
        mentions = tuple(
            Mention(
                entidade=m["entidade"],
                original=m["original"],
                traducao=m["traducao"],
                rastreada=bool(m["traducao"]) and m["traducao"].lower() in texto.lower(),
            )
            for m in item.get("mencoes", [])
        )
        return TranslatedLine(
            id=src.id,
            texto=texto,
            incerto=incerto,
            motivo=motivo,
            genero_falante=gf,
            genero_ouvinte=go,
            mencoes=mentions,
        )

    def _condense(
        self, block: Block, lines: list[TranslatedLine], dst: str, usage: TranslationUsage
    ) -> tuple[list[TranslatedLine], TranslationUsage]:
        limit = {ln.id: ln.max_chars for ln in block.lines}
        long = [t for t in lines if len(t.texto) > limit[t.id] * self.config.condense_margin]
        if not long:
            return lines, usage
        user = "\n".join(f'#{t.id} (máx. {limit[t.id]} caracteres): "{t.texto}"' for t in long)
        system = CONDENSE.format(dst=dst)
        try:
            data, meta = self.client.chat_json(system, user, CONDENSE_SCHEMA, name="condensar")
        except InvalidInput as e:
            log.warning("condensing block %d: %s", block.index, e)
            return lines, usage
        usage = _add(usage, meta)
        shorter = {int(f["id"]): str(f["texto"]).strip() for f in data["falas"]}
        updated = [
            t.model_copy(update={"texto": shorter[t.id]}) if shorter.get(t.id) else t for t in lines
        ]
        return updated, usage


def _add(usage: TranslationUsage, meta: ChatUsage) -> TranslationUsage:
    return TranslationUsage(
        calls=usage.calls + 1,
        prompt_tokens=usage.prompt_tokens + meta.prompt_tokens,
        completion_tokens=usage.completion_tokens + meta.completion_tokens,
        cached_tokens=usage.cached_tokens + meta.cached_tokens,
        seconds=round(usage.seconds + meta.seconds, 2),
    )


def register() -> None:
    translation.FACTORY.register(INFO, LLMTranslator)
