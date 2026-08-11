#!/usr/bin/env python3
"""Prepare and validate the persisted Avernet Star daily report artifacts."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
from typing import Any


CSV_FIELDS = [
    "date",
    "observed_at",
    "repo",
    "star_total",
    "rd_star_count",
    "non_rd_star_count",
]
EXPECTED_REPO = "inclusionAI/Avernet"
SHANGHAI_OFFSET = dt.timedelta(hours=8)
PNG_WIDTH = 2048
PNG_HEIGHT = 1092


class ContractError(RuntimeError):
    """Raised when an input or generated artifact violates the report contract."""


def normalize_row(raw: dict[str, Any], source: str) -> dict[str, str]:
    missing = [field for field in CSV_FIELDS if field not in raw]
    if missing:
        raise ContractError(f"{source} is missing CSV fields: {', '.join(missing)}")

    date_text = str(raw["date"])
    observed_text = str(raw["observed_at"])
    repo = str(raw["repo"])
    try:
        row_date = dt.date.fromisoformat(date_text)
        observed_at = dt.datetime.fromisoformat(observed_text)
    except ValueError as exc:
        raise ContractError(f"{source} has an invalid date or observed_at value") from exc

    if observed_at.utcoffset() != SHANGHAI_OFFSET:
        raise ContractError(f"{source} observed_at must use the Asia/Shanghai offset")
    if observed_at.date() != row_date:
        raise ContractError(f"{source} date does not match observed_at")
    if repo != EXPECTED_REPO:
        raise ContractError(f"{source} repo must be {EXPECTED_REPO}")

    counts: dict[str, int] = {}
    for field in ("star_total", "rd_star_count", "non_rd_star_count"):
        try:
            value = int(raw[field])
        except (TypeError, ValueError) as exc:
            raise ContractError(f"{source} {field} must be an integer") from exc
        if value < 0:
            raise ContractError(f"{source} {field} must not be negative")
        counts[field] = value

    if counts["star_total"] != counts["rd_star_count"] + counts["non_rd_star_count"]:
        raise ContractError(f"{source} does not satisfy star_total = RD + Non-RD")

    return {
        "date": date_text,
        "observed_at": observed_text,
        "repo": repo,
        "star_total": str(counts["star_total"]),
        "rd_star_count": str(counts["rd_star_count"]),
        "non_rd_star_count": str(counts["non_rd_star_count"]),
    }


def load_rows(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists():
        return []
    with csv_path.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != CSV_FIELDS:
            raise ContractError(f"CSV schema is invalid: {csv_path}")
        return [normalize_row(dict(row), f"CSV line {line}") for line, row in enumerate(reader, 2)]


def ensure_unique_dates(rows: list[dict[str, str]]) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for row in rows:
        date_text = row["date"]
        if date_text in seen:
            duplicates.add(date_text)
        seen.add(date_text)
    if duplicates:
        duplicate_text = ", ".join(sorted(duplicates))
        raise ContractError(f"CSV contains duplicate dates: {duplicate_text}")


def write_rows_atomic(csv_path: Path, rows: list[dict[str, str]]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{csv_path.name}.", dir=csv_path.parent, text=True
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary_path, csv_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def upsert_row(csv_path: Path, row_path: Path) -> dict[str, Any]:
    try:
        candidate_payload = json.loads(row_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContractError(f"Candidate row is invalid JSON: {row_path}") from exc
    if not isinstance(candidate_payload, dict):
        raise ContractError(f"Candidate row must be a JSON object: {row_path}")

    candidate = normalize_row(candidate_payload, "candidate row")
    existing_rows = load_rows(csv_path)
    rows = [row for row in existing_rows if row["date"] != candidate["date"]]
    rows.append(candidate)
    rows.sort(key=lambda row: row["date"])
    ensure_unique_dates(rows)
    write_rows_atomic(csv_path, rows)
    return {
        "date": candidate["date"],
        "replaced": sum(row["date"] == candidate["date"] for row in existing_rows),
        "rows": len(rows),
    }


def read_png_dimensions(png_path: Path) -> tuple[int, int]:
    header = png_path.read_bytes()[:24]
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ContractError(f"Image is not a valid PNG: {png_path}")
    return struct.unpack(">II", header[16:24])


def validate_report(csv_path: Path, png_path: Path, expected_date: str) -> dict[str, Any]:
    try:
        dt.date.fromisoformat(expected_date)
    except ValueError as exc:
        raise ContractError(f"Expected date is invalid: {expected_date}") from exc

    rows = load_rows(csv_path)
    if not rows:
        raise ContractError(f"CSV has no data rows: {csv_path}")
    ensure_unique_dates(rows)
    today_rows = [row for row in rows if row["date"] == expected_date]
    if len(today_rows) != 1:
        raise ContractError(f"CSV must contain exactly one row for {expected_date}")

    width, height = read_png_dimensions(png_path)
    if (width, height) != (PNG_WIDTH, PNG_HEIGHT):
        raise ContractError(
            f"PNG dimensions are {width}x{height}; expected {PNG_WIDTH}x{PNG_HEIGHT}"
        )

    latest = max(rows, key=lambda row: row["observed_at"])
    return {
        "date": expected_date,
        "external": int(today_rows[0]["non_rd_star_count"]),
        "height": height,
        "internal": int(today_rows[0]["rd_star_count"]),
        "latest_date": latest["date"],
        "rows": len(rows),
        "star_total": int(today_rows[0]["star_total"]),
        "today_rows": len(today_rows),
        "width": width,
    }


def prepare_roster(output_path: Path, env_name: str) -> dict[str, str]:
    raw = os.environ.get(env_name, "")
    if not raw:
        raise ContractError(f"Required GitHub Secret is missing: {env_name}")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ContractError(f"GitHub Secret {env_name} is not valid JSON") from exc
    if not isinstance(payload, dict) or not payload:
        raise ContractError(f"GitHub Secret {env_name} must be a non-empty JSON object")
    for login, nickname in payload.items():
        if not isinstance(login, str) or not login.strip():
            raise ContractError(f"GitHub Secret {env_name} contains an invalid login")
        if not isinstance(nickname, str) or not nickname.strip():
            raise ContractError(f"GitHub Secret {env_name} contains an invalid nickname")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    output_path.chmod(0o600)
    return {"status": "prepared"}


def public_log_result(command: str, result: dict[str, Any]) -> dict[str, Any]:
    if command == "validate":
        return {
            "date": result["date"],
            "height": result["height"],
            "rows": result["rows"],
            "status": "valid",
            "today_rows": result["today_rows"],
            "width": result["width"],
        }
    return result


VISUAL_QA_ISSUE_CODES = {
    "growth_line_unclear",
    "latest_emphasis_missing",
    "label_vocabulary_invalid",
    "label_overlap",
    "content_cropped",
    "text_missing",
    "value_mismatch",
    "rendering_error",
    "blank_region_abnormal",
}


def require_visual_qa(env_name: str) -> dict[str, str]:
    raw = os.environ.get(env_name, "")
    if not raw:
        raise ContractError(f"Codex visual QA output is missing: {env_name}")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ContractError("Codex visual QA output is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ContractError("Codex visual QA output must be a JSON object")
    if set(payload) != {"status", "issue_codes"}:
        raise ContractError("Codex visual QA output fields are invalid")

    status = payload.get("status")
    issue_codes = payload.get("issue_codes")
    if status not in {"pass", "fail"}:
        raise ContractError("Codex visual QA status must be pass or fail")
    if (
        not isinstance(issue_codes, list)
        or any(
            not isinstance(code, str) or code not in VISUAL_QA_ISSUE_CODES
            for code in issue_codes
        )
        or len(issue_codes) != len(set(issue_codes))
    ):
        raise ContractError("Codex visual QA issue codes are invalid")
    if status != "pass" or issue_codes:
        raise ContractError("Codex visual QA failed")
    return {"status": status}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    upsert = subparsers.add_parser("upsert", help="Replace today's row and keep one row per date")
    upsert.add_argument("--csv", type=Path, required=True)
    upsert.add_argument("--row-json", type=Path, required=True)

    validate = subparsers.add_parser("validate", help="Validate CSV and PNG artifacts")
    validate.add_argument("--csv", type=Path, required=True)
    validate.add_argument("--png", type=Path, required=True)
    validate.add_argument("--expected-date", required=True)
    validate.add_argument("--output-json", type=Path)

    roster = subparsers.add_parser("prepare-roster", help="Materialize the private roster Secret")
    roster.add_argument("--output", type=Path, required=True)
    roster.add_argument("--env-name", default="AVERNET_RD_ROSTER_JSON")

    qa = subparsers.add_parser("require-visual-qa", help="Fail unless Codex visual QA passed")
    qa.add_argument("--env-name", default="AVERNET_STAR_QA_JSON")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "upsert":
            result = upsert_row(args.csv, args.row_json)
        elif args.command == "validate":
            result = validate_report(args.csv, args.png, args.expected_date)
            if args.output_json:
                args.output_json.parent.mkdir(parents=True, exist_ok=True)
                args.output_json.write_text(
                    json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
        elif args.command == "prepare-roster":
            result = prepare_roster(args.output, args.env_name)
        else:
            result = require_visual_qa(args.env_name)
        print(json.dumps(public_log_result(args.command, result), ensure_ascii=False, sort_keys=True))
        return 0
    except (ContractError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
