"""Testes no navegador de verdade (Playwright), com a biblioteca de exemplo servida de fato.

Ficam fora da rodada normal: `uv run pytest -m e2e`. Na primeira vez, baixe o navegador com
`uv run playwright install chromium`. A interface de hoje carrega BeerCSS e htmx de CDN, então
estes testes precisam de internet.
"""

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
import uvicorn
from pages import LegacyUi
from playwright.sync_api import Browser, sync_playwright
from uikit import World, seed_world


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


@pytest.fixture
def served(tmp_path: Path) -> Iterator[Served]:
    """A interface antiga com a biblioteca de exemplo."""
    yield from serve(seed_world(tmp_path))


@pytest.fixture
def ui(browser: Browser, served: Served) -> Iterator[LegacyUi]:
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    page.set_default_timeout(8000)
    yield LegacyUi(page, served.url)
    context.close()
