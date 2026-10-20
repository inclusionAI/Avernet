"""Admission rows of ``/openapi/v1/bots/{bot_id}/genome`` (RSI-03), merged into ``ADMISSION``.

Every operation addresses a bot that may be shared, so each takes the
addressed-bot grant, like the config-manifest group beside it.
"""
from .admission_modes import AdmissionMode

_GENOME = "/openapi/v1/bots/{bot_id}/genome"

GENOME_ADMISSION: dict[tuple[str, str], AdmissionMode] = {
    (method, f"{_GENOME}{suffix}"): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT
    for method, suffix in (
        ("GET", "/revisions"),
        ("POST", "/revisions"),
        ("POST", "/revisions:capture"),
        ("GET", "/revisions/{rev:path}/diff"),
        ("GET", "/revisions/{rev:path}"),
        ("PUT", "/policy"),
        ("GET", "/refs"),
        ("PUT", "/refs/draft"),
        ("GET", "/content/{digest}"),
        ("PUT", "/content"),
        ("POST", "/promotions"),
        ("GET", "/promotions"),
        ("GET", "/promotions/{promotion_id}"),
    )
}
