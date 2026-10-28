"""Decisor da pipeline de legendas: estado do item → próximos passos. Função pura, sem disco.

Ordem (do pedido do usuário):
1. metadados (TMDb) — não achou segue; falhou para.
2. idiomas alvo resolvidos por legenda embutida/externa de outra fonte → se todos, acabou.
3. legenda principal: embutida no idioma original → externa no original → embutida/externa
   num idioma de apoio → baixada no original (sincronizada pelo ASR) → gerada pelo ASR.
4. principal num idioma alvo → publica a nossa naquele idioma.
5. cada alvo que falta → traduz a principal.

"Não encontrado" é resultado normal (segue para o próximo ramo); "falhou" bloqueia o ramo até a
próxima rodada da rotina (`retry_failed`) ou do botão "tentar agora".
"""

from typing import Literal

from pydantic import Field

from c3parr.core.models import Model
from c3parr.library.store import AppConfig
from c3parr.pipeline.naming import same_base, same_lang

ActionKind = Literal["metadados", "extrair", "asr", "sincronizar", "gerar",
                     "traduzir", "publicar", "remover"]  # fmt: skip
SourceKind = Literal["embedded", "external", "asr"]


class EmbeddedTrack(Model):
    index: int
    lang: str | None = None
    forced: bool = False
    text_based: bool = True
    extracted_path: str | None = None
    """Arquivo `.embedded.<idioma>.c3parr` já extraído, se houver."""


class ExternalSub(Model):
    path: str
    lang: str | None = None
    forced: bool = False


class OurOutput(Model):
    path: str
    main_version: int
    exists: bool = True
    modified: bool = False
    """O arquivo não bate com o que gravamos: alguém mexeu (não tocamos mais nele)."""


class MainSub(Model):
    path: str
    lang: str
    source: SourceKind
    version: int = 0


class ItemState(Model):
    video: str
    original_language: str | None = None
    audio_language: str | None = None
    embedded: tuple[EmbeddedTrack, ...] = ()
    externals: tuple[ExternalSub, ...] = ()
    """Legendas de outras fontes ao lado do vídeo (sem as nossas)."""
    outputs: dict[str, OurOutput] = Field(default_factory=dict)
    main: MainSub | None = None
    asr_path: str | None = None
    asr_language: str | None = None
    generated: dict[str, str] = Field(default_factory=dict)
    """idioma → arquivo `.main.<idioma>.c3parr` gerado do ASR."""
    steps: dict[str, str] = Field(default_factory=dict)
    """`acao` ou `acao:idioma` → ok | nao_encontrado | falhou (último resultado)."""


class Action(Model):
    kind: ActionKind
    lang: str | None = None
    arg: str | None = None
    """Faixa embutida (índice) ou caminho da legenda, quando a ação precisa."""
    reason: str


class Decision(Model):
    actions: tuple[Action, ...] = ()
    resolved: dict[str, str] = Field(default_factory=dict)
    """idioma alvo → como foi resolvido (embedded, external, ours, outdated, edited)."""
    main: MainSub | None = None
    """Principal que deve valer agora (o reconciliador grava se mudou)."""
    blocked: tuple[str, ...] = ()
    """Passos que falharam e esperam nova tentativa."""
    notes: tuple[str, ...] = ()
    queued: int = 0
    """Quantos passos viraram tarefa nova (o reconciliador preenche; já na fila não conta)."""


def step_key(action: str, lang: str | None = None) -> str:
    return f"{action}:{lang}" if lang else action


def sync_key(path: str) -> str:
    """O passo de sincronizar é por arquivo (pode haver duas legendas no mesmo idioma)."""
    return f"sincronizar:{path}"


class _Ctx:
    def __init__(self, s: ItemState, retry_failed: bool) -> None:
        self.s = s
        self.retry = retry_failed
        self.blocked: list[str] = []

    def state(self, key: str) -> str | None:
        return self.s.steps.get(key)

    def needs(self, key: str) -> bool:
        """O passo ainda precisa rodar? (nunca rodou, ou falhou e é hora de tentar de novo)."""
        st = self.state(key)
        if st == "falhou":
            if self.retry:
                return True
            self.blocked.append(key)
            return False
        return st is None

    def failed_waiting(self, key: str) -> bool:
        return self.state(key) == "falhou" and not self.retry


def _usable_embedded(t: EmbeddedTrack, cfg: AppConfig) -> bool:
    return cfg.use_embedded and t.text_based and not t.forced and t.lang is not None


def _done(actions: list[Action], **kw: object) -> Decision:
    unique = list({(a.kind, a.lang, a.arg): a for a in actions}.values())
    return Decision.model_validate({"actions": tuple(unique), **kw})


def decide(s: ItemState, cfg: AppConfig, retry_failed: bool = False) -> Decision:
    c = _Ctx(s, retry_failed)
    actions: list[Action] = []
    notes = [f"embedded track {t.index} ({t.lang or 'und'}) is an image subtitle (needs OCR); "
             "ignored" for t in s.embedded if not t.text_based]  # fmt: skip

    # 1. metadados: idioma original e elenco. Falhou = para tudo neste item.
    if c.needs("metadados"):
        return _done([Action(kind="metadados", reason="no metadata yet")], notes=tuple(notes))
    if c.failed_waiting("metadados"):
        return _done([], blocked=("metadados",), notes=tuple(notes))

    # 2. alvos resolvidos por outras fontes
    resolved: dict[str, str] = {}
    for lang in cfg.target_languages:
        if (
            cfg.embedded_resolves
            and cfg.use_embedded
            and any(
                same_lang(t.lang, lang) and t.text_based and (not t.forced or cfg.forced_resolves)
                for t in s.embedded
            )
        ):
            resolved[lang] = "embedded"
        elif cfg.external_resolves and any(
            same_lang(e.lang, lang) and (not e.forced or cfg.forced_resolves) for e in s.externals
        ):
            resolved[lang] = "external"

    # legenda externa nova: sincroniza (quando há referência de tempo) e apaga a nossa
    reference = s.asr_path or (s.main.path if s.main else None)
    for e in s.externals:
        if e.lang is None or e.forced or not cfg.sync_new_external or reference is None:
            continue
        if s.main is not None and e.path == s.main.path and not s.asr_path:
            continue  # ela mesma é a referência de tempo
        if c.needs(sync_key(e.path)):
            actions.append(Action(kind="sincronizar", lang=e.lang, arg=e.path,
                                  reason="new external subtitle"))  # fmt: skip
    for lang, out in s.outputs.items():
        human = resolved.get(lang) in ("embedded", "external")
        if human and out.exists and cfg.delete_ours_on_human and not out.modified:
            actions.append(
                Action(
                    kind="remover",
                    lang=lang,
                    arg=out.path,
                    reason=f"a {resolved[lang]} subtitle now covers {lang}",
                )
            )

    pending = [lang for lang in cfg.target_languages if lang not in resolved]
    if not pending:
        return _done(actions, resolved=resolved, blocked=tuple(c.blocked), notes=tuple(notes))

    # 3. legenda principal
    best, wait = _choose_main(s, cfg, c, actions)
    current = s.main
    if best is None:
        if current is None or not _exists_in(s, current):
            return _done(actions, resolved=resolved, blocked=tuple(c.blocked),
                         notes=tuple([*notes, *wait]))  # fmt: skip
        main = current  # a melhor ainda está a caminho; segue com a atual
    elif current is None:
        main = best.model_copy(update={"version": 1})
    elif (current.path, current.lang, current.source) == (best.path, best.lang, best.source):
        main = current
    elif cfg.new_external_becomes_main or not _exists_in(s, current):
        main = best.model_copy(update={"version": current.version + 1})
    else:
        main = current

    if cfg.asr_always and cfg.asr_enabled and s.asr_path is None:
        if c.needs("asr"):
            actions.append(Action(kind="asr", reason="ASR always on (more context)"))
        return _done(actions, resolved=resolved, main=main, blocked=tuple(c.blocked),
                     notes=tuple(notes))  # fmt: skip

    # 4-5. principal publicada no próprio idioma; o resto traduzido
    for lang in pending:
        cur = s.outputs.get(lang)
        kind: ActionKind = "publicar" if same_lang(main.lang, lang) else "traduzir"
        if cur is not None and cur.exists:
            if cur.modified:
                resolved[lang] = "edited"
                continue
            if cur.main_version >= main.version:
                resolved[lang] = "ours"
                continue
            if cfg.derived_on_main_change != "regenerate":
                resolved[lang] = "ours" if cfg.derived_on_main_change == "keep" else "outdated"
                continue
        key = step_key(kind, lang)
        if c.failed_waiting(key):
            c.blocked.append(key)
            continue
        stale = cur is not None and cur.exists
        why = "main subtitle changed" if stale else f"no {lang} subtitle yet"
        actions.append(Action(kind=kind, lang=lang, arg=main.path, reason=why))
    return _done(actions, resolved=resolved, main=main,
                 blocked=tuple(dict.fromkeys(c.blocked)), notes=tuple(notes))  # fmt: skip


def _exists_in(s: ItemState, m: MainSub) -> bool:
    """A principal atual ainda está disponível (arquivo de trabalho ou legenda externa)?"""
    known = {e.path for e in s.externals} | {t.extracted_path for t in s.embedded}
    known |= set(s.generated.values())
    return m.path in known


def _original(s: ItemState) -> str | None:
    return s.original_language or s.audio_language


def _choose_main(s: ItemState, cfg: AppConfig, c: _Ctx,
                 actions: list[Action]) -> tuple[MainSub | None, list[str]]:  # fmt: skip
    """A melhor principal disponível agora, ou o passo que falta para chegar nela."""
    orig = _original(s)
    for lang in dict.fromkeys(x for x in [orig, *cfg.fallback_languages] if x):
        emb = next((t for t in s.embedded if _usable_embedded(t, cfg) and same_base(t.lang, lang)),
                   None)  # fmt: skip
        if emb is not None:
            if emb.extracted_path:
                return MainSub(
                    path=emb.extracted_path, lang=emb.lang or lang, source="embedded"
                ), []
            track_lang = emb.lang or lang  # o arquivo extraído leva o idioma da faixa
            key = step_key("extrair", track_lang)
            if c.needs(key):
                actions.append(
                    Action(
                        kind="extrair",
                        lang=track_lang,
                        arg=str(emb.index),
                        reason=f"embedded {track_lang} subtitle becomes the main one",
                    )
                )
            return None, []
        ext = next((e for e in s.externals if same_base(e.lang, lang) and not e.forced), None)
        if ext is not None:
            skey = sync_key(ext.path)
            if cfg.sync_new_external and s.asr_path is not None and c.state(skey) is None:
                actions.append(
                    Action(
                        kind="sincronizar",
                        lang=lang,
                        arg=ext.path,
                        reason=f"external {lang} subtitle becomes the main one",
                    )
                )
                return None, []
            if c.failed_waiting(skey):
                c.blocked.append(skey)
                return None, []
            return MainSub(path=ext.path, lang=ext.lang or lang, source="external"), []

    # nada pronto: ASR e a legenda gerada dele (buscar legenda online fica com o Bazarr)
    if s.asr_path is None:
        if not cfg.asr_enabled:
            return None, ["no subtitle to use as main and ASR is disabled"]
        if c.needs("asr"):
            actions.append(Action(kind="asr", reason="no subtitle to use as the main one"))
        return None, []
    lang = orig or s.asr_language or "und"
    if s.generated.get(lang):
        return MainSub(path=s.generated[lang], lang=lang, source="asr"), []
    key = step_key("gerar", lang)
    if c.needs(key):
        actions.append(Action(kind="gerar", lang=lang, reason="subtitle from the ASR transcript"))
    return None, []
