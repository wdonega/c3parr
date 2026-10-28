"""Cliente de LLM com API compatível com OpenAI (ADR 0002).

Respostas sempre em JSON validado por esquema. A máquina do LLM local acorda por wake-on-LAN, então
a primeira chamada pode ficar presa ~15 s: o timeout é longo e não há timeout curto de conexão.
"""

import json
import logging
import threading
import time
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, Field, SecretStr

from c3parr.core.config import LLM_ENV, load_env
from c3parr.core.errors import InvalidInput, RateLimited, TransientFailure, Unavailable
from c3parr.core.models import Model

log = logging.getLogger("c3parr.llm")


LLMFlavor = Literal["custom", "openai", "anthropic", "gemini", "deepseek"]
# endereço e modelo de cada provedor; a pessoa só põe a chave (e muda o modelo se quiser).
# O servidor local não tem padrão: vem de Settings ou de C3PARR_LLM_BASE_URL/C3PARR_LLM_MODEL.
LLM_PRESETS: dict[str, tuple[str, str]] = {
    "custom": ("", ""),
    "openai": ("https://api.openai.com/v1", "gpt-4.1-mini"),
    "anthropic": ("https://api.anthropic.com", "claude-haiku-5-5"),
    # "latest" acompanha a versão nova: o 2.5 fixo passou a dar 404 para contas novas
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-flash-latest"),
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat"),
}
LLM_KEY_ENV = {
    "custom": "C3PARR_LLM_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}


def llm_defaults(flavor: str) -> tuple[str, str]:
    """(URL, modelo) usados quando Settings não diz outro."""
    if flavor == "custom":
        env = load_env(LLM_ENV)
        return env.get("C3PARR_LLM_BASE_URL", ""), env.get("C3PARR_LLM_MODEL", "")
    return LLM_PRESETS[flavor]


class LLMConfig(BaseModel):
    flavor: LLMFlavor = "custom"
    """Quem atende: servidor local/compatível (`custom`) ou um provedor de nuvem."""
    base_url: str = Field(default_factory=lambda: llm_defaults("custom")[0])
    model: str = Field(default_factory=lambda: llm_defaults("custom")[1])
    timeout_s: float = 180.0
    temperature: float = 0.1
    enable_thinking: bool = False
    effort: Literal["low", "medium"] | None = None
    """Raciocínio na Anthropic (`output_config.effort`) e no Gemini (`reasoning_effort`); None
    deixa o padrão do provedor. Medido na decisão 0006: low traduz igual e gasta menos."""
    max_attempts: int = Field(6, ge=1)
    """2 + 4 + 8 + 16 + 30 s: cobre a máquina acordando (wake-on-LAN) e o servidor reiniciando."""
    backoff_s: float = 2.0
    max_backoff_s: float = 30.0
    api_key: SecretStr | None = Field(None, json_schema_extra={"secret_env": "C3PARR_LLM_API_KEY"})
    sends_data_off_machine: bool = True
    """O servidor local fica em outra máquina da rede; o texto sai deste computador."""


class ChatUsage(Model):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    seconds: float = 0.0


def _structured_output_failed(r: httpx.Response) -> bool:
    if r.status_code < 500:
        return False
    try:
        error = r.json().get("error", {})
    except ValueError:
        return False
    return isinstance(error, dict) and error.get("type") == "structured_output_failed"


class OpenAICompatibleClient:
    def __init__(self, config: LLMConfig, transport: httpx.BaseTransport | None = None) -> None:
        self.config = config
        if config.flavor != "custom" and config.api_key is None:
            raise Unavailable(f"no API key for {config.flavor} (Settings → Integrations)",
                              provider="llm")  # fmt: skip
        headers = {"Content-Type": "application/json"}
        if config.api_key is not None:
            headers["Authorization"] = f"Bearer {config.api_key.get_secret_value()}"
        timeout = httpx.Timeout(config.timeout_s, connect=config.timeout_s)
        self._http = httpx.Client(
            base_url=config.base_url, headers=headers, timeout=timeout, transport=transport
        )

    def warm_up(self) -> threading.Thread:
        """Acorda a máquina do LLM em segundo plano (enquanto o ASR roda); erros são ignorados."""

        def ping() -> None:
            try:
                self._http.get("/models")
            except httpx.HTTPError as e:
                log.debug("LLM warm-up failed: %s", e)

        thread = threading.Thread(target=ping, name="llm-warm-up", daemon=True)
        thread.start()
        return thread

    def health(self) -> tuple[bool, str]:
        try:
            r = self._http.get("/models")
        except httpx.HTTPError as e:
            return False, f"no response: {e}"
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}"
        ids = [str(m.get("id")) for m in r.json().get("data", [])]
        # o Gemini lista como "models/<nome>"
        if not any(i == self.config.model or i.endswith("/" + self.config.model) for i in ids):
            shown = ", ".join(ids[:8]) + (f" and {len(ids) - 8} more" if len(ids) > 8 else "")
            return False, f"model {self.config.model} is not on the server ({shown})"
        return True, "ok"

    def chat_json(
        self, system: str, user: str, schema: dict[str, Any], name: str = "resposta"
    ) -> tuple[Any, ChatUsage]:
        """Uma chamada com saída JSON estrita.

        JSON que não abre vira InvalidInput (quem chama decide se tenta de novo); falha de rede e
        HTTP 5xx tentam de novo aqui, com espera crescente.
        """
        c = self.config
        body: dict[str, Any] = {
            "model": c.model,
            "temperature": c.temperature,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": name, "schema": schema, "strict": True},
            },
        }
        if c.flavor == "custom":  # llama.cpp/vLLM: liga/desliga o raciocínio do modelo
            body["chat_template_kwargs"] = {"enable_thinking": c.enable_thinking}
        if c.flavor == "gemini" and c.effort:  # o raciocínio é cobrado fora de completion_tokens
            body["reasoning_effort"] = c.effort
        if c.flavor == "deepseek":  # só aceita "JSON qualquer": o esquema vai no prompt
            body["response_format"] = {"type": "json_object"}
            body["messages"][0]["content"] = (
                f"{system}\n\nAnswer only with JSON that follows this schema:\n"
                f"{json.dumps(schema, ensure_ascii=False)}"
            )
        last: Exception | None = None
        for attempt in range(1, c.max_attempts + 1):
            t0 = time.perf_counter()
            try:
                r = self._http.post("/chat/completions", json=body)
            except httpx.HTTPError as e:
                last = TransientFailure(f"LLM did not respond: {e}", provider="llm")
            else:
                if r.status_code == 429:
                    retry = float(r.headers.get("retry-after", c.backoff_s * attempt))
                    last = RateLimited(
                        "LLM rate-limited the calls", provider="llm", retry_after=retry
                    )
                elif _structured_output_failed(r):
                    # o servidor checa o JSON e responde 502 quando o modelo erra o formato: é
                    # resposta inválida (quem chamou tenta de novo ou cai no fallback), não queda
                    raise InvalidInput(f"LLM returned invalid JSON: {r.text[:200]}", provider="llm")
                elif r.status_code >= 500:
                    last = TransientFailure(f"LLM answered HTTP {r.status_code}", provider="llm")
                elif r.status_code in (401, 403):
                    # chave errada é falha (alerta), não "resposta inválida" (que cairia no
                    # fallback e deixaria o texto original sem avisar)
                    raise Unavailable(
                        f"LLM rejected the key (HTTP {r.status_code})", provider="llm"
                    )
                elif r.status_code == 404:
                    msg = f"LLM endpoint or model not found (HTTP 404): {r.text[:200]}"
                    raise Unavailable(msg, provider="llm")
                elif r.status_code >= 400:
                    msg = f"LLM refused the call: HTTP {r.status_code} {r.text[:300]}"
                    raise InvalidInput(msg, provider="llm")
                else:
                    return self._parse(r.json(), time.perf_counter() - t0)
            if attempt < c.max_attempts:
                wait = getattr(last, "retry_after", None) or min(
                    c.max_backoff_s, c.backoff_s * 2 ** (attempt - 1)
                )
                log.warning("LLM: %s; retrying in %.0f s", last, wait)
                time.sleep(wait)
        msg = f"LLM unavailable after {c.max_attempts} attempts: {last}"
        raise Unavailable(msg, provider="llm")

    @staticmethod
    def _parse(data: dict[str, Any], seconds: float) -> tuple[Any, ChatUsage]:
        usage = data.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        meta = ChatUsage(
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            cached_tokens=int(details.get("cached_tokens") or 0),
            seconds=round(seconds, 2),
        )
        try:
            content = data["choices"][0]["message"]["content"]
            return json.loads(content), meta
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
            raise InvalidInput(f"LLM response is not valid JSON: {e}", provider="llm") from e


class LLMClient(Protocol):
    def warm_up(self) -> threading.Thread: ...

    def health(self) -> tuple[bool, str]: ...

    def chat_json(
        self, system: str, user: str, schema: dict[str, Any], name: str = "resposta"
    ) -> tuple[Any, ChatUsage]: ...


def make_llm_client(config: LLMConfig) -> LLMClient:
    """O cliente certo para o provedor escolhido em Settings → Integrations."""
    if not config.base_url or not config.model:
        raise Unavailable("no LLM configured: set the URL and model in Settings → Integrations",
                          provider="llm")  # fmt: skip
    if config.flavor == "anthropic":
        from c3parr.llm.anthropic_client import AnthropicClient

        return AnthropicClient(config)
    return OpenAICompatibleClient(config)
