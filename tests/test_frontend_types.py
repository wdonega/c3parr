"""Os tipos TypeScript da interface saem do contrato da API, e este teste acusa se ficarem
para trás.

Mudou um formato em `web/schemas.py`? Regrave com
`C3PARR_UPDATE_GOLDEN=1 uv run pytest tests/test_frontend_types.py`: o `tsc` do frontend passa a
apontar cada tela que usa o campo que mudou.
"""

import os
from pathlib import Path
from typing import Any

from uikit import empty_world

TYPES = Path(__file__).resolve().parents[1] / "frontend" / "src" / "api" / "types.ts"
HEADER = """// Gerado do contrato da API (/api/openapi.json) por tests/test_frontend_types.py.
// Não edite à mão: mude web/schemas.py e regrave com
//   C3PARR_UPDATE_GOLDEN=1 uv run pytest tests/test_frontend_types.py
"""


def _name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1].replace("-Input", "").replace("-Output", "")


def ts_type(schema: dict[str, Any]) -> str:
    """Um trecho de JSON Schema em TypeScript."""
    if "$ref" in schema:
        return _name(schema["$ref"])
    if "const" in schema:
        return repr(schema["const"]).replace("'", '"')
    if "enum" in schema:
        return " | ".join(f'"{v}"' for v in schema["enum"])
    for key in ("anyOf", "oneOf"):
        if key in schema:
            return " | ".join(dict.fromkeys(ts_type(option) for option in schema[key]))
    kind = schema.get("type")
    if kind == "array" and "prefixItems" in schema:  # tupla de tamanho fixo
        return "[" + ", ".join(ts_type(item) for item in schema["prefixItems"]) + "]"
    if kind == "array":
        inner = ts_type(schema.get("items", {}))
        return f"({inner})[]" if " | " in inner else f"{inner}[]"
    if kind == "object":
        extra = schema.get("additionalProperties")
        value = ts_type(extra) if isinstance(extra, dict) else "unknown"
        return f"Record<string, {value}>"
    simple = {"string": "string", "integer": "number", "number": "number", "boolean": "boolean",
              "null": "null"}  # fmt: skip
    return simple.get(str(kind), "unknown")


def _request_bodies(schema: dict[str, Any]) -> set[str]:
    """Os formatos que a interface envia: neles, campo com padrão é opcional."""
    names: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            if "$ref" in node:
                names.add(_name(node["$ref"]))
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    for item in schema["paths"].values():
        for operation in item.values():
            visit(operation.get("requestBody", {}))
    return names


def typescript(schema: dict[str, Any]) -> str:
    sent = _request_bodies(schema)
    blocks: dict[str, str] = {}
    for full_name, model in schema["components"]["schemas"].items():
        name = _name(full_name)
        if "properties" not in model:
            continue  # erros de validação do FastAPI e afins
        required = set(model.get("required", []))
        lines = []
        for field, spec in model["properties"].items():
            optional = "?" if name in sent and field not in required else ""
            lines.append(f"  {field}{optional}: {ts_type(spec)}")
        body = "\n".join(lines)
        if name not in blocks or len(body) > len(blocks[name]):  # -Input e -Output: o maior
            blocks[name] = body
    parts = [f"export interface {name} {{\n{blocks[name]}\n}}" for name in sorted(blocks)]
    return HEADER + "\n" + "\n\n".join(parts) + "\n"


def test_frontend_types_match_the_api(tmp_path: Path) -> None:
    generated = typescript(empty_world(tmp_path).app.openapi())
    if os.environ.get("C3PARR_UPDATE_GOLDEN"):
        TYPES.parent.mkdir(parents=True, exist_ok=True)
        TYPES.write_text(generated)
    assert TYPES.read_text() == generated, "regrave os tipos (veja o cabeçalho deste teste)"


def test_json_schema_pieces_become_typescript() -> None:
    assert ts_type({"type": "string"}) == "string"
    assert ts_type({"type": "integer"}) == "number"
    assert ts_type({"anyOf": [{"type": "string"}, {"type": "null"}]}) == "string | null"
    assert ts_type({"enum": ["queued", "running"], "type": "string"}) == '"queued" | "running"'
    assert (
        ts_type({"type": "array", "items": {"$ref": "#/components/schemas/JobRow"}}) == "JobRow[]"
    )
    nullable = {"anyOf": [{"$ref": "#/components/schemas/JobRef"}, {"type": "null"}]}
    assert ts_type({"type": "array", "items": nullable}) == "(JobRef | null)[]"
    assert ts_type({"type": "object", "additionalProperties": {"type": "integer"}}) == (
        "Record<string, number>"
    )
    assert ts_type({"type": "object", "additionalProperties": True}) == "Record<string, unknown>"
    pair = {"type": "array", "prefixItems": [{"type": "string"}, {"type": "string"}]}
    assert ts_type({"type": "array", "items": pair}) == "[string, string][]"
    assert ts_type({}) == "unknown"
