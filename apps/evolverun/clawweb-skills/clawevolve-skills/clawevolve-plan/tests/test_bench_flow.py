from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from clawevolve_plan.pipeline.bench_flow import _ensure_clawweb_domain  # noqa: E402


class BenchFlowUploadTest(unittest.TestCase):
    def test_uploads_and_requires_verified_published_domain(self):
        with tempfile.TemporaryDirectory() as td:
            output_dir = Path(td)
            zip_path = output_dir / "bench.zip"
            zip_path.write_bytes(b"zip")
            args = argparse.Namespace(
                overwrite=True,
                skip_clawweb_report=False,
                owner_id="owner-1",
            )
            expected = {
                "status": "published",
                "published": True,
                "verified": True,
                "domain_id": "domain-1",
            }
            with mock.patch(
                "clawevolve_plan.pipeline.bench_flow._upload_templates_to_clawweb",
                return_value=expected,
            ) as upload:
                actual = _ensure_clawweb_domain(
                    args, {"bot_id": "bot-1"}, output_dir, zip_path, ["case-1"]
                )

            self.assertEqual(actual, expected)
            self.assertEqual(upload.call_args.kwargs["explicit_user_id"], "owner-1")

    def test_unverified_upload_fails_plan_before_domain_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            output_dir = Path(td)
            zip_path = output_dir / "bench.zip"
            zip_path.write_bytes(b"zip")
            args = argparse.Namespace(
                overwrite=True,
                skip_clawweb_report=False,
                owner_id="owner-1",
            )
            with mock.patch(
                "clawevolve_plan.pipeline.bench_flow._upload_templates_to_clawweb",
                return_value={
                    "status": "published_verify_failed",
                    "published": True,
                    "verified": False,
                    "domain_id": "domain-1",
                },
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "did not produce a verified published domain"
                ):
                    _ensure_clawweb_domain(
                        args,
                        {"bot_id": "bot-1"},
                        output_dir,
                        zip_path,
                        ["case-1"],
                    )

    def test_local_skip_does_not_call_upload(self):
        with tempfile.TemporaryDirectory() as td:
            output_dir = Path(td)
            zip_path = output_dir / "bench.zip"
            zip_path.write_bytes(b"zip")
            args = argparse.Namespace(
                overwrite=True,
                skip_clawweb_report=True,
                owner_id="owner-1",
            )
            with mock.patch(
                "clawevolve_plan.pipeline.bench_flow._upload_templates_to_clawweb"
            ) as upload:
                result = _ensure_clawweb_domain(
                    args, {"bot_id": "bot-1"}, output_dir, zip_path, ["case-1"]
                )

            upload.assert_not_called()
            self.assertEqual(result["status"], "skipped")
            self.assertEqual(result["reason"], "skip_clawweb_report")


if __name__ == "__main__":
    unittest.main()
