#!/usr/bin/env python3
"""Read-only TC CSV inventory; --sqlite writes locally, --url imports through v2."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path

import httpx

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP_ROOT / "src"), str(APP_ROOT / "packages/search-core/src")]

from search_core.providers.sql_catalog import sqlite_catalog_repository

from app.tc.migration import MigrationWriteError, migrate_rows
from app.tc.migration_api import ApiMigrationRepository


def _legacy_index(path: str, stack: ExitStack):
    """Disk-backed lookup keeps CSV and supplemental JSONL streaming."""
    directory = stack.enter_context(tempfile.TemporaryDirectory(prefix="tc-migration-"))
    connection = sqlite3.connect(str(Path(directory) / "legacy.sqlite"))
    stack.callback(connection.close)
    connection.execute(
        "CREATE TABLE legacy (env TEXT, worker TEXT, payload TEXT, PRIMARY KEY(env, worker))"
    )
    with open(path, encoding="utf-8") as source:
        for line in source:
            item = json.loads(line)
            if not isinstance(item, dict):
                raise TypeError("invalid legacy record")
            environment, worker = (
                item.pop("environment", None),
                item.pop("worker_id", None),
            )
            if not isinstance(environment, str) or not environment.strip():
                raise ValueError("invalid legacy environment")
            if not isinstance(worker, str) or not worker.strip():
                raise ValueError("invalid legacy worker")
            connection.execute(
                "INSERT INTO legacy VALUES (?, ?, ?)",
                (environment, worker, json.dumps(item, allow_nan=False)),
            )
    connection.commit()

    def lookup(environment, worker):
        record = connection.execute(
            "SELECT payload FROM legacy WHERE env=? AND worker=?", (environment, worker)
        ).fetchone()
        return json.loads(record[0]) if record else None

    return lookup


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", help="user-provided bcs_bots CSV")
    parser.add_argument(
        "--environment", required=True, help="select exactly one source environment"
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        "--sqlite", help="explicitly write this isolated local SQLite catalog"
    )
    target.add_argument("--url", help="explicit TC service URL; import only through v2")
    parser.add_argument(
        "--legacy-jsonl",
        help="optional selected identity/state/config and archival material",
    )
    parser.add_argument(
        "--archive", help="create a local JSONL reconciliation archive with mode 0600"
    )
    args = parser.parse_args(argv)
    try:
        with ExitStack() as stack:
            lookup = (
                _legacy_index(args.legacy_jsonl, stack) if args.legacy_jsonl else None
            )
            repository = None
            if args.url:
                token = os.getenv("BCSFUSE_AUTH_TOKEN")
                if not token:
                    raise ValueError("BCSFUSE_AUTH_TOKEN is required for API migration")
                client = stack.enter_context(
                    httpx.Client(
                        base_url=args.url.rstrip("/"),
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=60,
                    )
                )
                repository = ApiMigrationRepository(client, args.environment)
            if args.sqlite:
                descriptor = os.open(args.sqlite, os.O_RDWR | os.O_CREAT, 0o600)
                os.close(descriptor)
                repository = sqlite_catalog_repository(
                    args.sqlite,
                    environment=args.environment,
                )
            archive = None
            if args.archive:
                descriptor = os.open(
                    args.archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
                output = stack.enter_context(
                    os.fdopen(descriptor, "w", encoding="utf-8")
                )

                def archive(record):
                    output.write(
                        json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n"
                    )
                    output.flush()

            source = stack.enter_context(
                open(args.csv, newline="", encoding="utf-8-sig")
            )
            report = migrate_rows(
                csv.DictReader(source),
                repository,
                environment=args.environment,
                legacy_lookup=lookup,
                archive=archive,
                dry_run=repository is None,
            )
        print(json.dumps(report.as_dict(), ensure_ascii=False))
        return 1 if report.counts.get("invalid") else 0
    except MigrationWriteError as error:
        print(json.dumps(error.report.as_dict(), ensure_ascii=False))
        return 1
    except (OSError, ValueError, TypeError, sqlite3.Error, httpx.HTTPError):
        print(
            json.dumps(
                {
                    "error": (
                        "invalid_or_unavailable_input_or_target"
                        if args.url
                        else "invalid_or_unavailable_input_or_local_catalog"
                    )
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
