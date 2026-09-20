"""Transport-neutral build exclusion values and literal path validation."""

from dataclasses import dataclass
from typing import Literal

MAX_RULE_BYTES = 1024 * 1024


class BuildIgnoreError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class BuildIgnoreConfig:
    paths: tuple[str, ...] = ()
    revision: int = 0


@dataclass(frozen=True)
class BuildIgnoreQuery:
    bot_id: str
    entity_id: str
    request_id: str


@dataclass(frozen=True)
class BuildIgnoreCommand(BuildIgnoreQuery):
    operation: Literal["add", "remove"]
    path: str


def normalize_build_ignore_path(path: str) -> str:
    # COSEC: rules are literal relative paths, never glob or rsync filter syntax.
    if len(path) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in path):
        raise BuildIgnoreError("invalid_ignore_path")
    while path.startswith("./"):
        path = path[2:]
    path = path.rstrip("/")
    if (
        not path
        or path.startswith(("/", "#", "!"))
        or any(c in path for c in "*?[]\\")
        or any(part in ("", ".", "..") for part in path.split("/"))
    ):
        raise BuildIgnoreError("invalid_ignore_path")
    return path
