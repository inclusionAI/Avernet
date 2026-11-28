"""Direct helpers for tests that mutate the real Collet context_spec envelope."""

import json


def stored_context_value(row, key: str):
    data = json.loads(row.context_spec)
    return data[key]


def store_context_value(row, key: str, value) -> None:
    data = json.loads(row.context_spec)
    data[key] = value
    row.context_spec = json.dumps(data)
