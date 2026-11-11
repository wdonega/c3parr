"""Retrato do que a API JSON responde, com a biblioteca de exemplo: o contrato com a interface.

Mudou de propósito? Regrave com `C3PARR_UPDATE_GOLDEN=1 uv run pytest tests/test_api_snapshots.py`
e confira o diff no git: a interface depende de cada campo daqui.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from uikit import normalize, seed_world

GOLDEN = Path(__file__).parent / "golden" / "api.json"


def routes(ids: dict[str, int]) -> list[tuple[str, str, dict[str, str]]]:
    out: list[tuple[str, str, dict[str, str]]] = [
        ("shell", "/shell", {}),
        ("dashboard", "/dashboard", {}),
        ("movies", "/movies", {}),
        ("movies_missing", "/movies", {"show": "missing"}),
        ("movies_search", "/movies", {"q": "inc"}),
        ("shows", "/shows", {}),
        ("show", f"/shows/{ids['Supernatural']}", {}),
        ("queue", "/queue", {}),
        ("queue_failed", "/queue", {"status": "failed"}),
        ("queue_search", "/queue", {"q": "super"}),
        ("queue_upcoming", "/queue/upcoming", {}),
        ("queue_recent", "/queue/recent", {}),
        ("search", "/search", {"q": "a"}),
        ("movies_by_missing", "/movies", {"sort": "missing"}),
    ]
    out += [(f"job_{role}", f"/queue/{ids[role]}", {})
            for role in ("done", "warned", "failed", "cancelled", "running", "queued")]  # fmt: skip
    out += [
        ("settings", "/settings", {}),
        ("system_tasks", "/system/tasks", {}),
        ("system_alerts", "/system/alerts", {}),
        ("system_logs", "/system/logs", {}),
        ("system_status", "/system/status", {}),
    ]
    return out


def _stable(name: str, data: Any) -> Any:
    """Tira o que muda de máquina para máquina e não é contrato."""
    if name == "shell":
        data["version"], data["release_url"] = "<varies>", "<varies>"
    if name == "system_status":
        for fact in data["about"]:
            if fact["label"] in ("Version", "Python"):
                fact["value"] = "<varies>"
    if name == "system_logs":  # só o aviso do teste: o resto depende do que rodou antes
        data = [{**r, "time": "<time>"} for r in data if r["name"] == "c3parr.test"]
    return data


def test_the_api_answers_the_same_for_the_example_library(tmp_path: Path) -> None:
    world = seed_world(tmp_path)
    seen: dict[str, Any] = {}
    with TestClient(world.app) as client:
        logging.getLogger("c3parr.test").warning("aviso de exemplo")
        for name, path, params in routes(world.ids):
            response = client.get(f"/api/v1{path}", params=params)
            assert response.status_code == 200, f"{name}: {response.status_code}"
            seen[name] = _stable(name, normalize(response.json(), tmp_path))
        schema = client.get("/api/openapi.json").json()

    # o contrato publicado lista só a API, e toda rota dela
    assert all(path.startswith("/api/v1/") for path in schema["paths"])
    assert len(schema["paths"]) >= 20

    if os.environ.get("C3PARR_UPDATE_GOLDEN"):
        GOLDEN.write_text(json.dumps(seen, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    expected = json.loads(GOLDEN.read_text())
    assert sorted(seen) == sorted(expected)
    changed = [name for name in seen if seen[name] != expected[name]]
    for name in changed[:1]:  # o diff da primeira rota diferente; as outras só pelo nome
        assert seen[name] == expected[name], f"routes that changed: {changed}"
