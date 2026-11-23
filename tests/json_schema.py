from __future__ import annotations

from collections.abc import Iterator
from typing import Any


def object_schemas(schema: Any) -> Iterator[dict[str, Any]]:
    """遞迴列出 JSON schema 中所有 type 為 object 的節點。"""
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            yield schema
        for value in schema.values():
            yield from object_schemas(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from object_schemas(value)
