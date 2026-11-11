"""Retrato dos dados que cada tela recebe, com a biblioteca de exemplo.

É a prova de que mexer na camada da interface não mudou a regra: os dados de cada tela têm de
continuar idênticos ao que está em `golden/screens.json`. Mudou de propósito? Regrave com
`C3PARR_UPDATE_GOLDEN=1 uv run pytest tests/test_ui_snapshots.py` e confira o diff no git.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from uikit import ContextSpy, screen_data, seed_world

GOLDEN = Path(__file__).parent / "golden" / "screens.json"
# iguais em toda tela (rótulos, idiomas, menus): ficam uma vez só, em `_constants`
CONSTANTS = ("labels", "languages", "language_names", "finished", "settings_pages", "system_pages")


def screens(ids: dict[str, int]) -> list[tuple[str, str, dict[str, str]]]:
    """(nome, caminho, parâmetros). Alertas por último: abrir a página marca como vistos."""
    out: list[tuple[str, str, dict[str, str]]] = [
        ("dashboard", "/dashboard", {}),
        ("movies", "/movies", {}),
        ("movies_missing", "/movies", {"show": "missing"}),
        ("movies_complete", "/movies", {"show": "complete"}),
        ("movies_queued", "/movies", {"show": "queued"}),
        ("movies_search", "/movies", {"q": "inc"}),
        ("tv", "/tv", {}),
        ("tv_queued", "/tv", {"show": "queued"}),
        ("show", f"/tv/{ids['Supernatural']}", {}),
        ("queue", "/queue", {}),
        ("queue_failed", "/queue", {"status": "failed"}),
        ("queue_rows_search", "/queue/rows", {"q": "super"}),
        ("queue_badge", "/queue/count", {}),
    ]
    out += [(f"job_{role}", f"/queue/{ids[role]}", {})
            for role in ("done", "warned", "failed", "cancelled", "running", "queued")]  # fmt: skip
    out += [(f"settings_{page}", f"/settings/{page}", {})
            for page in ("library", "integrations", "languages", "engines", "subtitles")]  # fmt: skip
    out += [
        ("settings_library_sonarr", "/settings/library/sonarr", {}),
        ("system_tasks", "/system/tasks", {}),
        ("system_task_rows", "/system/tasks/rows", {}),
        ("system_status", "/system/status", {}),
        ("system_logs", "/system/logs", {}),
        ("system_alerts", "/system/alerts", {}),
        ("dashboard_after_alerts", "/dashboard", {}),
    ]
    return out


def _stable(name: str, data: dict[str, Any]) -> dict[str, Any]:
    """Tira o que muda de máquina para máquina e não é regra."""
    if name == "system_status":
        data["about"] = [[label, "<varies>" if label in ("Version", "Python") else value]
                         for label, value in data["about"]]  # fmt: skip
    if name == "system_logs":  # só o aviso do teste: o resto depende do que rodou antes
        data["records"] = [{**r, "time": "<time>"} for r in data["records"]
                           if r["name"] == "c3parr.test"]  # fmt: skip
    return data


def test_every_screen_receives_the_same_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = seed_world(tmp_path)
    spy = ContextSpy(monkeypatch)
    seen: dict[str, Any] = {}
    with TestClient(world.app) as client:
        logging.getLogger("c3parr.test").warning("aviso de exemplo")
        for name, path, params in screens(world.ids):
            response = client.get(path, params=params)
            assert response.status_code == 200, f"{name}: {response.status_code}"
            template, context = spy.last()
            data = _stable(name, screen_data(context, tmp_path))
            constants = {key: data.pop(key) for key in CONSTANTS}
            assert seen.setdefault("_constants", constants) == constants
            seen[name] = {"template": template, "data": data}

    if os.environ.get("C3PARR_UPDATE_GOLDEN"):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(seen, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    expected = json.loads(GOLDEN.read_text())
    assert sorted(seen) == sorted(expected)
    changed = [name for name in seen if seen[name] != expected[name]]
    for name in changed[:1]:  # o diff da primeira tela diferente; as outras só pelo nome
        assert seen[name] == expected[name], f"screens that changed: {changed}"
