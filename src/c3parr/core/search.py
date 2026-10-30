"""Busca por texto das listas da interface: sem caixa nem acento, palavras em qualquer ordem."""

import unicodedata


def fold(text: str | None) -> str:
    """`Quarantième` vira `quarantieme`: a busca acha com ou sem acento e maiúscula."""
    base = unicodedata.normalize("NFKD", (text or "").casefold())
    return "".join(ch for ch in base if not unicodedata.combining(ch))


def words(query: str | None) -> list[str]:
    return fold(query).split()


def matches(query: str | None, *fields: object) -> bool:
    """Cada palavra da busca aparece em algum campo (`super s02` acha `Supernatural - S02E10`)."""
    haystack = fold(" ".join(str(f) for f in fields if f is not None))
    return all(w in haystack for w in words(query))
