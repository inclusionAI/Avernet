"""Typed deployment configuration for the acli CLI market search."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AcliMarketConfig:
    """The acli CLI market's search API, called from the backend.

    ``search_url`` is the full URL of the market's search endpoint; it comes
    from the deployment overlay and is empty by default, which turns
    ``GET /openapi/v1/bots/market/clis`` into a "not configured" refusal.
    """

    search_url: str = ""
