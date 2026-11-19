#!/usr/bin/env python3
"""Read-only JSONL execution monitor. Does not import/bootstrap Backend or login."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

from avernet_evolution.core.evolution_run.events import ExecutionEvents


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--after", type=int, default=0)
    parser.add_argument("--follow", action="store_true")
    args = parser.parse_args()
    # mode=ro also refuses to accidentally create an empty database at a typo path.
    conn = sqlite3.connect(f"{args.database.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    events = ExecutionEvents(conn, initialize=False)
    cursor = args.after
    try:
        while True:
            rows = events.for_run(args.run_id, after=cursor)
            for row in rows:
                print(json.dumps(row, ensure_ascii=False), flush=True)
                cursor = row["id"]
            if len(rows) == 500:
                continue
            if not args.follow:
                break
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        conn.close()


if __name__ == "__main__":
    main()
