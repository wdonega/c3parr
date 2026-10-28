"""Nomes de idioma para os prompts (o LLM entende melhor "português do Brasil" que "pt-BR")."""

NAMES = {
    "pt-br": "português do Brasil",
    "pt": "português",
    "pt-pt": "português de Portugal",
    "en": "inglês",
    "fr": "francês",
    "es": "espanhol",
    "it": "italiano",
    "de": "alemão",
    "ar": "árabe",
    "ja": "japonês",
}


def language_name(code: str) -> str:
    key = code.lower()
    return NAMES.get(key) or NAMES.get(key.split("-")[0]) or code


# nome do idioma escrito no idioma da legenda, para a marca de fala em outra língua
NAMES_EN = {
    "en": "English", "fr": "French", "es": "Spanish", "it": "Italian", "de": "German",
    "ar": "Arabic", "ja": "Japanese", "pt": "Portuguese", "ru": "Russian", "zh": "Chinese",
    "ko": "Korean", "he": "Hebrew", "tr": "Turkish", "pl": "Polish", "nl": "Dutch",
}  # fmt: skip
NAMES_PT_EXTRA = {
    "ru": "russo", "zh": "chinês", "ko": "coreano", "he": "hebraico", "tr": "turco",
    "pl": "polonês", "nl": "holandês", "la": "latim", "el": "grego", "hi": "híndi",
}  # fmt: skip


def foreign_tag(lang: str, dst: str) -> str:
    """Marca no começo da fala dita em outra língua: `[em árabe]` numa legenda em português."""
    base = lang.lower().split("-")[0]
    if dst.lower().startswith("pt"):
        name = NAMES.get(base) or NAMES_PT_EXTRA.get(base) or base
        return f"[em {name}]"
    return f"[in {NAMES_EN.get(base, base)}]"
