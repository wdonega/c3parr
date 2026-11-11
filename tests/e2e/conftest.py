"""Testes no navegador de verdade (Playwright), com a biblioteca de exemplo servida de fato.

Ficam fora da rodada normal: `uv run pytest -m e2e`. Na primeira vez, baixe o navegador com
`uv run playwright install chromium` e construa a interface (`npm run build` em frontend/).
"""

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
import uvicorn
from playwright.sync_api import Browser, sync_playwright
from uikit import World


class Served(NamedTuple):
    world: World
    url: str


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    here = Path(__file__).parent
    for item in items:
        if here in item.path.parents:
            item.add_marker(pytest.mark.e2e)


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch()
        yield chromium
        chromium.close()


def serve(world: World) -> Iterator[Served]:
    """Sobe o app numa porta livre e o derruba no fim."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(world.app, host="127.0.0.1", port=port,
                                           log_level="warning"))  # fmt: skip
    thread = threading.Thread(target=server.run, name="c3parr-e2e", daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.05)
    yield Served(world, f"http://127.0.0.1:{port}")
    server.should_exit = True
    thread.join(5)
