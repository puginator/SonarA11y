from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest


@pytest.mark.parametrize(
    "schema_name",
    [
        "axe-violation-payload.schema.json",
        "pdf-violation-payload.schema.json",
        "fix-report.schema.json",
    ],
)
def test_schema_is_valid(schema_name: str) -> None:
    schema_path = Path(__file__).resolve().parents[2] / "contracts" / schema_name
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator.check_schema(schema)
