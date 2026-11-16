"""Explicit deployment-owned Qdrant connection settings; no environment reads."""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit


@dataclass(frozen=True)
class QdrantConnection:
    mode: Literal["local", "server"] = "local"
    url: str = ""
    grpc_port: int = 6334
    prefer_grpc: bool = True
    timeout: float = 10.0
    api_key: str = field(default="", repr=False)

    def __post_init__(self):
        if self.mode not in {"local", "server"}:
            raise ValueError("QDRANT_MODE must be local or server")
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("QDRANT_TIMEOUT_SECONDS must be positive and finite")
        if not 0 < self.grpc_port < 65536:
            raise ValueError("invalid QDRANT_GRPC_PORT")
        if self.mode == "server":
            parsed = urlsplit(self.url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("Server mode requires an HTTP(S) QDRANT_URL")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError(
                    "QDRANT_URL must not contain credentials, query or fragment"
                )

    @classmethod
    def from_mapping(cls, values: Mapping, *, default_mode: str = "local"):
        grpc = str(values.get("QDRANT_PREFER_GRPC", "true")).lower()
        if grpc not in {"true", "false", "1", "0"}:
            raise ValueError("QDRANT_PREFER_GRPC must be true or false")
        auth_value = str(values.get("QDRANT_API_KEY", ""))
        return cls(
            mode=str(values.get("QDRANT_MODE", default_mode)).lower(),
            url=str(values.get("QDRANT_URL", "")),
            grpc_port=int(values.get("QDRANT_GRPC_PORT", 6334)),
            prefer_grpc=grpc in {"true", "1"},
            timeout=float(values.get("QDRANT_TIMEOUT_SECONDS", 10)),
            api_key=auth_value,
        )


LOCAL_QDRANT = QdrantConnection()
