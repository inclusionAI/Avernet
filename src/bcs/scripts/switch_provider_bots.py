#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Call the BCS Provider switch-bot delivery endpoint.

Secrets are intentionally read from environment variables or local files so
they are not committed into the repository.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any


JsonObject = dict[str, Any]
DEFAULT_BASE_URL = "http://127.0.0.1:21000"
DEFAULT_PROVIDER_ID = "prv_example"
DEFAULT_BOT_ID = "demo-provider-bot:11111111"
DEFAULT_BOT_DESC = "Demo provider bot"


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def mysql_double_quoted_literal(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def sql_in_values(values: list[str]) -> str:
    return ", ".join(mysql_double_quoted_literal(value) for value in values)


def print_verify_sql(bot_ids: list[str], env: str) -> None:
    if not bot_ids:
        return
    values = sql_in_values(bot_ids)
    print()
    print("-- Verify provider switch result")
    print("select * from bcs_provider_bot_bindings where provider_bot_ref in (")
    print(f"  {values}")
    print(");")
    print()
    print("select * from bcs_bots")
    print("where")
    print(f" bot_uuid in ({values})")
    print(f" and env = {mysql_double_quoted_literal(env)};")


def read_text_file(path: str) -> str:
    return Path(path).expanduser().read_text(encoding="utf-8").strip()


def read_secret(value: str | None, file_path: str | None, env_name: str, label: str) -> str:
    if value:
        return value.strip()
    if file_path:
        return read_text_file(file_path)
    env_value = os.environ.get(env_name)
    if env_value:
        return env_value.strip()
    raise SystemExit(
        f"missing {label}; pass --{label.replace('_', '-')} / --{label.replace('_', '-')}-file "
        f"or set {env_name}"
    )


def authorization_header(args: argparse.Namespace) -> str:
    if args.authorization:
        auth = args.authorization.strip()
    elif args.authorization_file:
        auth = read_text_file(args.authorization_file)
    elif os.environ.get(args.authorization_env):
        auth = os.environ[args.authorization_env].strip()
    elif args.token:
        auth = args.token.strip()
    elif args.token_file:
        auth = read_text_file(args.token_file)
    elif os.environ.get(args.token_env):
        auth = os.environ[args.token_env].strip()
    else:
        raise SystemExit(
            "missing authorization; pass --authorization / --token, "
            f"or set {args.authorization_env} / {args.token_env}"
        )

    if auth.lower().startswith("bearer "):
        return auth
    return f"Bearer {auth}"


def find_records(value: Any) -> list[Any] | None:
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        return None
    if "bot_uuid" in value:
        return [value]

    preferred_keys = ("data", "items", "rows", "records", "list", "bots", "result")
    for key in preferred_keys:
        if key in value:
            records = find_records(value[key])
            if records is not None:
                return records

    for nested in value.values():
        if isinstance(nested, list) and all(isinstance(item, dict) for item in nested):
            return nested
    return None


def value_from_keys(record: JsonObject, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def payloads_from_file(path: str, args: argparse.Namespace) -> list[JsonObject]:
    with Path(path).expanduser().open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    records = find_records(data)
    if records is None:
        raise SystemExit(f"cannot find a JSON list in input file: {path}")

    payloads: list[JsonObject] = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise SystemExit(f"record #{index} is not a JSON object")
        bot_id = value_from_keys(record, (args.uuid_key, "bot_uuid", "botUuid", "bot_id", "id", "uuid"))
        if not bot_id:
            raise SystemExit(f"record #{index} missing {args.uuid_key}/bot_uuid")
        desc = value_from_keys(
            record,
            (args.desc_key, "bot_desc", "botDesc", "summary", "description", "name", "bot_name"),
        )
        if not desc:
            desc = bot_id
        provider_bot_ref = value_from_keys(record, (args.provider_ref_key, "provider_bot_ref")) or bot_id
        payloads.append(
            {
                "bot_id": bot_id,
                "provider_bot_ref": provider_bot_ref,
                "name": desc,
                "summary": desc,
            }
        )
    return payloads


def payloads_from_args(args: argparse.Namespace) -> list[JsonObject]:
    if args.input:
        return payloads_from_file(args.input, args)

    bot_desc = args.bot_desc or DEFAULT_BOT_DESC
    return [
        {
            "bot_id": args.bot_id,
            "provider_bot_ref": args.provider_bot_ref or args.bot_id,
            "name": args.name or bot_desc,
            "summary": args.summary or bot_desc,
        }
    ]


def response_text(response: urllib.response.addinfourl) -> str:
    raw = response.read()
    if not raw:
        return ""
    return raw.decode("utf-8", errors="replace")


def post_json(
    url: str,
    headers: dict[str, str],
    payload: JsonObject,
    timeout: float,
    insecure: bool,
) -> tuple[int, str]:
    body = compact_json(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
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


def build_headers(args: argparse.Namespace, cookie: str, authorization: str) -> dict[str, str]:
    return {
        "___internal-request-id": args.request_id or str(uuid.uuid4()),
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "authorization": authorization,
        "content-type": "application/json",
        "cookie": cookie,
        "priority": "u=1, i",
        "sec-ch-ua": '"Not/A)Brand";v="99", "Chromium";v="148"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "none",
        "sec-fetch-storage-access": "active",
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Switch personal bots to a Provider delivery bot by calling /delivery/switch-bot."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--provider-id", default=DEFAULT_PROVIDER_ID)
    parser.add_argument("--input", help="JSON file containing records with bot_uuid and bot_desc")
    parser.add_argument("--uuid-key", default="bot_uuid")
    parser.add_argument("--desc-key", default="bot_desc")
    parser.add_argument("--provider-ref-key", default="provider_bot_ref")

    parser.add_argument("--bot-id", default=DEFAULT_BOT_ID)
    parser.add_argument("--provider-bot-ref")
    parser.add_argument("--bot-desc")
    parser.add_argument("--name")
    parser.add_argument("--summary")

    parser.add_argument("--authorization")
    parser.add_argument("--authorization-file")
    parser.add_argument("--authorization-env", default="BCS_SWITCH_AUTHORIZATION")
    parser.add_argument("--token")
    parser.add_argument("--token-file")
    parser.add_argument("--token-env", default="BCS_SWITCH_TOKEN")
    parser.add_argument("--cookie")
    parser.add_argument("--cookie-file")
    parser.add_argument("--cookie-env", default="BCS_COOKIE")

    parser.add_argument("--request-id", help="Use one fixed ___internal-request-id; default generates one per run")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--sleep", type=float, default=0.0, help="Seconds to sleep between batch requests")
    parser.add_argument("--bot-env", default="prod", help="env value used in the generated bcs_bots SQL")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS certificate verification")
    parser.add_argument("--dry-run", action="store_true", help="Print generated payloads without sending requests")
    parser.add_argument("--stop-on-error", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    payloads = payloads_from_args(args)
    url = f"{args.base_url.rstrip('/')}/providers/{args.provider_id}/delivery/switch-bot"

    if args.dry_run:
        print(f"url: {url}")
        print(pretty_json(payloads))
        print_verify_sql([str(payload["bot_id"]) for payload in payloads], args.bot_env)
        return 0

    cookie = read_secret(args.cookie, args.cookie_file, args.cookie_env, "cookie")
    authorization = authorization_header(args)

    failures = 0
    total = len(payloads)
    processed_bot_ids: list[str] = []
    for index, payload in enumerate(payloads, start=1):
        headers = build_headers(args, cookie, authorization)
        if not args.request_id:
            headers["___internal-request-id"] = str(uuid.uuid4())
        bot_id = payload["bot_id"]
        processed_bot_ids.append(str(bot_id))
        try:
            status, body = post_json(url, headers, payload, args.timeout, args.insecure)
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

    print_verify_sql(processed_bot_ids, args.bot_env)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
