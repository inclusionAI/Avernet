import json
import time
from typing import Any


def _dumps(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(text: str | None) -> Any:
    if text is None:
        return None
    return json.loads(text)


def _now_ms() -> int:
    return int(time.time() * 1000)
