"""Provision one private, loopback-only Provider for live coverage acceptance.

This launcher helper never configures production: both operations require the
coverage opt-in, and authorization touches only the caller's generated TOML.
Provider credentials are written directly to a private fixture, never stdout.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit
import uuid

import httpx
import tomlkit


def require_coverage() -> None:
    if os.environ.get("SINGLEBOX_COVERAGE") != "1":
        raise ValueError("Provider fixture requires singlebox coverage mode")


def loopback_url(value: str) -> str:
    parsed = urlsplit(value)
    try:
        local = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        local = False
    if (parsed.scheme != "http" or not local or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
        raise ValueError("Provider fixture requires an HTTP loopback base URL")
    return value.rstrip("/")


def credentials(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("Invalid Provider fixture")
    provider_id, token = value.get("provider_id"), value.get("provider_admin_token")
    if (not isinstance(provider_id, str) or not re.fullmatch(r"prv_[A-Za-z0-9_-]+", provider_id)
            or not isinstance(token, str) or not token.strip()):
        raise ValueError("Invalid Provider fixture")
    return {"provider_id": provider_id, "provider_admin_token": token}


def provision(bcs_url: str, mock_url: str, fixture: Path) -> None:
    require_coverage()
    bcs_url, mock_url = loopback_url(bcs_url), loopback_url(mock_url)
    with httpx.Client(trust_env=False, timeout=30) as client:
        response = client.post(
            bcs_url + "/providers",
            headers={"X-Mock-User-Id": "singlebox-repair-coverage"},
            json={"name": "Singlebox collaboration repair coverage",
                  "slug": "singlebox-repair-" + uuid.uuid4().hex,
                  "auth": {"mode": "static_bearer"}, "protocol_version": "2.0",
                  "webhook_url": mock_url + "/provider/webhook"},
        )
        # Do not expose error bodies: upstream responses can contain tokens.
        if response.status_code != 200:
            raise ValueError("Provider fixture registration failed")
        payload = credentials(response.json())
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".repair-provider-", dir=fixture.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream)
            stream.write("\n")
        os.replace(temporary, fixture)
    finally:
        Path(temporary).unlink(missing_ok=True)


def authorize(fixture: Path, config: Path) -> None:
    require_coverage()
    provider_id = credentials(json.loads(fixture.read_text(encoding="utf-8")))["provider_id"]
    document = tomlkit.parse(config.read_text(encoding="utf-8"))
    allowed = document.get("allowed_switch_provider_ids", [])
    if not isinstance(allowed, list) or not all(isinstance(value, str) for value in allowed):
        raise ValueError("Invalid Provider allowlist in generated BCS config")
    if provider_id not in allowed:
        allowed.append(provider_id)
    document["allowed_switch_provider_ids"] = allowed
    config.write_text(tomlkit.dumps(document), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    register = actions.add_parser("provision")
    register.add_argument("--bcs-url", required=True)
    register.add_argument("--mock-url", required=True)
    register.add_argument("--fixture", type=Path, required=True)
    config = actions.add_parser("authorize")
    config.add_argument("--fixture", type=Path, required=True)
    config.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == "provision":
            provision(args.bcs_url, args.mock_url, args.fixture)
        else:
            authorize(args.fixture, args.config)
    except (ValueError, OSError, httpx.HTTPError):
        # No exception text, response body, or fixture contents enter logs.
        parser.exit(1, "Coverage Provider fixture operation failed\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
