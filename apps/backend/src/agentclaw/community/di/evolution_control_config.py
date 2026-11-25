"""Internal service location, never browser login or user configuration."""

from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from injector import inject


@dataclass(frozen=True)
class EvolutionControlConfig:
    upstream: str = ""
    timeout_s: float = 30
    builtin_data_tenant: str = ""  # empty disables the built-in operator data channel

    def __post_init__(self):
        if self.upstream:
            url = urlsplit(self.upstream)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
                or url.path not in {"", "/"}
                or url.query
                or url.fragment
            ):
                raise ValueError("evolution_control.upstream must be an HTTP(S) origin")
        if not 0 < self.timeout_s <= 120:
            raise ValueError("evolution_control.timeout_s must be in (0, 120]")


class EvolutionControlClientFactory:
    """Per-delivery HTTP client assembly; injectable only at the network edge.

    Clients are closed by the relay's async context manager. Do not add a
    shared cookie jar, ambient proxy, redirect or automatic request retry.
    """

    @inject
    def __init__(self, config: EvolutionControlConfig):
        self.config = config

    def open(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.config.upstream.rstrip("/"),
            timeout=self.config.timeout_s,
            trust_env=False,
            follow_redirects=False,
        )
