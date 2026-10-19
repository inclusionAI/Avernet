"""Owner self-service only; applications cannot opt into personal DWS consent."""

OPERATIONS = (
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
