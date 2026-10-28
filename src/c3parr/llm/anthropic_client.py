"""Cliente do Claude (API da Anthropic, SDK oficial) com a mesma interface do cliente OpenAI.

Saída JSON pelo esquema (`output_config.format`), cache do prompt de sistema (a bíblia se repete
em todos os blocos do filme) e fallback automático do servidor quando o modelo recusa.
"""

import json
import logging
import threading
import time
from typing import Any

import anthropic
from anthropic.types.beta import BetaOutputConfigParam

from c3parr.core.errors import InvalidInput, RateLimited, TransientFailure, Unavailable
from c3parr.llm.client import ChatUsage, LLMConfig

log = logging.getLogger("c3parr.llm")
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def closed_schema(schema: Any) -> Any:
    """O esquema com `additionalProperties: false` em todo objeto: a saída estruturada da
    Anthropic recusa (400) objeto sem isso; os servidores compatíveis com OpenAI não exigem."""
    if isinstance(schema, list):
        return [closed_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out = {k: closed_schema(v) for k, v in schema.items()}
    if out.get("type") == "object" or "properties" in out:
        out["additionalProperties"] = False
    return out


class AnthropicClient:
    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        key = config.api_key.get_secret_value() if config.api_key else None
        if not key:
            raise Unavailable("no Anthropic API key (Settings → Integrations)", provider="llm")
        self._client = anthropic.Anthropic(api_key=key, timeout=config.timeout_s, max_retries=3)

    def warm_up(self) -> threading.Thread:
        thread = threading.Thread(target=lambda: None, name="llm-warm-up", daemon=True)
        thread.start()
        return thread

    def health(self) -> tuple[bool, str]:
        try:
            self._client.models.retrieve(self.config.model)
        except anthropic.AuthenticationError:
            return False, "Anthropic rejected the key"
        except anthropic.NotFoundError:
            return False, f"model {self.config.model} not found"
        except anthropic.APIError as e:
            return False, f"no response: {e}"
        return True, "ok"

    def chat_json(
        self, system: str, user: str, schema: dict[str, Any], name: str = "resposta"
    ) -> tuple[Any, ChatUsage]:
        t0 = time.perf_counter()
        output_config: BetaOutputConfigParam = {
            "format": {"type": "json_schema", "schema": closed_schema(schema)}
        }
        if self.config.effort:
            output_config["effort"] = self.config.effort
        try:
            response = self._client.beta.messages.create(
                model=self.config.model,
                max_tokens=16000,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_config=output_config,
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise Unavailable(f"Anthropic rejected the key: {e}", provider="llm") from e
        except anthropic.NotFoundError as e:
            raise Unavailable(
                f"Anthropic model {self.config.model} not found", provider="llm"
            ) from e
        except anthropic.RateLimitError as e:
            raise RateLimited("Anthropic rate-limited the calls", provider="llm") from e
        except anthropic.BadRequestError as e:
            raise InvalidInput(f"Anthropic refused the call: {e.message}", provider="llm") from e
        except (anthropic.APIConnectionError, anthropic.APIStatusError) as e:
            raise TransientFailure(f"Anthropic did not respond: {e}", provider="llm") from e
        if response.stop_reason == "refusal":
            raise InvalidInput("Claude declined this block", provider="llm")
        if response.stop_reason == "max_tokens":
            raise InvalidInput("Claude's answer was cut off (max_tokens)", provider="llm")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise InvalidInput(f"Claude's answer is not valid JSON: {e}", provider="llm") from e
        u = response.usage
        cached = int(u.cache_read_input_tokens or 0)
        written = int(u.cache_creation_input_tokens or 0)
        usage = ChatUsage(
            prompt_tokens=int(u.input_tokens or 0) + cached + written,
            completion_tokens=int(u.output_tokens or 0),
            cached_tokens=cached,
            seconds=round(time.perf_counter() - t0, 2),
        )
        return data, usage
