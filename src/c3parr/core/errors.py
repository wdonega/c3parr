"""Erros tipados que qualquer provider pode levantar; o motor os converte em falha do estágio."""

from typing import ClassVar


class ProviderError(Exception):
    kind: ClassVar[str] = "erro"

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider


class Unavailable(ProviderError):
    kind = "indisponivel"


class RateLimited(ProviderError):
    kind = "limite_de_uso"

    def __init__(
        self, message: str, *, provider: str | None = None, retry_after: float | None = None
    ) -> None:
        super().__init__(message, provider=provider)
        self.retry_after = retry_after


class InvalidInput(ProviderError):
    kind = "entrada_invalida"


class TransientFailure(ProviderError):
    kind = "falha_temporaria"
