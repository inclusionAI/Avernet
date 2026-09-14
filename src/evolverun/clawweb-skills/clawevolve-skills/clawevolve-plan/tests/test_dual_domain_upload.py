from __future__ import annotations

# Test modules add the package root to sys.path before importing project code.
# ruff: noqa: E402

import argparse
import hashlib
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

PLAN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLAN_ROOT))

from clawevolve_plan.bench.template_builder import (
    ensure_split_packages,
    render_templates,
)
from clawevolve_plan.integration.clawweb import (
    ClawWebClient,
    ClawWebHTTPError,
    _body_list,
    upload_templates,
)
from clawevolve_plan.pipeline.bench_flow import _ensure_clawweb_domains
from clawevolve_plan.pipeline.existing import (
    _existing_reusable_clawweb_upload_result,
)
from clawevolve_plan.pipeline.reporting import (
    FinalStepReportGate,
    _result,
    submit_final_step_report,
)
from clawevolve_plan.pipeline.runner import run_plan_command
from clawevolve_plan.pipeline.step_report_payload import build_step_report_output
from clawevolve_plan.pipeline.upload import (
    _domain_id,
    revalidate_cached_bench_domains,
    upload_bench_domains,
)
from clawevolve_plan.runtime_identity import resolve_runtime_user_id


def _write_split_zip(path: Path, split: str, template_names: list[str]) -> str:
    prefix = "opt" if split == "train" else "val"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in template_names:
            archive.writestr(f"{prefix}/{name}.md", f"# {name}\n")
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DualDomainTemplateTests(unittest.TestCase):
    def test_renderer_creates_isolated_train_and_test_packages(self):
        plan = {
            "bot_id": "bot-1",
            "cases": [
                {
                    "case_id": f"case-{index}",
                    "query": f"query {index}",
                    "case_type": "bad",
                }
                for index in range(1, 6)
            ],
        }
        with tempfile.TemporaryDirectory(prefix="dual-domain-zips-") as td:
            _, aggregate, _, manifest = render_templates(plan, Path(td))
            train = manifest["split_packages"]["train"]
            test = manifest["split_packages"]["test"]
            with zipfile.ZipFile(train["zip_path"]) as archive:
                self.assertTrue(archive.namelist())
                self.assertTrue(
                    all(name.startswith("opt/") for name in archive.namelist())
                )
            with zipfile.ZipFile(test["zip_path"]) as archive:
                self.assertTrue(archive.namelist())
                self.assertTrue(
                    all(name.startswith("val/") for name in archive.namelist())
                )
            with zipfile.ZipFile(aggregate) as archive:
                self.assertEqual(
                    len([name for name in archive.namelist() if name.endswith(".md")]),
                    5,
                )
            self.assertEqual(train["template_count"] + test["template_count"], 5)

    def test_uploads_two_distinct_domains_with_split_template_sets(self):
        with tempfile.TemporaryDirectory(prefix="dual-domain-upload-") as td:
            root = Path(td)
            train_zip = root / "train.zip"
            test_zip = root / "test.zip"
            train_digest = _write_split_zip(
                train_zip, "train", ["task_train_1", "task_train_2"]
            )
            test_digest = _write_split_zip(test_zip, "test", ["task_test_1"])
            manifest = {
                "split_packages": {
                    "train": {
                        "zip_path": str(train_zip),
                        "zip_sha256": train_digest,
                        "template_names": ["task_train_1", "task_train_2"],
                    },
                    "test": {
                        "zip_path": str(test_zip),
                        "zip_sha256": test_digest,
                        "template_names": ["task_test_1"],
                    },
                }
            }
            calls = []

            def fake_upload(
                domain_id,
                zip_path,
                user_id,
                cookie="",
                *,
                expected_template_names=None,
                expected_template_hashes=None,
            ):
                calls.append(
                    (
                        domain_id,
                        Path(zip_path).name,
                        user_id,
                        list(expected_template_names or []),
                        dict(expected_template_hashes or {}),
                    )
                )
                return {
                    "enabled": True,
                    "status": "published",
                    "domain_id": domain_id,
                    "owner_user_id": user_id,
                    "base_url": "https://clawweb.example",
                    "published": True,
                    "verified": True,
                }

            with (
                patch.dict(os.environ, {"CLAWBENCH_OWNER_ID": "owner-1"}, clear=False),
                patch(
                    "clawevolve_plan.pipeline.upload.upload_templates",
                    side_effect=fake_upload,
                ),
            ):
                result = upload_bench_domains(
                    plan={"bot_id": "bot-1"}, manifest=manifest, task_id="EV-1"
                )

            self.assertTrue(result["published"])
            self.assertTrue(result["verified"])
            self.assertEqual(len(calls), 2)
            self.assertNotEqual(calls[0][0], calls[1][0])
            self.assertEqual(calls[0][3], ["task_train_1", "task_train_2"])
            self.assertEqual(calls[1][3], ["task_test_1"])
            self.assertEqual(set(calls[0][4]), {"task_train_1", "task_train_2"})
            self.assertEqual(set(calls[1][4]), {"task_test_1"})

    def test_single_case_reuses_published_train_domain_for_test_execution(self):
        plan = {
            "bot_id": "bot-1",
            "cases": [
                {
                    "case_id": "case-only",
                    "query": "single replayable task",
                    "case_type": "good",
                }
            ],
        }
        calls = []

        def fake_upload(
            domain_id,
            zip_path,
            user_id,
            cookie="",
            *,
            expected_template_names=None,
            expected_template_hashes=None,
        ):
            calls.append(
                (domain_id, Path(zip_path), list(expected_template_names or []))
            )
            return {
                "enabled": True,
                "status": "published",
                "domain_id": domain_id,
                "owner_user_id": user_id,
                "base_url": "https://clawweb.example",
                "published": True,
                "verified": True,
            }

        with tempfile.TemporaryDirectory(prefix="single-case-domain-") as td:
            _, _, _, manifest = render_templates(plan, Path(td))
            with (
                patch.dict(os.environ, {"CLAWBENCH_OWNER_ID": "owner-1"}, clear=False),
                patch(
                    "clawevolve_plan.pipeline.upload.upload_templates",
                    side_effect=fake_upload,
                ),
            ):
                result = upload_bench_domains(
                    plan=plan, manifest=manifest, task_id="EV-SINGLE"
                )
            report = build_step_report_output({}, {}, manifest, result)

        self.assertEqual(len(calls), 1)
        self.assertEqual(
            calls[0][2], manifest["split_packages"]["train"]["template_names"]
        )
        self.assertTrue(result["published"])
        self.assertFalse(result["validation_independent"])
        self.assertEqual(result["validation_mode"], "shared_train_domain")
        self.assertEqual(result["train_domain_id"], result["test_domain_id"])
        self.assertEqual(result["domains"]["test"]["alias_of"], "train")
        self.assertEqual(report["benchCases"]["trainCount"], 1)
        self.assertEqual(report["benchCases"]["testCount"], 0)
        self.assertEqual(
            report["benchDomains"]["trainBenchDomainId"],
            report["benchDomains"]["testBenchDomainId"],
        )
        self.assertFalse(report["benchDomains"]["validationIndependent"])

    def test_invalid_test_package_prevents_any_domain_creation(self):
        with tempfile.TemporaryDirectory(prefix="dual-domain-preflight-") as td:
            root = Path(td)
            train_zip = root / "train.zip"
            train_digest = _write_split_zip(train_zip, "train", ["task_train"])
            manifest = {
                "split_packages": {
                    "train": {
                        "zip_path": str(train_zip),
                        "zip_sha256": train_digest,
                        "template_names": ["task_train"],
                    },
                    "test": {
                        "zip_path": str(root / "missing-test.zip"),
                        "zip_sha256": "missing",
                        "template_names": ["task_test"],
                    },
                }
            }
            with patch("clawevolve_plan.pipeline.upload.upload_templates") as upload:
                result = upload_bench_domains(
                    plan={"bot_id": "bot-1"}, manifest=manifest, task_id="EV-1"
                )

        upload.assert_not_called()
        self.assertFalse(result["published"])
        self.assertEqual(result["domains"]["train"]["status"], "not_attempted")
        self.assertIn("does not exist", result["domains"]["test"]["reason"])

    def test_overlapping_split_names_prevent_any_domain_creation(self):
        with tempfile.TemporaryDirectory(prefix="dual-domain-overlap-") as td:
            root = Path(td)
            packages = {}
            for split in ("train", "test"):
                path = root / f"{split}.zip"
                packages[split] = {
                    "zip_path": str(path),
                    "zip_sha256": _write_split_zip(path, split, ["task_shared"]),
                    "template_names": ["task_shared"],
                }
            with patch("clawevolve_plan.pipeline.upload.upload_templates") as upload:
                result = upload_bench_domains(
                    plan={"bot_id": "bot-1"},
                    manifest={"split_packages": packages},
                    task_id="EV-1",
                )

        upload.assert_not_called()
        self.assertEqual(result["status"], "failed")
        self.assertIn("overlap", result["domains"]["train"]["reason"])
        self.assertIn("overlap", result["domains"]["test"]["reason"])

    def test_symbolic_link_archive_entry_prevents_any_domain_creation(self):
        with tempfile.TemporaryDirectory(prefix="dual-domain-symlink-") as td:
            root = Path(td)
            train_zip = root / "train.zip"
            link_info = zipfile.ZipInfo("opt/task_train.md")
            link_info.create_system = 3
            link_info.external_attr = (0o120777 << 16)
            with zipfile.ZipFile(train_zip, "w") as archive:
                archive.writestr(link_info, "../../secret.md")
            test_zip = root / "test.zip"
            manifest = {
                "split_packages": {
                    "train": {
                        "zip_path": str(train_zip),
                        "zip_sha256": hashlib.sha256(
                            train_zip.read_bytes()
                        ).hexdigest(),
                        "template_names": ["task_train"],
                    },
                    "test": {
                        "zip_path": str(test_zip),
                        "zip_sha256": _write_split_zip(
                            test_zip, "test", ["task_test"]
                        ),
                        "template_names": ["task_test"],
                    },
                }
            }
            with patch("clawevolve_plan.pipeline.upload.upload_templates") as upload:
                result = upload_bench_domains(
                    plan={"bot_id": "bot-1"}, manifest=manifest, task_id="EV-1"
                )

        upload.assert_not_called()
        self.assertIn("symbolic links", result["domains"]["train"]["reason"])
        self.assertEqual(result["domains"]["test"]["status"], "not_attempted")

    def test_nested_archive_path_prevents_any_domain_creation(self):
        with tempfile.TemporaryDirectory(prefix="dual-domain-nested-path-") as td:
            root = Path(td)
            train_zip = root / "train.zip"
            test_zip = root / "test.zip"
            with zipfile.ZipFile(train_zip, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("opt/nested/task_train.md", "# train\n")
            packages = {
                "train": {
                    "zip_path": str(train_zip),
                    "zip_sha256": hashlib.sha256(train_zip.read_bytes()).hexdigest(),
                    "template_names": ["task_train"],
                },
                "test": {
                    "zip_path": str(test_zip),
                    "zip_sha256": _write_split_zip(test_zip, "test", ["task_test"]),
                    "template_names": ["task_test"],
                },
            }
            with patch("clawevolve_plan.pipeline.upload.upload_templates") as upload:
                result = upload_bench_domains(
                    plan={"bot_id": "bot-1"},
                    manifest={"split_packages": packages},
                    task_id="EV-1",
                )

        upload.assert_not_called()
        self.assertIn("outside opt/*.md", result["domains"]["train"]["reason"])
        self.assertEqual(result["domains"]["test"]["status"], "not_attempted")

    def test_wrong_split_archive_prevents_any_domain_creation(self):
        with tempfile.TemporaryDirectory(prefix="dual-domain-wrong-split-") as td:
            root = Path(td)
            train_zip = root / "train.zip"
            test_zip = root / "test.zip"
            packages = {
                "train": {
                    "zip_path": str(train_zip),
                    "zip_sha256": _write_split_zip(train_zip, "test", ["task_train"]),
                    "template_names": ["task_train"],
                },
                "test": {
                    "zip_path": str(test_zip),
                    "zip_sha256": _write_split_zip(test_zip, "test", ["task_test"]),
                    "template_names": ["task_test"],
                },
            }
            with patch("clawevolve_plan.pipeline.upload.upload_templates") as upload:
                result = upload_bench_domains(
                    plan={"bot_id": "bot-1"},
                    manifest={"split_packages": packages},
                    task_id="EV-1",
                )

        upload.assert_not_called()
        self.assertIn("outside opt/*.md", result["domains"]["train"]["reason"])
        self.assertEqual(result["domains"]["test"]["status"], "not_attempted")

    def test_one_split_upload_failure_fails_whole_result(self):
        with tempfile.TemporaryDirectory(prefix="dual-domain-partial-") as td:
            root = Path(td)
            packages = {}
            for split in ("train", "test"):
                path = root / f"{split}.zip"
                template_names = [f"task_{split}"]
                packages[split] = {
                    "zip_path": str(path),
                    "zip_sha256": _write_split_zip(path, split, template_names),
                    "template_names": template_names,
                }

            def fake_upload(domain_id, zip_path, user_id, **kwargs):
                success = Path(zip_path).name == "train.zip"
                return {
                    "domain_id": domain_id,
                    "owner_user_id": user_id,
                    "published": success,
                    "verified": success,
                    "status": "published" if success else "published_verify_failed",
                }

            with (
                patch.dict(os.environ, {"CLAWBENCH_OWNER_ID": "owner-1"}, clear=False),
                patch(
                    "clawevolve_plan.pipeline.upload.upload_templates",
                    side_effect=fake_upload,
                ),
            ):
                result = upload_bench_domains(
                    plan={"bot_id": "bot-1"},
                    manifest={"split_packages": packages},
                    task_id="EV-1",
                )

        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["published"])
        self.assertTrue(result["domains"]["train"]["verified"])
        self.assertFalse(result["domains"]["test"]["verified"])

    def test_step_report_returns_only_verified_split_domains(self):
        upload_result = {
            "published": True,
            "verified": True,
            "domains": {
                "train": {
                    "domain_id": "train-domain",
                    "owner_user_id": "owner",
                    "base_url": "https://clawweb.example",
                    "published": True,
                    "verified": True,
                },
                "test": {
                    "domain_id": "test-domain",
                    "owner_user_id": "owner",
                    "base_url": "https://clawweb.example",
                    "published": True,
                    "verified": True,
                },
            },
        }
        output = build_step_report_output({}, {}, {}, upload_result)
        domains = output["benchDomains"]
        self.assertEqual(domains["trainBenchDomainId"], "train-domain")
        self.assertEqual(domains["testBenchDomainId"], "test-domain")
        self.assertNotEqual(
            domains["trainBenchDomainFullURL"], domains["testBenchDomainFullURL"]
        )

    def test_incomplete_dual_upload_returns_no_partial_domain_ids(self):
        upload_result = {
            "published": False,
            "verified": False,
            "domains": {
                "train": {
                    "domain_id": "real-but-partial-train",
                    "owner_user_id": "owner",
                    "published": True,
                    "verified": True,
                },
                "test": {
                    "domain_id": "failed-test",
                    "owner_user_id": "owner",
                    "published": False,
                    "verified": False,
                },
            },
        }
        domains = build_step_report_output({}, {}, {}, upload_result)["benchDomains"]
        self.assertEqual(domains["trainBenchDomainId"], "")
        self.assertEqual(domains["testBenchDomainId"], "")

    def test_runtime_owner_id_can_come_from_environment(self):
        with patch.dict(
            os.environ, {"CLAWBENCH_OWNER_ID": "owner-from-env"}, clear=False
        ):
            user_id, meta = resolve_runtime_user_id()
        self.assertEqual(user_id, "owner-from-env")
        self.assertEqual(meta["source"], "env:CLAWBENCH_OWNER_ID")


class ExistingManifestPackagingSafetyTests(unittest.TestCase):
    def test_parent_path_escape_is_rejected_before_zip_creation(self):
        with tempfile.TemporaryDirectory(prefix="manifest-path-escape-") as td:
            root = Path(td)
            templates = root / "templates"
            (templates / "val").mkdir(parents=True)
            (templates / "val" / "task_test.md").write_text(
                "# test\n", encoding="utf-8"
            )
            secret = root.parent / "secret.md"
            secret.write_text("secret\n", encoding="utf-8")
            manifest = {
                "templates": [
                    {
                        "id": "task_bad",
                        "split": "train",
                        "relative_path": "../../secret.md",
                    },
                    {
                        "id": "task_test",
                        "split": "test",
                        "relative_path": "val/task_test.md",
                    },
                ]
            }

            with self.assertRaisesRegex(ValueError, "opt/<file>.md"):
                ensure_split_packages(root, templates, manifest)

            self.assertFalse((root / "clawbench_train_dataset.zip").exists())
            self.assertFalse((root / "clawbench_test_dataset.zip").exists())
            secret.unlink()

    def test_train_manifest_entry_cannot_reference_val_directory(self):
        with tempfile.TemporaryDirectory(prefix="manifest-split-confusion-") as td:
            root = Path(td)
            templates = root / "templates"
            (templates / "val").mkdir(parents=True)
            (templates / "val" / "task_bad.md").write_text(
                "# wrong split\n", encoding="utf-8"
            )
            manifest = {
                "templates": [
                    {
                        "id": "task_bad",
                        "split": "train",
                        "relative_path": "val/task_bad.md",
                    }
                ]
            }

            with self.assertRaisesRegex(
                ValueError, "train template path must use opt/"
            ):
                ensure_split_packages(root, templates, manifest)

            self.assertFalse((root / "clawbench_train_dataset.zip").exists())


class StrictTemplateVerificationTests(unittest.TestCase):
    def test_missing_expected_template_fails_before_publish(self):
        class Client:
            base_url = "https://clawweb.example"

            def create_domain(self, domain_id, description):
                return {"status_code": 200, "body": {"ownerUserId": "owner"}}

            def upload_zip(self, owner, domain, path):
                return {
                    "status_code": 200,
                    "body": {
                        "items": [
                            {
                                "templateName": "task_one",
                                "imported": True,
                                "action": "import",
                            }
                        ]
                    },
                }

            def batch_publish(self, *args):
                raise AssertionError("publish must not run for an incomplete scan")

        with tempfile.TemporaryDirectory(prefix="strict-upload-") as td:
            archive = Path(td) / "templates.zip"
            archive.write_bytes(b"zip")
            with patch(
                "clawevolve_plan.integration.clawweb.ClawWebClient",
                return_value=Client(),
            ):
                result = upload_templates(
                    "domain",
                    archive,
                    "owner",
                    expected_template_names=["task_one", "task_two"],
                )
        self.assertEqual(result["status"], "uploaded_template_set_mismatch")
        self.assertEqual(result["missing_from_scan"], ["task_two"])
        self.assertFalse(result["verified"])

    def test_zero_publish_count_is_verified_as_idempotent_success(self):
        class Client:
            base_url = "https://clawweb.example"

            def create_domain(self, domain_id, description):
                return {"status_code": 200, "body": {"ownerUserId": "owner"}}

            def upload_zip(self, owner, domain, path):
                return {
                    "status_code": 200,
                    "body": {
                        "items": [
                            {
                                "templateName": "task_one",
                                "imported": False,
                                "action": "skip",
                            }
                        ]
                    },
                }

            def batch_publish(self, owner, domain, templates):
                return {
                    "status_code": 200,
                    "body": {"published": 0, "failed": 0},
                }

            def list_published_templates(self, owner, domain):
                return {
                    "status_code": 200,
                    "body": [{"templateName": "task_one"}],
                }

        with tempfile.TemporaryDirectory(prefix="strict-zero-publish-") as td:
            archive = Path(td) / "templates.zip"
            archive.write_bytes(b"zip")
            with patch(
                "clawevolve_plan.integration.clawweb.ClawWebClient",
                return_value=Client(),
            ):
                result = upload_templates(
                    "domain",
                    archive,
                    "owner",
                    expected_template_names=["task_one"],
                )

        self.assertEqual(result["publish_reported_count"], 0)
        self.assertEqual(result["status"], "published")
        self.assertTrue(result["verified"])

    def test_unexpected_published_template_fails_verification(self):
        class Client:
            base_url = "https://clawweb.example"

            def create_domain(self, domain_id, description):
                return {"status_code": 200, "body": {"ownerUserId": "owner"}}

            def upload_zip(self, owner, domain, path):
                return {
                    "status_code": 200,
                    "body": {
                        "items": [
                            {
                                "templateName": "task_one",
                                "imported": True,
                                "action": "import",
                            }
                        ]
                    },
                }

            def batch_publish(self, owner, domain, templates):
                return {
                    "status_code": 200,
                    "body": {"published": 1, "failed": 0},
                }

            def list_published_templates(self, owner, domain):
                return {
                    "status_code": 200,
                    "body": [
                        {"templateName": "task_one"},
                        {"templateName": "unexpected_task"},
                    ],
                }

        with tempfile.TemporaryDirectory(prefix="strict-extra-published-") as td:
            archive = Path(td) / "templates.zip"
            archive.write_bytes(b"zip")
            with patch(
                "clawevolve_plan.integration.clawweb.ClawWebClient",
                return_value=Client(),
            ):
                result = upload_templates(
                    "domain",
                    archive,
                    "owner",
                    expected_template_names=["task_one"],
                )

        self.assertEqual(result["status"], "published_verify_failed")
        self.assertFalse(result["verified"])
        self.assertEqual(result["unexpected_published_templates"], ["unexpected_task"])

    def test_multipart_upload_uses_files_field(self):
        captured = {}
        client = ClawWebClient(user_id="owner", base_url="https://clawweb.example")

        def fake_open(request, path, method, timeout=None):
            captured["body"] = request.data
            captured["content_type"] = request.headers.get("Content-type")
            return 200, "{}"

        with tempfile.TemporaryDirectory(prefix="multipart-files-") as td:
            archive = Path(td) / "templates.zip"
            archive.write_bytes(b"zip-content")
            with patch.object(client, "_open_with_retry", side_effect=fake_open):
                client.upload_zip("owner", "domain", archive)

        self.assertIn(b'name="files"', captured["body"])
        self.assertNotIn(b'name="file";', captured["body"])
        self.assertIn("multipart/form-data", captured["content_type"])

    def test_conflict_scan_can_be_verified_as_idempotent_success(self):
        class Client:
            base_url = "https://clawweb.example"

            def create_domain(self, domain_id, description):
                raise ClawWebHTTPError("POST", "/api/bench/domains", 409, "exists")

            def get_domain(self, owner, domain):
                return {"status_code": 200, "body": {"ownerUserId": owner}}

            def upload_zip(self, owner, domain, path):
                return {
                    "status_code": 200,
                    "body": {
                        "items": [
                            {
                                "templateName": "task_one",
                                "imported": False,
                                "action": "conflict",
                            }
                        ]
                    },
                }

            def batch_publish(self, *args):
                raise AssertionError("no draft candidate should be republished")

            def list_published_templates(self, owner, domain):
                return {
                    "status_code": 200,
                    "body": {"data": {"items": [{"templateName": "task_one"}]}},
                }

        with tempfile.TemporaryDirectory(prefix="strict-idempotent-") as td:
            archive = Path(td) / "templates.zip"
            archive.write_bytes(b"zip")
            with patch(
                "clawevolve_plan.integration.clawweb.ClawWebClient",
                return_value=Client(),
            ):
                result = upload_templates(
                    "domain",
                    archive,
                    "owner",
                    expected_template_names=["task_one"],
                )

        self.assertEqual(result["status"], "published")
        self.assertTrue(result["published"])
        self.assertTrue(result["verified"])


class DualDomainCacheTests(unittest.TestCase):
    def _complete_result(self):
        return {
            "published": True,
            "verified": True,
            "domains": {
                "train": {
                    "published": True,
                    "verified": True,
                    "zip_sha256": "train-digest",
                    "expected_template_names": ["task_train"],
                },
                "test": {
                    "published": True,
                    "verified": True,
                    "zip_sha256": "test-digest",
                    "expected_template_names": ["task_test"],
                },
            },
        }

    def _manifest(self):
        return {
            "split_packages": {
                "train": {
                    "zip_sha256": "train-digest",
                    "template_names": ["task_train"],
                },
                "test": {
                    "zip_sha256": "test-digest",
                    "template_names": ["task_test"],
                },
            }
        }

    def test_complete_matching_upload_is_reused(self):
        with tempfile.TemporaryDirectory(prefix="dual-domain-cache-") as td:
            root = Path(td)
            (root / "clawweb_upload_result.json").write_text(
                json.dumps(self._complete_result()), encoding="utf-8"
            )
            result = _existing_reusable_clawweb_upload_result(root, self._manifest())
        self.assertIsNotNone(result)
        self.assertTrue(result["reused"])

    def test_digest_or_template_drift_disables_cache(self):
        for field in ("digest", "names"):
            with (
                self.subTest(field=field),
                tempfile.TemporaryDirectory(prefix="dual-domain-cache-drift-") as td,
            ):
                root = Path(td)
                (root / "clawweb_upload_result.json").write_text(
                    json.dumps(self._complete_result()), encoding="utf-8"
                )
                manifest = self._manifest()
                if field == "digest":
                    manifest["split_packages"]["test"]["zip_sha256"] = "changed"
                else:
                    manifest["split_packages"]["test"]["template_names"] = ["changed"]
                self.assertIsNone(
                    _existing_reusable_clawweb_upload_result(root, manifest)
                )


class PlanCompletionGateTests(unittest.TestCase):
    def test_published_domains_without_final_report_are_not_ready(self):
        upload_result = {
            "required": True,
            "published": True,
            "verified": True,
            "domains": {
                split: {"published": True, "verified": True}
                for split in ("train", "test")
            },
        }
        with tempfile.TemporaryDirectory(prefix="plan-result-gate-") as td:
            root = Path(td)
            result = _result(
                {"deliverables": {}},
                root / "objective.md",
                root / "objective.json",
                root / "spec.md",
                root / "spec.json",
                root / "templates",
                root / "bench.zip",
                upload_result,
                {},
                {},
                {"final": {"status": "deferred"}},
            )

        self.assertEqual(result["status"], "error")
        self.assertFalse(result["ready_for_patch_loop"])
        self.assertEqual(result["agent_next_action"], "retry_clawweb_report")

    def test_verified_domains_and_successful_report_are_ready(self):
        upload_result = {
            "required": True,
            "published": True,
            "verified": True,
            "domains": {
                split: {"published": True, "verified": True}
                for split in ("train", "test")
            },
        }
        with tempfile.TemporaryDirectory(prefix="plan-result-ready-") as td:
            root = Path(td)
            result = _result(
                {"deliverables": {}},
                root / "objective.md",
                root / "objective.json",
                root / "spec.md",
                root / "spec.json",
                root / "templates",
                root / "bench.zip",
                upload_result,
                {},
                {},
                {"final": {"status": "ok"}},
            )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["ready_for_patch_loop"])
        self.assertEqual(result["agent_next_action"], "start_patch_loop")

    def test_existing_upload_failure_returns_nonzero_exit(self):
        with tempfile.TemporaryDirectory(prefix="plan-existing-exit-") as td:
            args = argparse.Namespace(
                task_id="EV-1",
                step_id="STEP-1",
                evolve_results_dir=td,
                run_dir="",
                goal="",
                target=[],
                overwrite=False,
                resolve_plan_source=False,
                skip_clawweb_report=False,
            )
            with patch(
                "clawevolve_plan.pipeline.runner._handle_existing_plan",
                return_value={"status": "upload_incomplete"},
            ):
                result = run_plan_command(args, step_reporter=lambda *a, **k: {})

        self.assertEqual(result.exit_code, 2)


class FinalReportSafetyTests(unittest.TestCase):
    def test_local_failure_after_successful_terminal_report_does_not_report_again(self):
        with tempfile.TemporaryDirectory(prefix="plan-final-gate-") as td:
            args = argparse.Namespace(
                task_id="EV-FINAL-GATE",
                step_id="STEP-FINAL-GATE",
                evolve_results_dir=td,
                run_dir="",
                goal="test final report gate",
                target=[],
                overwrite=True,
                resolve_plan_source=False,
                skip_clawweb_report=False,
            )
            external_reporter = unittest.mock.Mock(return_value={"status": "ok"})

            def report_then_fail(**kwargs):
                result = kwargs["step_reporter"](
                    "EV-FINAL-GATE",
                    "STEP-FINAL-GATE",
                    status="succeeded",
                    summary="remote workflow already completed",
                )
                self.assertEqual(result["status"], "ok")
                raise OSError("local result persistence failed")

            with patch(
                "clawevolve_plan.pipeline.runner.run_fresh_plan",
                side_effect=report_then_fail,
            ):
                result = run_plan_command(args, step_reporter=external_reporter)

        external_reporter.assert_called_once()
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.payload["clawweb_step_report"]["final"]["status"], "ok")
        self.assertIn(
            "local result persistence failed",
            result.payload["clawweb_step_report"]["post_report_local_error"],
        )

    def test_reporter_exception_then_local_failure_preserves_first_result(self):
        with tempfile.TemporaryDirectory(prefix="plan-final-error-gate-") as td:
            args = argparse.Namespace(
                task_id="EV-FINAL-ERROR-GATE",
                step_id="STEP-FINAL-ERROR-GATE",
                evolve_results_dir=td,
                run_dir="",
                goal="test final report error gate",
                target=[],
                overwrite=True,
                resolve_plan_source=False,
                skip_clawweb_report=False,
            )
            external_reporter = unittest.mock.Mock(
                side_effect=RuntimeError("transport adapter broke")
            )

            def report_then_fail(**kwargs):
                report = kwargs["step_reporter"](
                    "EV-FINAL-ERROR-GATE",
                    "STEP-FINAL-ERROR-GATE",
                    status="succeeded",
                    summary="remote report attempted",
                )
                self.assertEqual(report["status"], "error")
                raise OSError("local result persistence failed")

            with patch(
                "clawevolve_plan.pipeline.runner.run_fresh_plan",
                side_effect=report_then_fail,
            ):
                result = run_plan_command(args, step_reporter=external_reporter)

        external_reporter.assert_called_once()
        final = result.payload["clawweb_step_report"]["final"]
        self.assertEqual(final["status"], "error")
        self.assertEqual(final["error_category"], "step_reporter_exception")
        self.assertIn("transport adapter broke", final["error"])
        self.assertIn(
            "local result persistence failed",
            result.payload["clawweb_step_report"]["post_report_local_error"],
        )

    def test_reporter_exception_is_converted_without_a_second_call(self):
        reporter = unittest.mock.Mock(
            side_effect=RuntimeError("transport adapter broke")
        )

        result = submit_final_step_report(
            reporter,
            "EV-1",
            "STEP-1",
            status="succeeded",
            summary="done",
        )

        reporter.assert_called_once()
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_category"], "step_reporter_exception")

    def test_gate_retains_reporter_exception_for_later_local_failure(self):
        reporter = unittest.mock.Mock(
            side_effect=RuntimeError("transport adapter broke")
        )
        gate = FinalStepReportGate(reporter)

        first = submit_final_step_report(
            gate,
            "EV-1",
            "STEP-1",
            status="succeeded",
            summary="done",
        )
        suppressed = gate(
            "EV-1",
            "STEP-1",
            status="failed",
            summary="must not be sent",
        )

        reporter.assert_called_once()
        self.assertEqual(first["status"], "error")
        self.assertEqual(first["error_category"], "step_reporter_exception")
        self.assertEqual(gate.result, first)
        self.assertEqual(suppressed["status"], "suppressed")
        self.assertEqual(suppressed["original_result"], first)


class SkipClawWebTests(unittest.TestCase):
    def test_skip_prevents_domain_upload(self):
        manifest = {
            "split_packages": {
                split: {
                    "zip_path": f"/{split}.zip",
                    "zip_sha256": f"{split}-digest",
                    "template_names": [f"task_{split}"],
                }
                for split in ("train", "test")
            }
        }
        args = argparse.Namespace(skip_clawweb_report=True, overwrite=False)

        with (
            tempfile.TemporaryDirectory(prefix="plan-skip-upload-") as td,
            patch("clawevolve_plan.pipeline.bench_flow.upload_bench_domains") as upload,
        ):
            result = _ensure_clawweb_domains(
                args, {}, Path(td), manifest, task_id="EV-SKIP"
            )

        upload.assert_not_called()
        self.assertFalse(result["required"])
        self.assertFalse(result["enabled"])
        self.assertEqual(result["status"], "skipped")

    def test_skip_replaces_external_step_reporter_with_local_noop(self):
        with tempfile.TemporaryDirectory(prefix="plan-skip-report-") as td:
            args = argparse.Namespace(
                task_id="EV-SKIP",
                step_id="STEP-SKIP",
                evolve_results_dir=td,
                run_dir="",
                goal="local test",
                target=[],
                overwrite=True,
                resolve_plan_source=False,
                skip_clawweb_report=True,
            )
            external_reporter = unittest.mock.Mock()

            def fake_fresh_plan(**kwargs):
                report = kwargs["step_reporter"](
                    "EV-SKIP",
                    "STEP-SKIP",
                    status="succeeded",
                    summary="local only",
                )
                return {"status": "ok", "clawweb_step_report": {"final": report}}

            with patch(
                "clawevolve_plan.pipeline.runner.run_fresh_plan",
                side_effect=fake_fresh_plan,
            ):
                result = run_plan_command(args, step_reporter=external_reporter)

        external_reporter.assert_not_called()
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(
            result.payload["clawweb_step_report"]["final"]["status"], "skipped"
        )


class ClawWebContractHardeningTests(unittest.TestCase):
    @staticmethod
    def _content_hash(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()[:16]

    def test_batch_publish_error_is_recovered_by_authoritative_verification(self):
        content = b"# task_one\n"
        source_hash = self._content_hash(content)

        class Client:
            base_url = "https://clawweb.example"

            def create_domain(self, domain_id, description):
                return {
                    "status_code": 201,
                    "body": {"ownerUserId": "owner", "status": "active"},
                }

            def upload_zip(self, owner, domain, path):
                return {
                    "status_code": 200,
                    "body": {
                        "items": [
                            {
                                "templateName": "task_one",
                                "sourceHash": source_hash,
                                "imported": True,
                                "action": "new",
                            }
                        ]
                    },
                }

            def batch_publish(self, owner, domain, templates):
                raise TimeoutError("response lost after server commit")

            def list_published_templates(self, owner, domain):
                return {
                    "status_code": 200,
                    "body": [{"templateName": "task_one", "sourceHash": source_hash}],
                }

        with tempfile.TemporaryDirectory(prefix="publish-recovery-") as td:
            archive = Path(td) / "templates.zip"
            archive.write_bytes(b"zip")
            with patch(
                "clawevolve_plan.integration.clawweb.ClawWebClient",
                return_value=Client(),
            ):
                result = upload_templates(
                    "domain",
                    archive,
                    "owner",
                    expected_template_names=["task_one"],
                    expected_template_hashes={"task_one": source_hash},
                )

        self.assertTrue(result["verified"])
        self.assertTrue(result["published"])
        self.assertTrue(result["batch_publish_reported_failure"])
        self.assertIn("TimeoutError", result["batch_publish_error"])

    def test_scan_source_hash_mismatch_fails_before_publish(self):
        class Client:
            base_url = "https://clawweb.example"

            def create_domain(self, domain_id, description):
                return {"status_code": 201, "body": {"ownerUserId": "owner"}}

            def upload_zip(self, owner, domain, path):
                return {
                    "status_code": 200,
                    "body": {
                        "items": [
                            {
                                "templateName": "task_one",
                                "sourceHash": "wrong",
                                "imported": True,
                                "action": "new",
                            }
                        ]
                    },
                }

            def batch_publish(self, *args):
                raise AssertionError("hash mismatch must stop before publish")

        with tempfile.TemporaryDirectory(prefix="scan-hash-") as td:
            archive = Path(td) / "templates.zip"
            archive.write_bytes(b"zip")
            with patch(
                "clawevolve_plan.integration.clawweb.ClawWebClient",
                return_value=Client(),
            ):
                result = upload_templates(
                    "domain",
                    archive,
                    "owner",
                    expected_template_names=["task_one"],
                    expected_template_hashes={"task_one": "expected"},
                )

        self.assertEqual(result["status"], "uploaded_template_set_mismatch")
        self.assertEqual(result["scan_hash_mismatches"]["task_one"]["actual"], "wrong")

    def test_published_source_hash_mismatch_fails_verification(self):
        class Client:
            base_url = "https://clawweb.example"

            def create_domain(self, domain_id, description):
                return {"status_code": 201, "body": {"ownerUserId": "owner"}}

            def upload_zip(self, owner, domain, path):
                return {
                    "status_code": 200,
                    "body": {
                        "items": [
                            {
                                "templateName": "task_one",
                                "sourceHash": "expected",
                                "imported": True,
                                "action": "new",
                            }
                        ]
                    },
                }

            def batch_publish(self, owner, domain, templates):
                return {
                    "status_code": 200,
                    "body": {"published": 1, "failed": 0},
                }

            def list_published_templates(self, owner, domain):
                return {
                    "status_code": 200,
                    "body": [{"templateName": "task_one", "sourceHash": "wrong"}],
                }

        with tempfile.TemporaryDirectory(prefix="published-hash-") as td:
            archive = Path(td) / "templates.zip"
            archive.write_bytes(b"zip")
            with patch(
                "clawevolve_plan.integration.clawweb.ClawWebClient",
                return_value=Client(),
            ):
                result = upload_templates(
                    "domain",
                    archive,
                    "owner",
                    expected_template_names=["task_one"],
                    expected_template_hashes={"task_one": "expected"},
                )

        self.assertEqual(result["status"], "published_verify_failed")
        self.assertFalse(result["verified"])
        self.assertEqual(
            result["published_hash_mismatches"]["task_one"]["actual"], "wrong"
        )

    def test_body_list_accepts_nested_data_items(self):
        self.assertEqual(
            _body_list({"body": {"data": {"items": [{"templateName": "x"}]}}}),
            [{"templateName": "x"}],
        )

    def test_more_than_500_templates_fails_before_any_domain_creation(self):
        with tempfile.TemporaryDirectory(prefix="domain-limit-") as td:
            root = Path(td)
            train_names = [f"task_train_{index:03d}" for index in range(501)]
            train_zip = root / "train.zip"
            test_zip = root / "test.zip"
            manifest = {
                "split_packages": {
                    "train": {
                        "zip_path": str(train_zip),
                        "zip_sha256": _write_split_zip(train_zip, "train", train_names),
                        "template_names": train_names,
                    },
                    "test": {
                        "zip_path": str(test_zip),
                        "zip_sha256": _write_split_zip(test_zip, "test", ["task_test"]),
                        "template_names": ["task_test"],
                    },
                }
            }
            with patch("clawevolve_plan.pipeline.upload.upload_templates") as upload:
                result = upload_bench_domains(
                    plan={"bot_id": "bot"}, manifest=manifest, task_id="EV"
                )

        upload.assert_not_called()
        self.assertIn("limit 500", result["domains"]["train"]["reason"])
        self.assertEqual(result["domains"]["test"]["status"], "not_attempted")

    def test_domain_id_respects_limit_and_hashes_full_identity(self):
        plan = {"bot_id": "bot-" + "x" * 200}
        digest = "abcdef012345" + "9" * 52
        long_prefix = "EV-" + "y" * 200
        train = _domain_id(plan, long_prefix + "-one", "train", digest)
        test = _domain_id(plan, long_prefix + "-one", "test", digest)
        other_task = _domain_id(plan, long_prefix + "-two", "train", digest)
        self.assertLessEqual(len(train), 128)
        self.assertLessEqual(len(test), 128)
        self.assertIn("_train_abcdef012345_", train)
        self.assertIn("_test_abcdef012345_", test)
        self.assertNotEqual(train, test)
        self.assertNotEqual(train, other_task)

    def test_only_typed_create_domain_409_is_reused(self):
        class Client:
            base_url = "https://clawweb.example"

            def create_domain(self, domain_id, description):
                raise RuntimeError("message happens to contain HTTP 409")

            def get_domain(self, owner, domain):
                raise AssertionError("untyped errors must not reuse a domain")

        with tempfile.TemporaryDirectory(prefix="typed-409-") as td:
            archive = Path(td) / "templates.zip"
            archive.write_bytes(b"zip")
            with patch(
                "clawevolve_plan.integration.clawweb.ClawWebClient",
                return_value=Client(),
            ):
                with self.assertRaisesRegex(RuntimeError, "HTTP 409"):
                    upload_templates("domain", archive, "owner")


class CachedDomainRemoteValidationTests(unittest.TestCase):
    def test_cached_domains_require_current_remote_name_and_hash_match(self):
        with tempfile.TemporaryDirectory(prefix="remote-cache-") as td:
            root = Path(td)
            packages = {}
            domains = {}
            for split in ("train", "test"):
                name = f"task_{split}"
                archive = root / f"{split}.zip"
                digest = _write_split_zip(archive, split, [name])
                with zipfile.ZipFile(archive) as zipped:
                    content_hash = hashlib.sha256(
                        zipped.read(f"{'opt' if split == 'train' else 'val'}/{name}.md")
                    ).hexdigest()[:16]
                packages[split] = {
                    "zip_path": str(archive),
                    "zip_sha256": digest,
                    "template_names": [name],
                }
                domains[split] = {
                    "domain_id": f"domain-{split}",
                    "user_id": "owner",
                    "owner_user_id": "owner",
                    "zip_sha256": digest,
                    "expected_template_names": [name],
                    "expected_template_hashes": {name: content_hash},
                    "published": True,
                    "verified": True,
                }
            result = {
                "published": True,
                "verified": True,
                "domains": domains,
            }
            with patch(
                "clawevolve_plan.pipeline.upload.verify_published_domain",
                return_value={"verified": True, "status": "verified"},
            ) as verify:
                reused = revalidate_cached_bench_domains(
                    result, {"split_packages": packages}
                )

        self.assertIsNotNone(reused)
        self.assertTrue(reused["remote_revalidated"])
        self.assertEqual(verify.call_count, 2)

    def test_cached_shared_train_domain_remains_reusable(self):
        with tempfile.TemporaryDirectory(prefix="shared-domain-cache-") as td:
            root = Path(td)
            train_zip = root / "train.zip"
            test_zip = root / "test.zip"
            train_digest = _write_split_zip(train_zip, "train", ["task_only"])
            test_digest = _write_split_zip(test_zip, "test", [])
            with zipfile.ZipFile(train_zip) as zipped:
                content_hash = hashlib.sha256(
                    zipped.read("opt/task_only.md")
                ).hexdigest()[:16]
            packages = {
                "train": {
                    "zip_path": str(train_zip),
                    "zip_sha256": train_digest,
                    "template_names": ["task_only"],
                },
                "test": {
                    "zip_path": str(test_zip),
                    "zip_sha256": test_digest,
                    "template_names": [],
                },
            }
            shared = {
                "domain_id": "domain-shared",
                "user_id": "owner",
                "owner_user_id": "owner",
                "zip_sha256": train_digest,
                "expected_template_names": ["task_only"],
                "expected_template_hashes": {"task_only": content_hash},
                "published": True,
                "verified": True,
            }
            result = {
                "published": True,
                "verified": True,
                "validation_independent": False,
                "validation_mode": "shared_train_domain",
                "domains": {
                    "train": {**shared, "split": "train"},
                    "test": {
                        **shared,
                        "split": "test",
                        "alias_of": "train",
                        "independent": False,
                    },
                },
            }
            with patch(
                "clawevolve_plan.pipeline.upload.verify_published_domain",
                return_value={"verified": True, "status": "verified"},
            ) as verify:
                reused = revalidate_cached_bench_domains(
                    result, {"split_packages": packages}
                )

        self.assertIsNotNone(reused)
        self.assertEqual(
            reused["domains"]["train"]["domain_id"],
            reused["domains"]["test"]["domain_id"],
        )
        self.assertEqual(verify.call_count, 2)

    def test_stale_remote_domain_disables_cache(self):
        result = {
            "published": True,
            "verified": True,
            "domains": {
                split: {
                    "domain_id": split,
                    "user_id": "owner",
                    "owner_user_id": "owner",
                    "zip_sha256": f"{split}-digest",
                    "expected_template_names": [f"task_{split}"],
                    "published": True,
                    "verified": True,
                }
                for split in ("train", "test")
            },
        }
        with tempfile.TemporaryDirectory(prefix="stale-cache-") as td:
            root = Path(td)
            packages = {}
            for split in ("train", "test"):
                path = root / f"{split}.zip"
                digest = _write_split_zip(path, split, [f"task_{split}"])
                result["domains"][split]["zip_sha256"] = digest
                packages[split] = {
                    "zip_path": str(path),
                    "zip_sha256": digest,
                    "template_names": [f"task_{split}"],
                }
            with patch(
                "clawevolve_plan.pipeline.upload.verify_published_domain",
                return_value={"verified": False, "status": "verification_failed"},
            ):
                reused = revalidate_cached_bench_domains(
                    result, {"split_packages": packages}
                )

        self.assertIsNone(reused)


if __name__ == "__main__":
    unittest.main()
