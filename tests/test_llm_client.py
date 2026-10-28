import json
from typing import Any

import httpx
import pytest

from c3parr.core.errors import InvalidInput, Unavailable
from c3parr.llm.client import LLMConfig, OpenAICompatibleClient

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def completion(content: str) -> dict[str, Any]:
    usage = {
        "prompt_tokens": 10,
        "completion_tokens": 3,
        "prompt_tokens_details": {"cached_tokens": 8},
    }
    return {"choices": [{"message": {"content": content}}], "usage": usage}


def make_client(
    responses: list[httpx.Response], seen: list[dict[str, Any]] | None = None
) -> OpenAICompatibleClient:
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        return queue.pop(0)

    config = LLMConfig(base_url="http://llm.test/v1", backoff_s=0.0)
    return OpenAICompatibleClient(config, transport=httpx.MockTransport(handler))


def test_parses_json_and_usage() -> None:
    seen: list[dict[str, Any]] = []
    llm = make_client([httpx.Response(200, json=completion('{"ok": true}'))], seen)
    data, usage = llm.chat_json("sys", "user", SCHEMA)
    assert data == {"ok": True}
    assert (usage.prompt_tokens, usage.cached_tokens) == (10, 8)
    assert seen[0]["response_format"]["json_schema"]["strict"] is True
    assert seen[0]["chat_template_kwargs"] == {"enable_thinking": False}


def test_server_error_is_retried() -> None:
    responses = [httpx.Response(503), httpx.Response(200, json=completion('{"ok": false}'))]
    data, _ = make_client(responses).chat_json("s", "u", SCHEMA)
    assert data == {"ok": False}


def test_invalid_json_is_invalid_input() -> None:
    llm = make_client([httpx.Response(200, json=completion("isso não é json"))])
    with pytest.raises(InvalidInput, match="not valid JSON"):
        llm.chat_json("s", "u", SCHEMA)


def test_persistent_failure_is_unavailable() -> None:
    with pytest.raises(Unavailable, match="6 attempts"):
        make_client([httpx.Response(502)] * 6).chat_json("s", "u", SCHEMA)


def test_server_waking_up_is_retried_until_it_answers() -> None:
    ok = httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})
    data, _ = make_client([httpx.Response(502)] * 4 + [ok]).chat_json("s", "u", SCHEMA)
    assert data == {"ok": True}


def test_client_error_is_not_retried() -> None:
    with pytest.raises(InvalidInput, match="HTTP 400"):
        make_client([httpx.Response(400, text="bad")]).chat_json("s", "u", SCHEMA)


def test_invalid_json_reported_as_502_is_not_an_outage() -> None:
    bad = httpx.Response(502, json={"error": {"type": "structured_output_failed",
                                              "message": "model did not return valid JSON"}})  # fmt: skip
    with pytest.raises(InvalidInput, match="invalid JSON"):
        make_client([bad]).chat_json("s", "u", SCHEMA)


def test_anthropic_schema_closes_every_object() -> None:
    from c3parr.llm.anthropic_client import closed_schema
    from c3parr.translate.llm_translator import SCHEMA

    # a saída estruturada da Anthropic recusa (400) objeto sem additionalProperties: false
    def objects(node: object) -> list[dict]:
        if isinstance(node, list):
            return [o for n in node for o in objects(n)]
        if not isinstance(node, dict):
            return []
        own = [node] if node.get("type") == "object" or "properties" in node else []
        return own + [o for v in node.values() for o in objects(v)]

    closed = closed_schema(SCHEMA)
    assert objects(closed) and all(o["additionalProperties"] is False for o in objects(closed))
    assert "additionalProperties" not in SCHEMA  # o original não muda


def test_gemini_sends_the_reasoning_effort() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=completion('{"ok": true}'))

    config = LLMConfig(
        flavor="gemini", base_url="http://llm.test/v1", effort="low", api_key="k", backoff_s=0.0
    )
    OpenAICompatibleClient(config, transport=httpx.MockTransport(handler)).chat_json(
        "s", "u", SCHEMA
    )
    assert seen[0]["reasoning_effort"] == "low"
    assert "chat_template_kwargs" not in seen[0]  # isso é só do servidor local
