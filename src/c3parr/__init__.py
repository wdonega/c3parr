"""c3parr: gerador e ressincronizador de legendas."""

import os
from importlib.metadata import version

# a imagem Docker grava a versão do build (1.0.N, a mesma da release); fora dela, a do pacote
__version__ = os.environ.get("C3PARR_VERSION") or version("c3parr")
REPO_URL = "https://github.com/wdonega/c3parr"


def main() -> None:
    from c3parr.cli.app import app

    app()
