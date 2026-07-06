#!/usr/bin/env python3
"""Delete a batch of BCS Provider bots.

Secrets are intentionally read from arguments, environment variables, or local
files so they are not committed into the repository.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "http://127.0.0.1:21000"
DEFAULT_PROVIDER_ID = "prv_example"
DEFAULT_BOT_IDS = [
]


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def read_text_file(path: str) -> str:
    return Path(path).expanduser().read_text(encoding="utf-8").strip()


def optional_secret(value: str | None, file_path: str | None, env_name: str) -> str | None:
    if value:
        return value.strip()
    if file_path:
        return read_text_file(file_path)
    env_value = os.environ.get(env_name)
    if env_value:
        return env_value.strip()
    return None


def authorization_header(args: argparse.Namespace) -> str:
    auth = optional_secret(args.authorization, args.authorization_file, args.authorization_env)
    if not auth:
        token = optional_secret(args.token, args.token_file, args.token_env)
        if token:
            auth = token
    if not auth:
        raise SystemExit(
            "missing authorization; pass --authorization / --token, "
            f"or set {args.authorization_env} / {args.token_env}"
        )
    if auth.lower().startswith("bearer "):
        return auth
    return f"Bearer {auth}"


def split_bot_ids(text: str) -> list[str]:
    bot_ids: list[str] = []
    for raw_line in text.replace(",", "\n").splitlines():
        value = raw_line.strip()
        if not value or value.startswith("#"):
            continue
        bot_ids.append(value)
    return bot_ids


def bot_ids_from_args(args: argparse.Namespace) -> list[str]:
    bot_ids: list[str] = []
    for value in args.bot:
        bot_ids.extend(split_bot_ids(value))
    for path in args.bots_file:
        bot_ids.extend(split_bot_ids(read_text_file(path)))
    if not bot_ids:
        bot_ids = list(DEFAULT_BOT_IDS)
    return bot_ids


def delete_url(base_url: str, provider_id: str, bot_id: str) -> str:
    provider = urllib.parse.quote(provider_id, safe="")
    bot = urllib.parse.quote(bot_id, safe=":")
    return f"{base_url.rstrip('/')}/providers/{provider}/bots/{bot}"


def response_text(response: urllib.response.addinfourl) -> str:
    raw = response.read()
    if not raw:
        return ""
    return raw.decode("utf-8", errors="replace")


def delete_bot(
    url: str,
    headers: dict[str, str],
    timeout: float,
    insecure: bool,
) -> tuple[int, str]:
    request = urllib.request.Request(url, headers=headers, method="DELETE")
    context = ssl._create_unverified_context() if insecure else None
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return response.status, response_text(response)
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as error:
        raise RuntimeError(str(error)) from error


def print_response_body(body: str) -> None:
    if not body:
        return
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        print(body)
        return
    print(pretty_json(parsed))


def build_headers(args: argparse.Namespace, authorization: str, cookie: str | None) -> dict[str, str]:
    headers = {
        "___internal-request-id": args.request_id or str(uuid.uuid4()),
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "authorization": authorization,
        "priority": "u=1, i",
        "sec-ch-ua": '"Not/A)Brand";v="99", "Chromium";v="148"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "none",
        "sec-fetch-storage-access": "active",
    }
    if cookie:
        headers["cookie"] = cookie
    return headers


def mysql_double_quoted_literal(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def sql_in_values(values: list[str]) -> str:
    return ", ".join(mysql_double_quoted_literal(value) for value in values)


def print_verify_sql(bot_ids: list[str], provider_id: str) -> None:
    if not bot_ids:
        return
    values = sql_in_values(bot_ids)
    print()
    print("-- Verify provider bot delete result")
    print("select * from bcs_provider_bot_bindings")
    print(f"where provider_id = {mysql_double_quoted_literal(provider_id)}")
    print(f"  and provider_bot_ref in ({values});")
    print()
    print("select * from bcs_bots")
    print(f"where bot_uuid in ({values});")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Delete BCS Provider bots by calling DELETE /providers/{provider_id}/bots/{bot_id}."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--provider-id", default=DEFAULT_PROVIDER_ID)
    parser.add_argument("--bot", action="append", default=[], help="Bot id to delete; repeat or comma-separate")
    parser.add_argument("--bots-file", action="append", default=[], help="File with newline/comma-separated bot ids")

    parser.add_argument("--authorization")
    parser.add_argument("--authorization-file")
    parser.add_argument("--authorization-env", default="BCS_DELETE_AUTHORIZATION")
    parser.add_argument("--token")
    parser.add_argument("--token-file")
    parser.add_argument("--token-env", default="BCS_DELETE_TOKEN")
    parser.add_argument("--cookie")
    parser.add_argument("--cookie-file")
    parser.add_argument("--cookie-env", default="BCS_COOKIE")

    parser.add_argument("--request-id", help="Use one fixed ___internal-request-id; default generates one per request")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--sleep", type=float, default=0.0, help="Seconds to sleep between delete requests")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS certificate verification")
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--execute", action="store_true", help="Actually send DELETE requests")
    parser.add_argument("--dry-run", dest="execute", action="store_false", help="Only print requests; this is default")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    bot_ids = bot_ids_from_args(args)

    if not args.execute:
        print(f"dry-run: would delete {len(bot_ids)} bot(s)")
        for bot_id in bot_ids:
            print(delete_url(args.base_url, args.provider_id, bot_id))
        print_verify_sql(bot_ids, args.provider_id)
        print()
        print("Pass --execute to send DELETE requests.")
        return 0

    authorization = authorization_header(args)
    cookie = optional_secret(args.cookie, args.cookie_file, args.cookie_env)

    failures = 0
    total = len(bot_ids)
    processed_bot_ids: list[str] = []
    for index, bot_id in enumerate(bot_ids, start=1):
        headers = build_headers(args, authorization, cookie)
        if not args.request_id:
            headers["___internal-request-id"] = str(uuid.uuid4())
        url = delete_url(args.base_url, args.provider_id, bot_id)
        processed_bot_ids.append(bot_id)
        try:
            status, body = delete_bot(url, headers, args.timeout, args.insecure)
        except RuntimeError as error:
            failures += 1
            print(f"[{index}/{total}] {bot_id} request failed: {error}", file=sys.stderr)
            if args.stop_on_error:
                break
            continue

        ok = 200 <= status < 300
        print(f"[{index}/{total}] {bot_id} status={status} {'ok' if ok else 'failed'}")
        print_response_body(body)
        if not ok:
            failures += 1
            if args.stop_on_error:
                break
        if args.sleep and index < total:
            time.sleep(args.sleep)

    print_verify_sql(processed_bot_ids, args.provider_id)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
