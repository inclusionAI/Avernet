#!/usr/bin/env python3
"""Phase 4 Shadow: compare old friend-reads vs new edge_grants reads.

Runs both the old-path (ac_bot_friend / bcs_friendships) and the new-path
(edge_grants via BCS /admission or direct SQL) for a sample of friend pairs,
then reports mismatches. Does NOT affect users (pure background comparison).

Mismatches indicate either:
- Missed migration (old says friend, new doesn't) → re-run ETL/reconciliation.
- Phantom edge (new says friend, old doesn't) → stale edge not revoked.

Usage:
    DB_HOST=<mysql> DB_USER=<user> DB_PASSWORD=<pw> DB_NAME=<db> \
    BCS_BASE_URL=http://localhost:21000 \
    python3 scripts/shadow_compare_friend_checks.py [--env prod] [--sample 1000] [--use-sql]

    --use-sql: compare via direct SQL (edge_grants) instead of BCS /admission HTTP.
    --sample N: compare N random pairs (default 1000).
"""
from __future__ import annotations

import argparse
import os
import random
import sys
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 4 shadow friend-check comparison")
    parser.add_argument("--env", default=os.environ.get("BCS_ENV", "prod"))
    parser.add_argument("--sample", type=int, default=1000, help="number of pairs to compare")
    parser.add_argument("--use-sql", action="store_true", help="compare via SQL (not HTTP /admission)")
    parser.add_argument("--bcs-url", default=os.environ.get("BCS_BASE_URL", "http://localhost:21000"))
    args = parser.parse_args()

    db = _connect_db()
    cursor = db.cursor()

    # --- Collect friend pairs from old tables ---
    # System A: ac_bot_friend ACCEPTED (human → bot)
    cursor.execute("""
        SELECT requester_entity_id, target_bot_id, target_entity_id, env
        FROM ac_bot_friend
        WHERE status = 'ACCEPTED' AND env = %s
        ORDER BY RAND() LIMIT %s
    """, (args.env, args.sample))
    system_a_pairs = cursor.fetchall()

    # System B: bcs_friendships (bot ↔ bot)
    cursor.execute("""
        SELECT left_bot, right_bot, env
        FROM bcs_friendships
        WHERE left_bot NOT LIKE 'human_%%' AND right_bot NOT LIKE 'human_%%'
          AND env = %s
        ORDER BY RAND() LIMIT %s
    """, (args.env, args.sample))
    system_b_pairs = cursor.fetchall()

    print(f"[Shadow] Sampling {len(system_a_pairs)} System A + {len(system_b_pairs)} System B pairs")

    mismatches = {"system_a_missed": 0, "system_a_phantom": 0,
                   "system_b_missed": 0, "system_b_phantom": 0}
    checked = 0

    # --- Compare System A (human → bot) ---
    for row in system_a_pairs:
        requester, target_bot, target_owner, env = row
        bot_uuid = f"{target_bot}:{target_owner}"
        from_id = f"human_{requester}"

        old_is_friend = True  # we selected ACCEPTED → old says friend

        if args.use_sql:
            new_is_friend = _check_edge_sql(cursor, from_id, bot_uuid, env)
        else:
            new_is_friend = _check_admission_http(args.bcs_url, bot_uuid, from_id, env)

        if old_is_friend and not new_is_friend:
            mismatches["system_a_missed"] += 1
            print(f"  [MISSED] A: {from_id} → {bot_uuid} (env={env})")
        elif not old_is_friend and new_is_friend:
            mismatches["system_a_phantom"] += 1
        checked += 1

    # --- Compare System B (bot ↔ bot) ---
    for row in system_b_pairs:
        left, right, env = row

        old_is_friend = True  # pair exists in bcs_friendships → old says friend

        # Check both directions
        if args.use_sql:
            new_forward = _check_edge_sql(cursor, left, right, env)
            new_reverse = _check_edge_sql(cursor, right, left, env)
        else:
            # For System B, HTTP /admission checks actor→bot (directed).
            # Friend = any-direction default edge.
            new_forward = _check_admission_http(args.bcs_url, right, left, env)
            new_reverse = _check_admission_http(args.bcs_url, left, right, env)

        new_is_friend = new_forward or new_reverse

        if old_is_friend and not new_is_friend:
            mismatches["system_b_missed"] += 1
            print(f"  [MISSED] B: {left} ↔ {right} (env={env})")
        elif not old_is_friend and new_is_friend:
            mismatches["system_b_phantom"] += 1
        checked += 1

    # --- Report ---
    total_mismatches = sum(mismatches.values())
    rate = total_mismatches / max(checked, 1) * 100
    print(f"\n[Shadow] Checked: {checked}")
    print(f"[Shadow] Mismatches: {mismatches}")
    print(f"[Shadow] Diff rate: {rate:.2f}%")
    print(f"[Shadow] {'PASS ✓' if total_mismatches == 0 else 'FAIL ✗ — investigate mismatches above'}")

    cursor.close()
    db.close()
    return 0 if total_mismatches == 0 else 1


def _check_edge_sql(cursor, from_id: str, to_id: str, env: str) -> bool:
    """Check if a friend edge exists in edge_grants (direct SQL)."""
    cursor.execute("""
        SELECT EXISTS(
          SELECT 1 FROM edge_grants e
          JOIN permission_profiles p ON e.grant_ref_id = p.permission_profile_id
          WHERE e.from_id = %s AND e.to_id = %s AND e.env = %s
            AND e.status = 'approved' AND e.grant_kind = 'permission_profile'
            AND p.is_default = TRUE AND p.status = 'active'
            AND p.bot_id = e.to_id
        ) AS has_edge
    """, (from_id, to_id, env))
    row = cursor.fetchone()
    return bool(row and row[0])


def _check_admission_http(bcs_url: str, bot_uuid: str, actor: str, env: str) -> bool:
    """Check via BCS HTTP /admission endpoint."""
    import httpx
    try:
        resp = httpx.get(
            f"{bcs_url}/bots/{bot_uuid}/admission",
            params={"actor": actor, "env": env},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("allowed", False)
    except Exception as e:
        print(f"  [HTTP ERROR] {bot_uuid} admission: {e}", file=sys.stderr)
        return False


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


if __name__ == "__main__":
    sys.exit(main())