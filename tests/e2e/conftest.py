"""Testes no navegador de verdade (Playwright), com a biblioteca de exemplo servida de fato.

Ficam fora da rodada normal: `uv run pytest -m e2e`. Na primeira vez, baixe o navegador com
`uv run playwright install chromium` e construa a interface (`npm run build` em frontend/).
"""

import dataclasses
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
import uvicorn
from playwright.sync_api import Browser, Page, sync_playwright
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
def spa(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Served]:
    """A biblioteca de exemplo servida de fato, com a interface construída."""
    world = seed_world(tmp_path_factory.mktemp("spa"))
    service = world.app.state.service  # sem internet: nenhum pôster vem, fica o bloco de cor
    service.clients = dataclasses.replace(service.clients, fetch_poster=lambda kind, url: None)
    yield from serve(world)


def open_page(browser: Browser, served: Served, path: str, width: int = 1280,
              locale: str = "en-US") -> Page:  # fmt: skip
    """Abre a interface num navegador novo (nada guardado), no idioma dado: a interface escolhe
    o dela pelo do navegador. Sem dizer, inglês, seja qual for o idioma da máquina."""
    context = browser.new_context(viewport={"width": width, "height": 844}, locale=locale)
    page = context.new_page()
    page.set_default_timeout(8000)
    page.goto(served.url + path)
    page.get_by_role("heading", level=1).first.wait_for()
    return page
