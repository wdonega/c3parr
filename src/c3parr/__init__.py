"""c3parr: gerador e ressincronizador de legendas."""

from importlib.metadata import version

__version__ = version("c3parr")


def main() -> None:
    from c3parr.cli.app import app

    app()
