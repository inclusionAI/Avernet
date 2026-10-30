"""No-network contract tests for the remote Edge friend-mutation adapter."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest
from dataclasses import dataclass
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.remote_edge_friend_mutation_adapter import RemoteEdgeFriendMutationAdapter, _GATEWAY_HUMAN_DISCOVER_REJECTIONS
from bcs_e2e.route_bindings import _EDGE_CASES
from bcs_e2e.run_state import RunStateError

DRIVER = "driver-bot"
MEMBER = "member-bot"
HUMAN = "39464"
UNKNOWN = "00000000-0000-0000-0000-000000000000"
REGISTER_TOKEN = "station-register-token"
MEMBER_TOKEN = "registered-owned-bot-token"

GATEWAY_OPERATIONS = (
    "friend_connection_mutation.create",
    "friend_connection_mutation.accept",
    "friend_connection_mutation.reject",
    "friend_connection_mutation.cancel",
    "friend_connection_mutation.requests_sent.read",
    "friend_connection_mutation.requests_received.read",
    "friend_connection_mutation.requests_all.read",
    "friend_connection_mutation.connections.read",
    "friend_connection_mutation.bot_detail.read",
    "friend_connection_mutation.visibility.write",
    "friend_connection_mutation.revoke",
    "friend_connection_mutation.discover_denied.read",
)
STATION_OPERATIONS = (
    "friend_connection_mutation.register_token.read",
    "friend_connection_mutation.register_bot.create",
    "friend_connection_mutation.legacy_revoke",
    "friend_connection_mutation.station_bot_delete",
    "friend_connection_mutation.station_bot_read",
    "friend_connection_mutation.station_admission.read",
    "friend_connection_mutation.station_discover_collaborate.read",
    "friend_connection_mutation.station_discover_role_no_org.read",
    "friend_connection_mutation.station_discover_org_no_requester.read",
)


@dataclass
class Observation:
    data: object
    evidence: dict


def _binding(operation: str, **extra: str) -> dict:
    method, path, auth = _EDGE_CASES[operation]
    response = (
        "public-envelope" if operation in GATEWAY_OPERATIONS
        else "plain-json" if operation in {
            "friend_connection_mutation.register_token.read",
            "friend_connection_mutation.station_discover_collaborate.read",
            "friend_connection_mutation.station_discover_role_no_org.read",
            "friend_connection_mutation.station_discover_org_no_requester.read"}
        else "station-register-json" if operation == "friend_connection_mutation.register_bot.create"
        else "station-revoke-json" if operation == "friend_connection_mutation.legacy_revoke"
        else "station-bot-delete-json" if operation == "friend_connection_mutation.station_bot_delete"
        else "station-bot-read-json" if operation == "friend_connection_mutation.station_bot_read"
        else "admission-json"
    )
    return {
        "method": method, "path": path,
        "api_face": "public-v1" if operation in GATEWAY_OPERATIONS else "station-legacy",
        "auth": auth, "response": response, "traceparent": "disabled",
        **extra,
    }


def base_profiles() -> tuple[dict, dict]:
    # Start from the reviewer-approved local fixture so command receipt tests
    # exercise the complete shared pilot schema and route contract.
    base = json.loads((ROOT / "profiles/local.singlebox.example.json").read_text())
    common = {
        "environment": {"tier": "preprod", "id": "pre", "expected_bcs_env": "pre",
                        "lifecycle": "remote-attach", "deployment": "managed", "provider": "internal"},
        "human": {"user_id": HUMAN},
        "actors": {
            "driver": {"bot_id": DRIVER, "owner_id": HUMAN, "dedicated": True},
            "member": {"bot_id": MEMBER, "owner_id": HUMAN, "dedicated": True},
        },
        "observer": {"kind": "alisddbquery", "server": "observ",
                     "database": "agentclawdb", "environment_binding_confirmed": True},
        "auth": {"header_env": {"Cookie": "BCS_E2E_HUMAN_COOKIE"}},
        "relationships": {"friendship": "bounded-owned-bot-mutations"},
        "edge_case_bindings": {},
    }
    gateway = copy.deepcopy(base)
    station = copy.deepcopy(base)
    gateway.update(copy.deepcopy(common))
    station.update(copy.deepcopy(common))
    station["auth"]["header_env"]["Authorization"] = "BCS_E2E_STATION_BOT_TOKEN"
    gateway.update({
        "entrypoint": {"kind": "gateway", "origin": "https://gateway.example.com",
                       "api_face": "public-v1", "manifest_face": "internal-v1"},
        "browser_origin": "https://gateway.example.com",
    })
    station.update({
        "entrypoint": {"kind": "bcs-direct", "origin": "https://station.example.com",
                       "api_face": "public-v1", "manifest_face": "legacy"},
        "browser_origin": "https://station.example.com",
    })
    for profile, operations in ((gateway, GATEWAY_OPERATIONS), (station, STATION_OPERATIONS)):
        for operation in operations:
            binding = _binding(operation)
            if operation in {"friend_connection_mutation.create",
                             "friend_connection_mutation.accept",
                             "friend_connection_mutation.reject",
                             "friend_connection_mutation.cancel"}:
                binding.update({"from_actor_id": DRIVER, "to_actor_id": MEMBER})
            elif operation in {"friend_connection_mutation.requests_sent.read",
                               "friend_connection_mutation.requests_received.read",
                               "friend_connection_mutation.requests_all.read",
                               "friend_connection_mutation.connections.read"}:
                binding["actor_id"] = DRIVER
            elif operation == "friend_connection_mutation.station_admission.read":
                binding.update({"target_actor_id": MEMBER, "actor_id": DRIVER})
            elif operation in {
                "friend_connection_mutation.bot_detail.read",
                "friend_connection_mutation.visibility.write",
                "friend_connection_mutation.revoke",
                "friend_connection_mutation.discover_denied.read",
                "friend_connection_mutation.legacy_revoke",
                "friend_connection_mutation.station_discover_collaborate.read",
            }:
                binding["target_actor_id"] = MEMBER
            profile["edge_case_bindings"][operation] = binding
    return gateway, station

class Harness:
    def __init__(self, *, pair_count: int = 2, owner_id: str = HUMAN):
        self.owner_id = owner_id
        self.pair_count = pair_count
        self.registered: dict[str, str] = {}
        self.current_member = ""
        self.current_visibility = ""
        self.current_strategy = ""
        self.pending: list[dict] = []
        self.edges: set[tuple[str, str]] = set()
        self.sequence = 1000
        self.reads: list[tuple[str, dict]] = []
        self.writes: list[tuple[str, dict]] = []

    def _ids(self) -> list[str]:
        self.sequence += 1
        return [str(self.sequence), str(self.sequence + 100000)]

    def bot_detail(self, bot_id: str) -> dict:
        return {
            "bot_uuid": bot_id,
            "created_by": self.owner_id,
            "visibility": self.current_visibility if bot_id == self.current_member else "public",
            "friend_check_in_strategy": self.current_strategy if bot_id == self.current_member else "APPROVAL",
        }

    def register(self, bot_name: str) -> dict:
        bot_id = f"registered-{len(self.registered) + 1}"
        self.current_member = bot_id
        self.current_visibility = "public"
        self.registered[bot_name] = bot_id
        return {"bot_name": bot_name, "bot_uuid": bot_id, "bot_token": MEMBER_TOKEN}

    def create(self, from_actor: str, to_actor: str, *, pair_count: int | None = None) -> tuple[dict, dict, int]:
        self.writes.append(("create", {"from": from_actor, "to": to_actor}))
        if from_actor == to_actor:
            body = {"code": 40000, "message": "self", "data": {"error_code": "cannot_add_self"}}
            return body, {"operation": "create", "http_status": 400}, 400
        if to_actor == UNKNOWN:
            body = {"code": 40400, "message": "unknown", "data": {"error_code": "bot_not_found"}}
            return body, {"operation": "create", "http_status": 404}, 404
        if self.current_member and to_actor == self.current_member and self.current_visibility == "private":
            body = {"code": 50000, "message": "internal", "data": {"error_code": "internal_error"}}
            return body, {"operation": "create", "http_status": 500}, 500
        if (from_actor, to_actor) in self.edges:
            return {"request_ids": [], "status": "approved"}, {"operation": "create", "http_status": 201}, 201
        matches = [row for row in self.pending
                   if {row["from_actor"]["id"], row["to_actor"]["id"]} == {from_actor, to_actor}
                   and row["status"] == "pending"]
        if matches:
            ids = [row["request_id"] for row in matches]
            return {"request_ids": ids, "edge_ids": [], "status": "pending", "auto_accepted": False}, {"operation": "create", "http_status": 201}, 201
        count = self.pair_count if pair_count is None else pair_count
        ids = self._ids()[:max(1, count)]
        for index, request_id in enumerate(ids):
            sender, receiver = ((from_actor, to_actor) if index == 0 else (to_actor, from_actor))
            self.pending.append({
                "request_id": request_id,
                "from_actor": {"type": "bot", "id": sender},
                "to_actor": {"type": "bot", "id": receiver}, "status": "pending",
            })
        return {"request_ids": ids, "edge_ids": [], "status": "pending", "auto_accepted": False}, {"operation": "create", "http_status": 201}, 201

    def decide(self, *, action: str, request_id: str, from_actor: str, to_actor: str) -> tuple[dict, dict, int]:
        self.writes.append((action, {"request_id": request_id, "from": from_actor, "to": to_actor}))
        row = next(row for row in self.pending if row["request_id"] == request_id)
        self.pending.remove(row)
        status = {"accept": "approved", "reject": "rejected", "cancel": "cancelled"}[action]
        if action == "accept":
            pair = (row["from_actor"]["id"], row["to_actor"]["id"])
            self.edges.add(pair)
            self.edges.add((pair[1], pair[0]))
            self.pending = [candidate for candidate in self.pending
                            if {candidate["from_actor"]["id"], candidate["to_actor"]["id"]}
                            != {from_actor, to_actor}]
            return {"status": status, "decided_by": {"type": "bot", "id": to_actor},
                    "edge_ids": [7001, 7002], "edge_id": 7001}, {"operation": action, "http_status": 200}, 200
        value = {"status": status, "decided_by": {"type": "bot", "id": to_actor}}
        if action == "cancel":
            value["decision_reason"] = "cancelled by initiator"
        return value, {"operation": action, "http_status": 200}, 200

    def read(self, operation: str, kwargs: dict) -> tuple[object, dict]:
        self.reads.append((operation, dict(kwargs)))
        record = {"operation": operation, "http_status": 200}
        if operation == "friend.mutation.bot_detail.read":
            return self.bot_detail(kwargs["bot_id"]), record
        if operation in {"friend.mutation.requests_sent.read", "friend.mutation.requests_received.read", "friend.mutation.requests_all.read"}:
            actor = kwargs["actor_id"]
            rows = list(self.pending)
            if operation.endswith("sent.read"):
                rows = [row for row in rows if row["from_actor"]["id"] == actor]
            elif operation.endswith("received.read"):
                rows = [row for row in rows if row["to_actor"]["id"] == actor]
            return {"items": rows, "total": len(rows), "page": 1, "page_size": 20}, record
        if operation == "friend.mutation.connections.read":
            actor = kwargs["actor_id"]
            peer = self.current_member if actor == DRIVER else DRIVER
            visible = (actor, peer) in self.edges
            rows = [{"actor": {"type": "bot", "id": peer}}] if visible else []
            return {"items": rows, "total": len(rows), "page": 1, "page_size": 20}, record
        if operation == "station.register_token.read":
            return {"token": REGISTER_TOKEN}, record
        if operation == "station.admission.read":
            bot_id, actor = kwargs["bot_id"], kwargs["actor_id"]
            if bot_id == UNKNOWN:
                return {"allowed": False, "reason_code": "bot_not_found"}, record
            if actor.startswith("human_"):
                # Live precedence: a non-party human owns no bot-bot edge row,
                # so visibility alone decides (public_default vs no_edge).
                if self.current_visibility == "public":
                    return {"allowed": True, "reason": "public_default"}, record
                return {"allowed": False, "reason": "no_edge"}, record
            if (actor, bot_id) in self.edges:
                return {"allowed": True, "reason": "ok", "source": "edge_grant"}, record
            return {"allowed": False, "reason": "no_edge"}, record
        if operation == "station.discover.collaborate.read":
            if self.current_visibility == "private":
                return {"bots": [], "count": 0}, record
            # Live station rows are heterogeneous: provider_info is an
            # optional projection key on a subset of rows. Pin one row
            # without it and one row with it.
            rows = [
                {"bot_uuid": DRIVER, "is_friend": True, "visibility": "public"},
                {"bot_uuid": "peer-bot", "is_friend": False,
                 "visibility": "protected", "provider_info": {}},
            ]
            return {"bots": rows, "count": len(rows)}, record
        if operation == "station.discover.role_no_org.read":
            record["http_status"] = 400
            return {"code": "invalid_operation", "message": "role",
                    "data": {"params": {"message": "role_requires_organization_code"}}}, record
        if operation == "station.discover.org_no_requester.read":
            record["http_status"] = 403
            return {"code": "forbidden", "message": "org",
                    "data": {"error": "organization discovery requires a bot caller"}}, record
        if operation == "friend.mutation.discover_denied.read":
            record["http_status"] = 401
            return {"code": 401000, "message": "bot only", "data": {}}, record
        raise AssertionError("unsupported fake read: " + operation)


class FakeRead:
    def __init__(self, profile: dict, harness: Harness):
        self.profile = profile
        self.harness = harness
        self.headers = {"Cookie": "human-cookie", "Authorization": "temporary-station-token" if profile["entrypoint"]["kind"] == "bcs-direct" else ""}

    def read(self, operation: str, **kwargs):
        data, record = self.harness.read(operation, kwargs)
        return Observation(data, record)


class FakeWrite:
    def __init__(self, profile: dict, harness: Harness):
        self.profile = profile
        self.harness = harness
        station = "Authorization" in profile.get("auth", {}).get("header_env", {})
        self.headers = {"Cookie": "human-cookie", "Authorization": "registered-owned-bot-token"} if station else {"Cookie": "human-cookie"}

    def create_edge_friend_request(self, from_actor_id, to_actor_id, *, expected=(201,), admit_error=False):
        data, record, status = self.harness.create(from_actor_id, to_actor_id)
        record["http_status"] = status
        return data, record

    def decide_edge_friend_request(self, *, action, request_id, from_actor_id, to_actor_id, expected=(200,), admit_error=False):
        if request_id == UNKNOWN:
            self.harness.writes.append((action, {"request_id": request_id, "from": from_actor_id, "to": to_actor_id}))
            return ({"code": 40400, "message": "unknown request", "data": {"error_code": "friend_request_not_found"}},
                    {"operation": action, "http_status": 404})
        data, record, status = self.harness.decide(
            action=action, request_id=request_id, from_actor=from_actor_id, to_actor=to_actor_id)
        record["http_status"] = status
        return data, record

    def patch_bot_friend_profile(self, target, *, visibility=None, friend_check_in_strategy=None):
        self.harness.writes.append(("patch", {"target": target, "visibility": visibility, "strategy": friend_check_in_strategy}))
        if visibility is not None:
            self.harness.current_visibility = visibility
        if friend_check_in_strategy is not None:
            self.harness.current_strategy = friend_check_in_strategy
        return {"bot_uuid": target}, {"operation": "patch", "http_status": 200}

    def revoke_edge_friend_connection(self, target):
        self.harness.writes.append(("gateway_revoke", {"target": target}))
        return {"deleted": False}, {"operation": "gateway_revoke", "http_status": 200}

    def station_register_bot(self, token, bot_name):
        self.harness.writes.append(("register", {"bot_name": bot_name}))
        return self.harness.register(bot_name), {"operation": "register", "http_status": 200, "register_token_sha256": "a" * 64}

    def station_legacy_revoke(self, target_actor_id):
        self.harness.writes.append(("station_revoke", {"target": target_actor_id}))
        self.harness.edges.clear()
        self.harness.pending.clear()
        return {"success": True, "error": None, "data": {"revoked_edges": [7001, 7002]}}, {"operation": "station_revoke", "http_status": 200}

    def _friend_mutation_request(self, *, operation, binding_name, path, body=None,
                                    expected=(200,), admit_error=False,
                                    response_mode="public-envelope"):
        if body is None:
            body = {}
        record = {
            "operation": operation, "binding_name": binding_name,
            "path": path, "http_status": None,
            "credentials_sent": "Cookie" in self.headers,
            "authorization_sent": "Authorization" in self.headers,
            "error_admitted": bool(admit_error and 403 in expected),
        }
        if operation == "friend_connection_mutation.create":
            to_actor = body["to_actor"]["id"]
            from_actor = body.get("from_actor", {}).get("id", HUMAN)
            data, _, status = self.harness.create(
                from_actor, to_actor,
                pair_count=1 if from_actor == HUMAN else None,
            )
            record["http_status"] = status
            record["submitted_to_actor"] = to_actor
            return data, record
        if operation == "friend_connection_mutation.create_as_bot":
            to_actor = body["to_actor"]["id"]
            data, _, status = self.harness.create(
                body["from_actor"]["id"], to_actor)
            record["http_status"] = status
            record["submitted_to_actor"] = to_actor
            return data, record
        if operation == "friend_connection_mutation.accept":
            request_id = path.split("/requests/", 1)[1].split("/", 1)[0]
            row = next(item for item in self.harness.pending
                       if item["request_id"] == request_id)
            data, _, status = self.harness.decide(
                action="accept", request_id=request_id,
                from_actor=row["from_actor"]["id"],
                to_actor=row["to_actor"]["id"])
            data = {**data, "request_id": request_id}
            record["http_status"] = status
            return data, record
        if operation == "friend_connection_mutation.visibility.write":
            record["http_status"] = 200
            record["submitted_friend_ext"] = dict(body.get("friend_ext", {}))
            self.harness.writes.append((
                "public_patch",
                {"target": path.strip("/").rsplit("/", 1)[-1], "body": dict(body)},
            ))
            return {"bot_id": path.strip("/").rsplit("/", 1)[-1],
                    "friend_ext": body.get("friend_ext", {})}, record
        raise AssertionError("unsupported red-case fake write: " + operation)


class ClientFactory:
    def __init__(self, cls, harness):
        self.cls, self.harness = cls, harness

    def __call__(self, profile):
        return self.cls(profile, self.harness)


class AdapterTest(unittest.TestCase):
    def adapter(self, *, pair_count=2, gateway=None, station=None, owner=HUMAN):
        harness = Harness(pair_count=pair_count, owner_id=owner)
        if gateway is None:
            default_gateway, default_station = base_profiles()
            gateway = gateway if gateway is not None else default_gateway
            station = station if station is not None else default_station
        read_factory = ClientFactory(FakeRead, harness)
        write_factory = ClientFactory(FakeWrite, harness)
        with patch("bcs_e2e.remote_edge_friend_mutation_adapter.ReadClient", read_factory), \
             patch("bcs_e2e.remote_edge_friend_mutation_adapter.WriteClient", write_factory):
            adapter = RemoteEdgeFriendMutationAdapter(gateway, station)
        return adapter, harness

    def test_constructor_validates_profiles_before_clients(self):
        cases = []
        for mutate in (
            lambda g, s: g["relationships"].update(friendship="unchanged"),
            lambda g, s: g.pop("browser_origin"),
            lambda g, s: g["auth"]["header_env"].update(Authorization="X"),
            lambda g, s: s["entrypoint"].update(kind="gateway"),
            lambda g, s: s["auth"]["header_env"].pop("Authorization"),
            lambda g, s: s["actors"]["member"].update(bot_id="other"),
        ):
            gateway, station = base_profiles()
            mutate(gateway, station)
            cases.append((gateway, station))
        harness = Harness()
        for value in cases:
            with self.subTest(value=value):
                with patch("bcs_e2e.remote_edge_friend_mutation_adapter.ReadClient", ClientFactory(FakeRead, harness)), \
                     patch("bcs_e2e.remote_edge_friend_mutation_adapter.WriteClient", ClientFactory(FakeWrite, harness)):
                    with self.assertRaises(RunStateError):
                        RemoteEdgeFriendMutationAdapter(value[0], value[1])

    def test_ownership_failure_blocks_before_case_execution(self):
        with self.assertRaisesRegex(RunStateError, "owned by the admitted Human"):
            self.adapter(owner="other")

    def run_method(self, name, *args, **kwargs):
        if "pair_count" in kwargs:
            pair_count = kwargs.pop("pair_count")
        else:
            pair_count = 2
        if "owner" in kwargs:
            owner = kwargs.pop("owner")
        else:
            owner = HUMAN
        harness = Harness(pair_count=pair_count, owner_id=owner)
        default_gateway, default_station = base_profiles()
        read_factory = ClientFactory(FakeRead, harness)
        write_factory = ClientFactory(FakeWrite, harness)
        with patch("bcs_e2e.remote_edge_friend_mutation_adapter.ReadClient", read_factory), \
             patch("bcs_e2e.remote_edge_friend_mutation_adapter.WriteClient", write_factory):
            adapter = RemoteEdgeFriendMutationAdapter(default_gateway, default_station)
            result = getattr(adapter, name)()
        self.assertTrue(result.evidence["remote_validation"] == "NOT_RUN")
        self.assertFalse(result.evidence["grants_release_credit"])
        self.assertNotIn(MEMBER_TOKEN, repr(result.evidence))
        return result, adapter, harness

    def test_mutual_auto_approve_chain(self):
        result, adapter, harness = self.run_method("run_mutual_auto_approve")
        self.assertTrue(result.data["mutual"])
        methods = [name for name, _ in harness.writes]
        self.assertEqual(methods, ["register", "patch", "create", "accept"])
        self.assertEqual(result.evidence["fixture_effect"], "REGISTERED_BOT_RETAINED;BOTH_EDGES_RETAINED")

    def test_revoke_chain_uses_gateway_then_station_dual_identity(self):
        result, adapter, harness = self.run_method("run_revoke")
        methods = [name for name, _ in harness.writes]
        self.assertEqual(methods, ["register", "patch", "create", "accept", "gateway_revoke", "station_revoke", "create"])
        self.assertFalse(result.data["gateway_deleted"])
        self.assertEqual(result.data["station_revoked_edges"], [7001, 7002])
        self.assertEqual(adapter.station_writer.headers["Authorization"], MEMBER_TOKEN)

    def test_full_lifecycle_chain_restores_public_and_recreates_pending(self):
        result, adapter, harness = self.run_method("run_full_lifecycle")
        methods = [name for name, _ in harness.writes]
        self.assertEqual(methods, ["register", "patch", "create", "accept", "station_revoke", "patch", "create"])
        self.assertEqual(result.evidence["fixture_effect"], "REGISTERED_BOT_RETAINED;VISIBILITY_RESTORED_PUBLIC;FRESH_MUTUAL_PENDING_RETAINED")

    def test_accept_reject_cancel_uses_three_independent_pairs(self):
        result, adapter, harness = self.run_method("run_accept_reject_cancel", pair_count=1)
        methods = [name for name, _ in harness.writes]
        self.assertEqual(methods, ["register", "patch", "create", "cancel", "register", "patch", "create", "reject", "register", "patch", "create", "accept"])
        self.assertEqual([row["status"] for row in result.data["branches"]], ["cancelled", "rejected", "approved"])

    def test_error_and_admission_branches(self):
        result, adapter, harness = self.run_method("run_error_admission_branches")
        methods = [name for name, _ in harness.writes]
        self.assertEqual(methods, ["register", "patch", "create", "create", "accept", "patch", "patch", "patch", "create", "patch", "create", "create", "accept", "create"])
        patches = [write for name, write in harness.writes if name == "patch"]
        self.assertEqual([patch["visibility"] for patch in patches],
                         ["protected", "public", "protected", "private", "public"])
        self.assertEqual(result.data["self_add"]["data"], {"error_code": "cannot_add_self"})
        self.assertEqual(result.data["unknown_bot"]["code"], 40400)
        self.assertEqual(result.data["private_create_defect"]["code"], 50000)
        self.assertEqual(result.data["admission_branches"]["protected_no_edge"], "no_edge")
        self.assertEqual(result.data["admission_branches"]["protected_no_edge_human"], "no_edge")
        self.assertEqual(result.data["admission_branches"]["human_public_default"], "public_default")
        self.assertEqual(result.data["admission_branches"]["human_public_with_edge"], "public_default")
        self.assertEqual(result.data["admission_branches"]["bot_party_edge"], "edge_grant/ok")

    def test_gateway_human_discover_gap_accepts_the_observed_rejection_family(self):
        for status, code in ((401, 401000), (422, 422000)):
            self.assertIn((status, code), _GATEWAY_HUMAN_DISCOVER_REJECTIONS)
        self.assertNotIn((403, 403000), _GATEWAY_HUMAN_DISCOVER_REJECTIONS)

    def test_drift_text_tolerates_every_live_message_placement(self):
        live_role = {"http_status": 400, "code": "invalid_operation",
                     "message": "Invalid operation on request unknown: role_requires_organization_code",
                     "data": None}
        live_org = {"http_status": 403, "code": "forbidden",
                    "error": "organization discovery requires a bot caller", "data": None}
        twin_role = {"http_status": 400, "code": "invalid_operation",
                     "message": "role", "data": {"params": {"message": "role_requires_organization_code"}}}
        self.assertIn("role_requires_organization_code",
                      RemoteEdgeFriendMutationAdapter._drift_text(live_role))
        self.assertIn("organization discovery requires a bot caller",
                      RemoteEdgeFriendMutationAdapter._drift_text(live_org))
        self.assertIn("role_requires_organization_code",
                      RemoteEdgeFriendMutationAdapter._drift_text(twin_role))
        self.assertEqual(RemoteEdgeFriendMutationAdapter._drift_text(
            {"http_status": 400, "code": "invalid_operation", "message": "x", "data": None}), "x")

    def test_connect_private_and_discover_branches(self):
        result, adapter, harness = self.run_method("run_connect_private_discover")
        methods = [name for name, _ in harness.writes]
        self.assertEqual(methods, ["register", "patch", "create", "patch"])
        self.assertEqual(result.data["private_discovery_empty"], True)
        self.assertEqual(result.data["role_no_org_drift"]["data"]["params"]["message"], "role_requires_organization_code")
        self.assertEqual(result.data["organization_no_requester_drift"]["code"], "forbidden")
        self.assertEqual(result.data["gateway_human_discover"]["code"], 401000)

    def test_unadmitted_ensure_bot_service_scope_fails_closed(self):
        adapter, _ = self.adapter()
        methods = ("run_ensure_bot",)
        for method in methods:
            with self.subTest(method=method):
                with self.assertRaisesRegex(RunStateError, "PREPROD_SERVICE_SCOPE_GAP") as context:
                    getattr(adapter, method)()
                self.assertEqual(context.exception.gap["gap"], "PREPROD_SERVICE_SCOPE_GAP")
                self.assertTrue(context.exception.gap["singlebox_closure"])

    def test_red_tc_preprod_branch_executes_create_accept_then_fails_closed(self):
        adapter, harness = self.adapter()
        result = adapter.run_tc_friend_request_create_accept()
        self.assertEqual(result.data["status"], "approved")
        self.assertEqual(result.data["member_bot_id"], MEMBER)
        self.assertEqual([row["operation"] for row in result.evidence["requests"]], [
            "friend_connection_mutation.create",
            "friend_connection_mutation.accept",
        ])
        self.assertEqual(result.evidence["requests"][0]["path"],
                         "/openapi/v1/collaboration/friend-connections/requests")
        self.assertEqual(result.evidence["requests"][1]["path"],
                         "/openapi/v1/collaboration/friend-connections/requests/"
                         + result.data["request_id"] + "/accept")
        for record in result.evidence["requests"]:
            self.assertTrue(record["credentials_sent"])
            self.assertFalse(record["authorization_sent"])
        self.assertEqual(result.evidence["remote_validation"], "NOT_RUN")
        self.assertFalse(result.evidence["grants_release_credit"])
        with self.assertRaisesRegex(RunStateError, "bounded external work-order receipt") as context:
            adapter.run_tc_friend_auth_sync_readdress()
        self.assertEqual(context.exception.gap["gap"],
                         "TC_FRIEND_AUTH_SYNC_RECEIPT_OBSERVATION")
        self.assertEqual(context.exception.gap["tc_bot_id"], MEMBER)
        self.assertEqual(context.exception.gap["required_receipt"],
                         "one grant principal with iss=bcs and aud=backend")

    def test_red_dept_scope_preprod_branch_executes_all_four_obligations(self):
        adapter, harness = self.adapter()
        mixed = adapter.run_dept_scope_mixed_owner_patch()
        self.assertEqual(mixed.data["scopes"], {
            "no_check_scope_friend_deps": ["F4858", "蚂蚁集团-信贷事业群"],
            "view_scope_user_friend_deps": [],
            "view_scope_agent_friend_deps": ["L3480"],
        })
        self.assertEqual(mixed.evidence["requests"][0]["path"],
                         "/openapi/v1/collaboration/bots/" + MEMBER)
        self.assertEqual(mixed.data["dropped"], {
            "no_check_scope_friend_deps": [],
            "view_scope_user_friend_deps": [],
            "view_scope_agent_friend_deps": [1],
        })
        no_check = adapter.run_dept_scope_human_no_check()
        self.assertEqual(no_check.data["status"], "pending")
        self.assertEqual(no_check.evidence["requests"][0]["path"],
                         "/openapi/v1/collaboration/friend-connections/requests")
        human_scope = adapter.run_dept_scope_human_user_scope()
        self.assertEqual(human_scope.data["human_create_http_status"], 201)
        self.assertEqual(human_scope.evidence["requests"][0]["path"],
                         "/openapi/v1/collaboration/bots/" + MEMBER)
        self.assertEqual(human_scope.data["scopes"], {
            "no_check_scope_friend_deps": ["F4858", "蚂蚁集团-信贷事业群"],
            "view_scope_user_friend_deps": ["L3480"],
            "view_scope_agent_friend_deps": ["L3480"],
        })
        bot_scope = adapter.run_dept_scope_bot_agent_scope()
        self.assertEqual(bot_scope.data["bot_create_http_status"], 201)
        self.assertEqual(bot_scope.data["caller_bot_id"], DRIVER)
        self.assertEqual(bot_scope.data["target_bot_id"], MEMBER)
        self.assertEqual(bot_scope.evidence["requests"][0]["operation"],
                         "friend_connection_mutation.create_as_bot")
        self.assertEqual(bot_scope.evidence["requests"][0]["path"],
                         "/openapi/v1/collaboration/friend-connections/requests")
        self.assertTrue(bot_scope.evidence["requests"][0]["credentials_sent"])
        self.assertTrue(bot_scope.evidence["requests"][0]["authorization_sent"])
        for result in (mixed, no_check, human_scope, bot_scope):
            self.assertEqual(result.evidence["remote_validation"], "NOT_RUN")
            self.assertFalse(result.evidence["grants_release_credit"])


if __name__ == "__main__":
    unittest.main()
