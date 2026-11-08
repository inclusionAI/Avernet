"""Shared route policy keys for owner-only DWS admission and authorization."""

OPERATIONS = (
    ("GET", "/eligibility"),
    ("GET", ""),
    ("GET", "/settings"),
    ("PUT", "/settings"),
    ("PUT", "/enabled"),
    ("POST", "/authorization"),
    ("POST", "/authorization/check"),
    ("GET", "/conversations"),
    ("PUT", "/takeover"),
)
PATH = "/openapi/v1/bots/{bot_id}/dws-hosting"
