from __future__ import annotations

import csv
import importlib.util
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "avernet_star_report.py"
SPEC = importlib.util.spec_from_file_location("avernet_star_report", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
REPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORT)


def row(date: str, total: int, internal: int) -> dict[str, str | int]:
    return {
        "date": date,
        "observed_at": f"{date}T18:00:00+08:00",
        "repo": "inclusionAI/Avernet",
        "star_total": total,
        "rd_star_count": internal,
        "non_rd_star_count": total - internal,
    }


def write_csv(path: Path, rows: list[dict[str, str | int]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=REPORT.CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_png_header(path: Path, width: int = 2048, height: int = 1092) -> None:
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
    )


class AvernetStarReportTest(unittest.TestCase):
    def test_upsert_replaces_every_row_for_candidate_date(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "daily.csv"
            candidate_path = root / "candidate.json"
            write_csv(csv_path, [row("2026-08-10", 434, 30), row("2026-08-11", 437, 30)])
            candidate_path.write_text(json.dumps(row("2026-08-11", 438, 30)), encoding="utf-8")

            result = REPORT.upsert_row(csv_path, candidate_path)
            rows = REPORT.load_rows(csv_path)

            self.assertEqual(result, {"date": "2026-08-11", "replaced": 1, "rows": 2})
            self.assertEqual([item["date"] for item in rows], ["2026-08-10", "2026-08-11"])
            self.assertEqual(rows[-1]["star_total"], "438")

    def test_upsert_repairs_duplicate_candidate_date_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "daily.csv"
            candidate_path = root / "candidate.json"
            write_csv(csv_path, [row("2026-08-11", 437, 30), row("2026-08-11", 438, 30)])
            candidate_path.write_text(json.dumps(row("2026-08-11", 439, 30)), encoding="utf-8")

            REPORT.upsert_row(csv_path, candidate_path)

            self.assertEqual(len(REPORT.load_rows(csv_path)), 1)
            self.assertEqual(REPORT.load_rows(csv_path)[0]["star_total"], "439")

    def test_upsert_rejects_unbalanced_candidate_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "daily.csv"
            candidate_path = root / "candidate.json"
            candidate = row("2026-08-11", 438, 30)
            candidate["non_rd_star_count"] = 407
            candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

            with self.assertRaisesRegex(REPORT.ContractError, r"star_total = RD \+ Non-RD"):
                REPORT.upsert_row(csv_path, candidate_path)

            self.assertFalse(csv_path.exists())

    def test_validate_report_rejects_historical_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "daily.csv"
            png_path = root / "growth.png"
            write_csv(csv_path, [row("2026-08-10", 434, 30), row("2026-08-10", 435, 30)])
            write_png_header(png_path)

            with self.assertRaisesRegex(REPORT.ContractError, "duplicate dates"):
                REPORT.validate_report(csv_path, png_path, "2026-08-10")

    def test_validate_report_returns_machine_readable_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "daily.csv"
            png_path = root / "growth.png"
            write_csv(csv_path, [row("2026-08-10", 434, 30), row("2026-08-11", 438, 30)])
            write_png_header(png_path)

            result = REPORT.validate_report(csv_path, png_path, "2026-08-11")

            self.assertEqual(result["rows"], 2)
            self.assertEqual(result["today_rows"], 1)
            self.assertEqual(result["star_total"], 438)
            self.assertEqual((result["width"], result["height"]), (2048, 1092))

    def test_validate_report_rejects_wrong_png_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "daily.csv"
            png_path = root / "growth.png"
            write_csv(csv_path, [row("2026-08-11", 438, 30)])
            write_png_header(png_path, 1024, 546)

            with self.assertRaisesRegex(REPORT.ContractError, "expected 2048x1092"):
                REPORT.validate_report(csv_path, png_path, "2026-08-11")

    def test_prepare_roster_writes_private_file_without_returning_logins(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "roster.json"
            secret = json.dumps({"alice": "Alice", "bob": "Bob"})
            with mock.patch.dict(os.environ, {"TEST_ROSTER": secret}, clear=False):
                result = REPORT.prepare_roster(output, "TEST_ROSTER")

            self.assertEqual(result, {"entries": 2})
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), json.loads(secret))

    def test_require_visual_qa_fails_closed(self):
        payload = json.dumps({"status": "fail", "summary": "label overlap", "issues": ["Jul 10"]})
        with mock.patch.dict(os.environ, {"TEST_QA": payload}, clear=False):
            with self.assertRaisesRegex(REPORT.ContractError, "Jul 10"):
                REPORT.require_visual_qa("TEST_QA")

    def test_require_visual_qa_accepts_clean_pass(self):
        payload = json.dumps({"status": "pass", "summary": "layout is clear", "issues": []})
        with mock.patch.dict(os.environ, {"TEST_QA": payload}, clear=False):
            result = REPORT.require_visual_qa("TEST_QA")

        self.assertEqual(result, {"status": "pass", "summary": "layout is clear"})


if __name__ == "__main__":
    unittest.main()
