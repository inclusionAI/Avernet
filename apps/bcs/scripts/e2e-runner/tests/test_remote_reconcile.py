from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.cleanup import execute_cleanup
from bcs_e2e.correlation import RequestFailure
from bcs_e2e.ledger import Ledger
from bcs_e2e.profile import fingerprint, load
from bcs_e2e.remote_reconcile import remote_reconcile
from bcs_e2e.run_state import read as read_state, write as write_state

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "reconcile-test"


def create_test_profile():
    profile = load(ROOT / "profiles/preprod.example.json")
    profile["environment"].update(id="pre-test", expected_bcs_env="pre")
    profile["entrypoint"].update(kind="gateway", origin="https://gateway.example.test")
    profile["human"]["user_id"] = "39464"
    profile["actors"]["driver"].update(bot_id="driver-bot", owner_id="39464")
    profile["actors"]["member"].update(bot_id="member-bot", owner_id="39464")
    profile["observer"].update(server="alisd-db-query", database="agentclawdb",
                                environment_binding_confirmed=True)
    profile["policy"].update(fixture_pool="reconcile-test", exclusive_use_confirmed=True)
    profile["policy"]["remote_effect_review"] = {
        "implicit_bot_execution_allowed": True,
        "isolated_test_runtime": True,
        "no_operational_channels": True,
    }
    profile["identity_binding"] = {
        "method": "GET",
        "path": "/openapi/v1/collaboration/bots/{human_actor_id}/candidates?offset=0&limit=1",
        "response": "authorized-human-view",
    }
    return profile


def implicit_file_owner(actor_id: str = "driver-bot") -> dict:
    return {"actor_kind": "bot", "actor_id": actor_id}


def implicit_file_projection(*, owner: dict | None = None, session_id: str = "session-visible",
                            file_id: str = "implicit-file", file_name: str = "e2e-pilot-report.txt",
                            size: int = 328, storage_backend: str = "baas", status: str = "ready") -> dict:
    if owner is None:
        owner = implicit_file_owner()
    return {
        "file_id": file_id,
        "session_id": session_id,
        "file_name": file_name,
        "size": size,
        "owner": owner,
        "storage_backend": storage_backend,
        "status": status,
    }


class FakeDeliveryClient:
    def __init__(self, rows: dict[tuple[str, str], list[dict]]):
        self.rows = rows
        self.calls: list[tuple[str, str]] = []

    def list(self, session_id, message_id):
        self.calls.append((session_id, message_id))
        return list(self.rows[(session_id, message_id)]), {
            "operation": "message.deliveries.read", "method": "GET",
            "path": "/exact-public-delivery-route", "http_status": 200,
            "submitted_request_id": "safe-request-id", "credentials_sent": True,
        }


class FakeAdapter:
    termination_mode = "REVIEWED_REMOTE"
    resource_review = True

    def __init__(self, *, files: list[dict] | None = None,
                 hidden_files: list[dict] | None = None):
        self.file_list_items = list(files or [
            implicit_file_projection(owner=implicit_file_owner()),
        ])
        self.hidden_file_list_items = list(hidden_files or [
            implicit_file_projection(owner=implicit_file_owner(), session_id="session-hidden",
                                      file_id="implicit-hidden-file",
                                      file_name="driver-pilot-a-20261025-022146.txt", size=95),
        ])
        self.file_verified: list[tuple[str, str]] = []
        self.deleted_files: set[str] = set()
        self.deleted: set[str] = set()

    def close_production_channels(self, session_id): return None
    def quiesce_execution(self, session_id, *, reason=""): return None
    def close_termination_channel(self, session_id): return None

    def session_read(self, session_id):
        if session_id not in {"session-visible", "session-hidden"}:
            raise RuntimeError("unexpected Session")
        return {"session_id": session_id, "group_id": "group-1"}, {}

    def session_messages(self, session_id):
        if session_id == "session-hidden":
            error = RequestFailure("operation session.messages returned HTTP 403")
            error.with_record({"operation": "session.messages", "http_status": 403})
            raise error
        if session_id != "session-visible":
            raise RuntimeError("unexpected Session")
        return {
            "items": [{
                "id": "message-1", "sender": "system",
                "content": "uploaded implicit-file for the reviewed E2E pilot",
            }],
        }, {}

    def file_list(self, session_id):
        if session_id not in {"session-visible", "session-hidden"}:
            raise RuntimeError("unexpected exact Session file list")
        if session_id == "session-hidden":
            return {"items": [dict(item) for item in self.hidden_file_list_items]}, {}
        return {"items": [dict(item) for item in self.file_list_items]}, {}

    def file_verify_deleted(self, session_id, file_id):
        if any(item["file_id"] == file_id for item in self.file_list_items):
            raise RuntimeError("fake file is still listed")
        self.file_verified.append((session_id, file_id))
        return None, {}

    def file_delete(self, session_id, file_id):
        self.deleted_files.add(file_id)
        self.file_list_items = [item for item in self.file_list_items
                                if item["file_id"] != file_id]
        return {"deleted": True}, {}

    def verify_cleanup_scope(self, run_id, records):
        if not self.resource_review:
            return {"status": "BLOCKED", "reason": "reviewed remote effect admission is required"}
        if not isinstance(records, list):
            return {"status": "BLOCKED", "reason": "cleanup records are invalid"}
        live = [item for item in records if item.get("state") == "live"]
        by_handle = {item["handle"]: item for item in live}
        for session in [item for item in live if item.get("kind") == "session"]:
            parent = by_handle.get(session["parent"])
            if parent is None or parent["kind"] != "group":
                return {"status": "BLOCKED", "reason": "session parent is not a live group"}
            listed = self.file_list(session["resource_id"])[0]["items"]
            expected = {item["resource_id"] for item in live
                        if item["kind"] == "file" and item["parent"] == session["handle"]}
            actual = {item["file_id"] for item in listed}
            if actual != expected:
                return {"status": "BLOCKED", "reason": "session has unrecorded or missing live files",
                        "expected_file_ids": sorted(expected), "actual_file_ids": sorted(actual)}
            valid = True
            for selected in [item for item in live if item["kind"] == "file"
                             and item["parent"] == session["handle"]]:
                projection = next(item for item in listed
                                  if item["file_id"] == selected["resource_id"])
                valid = valid and projection["session_id"] == session["resource_id"]
                valid = valid and projection["status"] == "ready"
                valid = valid and projection["storage_backend"] == "baas"
            if not valid:
                return {"status": "BLOCKED", "reason": "file projection is invalid"}
        return {"status": "VERIFIED"}

    def session_delete(self, session_id):
        self.deleted.add(session_id)
        return {"deleted": True}, {}

    def session_verify_deleted(self, session_id):
        if session_id not in self.deleted:
            raise RuntimeError("fake Session was not deleted")
        return None, {}

    def group_delete(self, group_id):
        self.deleted.add(group_id)
        return {"deleted": True}, {}

    def group_verify_deleted(self, group_id):
        if group_id not in self.deleted:
            raise RuntimeError("fake group was not deleted")
        return None, {}


def build_state(path: Path, profile: dict, *, include_file=True,
                second_session=True) -> None:
    digest = fingerprint(profile)
    with Ledger(path / "run.sqlite", RUN_ID, digest, create=True) as ledger:
        group = ledger.intent("PRE-03", 1, "group", "req-group")
        ledger.observed(group, "group-1", origin="created")
        visible = ledger.intent("PRE-03", 1, "session", "req-visible", group)
        ledger.observed(visible, "session-visible", origin="created")
        if second_session:
            hidden = ledger.intent("PRE-03", 1, "session", "req-hidden", group)
            ledger.observed(hidden, "session-hidden", origin="created")
        if include_file:
            file = ledger.intent("PRE-03", 1, "file", "req-file", visible)
            ledger.observed(file, "file-1", origin="created")
    write_state(path / "run.json", {
        "schema_version": 1, "status": "FAILED", "quiescence": "UNKNOWN",
        "run_id": RUN_ID, "profile_digest": digest, "adapter_kind": "gateway",
        "ledger_path": str(path / "run.sqlite"), "lock_path": str(path / "pool.lock"),
    })


def delivery_client(rows=None):
    return FakeDeliveryClient(rows or {
        ("session-visible", "message-1"): [{
            "delivery_id": "delivery-1", "message_id": "message-1",
            "target_bot_id": "driver-bot", "kind": "inject", "status": "expired",
            "state_version": 2,
        }]
    })


def _reconcile(profile, state, adapter, client=None):
    return remote_reconcile(
        profile, state_dir=state, run_id=RUN_ID, confirm_run_id=RUN_ID,
        allow_read=True, allow_write=True,
        delivery_client_factory=lambda _: client or delivery_client(),
        _adapter_factory=lambda current, state_dir: adapter,
    )


class RemoteReconcileTests(unittest.TestCase):
    def test_reconciles_exact_file_and_adopts_reviewed_visible_and_hidden_implicit_files(self):
        profile = create_test_profile()
        adapter = FakeAdapter()
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            build_state(state, profile)
            report = _reconcile(profile, state, adapter)
            self.assertEqual(report["status"], "RECONCILED")
            self.assertEqual(adapter.file_verified, [("session-visible", "file-1")])
            adopted = [item for item in report["resources"]
                       if item.get("origin") == "implicit_reviewed"]
            self.assertEqual(len(adopted), 2)
            adopted_by_id = {item["resource_id"]: item for item in adopted}
            self.assertIn("exact Session file/message/owner projection",
                          adopted_by_id["implicit-file"]["evidence"])
            self.assertIn("implicit-hidden-file", adopted_by_id)
            self.assertIn("exact Session file/owner projection",
                          adopted_by_id["implicit-hidden-file"]["evidence"])

            # A repeated reconciliation must not duplicate the adopted file row.
            repeat = _reconcile(profile, state, adapter)
            self.assertEqual(repeat["status"], "RECONCILED")
            repeated_adopted = [item for item in repeat["resources"]
                                if item.get("origin") == "implicit_reviewed"]
            self.assertEqual(len(repeated_adopted), 2)

            with Ledger(state / "run.sqlite", RUN_ID, fingerprint(profile)) as ledger:
                plan = ledger.cleanup_plan()
                self.assertEqual(plan["status"], "PLANNED")
                self.assertEqual(len([row for row in plan["resources"]
                                       if row["kind"] == "file"]), 2)
                cleanup = execute_cleanup(ledger, adapter, dry_run=False)
                self.assertEqual(cleanup["status"], "PASS")
            self.assertIn("implicit-file", adapter.deleted_files)
            self.assertIn("implicit-hidden-file", adapter.deleted_files)
            self.assertIn("session-visible", adapter.deleted)
            self.assertIn("session-hidden", adapter.deleted)
            self.assertIn("group-1", adapter.deleted)
            self.assertEqual(read_state(state / "run.json")["quiescence"], "CONFIRMED")
            serialized = (state / "remote-reconcile.json").read_text()
            for forbidden in ("Cookie", "content_preview", "token="):
                self.assertNotIn(forbidden, serialized)

    def test_more_than_one_unrecorded_live_file_blocks_cleanup_admission(self):
        profile = create_test_profile()
        files = [
            implicit_file_projection(file_id="implicit-file-1"),
            implicit_file_projection(file_id="implicit-file-2"),
        ]
        adapter = FakeAdapter(files=files, hidden_files=[])
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            build_state(state, profile, include_file=False, second_session=False)
            with self.assertRaisesRegex(RuntimeError, "more than one unrecorded live file"):
                _reconcile(profile, state, adapter)
            self.assertEqual(read_state(state / "run.json")["quiescence"], "UNKNOWN")
            self.assertFalse((state / "remote-reconcile.json").exists())

    def test_implicit_file_requires_bound_owner_and_system_content_evidence(self):
        for owner, message_sender, message_content, expected_error in [
            (implicit_file_owner("other-bot"), "system", "implicit-file",
             "implicit file owner is not a bound test Bot"),
            (implicit_file_owner(), "human", "implicit-file",
             "implicit file has no unique exact message"),
        ]:
            with self.subTest(owner=owner["actor_id"], sender=message_sender):
                profile = create_test_profile()
                files = [implicit_file_projection(owner=owner)]
                adapter = FakeAdapter(files=files)
                adapter.session_messages = lambda session_id: ({  # type: ignore[method-assign]
                    "items": [{"id": "message-1", "sender": message_sender,
                               "content": message_content}],
                }, {})
                with tempfile.TemporaryDirectory() as tmp:
                    state = Path(tmp)
                    build_state(state, profile, include_file=False, second_session=False)
                    with self.assertRaisesRegex(RuntimeError, expected_error):
                        _reconcile(profile, state, adapter)
                    self.assertFalse((state / "remote-reconcile.json").exists())

    def test_implicit_file_message_id_does_not_bypass_exact_content_and_sender_checks(self):
        profile = create_test_profile()
        files = [implicit_file_projection(file_id="message-1")]
        adapter = FakeAdapter(files=files)
        adapter.session_messages = lambda session_id: ({  # type: ignore[method-assign]
            "items": [{"id": "message-1", "sender": "human", "content": ""}],
        }, {})
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            build_state(state, profile, include_file=False, second_session=False)
            with self.assertRaisesRegex(RuntimeError, "implicit file has no unique exact message"):
                _reconcile(profile, state, adapter)
            self.assertFalse((state / "remote-reconcile.json").exists())

    def test_participant_boundary_implicit_file_requires_bound_owner(self):
        profile = create_test_profile()
        hidden_files = [implicit_file_projection(
            owner=implicit_file_owner("other-bot"), session_id="session-hidden",
            file_id="implicit-hidden-file", size=95,
        )]
        adapter = FakeAdapter(files=[], hidden_files=hidden_files)
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            build_state(state, profile, include_file=False)
            with self.assertRaisesRegex(RuntimeError, "implicit file owner is not a bound test Bot"):
                _reconcile(profile, state, adapter)
            self.assertFalse((state / "remote-reconcile.json").exists())

    def test_nonterminal_delivery_blocks_cleanup_admission(self):
        profile = create_test_profile()
        adapter = FakeAdapter(files=[], hidden_files=[])
        rows = {("session-visible", "message-1"): [{
            "delivery_id": "delivery-1", "message_id": "message-1",
            "target_bot_id": "driver-bot", "kind": "send", "status": "running",
            "state_version": 1,
        }]}
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            build_state(state, profile, include_file=False, second_session=False)
            with self.assertRaisesRegex(RuntimeError, "terminal"):
                _reconcile(profile, state, adapter, FakeDeliveryClient(rows))
            self.assertEqual(read_state(state / "run.json")["quiescence"], "UNKNOWN")
            self.assertFalse((state / "remote-reconcile.json").exists())

    def test_unreviewed_remote_effect_policy_blocks_before_invocation(self):
        profile = create_test_profile()
        profile["policy"].pop("remote_effect_review")
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            build_state(state, profile, include_file=False, second_session=False)
            with self.assertRaisesRegex(Exception, "reviewed preprod remote effect policy"):
                remote_reconcile(
                    profile, state_dir=state, run_id=RUN_ID, confirm_run_id=RUN_ID,
                    allow_read=True, allow_write=True,
                    delivery_client_factory=lambda _: delivery_client(),
                    _adapter_factory=lambda current, state_dir: self.fail("adapter invoked"),
                )


if __name__ == "__main__":
    unittest.main()


def create_dual_header_profile():
    profile = create_test_profile()
    profile["entrypoint"].update(kind="bcs-direct", origin="https://bcs-pre.alipay.com")
    profile.pop("identity_binding", None)
    profile["auth"]["header_env"] = {"Authorization": "BCS_E2E_DRIVER_TOKEN", "Cookie": "BCS_E2E_HUMAN_COOKIE"}
    return profile


class RemoteReconcileDualHeaderTests(unittest.TestCase):
    def test_reconciles_a_bcs_direct_retained_state(self):
        profile = create_dual_header_profile()
        adapter = FakeAdapter()
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            build_state(state, profile)
            run = read_state(state / "run.json")
            run["adapter_kind"] = "bcs-direct"
            write_state(state / "run.json", run)
            report = _reconcile(profile, state, adapter)
            self.assertEqual(report["status"], "RECONCILED")
            self.assertEqual(adapter.file_verified, [("session-visible", "file-1")])
            adopted = [item for item in report["resources"] if item.get("origin") == "implicit_reviewed"]
            self.assertEqual(len(adopted), 2)
            with Ledger(state / "run.sqlite", RUN_ID, fingerprint(profile)) as ledger:
                cleanup = execute_cleanup(ledger, adapter, dry_run=False)
            self.assertEqual(cleanup["status"], "PASS")
            self.assertEqual(read_state(state / "run.json")["quiescence"], "CONFIRMED")

    def test_gateway_branded_state_forging_dual_header_profile_is_refused(self):
        profile = create_dual_header_profile()
        adapter = FakeAdapter()
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            build_state(state, profile)
            run = read_state(state / "run.json")
            run["adapter_kind"] = "gateway"
            run["profile_digest"] = fingerprint(create_test_profile())
            write_state(state / "run.json", run)
            with self.assertRaises(Exception):
                _reconcile(profile, state, adapter)
            self.assertEqual(adapter.file_verified, [])
