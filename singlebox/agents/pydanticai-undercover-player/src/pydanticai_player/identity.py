"""Persist only the player's own BCN identity, bound to its configured endpoint."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class IdentityStore:
    def __init__(self, path: Path, endpoint: str):
        self.path, self.endpoint = path, endpoint

    def load_token(self) -> str | None:
        if not self.path.exists():
            return None
        data = json.loads(self.path.read_text())
        if data.get("endpoint") != self.endpoint:
            raise ValueError("Identity belongs to another BCN endpoint; use a separate state file")
        if not isinstance(data.get("token"), str) or not data["token"]:
            raise ValueError("Invalid saved identity; not creating a replacement identity")
        return data["token"]

    def save(self, token: str, bot_uuid: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".identity-")
        try:
            with os.fdopen(fd, "w") as stream:
                os.chmod(temporary, 0o600)
                json.dump({"endpoint": self.endpoint, "token": token, "bot_uuid": bot_uuid}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
