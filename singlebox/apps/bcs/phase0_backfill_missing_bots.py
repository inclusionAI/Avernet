#!/usr/bin/env python3
"""Phase 0: Backfill missing bots from ac_bots to BCS (bcs_bots).

Finds bots that exist in the backend `ac_bots` table but are missing from BCS
`bcs_bots`, and calls `POST /admin/bots/{bot_uuid}/ensure` on each to register
them (with owner edges + default permission profile) using a service credential.

The bot_uuid is the composite `{bot_id}:{owner_id}` (D11). The script also
backfills `ac_bots.ext.bcs.bot_uuid` (D11 mapping) so the backend can resolve
the BCS id for /admission calls later (Installment 5).

Usage:
    BCS_BASE_URL=http://localhost:21000 \
    BCS_SERVICE_KEY=<shared-secret> \
    DB_HOST=<mysql-host> DB_PORT=3306 DB_USER=<user> DB_PASSWORD=<pw> DB_NAME=<db> \
    python3 scripts/phase0_backfill_missing_bots.py [--env prod] [--dry-run] [--qps 10]

Prerequisites:
    - Phase 1 Build deployed (edge-permission tables + ensure endpoint live).
    - Service key configured in BCS config.api_keys (admin key, empty bound_groups).
    - pymysql or mysql-connector for DB access; httpx for HTTP.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 0 backfill missing bots to BCS")
    parser.add_argument("--env", default=os.environ.get("BCS_ENV", "prod"), help="target env (prod/pre/dev)")
    parser.add_argument("--dry-run", action="store_true", help="show missing bots without calling ensure")
    parser.add_argument("--qps", type=int, default=10, help="rate limit (requests per second)")
    parser.add_argument("--bcs-url", default=os.environ.get("BCS_BASE_URL", "http://localhost:21000"))
    parser.add_argument("--service-key", default=os.environ.get("BCS_SERVICE_KEY", ""))
    args = parser.parse_args()

    import httpx

    # --- Step 0a: Find missing bots (ac_bots LEFT JOIN bcs_bots) ---
    # This query identifies bots registered in the backend but not in BCS.
    # The bot_uuid is the D11 composite: CONCAT(bot_id, ':', owner_id).
    find_missing_sql = """
        SELECT
            a.bot_id,
            a.owner_id,
            a.bot_name,
            a.bot_desc,
            a.public,
            JSON_EXTRACT(a.ext, '$.friend_approval') AS friend_approval
        FROM ac_bots a
        LEFT JOIN bcs_bots b ON b.bot_uuid = CONCAT(a.bot_id, ':', a.owner_id)
            AND b.env = %s
        WHERE b.bot_uuid IS NULL AND a.is_delete = 0
    """

    db = _connect_db()
    cursor = db.cursor()
    cursor.execute(find_missing_sql, (args.env,))
    missing = cursor.fetchall()
    print(f"[Phase 0] Found {len(missing)} missing bots for env={args.env}")

    if not missing:
        print("[Phase 0] All bots are registered in BCS. Nothing to do.")
        return 0

    if args.dry_run:
        for row in missing:
            bot_id, owner_id = row[0], row[1]
            bot_uuid = f"{bot_id}:{owner_id}"
            print(f"  [DRY RUN] would ensure {bot_uuid} (name={row[2]})")
        return 0

    # --- Step 0b: Call ensure endpoint for each missing bot ---
    client = httpx.Client(base_url=args.bcs_url, timeout=30,
                          headers={"X-BCS-Service-Key": args.service_key})

    ensured = 0
    failed = 0
    for row in missing:
        bot_id, owner_id = row[0], row[1]
        bot_uuid = f"{bot_id}:{owner_id}"
        visibility = _map_visibility(row[4], row[5])

        body: dict[str, Any] = {
            "name": row[2] or bot_id,
            "summary": row[3] or "",
            "staff_no": owner_id,
            "visibility": visibility,
        }

        try:
            resp = client.post(f"/admin/bots/{bot_uuid}/ensure", json=body)
            resp.raise_for_status()
            data = resp.json().get("data", resp.json())
            created = data.get("created", False)
            print(f"  ensured {bot_uuid}: created={created}")
            ensured += 1

            # D11: backfill ac_bots.ext.bcs.bot_uuid
            ext = _get_ext(cursor, bot_id, owner_id)
            if not ext.get("bcs", {}).get("bot_uuid"):
                bcs_ext = ext.get("bcs", {})
                bcs_ext["bot_uuid"] = bot_uuid
                ext["bcs"] = bcs_ext
                _update_ext(db, cursor, bot_id, owner_id, ext)

        except Exception as e:
            print(f"  FAILED {bot_uuid}: {e}", file=sys.stderr)
            failed += 1

        # Rate limit
        time.sleep(1.0 / max(args.qps, 1))

    # --- Step 0c: Verify completeness ---
    cursor.execute(find_missing_sql, (args.env,))
    remaining = len(cursor.fetchall())
    print(f"\n[Phase 0] Done: ensured={ensured}, failed={failed}, remaining={remaining}")
    cursor.close()
    db.close()
    return 1 if remaining > 0 else 0


def _connect_db():
    import pymysql
    return pymysql.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", "3306")),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"],
        charset="utf8mb4",
    )


def _map_visibility(public: str | None, friend_approval: str | None) -> str:
    """Map backend (public, friend_approval) → BCS visibility (spec §3.2)."""
    if public == "0":
        return "private"
    if friend_approval == "0":
        return "public"
    return "protected"


def _get_ext(cursor, bot_id: str, owner_id: str) -> dict:
    cursor.execute("SELECT ext FROM ac_bots WHERE bot_id=%s AND owner_id=%s", (bot_id, owner_id))
    row = cursor.fetchone()
    if row and row[0]:
        try:
            return json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _update_ext(db, cursor, bot_id: str, owner_id: str, ext: dict) -> None:
    ext_json = json.dumps(ext, ensure_ascii=False)
    cursor.execute(
        "UPDATE ac_bots SET ext=%s WHERE bot_id=%s AND owner_id=%s",
        (ext_json, bot_id, owner_id),
    )
    db.commit()


if __name__ == "__main__":
    sys.exit(main())