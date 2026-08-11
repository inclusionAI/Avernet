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

STATS_SCRIPT = Path(__file__).resolve().parents[1] / "vendor/avernet-star-daily/avernet_star_stats.py"
STATS_SPEC = importlib.util.spec_from_file_location("avernet_star_stats", STATS_SCRIPT)
assert STATS_SPEC is not None and STATS_SPEC.loader is not None
STATS = importlib.util.module_from_spec(STATS_SPEC)
STATS_SPEC.loader.exec_module(STATS)


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
            write_csv(csv_path, [row("2099-01-01", 100, 10), row("2099-01-02", 104, 11)])
            candidate_path.write_text(json.dumps(row("2099-01-02", 105, 11)), encoding="utf-8")

            result = REPORT.upsert_row(csv_path, candidate_path)
            rows = REPORT.load_rows(csv_path)

            self.assertEqual(result, {"date": "2099-01-02", "replaced": 1, "rows": 2})
            self.assertEqual([item["date"] for item in rows], ["2099-01-01", "2099-01-02"])
            self.assertEqual(rows[-1]["star_total"], "105")

    def test_upsert_repairs_duplicate_candidate_date_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "daily.csv"
            candidate_path = root / "candidate.json"
            write_csv(csv_path, [row("2099-01-02", 104, 11), row("2099-01-02", 105, 11)])
            candidate_path.write_text(json.dumps(row("2099-01-02", 106, 11)), encoding="utf-8")

            REPORT.upsert_row(csv_path, candidate_path)

            self.assertEqual(len(REPORT.load_rows(csv_path)), 1)
            self.assertEqual(REPORT.load_rows(csv_path)[0]["star_total"], "106")

    def test_upsert_rejects_unbalanced_candidate_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "daily.csv"
            candidate_path = root / "candidate.json"
            candidate = row("2099-01-02", 105, 11)
            candidate["non_rd_star_count"] = 93
            candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

            with self.assertRaisesRegex(REPORT.ContractError, r"star_total = RD \+ Non-RD"):
                REPORT.upsert_row(csv_path, candidate_path)

            self.assertFalse(csv_path.exists())

    def test_validate_report_rejects_historical_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "daily.csv"
            png_path = root / "growth.png"
            write_csv(csv_path, [row("2099-01-01", 100, 10), row("2099-01-01", 101, 10)])
            write_png_header(png_path)

            with self.assertRaisesRegex(REPORT.ContractError, "duplicate dates"):
                REPORT.validate_report(csv_path, png_path, "2099-01-01")

    def test_validate_report_returns_machine_readable_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "daily.csv"
            png_path = root / "growth.png"
            write_csv(csv_path, [row("2099-01-01", 100, 10), row("2099-01-02", 105, 11)])
            write_png_header(png_path)

            result = REPORT.validate_report(csv_path, png_path, "2099-01-02")

            self.assertEqual(result["rows"], 2)
            self.assertEqual(result["today_rows"], 1)
            self.assertEqual(result["star_total"], 105)
            self.assertEqual((result["width"], result["height"]), (2048, 1092))

    def test_validate_report_rejects_wrong_png_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "daily.csv"
            png_path = root / "growth.png"
            write_csv(csv_path, [row("2099-01-02", 105, 11)])
            write_png_header(png_path, 1024, 546)

            with self.assertRaisesRegex(REPORT.ContractError, "expected 2048x1092"):
                REPORT.validate_report(csv_path, png_path, "2099-01-02")

    def test_prepare_roster_writes_private_file_without_returning_logins(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "roster.json"
            secret = json.dumps({"alice": "Alice", "bob": "Bob"})
            with mock.patch.dict(os.environ, {"TEST_ROSTER": secret}, clear=False):
                result = REPORT.prepare_roster(output, "TEST_ROSTER")

            self.assertEqual(result, {"status": "prepared"})
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), json.loads(secret))

    def test_public_validate_log_omits_aggregate_counts(self):
        result = {
            "date": "2099-01-02",
            "external": 94,
            "height": 1092,
            "internal": 11,
            "latest_date": "2099-01-02",
            "rows": 2,
            "star_total": 105,
            "today_rows": 1,
            "width": 2048,
        }

        public = REPORT.public_log_result("validate", result)

        self.assertEqual(public["status"], "valid")
        self.assertNotIn("internal", public)
        self.assertNotIn("external", public)
        self.assertNotIn("star_total", public)

    def test_roster_errors_do_not_include_login_values(self):
        cases = [
            ({"sentinel-login": ""}, "invalid nickname"),
            ({"Sentinel-Login": "One", "sentinel-login": "Two"}, "duplicate GitHub logins"),
        ]
        for payload, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                roster = Path(directory) / "roster.json"
                roster.write_text(json.dumps(payload), encoding="utf-8")

                with self.assertRaisesRegex(RuntimeError, expected) as caught:
                    STATS.load_rd_team(roster)

                self.assertNotIn("sentinel-login", str(caught.exception).lower())

    def test_require_visual_qa_fails_closed(self):
        payload = json.dumps({"status": "fail", "issue_codes": ["label_overlap"]})
        with mock.patch.dict(os.environ, {"TEST_QA": payload}, clear=False):
            with self.assertRaisesRegex(REPORT.ContractError, "Codex visual QA failed"):
                REPORT.require_visual_qa("TEST_QA")

    def test_require_visual_qa_rejects_extra_text_without_echoing_it(self):
        payload = json.dumps(
            {
                "status": "fail",
                "issue_codes": ["label_overlap"],
                "summary": "SENTINEL-PRIVATE",
            }
        )
        with mock.patch.dict(os.environ, {"TEST_QA": payload}, clear=False):
            with self.assertRaisesRegex(REPORT.ContractError, "output fields are invalid") as caught:
                REPORT.require_visual_qa("TEST_QA")

        self.assertNotIn("SENTINEL", str(caught.exception))

    def test_require_visual_qa_rejects_unknown_issue_code_without_echoing_it(self):
        payload = json.dumps({"status": "fail", "issue_codes": ["SENTINEL-PRIVATE"]})
        with mock.patch.dict(os.environ, {"TEST_QA": payload}, clear=False):
            with self.assertRaisesRegex(REPORT.ContractError, "issue codes are invalid") as caught:
                REPORT.require_visual_qa("TEST_QA")

        self.assertNotIn("SENTINEL", str(caught.exception))

    def test_require_visual_qa_accepts_clean_pass(self):
        payload = json.dumps({"status": "pass", "issue_codes": []})
        with mock.patch.dict(os.environ, {"TEST_QA": payload}, clear=False):
            result = REPORT.require_visual_qa("TEST_QA")

        self.assertEqual(result, {"status": "pass"})


if __name__ == "__main__":
    unittest.main()
