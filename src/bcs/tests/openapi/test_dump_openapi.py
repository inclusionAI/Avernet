"""Tests for the deterministic BCN OpenAPI JSON export."""

import json
import sys
import tempfile
import unittest
from pathlib import Path


BCS_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = BCS_ROOT / "api-contracts" / "v1"
sys.path.insert(0, str(BCS_ROOT))

from scripts.dump_openapi import dump_contract  # noqa: E402


HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def _references(value: object):
    if isinstance(value, list):
        for item in value:
            yield from _references(item)
    elif isinstance(value, dict):
        if "$ref" in value:
            yield value["$ref"]
        for item in value.values():
            yield from _references(item)


class DumpOpenApiTests(unittest.TestCase):
    def test_dump_contract_writes_a_deterministic_self_contained_document(self) -> None:
        """Catches a non-deterministic, incomplete, or wrongly scoped Gateway artifact."""
        with tempfile.TemporaryDirectory() as directory:
            first_output = Path(directory) / "first.json"
            second_output = Path(directory) / "second.json"

            self.assertEqual(
                dump_contract(CONTRACT_ROOT, first_output),
                first_output,
            )
            dump_contract(CONTRACT_ROOT, second_output)

            self.assertEqual(
                first_output.read_bytes(),
                second_output.read_bytes(),
            )
            contract = json.loads(first_output.read_text(encoding="utf-8"))

        self.assertEqual(contract["openapi"], "3.1.0")
        operations = [
            (method, path)
            for path, path_item in contract["paths"].items()
            for method in path_item
            if method.lower() in HTTP_METHODS
        ]
        self.assertEqual(len(operations), 32)
        self.assertTrue(
            all(path.startswith("/openapi/v1/collaboration/") for _, path in operations)
        )
        self.assertTrue(all(reference.startswith("#/") for reference in _references(contract)))
