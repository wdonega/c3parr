"""O que o servidor diz e o catálogo da interface andam juntos.

O servidor escreve em inglês e manda o código da frase (ou o identificador do nome); a interface
escreve no idioma dela por `frontend/src/i18n`. Se uma frase nova nasce no servidor sem entrar no
catálogo, ela apareceria em inglês no meio de uma tela em português: este teste avisa antes.
Os outros idiomas são conferidos contra o inglês nos testes do frontend.
"""

import re
from pathlib import Path

import pytest
from uikit import empty_world

from c3parr.jobs.queue import ACTION_LABELS
from c3parr.web import schemas
from c3parr.web.service import (
    INTERFACE_LABELS,
    LLM_PROVIDERS,
    MESSAGES,
    NOTICES,
    SETUP,
    STEP_LABELS,
)

ROOT = Path(__file__).parent.parent
CATALOG = ROOT / "frontend" / "src" / "i18n" / "en.ts"
FORMAT = ROOT / "frontend" / "src" / "lib" / "format.ts"
# `progress(0.5, "texto")` ou `progress(0.5, f"texto {valor}")` nos motores
PROGRESS_CALL = re.compile(r"progress\([^\n\"]*?, f?\"([^\"]+)\"\)")
PROGRESS_PATTERN = re.compile(r"^  \[/(\^.+\$)/,", re.MULTILINE)
KEY = re.compile(r"^  '([^']+)':", re.MULTILINE)
TEXT = re.compile(r"'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\"")
NAME = re.compile(r"\{(\w+)\}")


def catalog() -> dict[str, list[str]]:
    """Chave → os textos dela (um, ou um por forma quando muda com a quantidade)."""
    source = CATALOG.read_text(encoding="utf-8")
    found = list(KEY.finditer(source))
    out = {}
    for match, following in zip(found, [*found[1:], None], strict=True):
        chunk = source[match.end() : following.start() if following else len(source)]
        out[match.group(1)] = [a or b for a, b in TEXT.findall(chunk.split("\n  //")[0])]
    return out


@pytest.fixture(scope="module")
def texts() -> dict[str, list[str]]:
    return catalog()


def names(*sentences: str) -> set[str]:
    return {name for sentence in sentences for name in NAME.findall(sentence)}


def test_the_catalog_is_read_as_written(texts: dict[str, list[str]]) -> None:
    assert texts["nav.queue"] == ["Queue"]
    assert texts["show.episodes"] == ["{n} episode", "{n} episodes"]
    assert len(texts) > 400


def test_every_sentence_of_the_server_is_in_the_catalog(texts: dict[str, list[str]]) -> None:
    for code, sentence in MESSAGES.items():
        key = f"msg.{code}"
        assert key in texts, f"add '{key}' to frontend/src/i18n (en, pt-BR, es-MX)"
        # a interface só pode pedir um valor que o servidor manda
        assert names(*texts[key]) <= names(sentence), key


@pytest.mark.parametrize(("kind", "table"), [("setup", SETUP), ("notice", NOTICES)])
def test_every_notice_of_the_server_is_in_the_catalog(
    texts: dict[str, list[str]], kind: str, table: dict[str, tuple[str, str]]
) -> None:
    for code, (title, detail) in table.items():
        for part in ("title", "detail"):
            key = f"{kind}.{code}.{part}"
            assert key in texts, f"add '{key}' to frontend/src/i18n (en, pt-BR, es-MX)"
            assert names(*texts[key]) <= names(title, detail), key


@pytest.mark.parametrize(
    ("prefix", "labels"),
    [
        ("step", STEP_LABELS),
        ("action", ACTION_LABELS),
        ("engine", INTERFACE_LABELS),
        ("llm", {"custom": dict(LLM_PROVIDERS)["custom"]}),
    ],
)
def test_names_sent_by_identifier_read_the_same_in_english(
    texts: dict[str, list[str]], prefix: str, labels: dict[str, str]
) -> None:
    for name, label in labels.items():
        assert texts.get(f"{prefix}.{name}") == [label], f"{prefix}.{name}"


def test_every_state_the_api_sends_has_a_word(texts: dict[str, list[str]]) -> None:
    for state in set(schemas.JOB_STATUS.values()):
        assert f"status.{state}" in texts
    for state in set(schemas.STEP_STATE.values()):
        assert f"stepState.{state}" in texts
    for kind in set(schemas.SUB_KIND.values()):
        assert f"sub.{kind}" in texts


def test_routines_engines_and_status_facts_have_a_name(
    texts: dict[str, list[str]], tmp_path: Path
) -> None:
    service = empty_world(tmp_path).app.state.service
    for task in service.task_rows():
        assert texts.get(f"routine.{task['name']}") == [task["label"]]
    for engine in service.interfaces(service.lib.config()):
        for provider in engine["providers"]:
            assert texts.get(f"provider.{provider.id}") == [provider.description]
    for key, label, _value in service.status_view()["about"]:
        assert texts.get(f"fact.{key}") == [label]
    assert {source for _name, source in service.status_view()["secrets"]} <= {
        "settings", "environment", ""}  # fmt: skip
    assert {"system.setIn.settings", "system.setIn.environment"} <= texts.keys()


def test_the_interface_knows_every_progress_message_the_engines_write() -> None:
    """Os motores escrevem o andamento em inglês (`block 3 of 12`); a interface reescreve os
    que reconhece (`progressText`). Um texto novo num motor tem de ganhar o padrão dele."""
    patterns = [re.compile(p) for p in PROGRESS_PATTERN.findall(FORMAT.read_text(encoding="utf-8"))]
    assert len(patterns) >= 9
    written = {}
    for source in sorted((ROOT / "src" / "c3parr").rglob("*.py")):
        if source.name == "fake.py":  # os de teste falam português e não chegam ao usuário
            continue
        for message in PROGRESS_CALL.findall(source.read_text(encoding="utf-8")):
            written[re.sub(r"\{[^}]*\}", "7", message)] = source.name
    assert len(written) >= 9, written
    for message, source in written.items():
        assert any(p.fullmatch(message) for p in patterns), f"{source}: {message!r}"
