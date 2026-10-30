"""Contract, route-probe and CLI tests for the dual Provider-subbots case."""

from __future__ import annotations

from contextlib import redirect_stdout
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.cli import main
from bcs_e2e.inventory import load_executor_migration
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.profile import ConfigError
from bcs_e2e.provider_subbots.contract import (
    PARENT,
    REQUIREMENTS,
    STATUS as LOCAL_STATUS,
    STORY_CHECKS,
)
from bcs_e2e.provider_subbots_case_run import (
    CASE_ID,
    CASE_KEYS,
    PROBE_BY_OPERATION,
    _BASELINE_PATH,
    _probe_path,
    dual_provider_subbots_case_run,
    provider_subbots_adaptability,
    provider_subbots_case_plan,
    run_preprod_route_probe,
    validate_provider_subbots_profile,
)
from bcs_e2e.run_state import read

RUN_ID = "20261031-migration-wpa-provider-subbots-test"
CASE_KEY = "provider-subbots"
COOKIE_ENV = "BCS_E2E_HUMAN_COOKIE"
BOT_ENV = "BCS_E2E_DRIVER_BOT_TOKEN"
COOKIE_CANARY = "synthetic-cookie-value-never-persisted"
BOT_CANARY = "synthetic-bot-token-never-persisted"


def provider_profile(*, preprod: bool = False) -> dict:
    value = {
        "schema_version": 1,
        "contract": "dual-provider-subbots-profile-v1",
        "environment": {
            "tier": "preprod" if preprod else "local",
            "id": "provider-subbots-preprod" if preprod else "provider-subbots-local",
            "expected_bcs_env": "pre" if preprod else "local",
        },
        "entrypoint": (
            {
                "kind": "gateway",
                "origin": "https://bcs-pre.alipay.com",
                "api_face": "legacy",
                "manifest_face": "legacy",
            }
            if preprod
            else {"kind": "owned-local"}
        ),
        "human": {"user_id": "39464" if preprod else "e2e-human-fixture"},
        "auth": (
            {
                "kind": "preprod-human-cookie",
                "cookie_env": COOKIE_ENV,
                "probe_bot_token_env": BOT_ENV,
            }
            if preprod
            else {"kind": "owned-local"}
        ),
        "fixture": {
            "webhook_url": "https://provider.example.com/bcs/webhook",
            "takeover_bot_id": "20261031-migration-wpa-subbots-bot",
        },
        "policy": {
            "fixture_pool": "20261031-migration-wpa-subbots",
            "exclusive_use_confirmed": True,
            "timeout_seconds": 10,
            "max_response_bytes": 65536,
            "failure_retention_hours": 24,
        },
        "cli": {
            "transport": "product-http" if preprod else "owned-local-story",
            "command_family": "provider-subbots",
            "api_face": "legacy",
        },
    }
    if preprod:
        value["browser_origin"] = "https://teamclaw-pre.alipay.com"
        value["credential_files"] = {
            COOKIE_ENV: "/Users/example/.codex/bcs-e2e/preprod-human-cookie.txt",
            BOT_ENV: "/Users/example/.codex/bcs-e2e/preprod-driver-bot-token-xq.txt",
        }
        value["route_bindings"] = {
            operation: {
                "method": row["canonical_method"],
                "path": row["path"],
                "api_face": "legacy",
                "auth": row["caller"],
                "response": "legacy-json",
                "traceparent": "disabled",
            }
            for operation, row in PROBE_BY_OPERATION.items()
        }
    return value


class FakeProbeClient:
    """Return scripted statuses without opening a socket."""

    def __init__(self, statuses: dict[str, int] | None = None, *, baseline: int = 404):
        self.statuses = statuses or {}
        self.baseline = baseline
        self.calls = []

    def request(self, *, method: str, path: str, bearer: bool = False) -> dict:
        self.calls.append({"method": method, "path": path, "bearer": bearer})
        if path == _BASELINE_PATH:
            status = self.baseline
        else:
            operation = next(
                operation for operation, row in PROBE_BY_OPERATION.items()
                if _probe_path(row["path"]) == path
            )
            status = self.statuses.get(operation, 405)
        return {
            "method": method,
            "path": path,
            "http_status": status,
            "response_bytes": 0,
            "authorization_sent": bearer,
            "cookie_sent": True,
        }


def local_result(*, observed: int = STORY_CHECKS) -> dict:
    return {
        "status": LOCAL_STATUS,
        "checks": {"status": "PASS", "checked": list(REQUIREMENTS)},
        "child_receipt": {"observed_checks": observed},
        "canonical_summary": {"status": "LOCAL_SUITE_PASS"},
    }


class ProviderSubbotsDualTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_plan_freezes_all_fifteen_obligations_and_131_checks(self):
        plan = provider_subbots_case_plan()
        adaptability = provider_subbots_adaptability()
        self.assertEqual(plan["case_id"], CASE_ID)
        self.assertEqual(plan["source_case_id"], "LOCAL-PROVIDER-SUBBOTS-01")
        self.assertEqual(plan["parent_id"], PARENT)
        self.assertEqual(plan["requirement_count"], 15)
        self.assertEqual(plan["requirement_ids"], list(REQUIREMENTS))
        self.assertEqual(plan["story_checks"], 131)
        self.assertEqual(adaptability["required_assertion_count"], 131)
        self.assertEqual(adaptability["preprod_executor_status"],
                         "ROUTE_PROBE_ONLY_AWAITING_FULL_STORY_CREDENTIALS")
        self.assertEqual(adaptability["remote_validation"], "NOT_RUN")
        self.assertFalse(adaptability["grants_release_credit"])

    def test_preprod_profile_requires_complete_routes_and_references(self):
        profile = validate_provider_subbots_profile(provider_profile(preprod=True))
        self.assertEqual(len(profile["route_bindings"]), len(PROBE_BY_OPERATION))
        for mutation, message in (
            ("cookie-env", "auth: missing or unknown fields"),
            ("probe-token-env", "auth: missing or unknown fields"),
            ("route", "differs from the reviewed contract"),
            ("credential-files", "missing or unknown fields"),
        ):
            value = copy.deepcopy(provider_profile(preprod=True))
            if mutation == "cookie-env":
                value["auth"].pop("cookie_env")
            elif mutation == "probe-token-env":
                value["auth"].pop("probe_bot_token_env")
            elif mutation == "route":
                value["route_bindings"]["provider.register"]["path"] = "/elsewhere"
            else:
                value.pop("credential_files")
            with self.subTest(mutation=mutation), \
                 self.assertRaisesRegex(ConfigError, message):
                validate_provider_subbots_profile(value)

    def test_route_probe_passes_only_when_every_reviewed_route_is_present(self):
        present = FakeProbeClient({operation: 405 for operation in PROBE_BY_OPERATION})
        result = run_preprod_route_probe({}, client=present)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["present_count"], len(PROBE_BY_OPERATION))
        self.assertEqual(result["absent_count"], 0)
        self.assertEqual(result["required_count"], len(PROBE_BY_OPERATION))
        self.assertEqual(len(present.calls), len(PROBE_BY_OPERATION) + 1)
        self.assertEqual(present.calls[0]["path"], _BASELINE_PATH)
        self.assertFalse(result["grants_release_credit"])
        self.assertEqual(result["remote_validation"], "NOT_RUN")

    def test_route_probe_blocks_on_404_or_unexpected_baseline(self):
        absent = FakeProbeClient({operation: 404 for operation in PROBE_BY_OPERATION})
        result = run_preprod_route_probe({}, client=absent)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["error_type"], "PREPROD_PROVIDER_SUBBOTS_ROUTE_ABSENT")
        self.assertEqual(result["present_count"], 0)
        self.assertEqual(result["absent_count"], len(PROBE_BY_OPERATION))

        baseline = run_preprod_route_probe({}, client=FakeProbeClient(baseline=401))
        self.assertEqual(baseline["status"], "BLOCKED")
        self.assertEqual(baseline["error_type"], "PREPROD_PROBE_BASELINE_UNEXPECTED")
        self.assertEqual(baseline["checks"], [])

    def test_preprod_execution_never_claims_the_full_story(self):
        profile = validate_provider_subbots_profile(provider_profile(preprod=True))
        probe = run_preprod_route_probe(
            profile,
            client=FakeProbeClient({operation: 401 for operation in PROBE_BY_OPERATION}),
        )
        self.assertEqual(probe["status"], "PASS")
        with patch(
            "bcs_e2e.provider_subbots_case_run.run_preprod_route_probe",
            return_value=probe,
        ):
            report = dual_provider_subbots_case_run(
                profile_path=self._write_profile(provider_profile(preprod=True), "preprod"),
                case_key=CASE_KEY,
                state_dir=self.root / "preprod-command",
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["execution"]["error_type"], "PREPROD_FULL_STORY_NOT_EXECUTED")
        self.assertEqual(report["execution"]["completed_count"], 0)
        self.assertEqual(report["execution"]["total_count"], 15)
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])
        self.assertEqual(report["assertion_summary"]["full_story_required_assertion_count"], 131)
        self.assertEqual(report["assertion_summary"]["preprod_probe_observed_count"], 9)

    def test_local_command_reuses_the_131_check_child_story(self):
        profile_path = self._write_profile(provider_profile(), "local")
        binary = self.root / "bcs-fixture"
        binary.write_bytes(b"not-executed-by-this-test")
        state_dir = self.root / "local-command"
        with patch(
            "bcs_e2e.provider_subbots_case_run.local_provider_subbots_run",
            return_value=local_result(),
        ) as execute:
            report = dual_provider_subbots_case_run(
                profile_path=profile_path,
                case_key=CASE_KEY,
                state_dir=state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
                binary=binary,
                binary_sha256="0" * 64,
            )
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["execution"]["completed_count"], 15)
        self.assertEqual(report["assertion_summary"]["full_story_observed_assertion_count"], 131)
        self.assertEqual(report["local_owned_run"]["checks"], "PASS")
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])
        execute.assert_called_once()
        receipts = "".join(path.read_text() for path in state_dir.glob("*.json"))
        self.assertNotIn(COOKIE_CANARY, receipts)
        self.assertNotIn(BOT_CANARY, receipts)

    def test_local_command_requires_exact_child_observation_count(self):
        profile_path = self._write_profile(provider_profile(), "local")
        with patch(
            "bcs_e2e.provider_subbots_case_run.local_provider_subbots_run",
            return_value=local_result(observed=130),
        ):
            report = dual_provider_subbots_case_run(
                profile_path=profile_path,
                case_key=CASE_KEY,
                state_dir=self.root / "local-short-child",
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
                binary=self.root / "bcs-fixture",
                binary_sha256="0" * 64,
            )
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["execution"]["completed_count"], 0)

    def test_command_requires_exact_confirmation_and_fresh_state(self):
        profile_path = self._write_profile(provider_profile(), "confirm")
        with self.assertRaisesRegex(ConfigError, "exact Run confirmation"):
            dual_provider_subbots_case_run(
                profile_path=profile_path,
                case_key=CASE_KEY,
                state_dir=self.root / "confirm-state",
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id="different",
            )
        existing = self.root / "existing-state"
        existing.mkdir()
        with self.assertRaisesRegex(ConfigError, "fresh private state directory"):
            dual_provider_subbots_case_run(
                profile_path=profile_path,
                case_key=CASE_KEY,
                state_dir=existing,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
                binary=self.root / "bcs-fixture",
                binary_sha256="0" * 64,
            )

    def test_migration_plan_and_cli_expose_the_dual_case(self):
        rows = {
            row["case_id"]
            for row in migration_plan()["cross_environment_product_cli_case_slices"]
        }
        self.assertIn(CASE_ID, rows)
        status = load_executor_migration()[PARENT]
        self.assertEqual(status, {
            "mode": "DUAL",
            "preprod_verdict": "NOT_RUN",
            "gates": [],
            "notes": ["DUAL_SUBBOTS_DENSITY_D4_131_LEAVES"],
        })

        profile_path = self._write_profile(provider_profile(), "cli")
        state_dir = self.root / "cli-state"
        arguments = [
            "dual-provider-subbots-run",
            "--profile", str(profile_path),
            "--case", CASE_KEY,
            "--state-dir", str(state_dir),
            "--run-id", RUN_ID,
            "--confirm-run-id", RUN_ID,
            "--allow-write",
            "--binary", "/tmp/fixture-bcs",
            "--binary-sha256", "0" * 64,
        ]
        with patch(
            "bcs_e2e.cli.dual_provider_subbots_case_run",
            return_value={"status": "PASS"},
        ) as execute, redirect_stdout(io.StringIO()) as stdout:
            rc = main(arguments)
        self.assertEqual(rc, 0)
        execute.assert_called_once_with(
            profile_path=profile_path,
            case_key=CASE_KEY,
            state_dir=state_dir,
            run_id=RUN_ID,
            allow_write=True,
            confirm_run_id=RUN_ID,
            binary=Path("/tmp/fixture-bcs"),
            binary_sha256="0" * 64,
        )
        self.assertEqual(json.loads(stdout.getvalue())["status"], "PASS")
        self.assertEqual(CASE_KEYS, ("provider-subbots",))

    def _write_profile(self, value: dict, name: str) -> Path:
        path = self.root / f"profile-{name}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path


if __name__ == "__main__":
    unittest.main()
