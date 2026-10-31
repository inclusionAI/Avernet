"""Shared invite-URL shape and HTTP-evidence helpers for the dual session-invite halves.

Split out of workspace_cli_runner under the 1,000-line source limit; text moved verbatim.
"""

from __future__ import annotations


def _invite_url_matches_product_shape(value: str, target: str,
                                      token: str, tier: str) -> bool:
    """Validate the product frontend invite URL shape without coupling API origin."""
    expected_path = f"/bcn/chat/invite/{target}/{token}"
    if not value.endswith(expected_path) or "?" in value or "#" in value:
        return False
    scheme = "https://" if tier == "preprod" else "http://"
    prefix = value[:-len(expected_path)]
    if not prefix.startswith(scheme):
        return False
    host = prefix[len(scheme):]
    return bool(host) and "@" not in host and "/" not in host


def _http_evidence(record: dict, profile: dict, *, case_class: str,
                   fixture_effect: str) -> dict:
    evidence = dict(record)
    evidence.update({
        "transport": "PRODUCT_HTTP",
        "caller_role": "driver",
        "credential_kind": ("owned_local_bot_token"
                            if profile["environment"]["tier"] == "local"
                            else "human_cookie"),
        "case_class": case_class,
        "fixture_effect": fixture_effect,
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
    })
    return evidence
