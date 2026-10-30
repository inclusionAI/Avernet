"""Dual-surface live adapter for the Edge friend-mutation case family."""
from __future__ import annotations

import hashlib
import os
from typing import Any
from urllib.parse import quote

from .case_adapters import AdapterCall
from .local_edge_backlog import MIXED_SCOPE, SCOPED_SHAPE, assert_dept_scope_parse
from .profile import ConfigError
from .route_bindings import require_edge_case_binding
from .run_state import RunStateError
from .transport import ReadClient
from .write_transport import WriteClient

_GATEWAY_OPERATIONS = (
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
_STATION_OPERATIONS = (
    "friend_connection_mutation.register_token.read",
    "friend_connection_mutation.register_bot.create",
    "friend_connection_mutation.legacy_revoke",
    "friend_connection_mutation.station_admission.read",
    "friend_connection_mutation.station_discover_collaborate.read",
    "friend_connection_mutation.station_discover_role_no_org.read",
    "friend_connection_mutation.station_discover_org_no_requester.read",
)
_MUTATION_RELATIONSHIP = "bounded-owned-bot-mutations"
_TRANSPORT = "public-v1-http"
HUMAN_COOKIE_ENV = "BCS_E2E_HUMAN_COOKIE"
STATION_BOT_TOKEN_ENV = "BCS_E2E_STATION_BOT_TOKEN"
_GATEWAY_HUMAN_DISCOVER_REJECTIONS = {
    # The Gateway public-v1 face never serves human-Cookie bot discovery;
    # observed rejections honestly span the gateway auth/validation seams:
    # 401/401000 (unauthenticated seam) and, after a cookie refresh, 422 with
    # 422000 "Invalid request" (request-validation seam). The reviewed
    # obligation is the rejection family, not one seam's status literal.
    (401, 401000),
    (422, 422000),
}

_PREPROD_BLOCKED_METHODS = {
    "run_ensure_bot": "admin Bot ensure requires the X-BCS-Service-Key user-credential batch",
    "run_tc_friend_request_create_accept": "forwarded Human authentication and TC friend request mutation require a mock Provider and the user-credential batch",
    "run_tc_friend_auth_sync_readdress": "the preprod TC friend-auth sync control plane needs a reachable mock Provider unavailable from the user-credential batch",
    "run_dept_scope_mixed_owner_patch": "legacy forwarded-Principal department headers require the user-credential batch unavailable in preprod",
    "run_dept_scope_human_no_check": "forwarded Human department headers require the user-credential batch unavailable in preprod",
    "run_dept_scope_human_user_scope": "forwarded user-scope department headers require the user-credential batch unavailable in preprod",
    "run_dept_scope_bot_agent_scope": "the scoped Bot tenant/department fixture required by the agent-scope parser is unavailable in preprod",
}


class RemoteEdgeFriendMutationAdapter:
    def __init__(self, gateway_profile: dict, station_profile: dict):
        if not isinstance(gateway_profile, dict) or not isinstance(station_profile, dict):
            raise RunStateError("Edge friend mutations require admitted Gateway and station profiles")
        gateway_entrypoint = gateway_profile.get("entrypoint", {})
        gateway_environment = gateway_profile.get("environment", {})
        gateway_admitted = (
            gateway_entrypoint.get("kind") == "gateway" and gateway_environment.get("tier") == "preprod"
        ) or (
            gateway_entrypoint.get("kind") == "singlebox" and gateway_environment.get("tier") == "local"
        )
        if not gateway_admitted or gateway_entrypoint.get("api_face") != "public-v1":
            raise RunStateError("Edge friend mutations require a public-v1 Gateway or Singlebox profile")
        station_entrypoint = station_profile.get("entrypoint", {})
        if station_entrypoint.get("kind") != "bcs-direct":
            raise RunStateError("Edge friend mutations require a station bcs-direct profile")
        for profile in (gateway_profile, station_profile):
            if profile.get("relationships", {}).get("friendship") != _MUTATION_RELATIONSHIP:
                raise RunStateError("Edge friend mutations require explicit bounded owned-Bot mutation admission")
        try:
            gateway_headers = gateway_profile["auth"]["header_env"]
            station_headers = station_profile["auth"]["header_env"]
        except (KeyError, TypeError):
            raise RunStateError("Edge friend-mutation profiles require explicit header admission") from None
        gateway_wrong_authorization = (
            gateway_environment.get("tier") == "preprod" and "Authorization" in gateway_headers
        )
        if (gateway_headers != {"Cookie": HUMAN_COOKIE_ENV}
                or gateway_wrong_authorization or not gateway_profile.get("browser_origin")):
            raise RunStateError("Edge friend mutations require a Cookie-bound Gateway browser context")
        if station_headers != {"Cookie": HUMAN_COOKIE_ENV,
                               "Authorization": STATION_BOT_TOKEN_ENV}:
            raise RunStateError("Edge friend mutations require the exact station dual-identity context")
        if gateway_profile.get("actors") != station_profile.get("actors"):
            raise RunStateError("Gateway and station profiles must declare the same dedicated actors")
        try:
            for profile, operations in ((gateway_profile, _GATEWAY_OPERATIONS), (station_profile, _STATION_OPERATIONS)):
                for operation in operations:
                    require_edge_case_binding(profile, operation)
        except (ConfigError, ValueError) as error:
            raise RunStateError(str(error)) from None

        self.gateway_profile = gateway_profile
        self.station_profile = station_profile
        self.driver_actor_id = gateway_profile["actors"]["driver"]["bot_id"]
        self.member_actor_id = gateway_profile["actors"]["member"]["bot_id"]
        self.human_user_id = gateway_profile["human"]["user_id"]
        self.reader = ReadClient(gateway_profile)
        self.writer = WriteClient(gateway_profile)
        self.station_reader = ReadClient(station_profile)
        self.station_writer: Any = None
        # The station Bot never becomes a second user identity.  The public-v1
        # copy of the Gateway profile exists solely for the department agent
        # parser leg, which must call the mounted openapi Friend route with the
        # dedicated Bot token while retaining the owning Human Cookie.
        station_gateway_profile = dict(gateway_profile)
        station_gateway_profile["auth"] = {
            "header_env": {
                "Cookie": HUMAN_COOKIE_ENV,
                "Authorization": STATION_BOT_TOKEN_ENV,
            }
        }
        station_bindings = {
            operation: dict(binding)
            for operation, binding in gateway_profile.get("edge_case_bindings", {}).items()
        }
        create = station_bindings.get("friend_connection_mutation.create")
        if not isinstance(create, dict):
            raise RunStateError("Edge red-case Bot create requires the public Friend create binding")
        station_bindings["friend_connection_mutation.create_as_bot"] = {
            **create, "auth": "human_cookie_owned_bot",
        }
        station_gateway_profile["edge_case_bindings"] = station_bindings
        self.station_gateway_writer = WriteClient(station_gateway_profile)
        self._registered: dict[str, dict] = {}
        self._verify_ownership()

    @staticmethod
    def _actor(actor_id: str) -> dict:
        return {"type": "bot", "id": actor_id}

    @staticmethod
    def _token_digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _require(condition: object, message: str) -> None:
        if not condition:
            raise RunStateError("Edge friend-mutation obligation was not satisfied: " + message)

    def _verify_ownership(self) -> None:
        for role, actor_id in (("driver", self.driver_actor_id), ("member", self.member_actor_id)):
            observation = self.reader.read("friend.mutation.bot_detail.read", bot_id=actor_id)
            data, record = observation.data, observation.evidence
            self._require(isinstance(data, dict) and data.get("created_by") == self.human_user_id, f"{role} Bot is not owned by the admitted Human")
            self._require(record.get("http_status") == 200, f"{role} Bot detail read failed")

    def _station_write_client(self) -> Any:
        if self.station_writer is None:
            raise RunStateError("station dual-identity writer requires a registered owned Bot")
        return self.station_writer

    def _finish(self, *, operation: str, fixture_effect: str, data: dict, requests: list[dict]) -> AdapterCall:
        evidence = {
            "transport": _TRANSPORT,
            "operation": operation,
            "fixture_effect": fixture_effect,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
            "projection": data,
            "requests": list(requests),
        }
        secrets = {**self.reader.headers, **self.writer.headers,
                   **self.station_reader.headers, **self.station_gateway_writer.headers}
        station_writer = self.station_writer
        if station_writer is not None:
            secrets = {**secrets, **station_writer.headers}
        if any(secret and secret in repr(evidence) for secret in secrets.values()):
            raise RunStateError("Edge friend-mutation evidence must not expose a credential")
        for registered in self._registered.values():
            token = registered.get("token")
            if token and token in repr(evidence):
                raise RunStateError("Edge friend-mutation evidence must not expose a registered Bot token")
        return AdapterCall(data=data, evidence=evidence)

    def _provision_member(self, tag: str, *, visibility: str, strategy: str) -> tuple[str, list[dict]]:
        requests: list[dict] = []
        observation = self.station_reader.read("station.register_token.read")
        token_data, token_record = observation.data, observation.evidence
        token = token_data.get("token") if isinstance(token_data, dict) else None
        if not isinstance(token, str) or not token:
            raise RunStateError("station registration token is missing")
        bot_name = f"e2e-friend-{tag}"
        authorization_ref = self.station_profile["auth"]["header_env"]["Authorization"]
        # The register-token writer uses only the register token; the profile's
        # Authorization reference is temporarily seeded and filtered by transport.
        os.environ[authorization_ref] = token
        register_writer = WriteClient(self.station_profile)
        data, register_record = register_writer.station_register_bot(token, bot_name)
        bot_id = data.get("bot_uuid") if isinstance(data, dict) else None
        bot_token = data.get("bot_token") if isinstance(data, dict) else None
        if not isinstance(bot_id, str) or not bot_id or not isinstance(bot_token, str) or not bot_token:
            raise RunStateError("station registration did not return a Bot UUID and owned-Bot token")
        # Rebuild only after registration so legacy dual-identity writes send the
        # newly owned Bot Bearer. The token exists in memory and this env ref only.
        os.environ[authorization_ref] = bot_token
        self.station_writer = WriteClient(self.station_profile)
        self._registered[tag] = {"bot_uuid": bot_id, "token": bot_token, "token_sha256": self._token_digest(bot_token)}
        patch_data, patch_record = self.writer.patch_bot_friend_profile(
            bot_id, visibility=visibility, friend_check_in_strategy=strategy
        )
        detail_observation = self.reader.read("friend.mutation.bot_detail.read", bot_id=bot_id)
        detail = detail_observation.data
        requests.extend([token_record, register_record, patch_record, detail_observation.evidence])
        self._require(isinstance(patch_data, dict), "Bot profile patch is not an object")
        self._require(
            isinstance(detail, dict)
            and detail.get("visibility") == visibility
            and detail.get("friend_check_in_strategy") == strategy
            and detail.get("created_by") == self.human_user_id,
            "registered member Bot profile or ownership was not confirmed",
        )
        return bot_id, requests

    def _read(self, client: Any, operation: str, **kwargs: Any) -> tuple[dict, dict]:
        observation = client.read(operation, **kwargs)
        return observation.data, observation.evidence

    @staticmethod
    def _page_rows(data: object) -> list[dict]:
        if not isinstance(data, dict) or not isinstance(data.get("items"), list) or type(data.get("total")) is not int or data.get("page") != 1:
            raise RunStateError("friend-mutation page is outside its bounded contract")
        rows = data["items"]
        if any(not isinstance(row, dict) for row in rows):
            raise RunStateError("friend-mutation page contains an invalid row")
        return rows

    def _request_row(self, *, data: object, candidates: set[str], sender: str | None = None, receiver: str | None = None) -> dict | None:
        matching = []
        for row in self._page_rows(data):
            if row.get("request_id") not in candidates:
                continue
            if sender is not None and row.get("from_actor") != self._actor(sender):
                continue
            if receiver is not None and row.get("to_actor") != self._actor(receiver):
                continue
            if row.get("status") != "pending":
                continue
            matching.append(row)
        return matching[0] if len(matching) == 1 else None

    def _create(self, sender: str, receiver: str, *, expected: tuple[int, ...], admit_error: bool = False) -> tuple[dict, dict]:
        data, record = self.writer.create_edge_friend_request(sender, receiver, expected=expected, admit_error=admit_error)
        if record.get("http_status") not in expected:
            raise RunStateError("friend-request create status is outside its declared contract")
        return data, record

    @staticmethod
    def _valid_pending_create(data: object) -> list[str]:
        if not isinstance(data, dict):
            raise RunStateError("friend-request create response is not an object")
        ids = data.get("request_ids")
        if (
            data.get("status") != "pending"
            or data.get("auto_accepted") is not False
            or data.get("edge_ids") != []
            or not isinstance(ids, list)
            or not ids
            or any(not isinstance(value, str) or not value for value in ids)
            or len(set(ids)) != len(ids)
        ):
            raise RunStateError("friend-request create did not return exact pending request IDs")
        return ids

    def _decide(self, *, action: str, sender: str, receiver: str, request_id: str, expected: tuple[int, ...], admit_error: bool = False) -> tuple[dict, dict]:
        data, record = self.writer.decide_edge_friend_request(
            action=action, request_id=request_id, from_actor_id=sender, to_actor_id=receiver,
            expected=expected, admit_error=admit_error
        )
        if record.get("http_status") not in expected:
            raise RunStateError("friend-request decision status is outside its declared contract")
        return data, record

    def _pending_pair_absent(self, *, member: str, actor_id: str,
                             requests: list[dict]) -> None:
        for operation, sender, receiver in (
                ("friend.mutation.requests_sent.read", actor_id, member),
                ("friend.mutation.requests_received.read", member, actor_id)):
            data, record = self._read(self.reader, operation, actor_id=actor_id)
            requests.append(record)
            if self._request_row(data=data, candidates=set(),
                                 sender=sender, receiver=receiver) is not None:
                raise RunStateError("friend pair still has a visible pending request")

    def _pending_absent(self, *, candidates: set[str], actor_id: str, requests: list[dict]) -> None:
        for operation, sender, receiver in (
            ("friend.mutation.requests_sent.read", actor_id, None),
            ("friend.mutation.requests_received.read", None, actor_id),
        ):
            data, record = self._read(self.reader, operation, actor_id=actor_id)
            requests.append(record)
            if self._request_row(data=data, candidates=candidates, sender=sender, receiver=receiver) is not None:
                raise RunStateError("friend pair still has a visible pending request")

    def _connections(self, *, actor_id: str, peer: str, requests: list[dict]) -> bool:
        data, record = self._read(self.reader, "friend.mutation.connections.read", actor_id=actor_id)
        requests.append(record)
        return any(row.get("actor") == self._actor(peer) for row in self._page_rows(data))

    def _admission(self, *, bot_id: str, actor_id: str, requests: list[dict]) -> dict:
        data, record = self._read(self.station_reader, "station.admission.read", bot_id=bot_id, actor_id=actor_id)
        requests.append(record)
        if not isinstance(data, dict) or record.get("http_status") != 200:
            raise RunStateError("station admission did not return a bounded 200 result")
        return data

    @staticmethod
    def _error_projection(data: object, record: dict) -> dict:
        if not isinstance(data, dict):
            raise RunStateError("friend-mutation error response is not an object")
        return {"http_status": record.get("http_status"), "code": data.get("code"), "message": data.get("message"), "data": data.get("data")}

    @staticmethod
    def _drift_text(error: dict) -> str:
        # Live bcs-pre station negative diagnostics place the reviewed token
        # on body-level message/error or, depending on the route, params
        # message/reason; the local twin carries it under data. The obligation
        # pins HTTP status, error code and token presence - never the wire
        # placement of the token - so the matcher reads every placement.
        parts: list[str] = []
        data = error.get("data")
        if isinstance(data, dict):
            for key in ("message", "error"):
                value = data.get(key)
                if isinstance(value, str):
                    parts.append(value)
            params = data.get("params")
            if isinstance(params, dict):
                for key in ("message", "reason"):
                    value = params.get(key)
                    if isinstance(value, str):
                        parts.append(value)
        for key in ("message", "error"):
            value = error.get(key)
            if isinstance(value, str):
                parts.append(value)
        return " | ".join(parts)

    def _service_gap(self, method: str) -> RunStateError:
        error = RunStateError("PREPROD_SERVICE_SCOPE_GAP: " + _PREPROD_BLOCKED_METHODS[method])
        error.gap = {"gap": "PREPROD_SERVICE_SCOPE_GAP", "singlebox_closure": True, "method": method}
        return error

    def _accept_pair(self, *, member: str, requests: list[dict]) -> tuple[str, int]:
        create_data, create_record = self._create(self.driver_actor_id, member, expected=(201,))
        candidates = set(self._valid_pending_create(create_data))
        sent_data, sent_record = self._read(self.reader, "friend.mutation.requests_sent.read", actor_id=self.driver_actor_id)
        received_data, received_record = self._read(self.reader, "friend.mutation.requests_received.read", actor_id=self.driver_actor_id)
        requests.extend([create_record, sent_record, received_record])
        forward = self._request_row(data=sent_data, candidates=candidates, sender=self.driver_actor_id, receiver=member)
        reverse = self._request_row(data=received_data, candidates=candidates, sender=member, receiver=self.driver_actor_id)
        if forward is None or reverse is None or forward.get("request_id") == reverse.get("request_id"):
            raise RunStateError("friend pair did not expose two distinct pending rows")
        decision_data, decision_record = self._decide(
            action="accept", sender=member, receiver=self.driver_actor_id,
            request_id=reverse["request_id"], expected=(200,)
        )
        requests.append(decision_record)
        edge_id = decision_data.get("edge_id") if isinstance(decision_data, dict) else None
        if type(edge_id) is not int:
            raise RunStateError("mutual friend acceptance did not create an exact edge ID")
        if decision_data.get("status") != "approved" or decision_data.get("decided_by") != self._actor(self.driver_actor_id):
            raise RunStateError("mutual friend acceptance did not acknowledge the receiver")
        return reverse["request_id"], edge_id

    def run_mutual_auto_approve(self) -> AdapterCall:
        member, requests = self._provision_member("mutual-auto-approve", visibility="protected", strategy="APPROVAL")
        request_id, edge_id = self._accept_pair(member=member, requests=requests)
        self._pending_absent(candidates={request_id}, actor_id=self.driver_actor_id, requests=requests)
        self._pending_absent(candidates={request_id}, actor_id=member, requests=requests)
        driver_visible = self._connections(actor_id=self.driver_actor_id, peer=member, requests=requests)
        member_visible = self._connections(actor_id=member, peer=self.driver_actor_id, requests=requests)
        if not driver_visible or not member_visible:
            raise RunStateError("mutual friend pair is not visible from both vantages")
        admissions = {}
        for requester, target in ((self.driver_actor_id, member), (member, self.driver_actor_id)):
            admission = self._admission(bot_id=target, actor_id=requester, requests=requests)
            if admission.get("allowed") is not True or admission.get("reason") != "ok" or admission.get("source") != "edge_grant":
                raise RunStateError("station admission did not honor the mutual edge grant")
            admissions[f"{requester}->{target}"] = {"allowed": True, "reason": "ok", "source": "edge_grant"}
        data = {"accepted_request_id": request_id, "edge_id": edge_id, "mutual": True, "admissions": admissions}
        return self._finish(
            operation="friend.mutation.mutual_auto_approve",
            fixture_effect="REGISTERED_BOT_RETAINED;BOTH_EDGES_RETAINED", data=data, requests=requests
        )

    def run_accept_reject_cancel(self) -> AdapterCall:
        outcomes = []
        adapters = []
        for action in ("cancel", "reject", "accept"):
            member, requests = self._provision_member(action, visibility="public", strategy="APPROVAL")
            create_data, create_record = self._create(self.driver_actor_id, member, expected=(201,))
            candidates = set(self._valid_pending_create(create_data))
            requests.append(create_record)
            row = None
            for operation, sender, receiver in (
                ("friend.mutation.requests_sent.read", self.driver_actor_id, member),
                ("friend.mutation.requests_received.read", member, self.driver_actor_id),
            ):
                data, record = self._read(self.reader, operation, actor_id=self.driver_actor_id)
                requests.append(record)
                row = self._request_row(data=data, candidates=candidates, sender=sender, receiver=receiver)
                if row is not None:
                    break
            if row is None:
                raise RunStateError(f"single friend request was not uniquely visible before {action}")
            status = {"cancel": "cancelled", "reject": "rejected", "accept": "approved"}[action]
            decision_data, decision_record = self._decide(
                action=action, sender=self.driver_actor_id, receiver=member,
                request_id=row["request_id"], expected=(200,)
            )
            requests.append(decision_record)
            if decision_data.get("status") != status:
                raise RunStateError(f"friend request {action} did not report its decision")
            if action == "cancel" and "cancelled" not in str(decision_data.get("decision_reason", "")):
                raise RunStateError("friend request cancellation did not report its reason")
            if action == "reject" and decision_data.get("decided_by") != self._actor(member):
                raise RunStateError("friend request rejection did not acknowledge the receiver")
            if action == "accept" and type(decision_data.get("edge_id")) is not int:
                raise RunStateError("friend acceptance did not create an edge")
            outcomes.append({"action": action, "request_id": row["request_id"], "status": status, "edge_id": decision_data.get("edge_id") if action == "accept" else None})
            adapters.append((member, requests, candidates))
        for member, requests, candidates in adapters:
            self._pending_absent(candidates=candidates, actor_id=self.driver_actor_id, requests=requests)
            self._pending_absent(candidates=candidates, actor_id=member, requests=requests)
            visible = self._connections(actor_id=self.driver_actor_id, peer=member, requests=requests)
            if visible is not (member == adapters[2][0]):
                raise RunStateError("friend decision did not update the connection pair")
        accepted = next(row for row in outcomes if row["action"] == "accept")
        admission = self._admission(bot_id=adapters[2][0], actor_id=self.driver_actor_id, requests=adapters[2][1])
        if admission.get("allowed") is not True or admission.get("reason") != "ok" or admission.get("source") != "edge_grant":
            raise RunStateError("retained accepted edge was not admitted")
        data = {
            "branches": outcomes,
            "accepted_request_id": accepted["request_id"],
            "accepted_edge_admission": {"allowed": True, "reason": "ok", "source": "edge_grant"},
        }
        return self._finish(
            operation="friend.mutation.accept_reject_cancel",
            fixture_effect="REGISTERED_BOTS_RETAINED", data=data,
            requests=[record for _, requests, _ in adapters for record in requests]
        )

    def run_error_admission_branches(self) -> AdapterCall:
        member, requests = self._provision_member("error-branches", visibility="protected", strategy="APPROVAL")
        self_create, self_record = self._create(self.driver_actor_id, self.driver_actor_id, expected=(400,), admit_error=True)
        self_error = self._error_projection(self_create, self_record)
        if self_error["http_status"] != 400 or self_error["code"] != 40000 or self_error["data"] != {"error_code": "cannot_add_self"}:
            raise RunStateError("self-add error envelope differs from the contract")
        unknown = "00000000-0000-0000-0000-000000000000"
        unknown_create, unknown_record = self._create(self.driver_actor_id, unknown, expected=(404,), admit_error=True)
        unknown_error = self._error_projection(unknown_create, unknown_record)
        if unknown_error["code"] != 40400 or unknown_error["data"] != {"error_code": "bot_not_found"}:
            raise RunStateError("unknown Bot create error differs from the contract")
        unknown_decision, unknown_decision_record = self._decide(
            action="accept", sender=member, receiver=self.driver_actor_id, request_id=unknown, expected=(404,), admit_error=True
        )
        unknown_request_error = self._error_projection(unknown_decision, unknown_decision_record)
        if unknown_request_error["code"] != 40400 or unknown_request_error["data"] != {"error_code": "friend_request_not_found"}:
            raise RunStateError("unknown request decision error differs from the contract")

        unknown_admission = self._admission(bot_id=unknown, actor_id=self.driver_actor_id, requests=requests)
        if unknown_admission.get("allowed") is not False or unknown_admission.get("reason_code") != "bot_not_found":
            raise RunStateError("unknown-Bot station admission differs from the contract")
        no_edge_admission = self._admission(bot_id=member, actor_id=self.driver_actor_id, requests=requests)
        if no_edge_admission.get("allowed") is not False or no_edge_admission.get("reason") != "no_edge":
            raise RunStateError("protected no-edge station admission differs from the contract")
        human_actor = "human_" + self.human_user_id
        # Local-twin parity and live bcs-pre matrix: the owning human on a
        # protected no-edge member is no_edge as well - visibility grants
        # never apply to a protected target for any actor.
        human_no_edge = self._admission(bot_id=member, actor_id=human_actor, requests=requests)
        if human_no_edge.get("allowed") is not False or human_no_edge.get("reason") != "no_edge":
            raise RunStateError("protected human no-edge station admission differs from the contract")
        # Live bcs-pre matrix: public_default only holds for a public target; a
        # protected member stays no_edge even for the owning human. Flip the
        # member public for this read-only branch, then restore protected.
        _, public_patch_record = self.writer.patch_bot_friend_profile(member, visibility="public")
        requests.append(public_patch_record)
        public_default = self._admission(bot_id=member, actor_id=human_actor, requests=requests)
        if public_default.get("allowed") is not True or public_default.get("reason") != "public_default":
            raise RunStateError("public default station admission differs from the contract")
        _, protected_patch_record = self.writer.patch_bot_friend_profile(member, visibility="protected")
        requests.append(protected_patch_record)

        # The reviewed private-target drift must be observed while the member
        # pair is still unconnected: a private target WITH an existing edge
        # returns the connected approved idempotency instead of the denial
        # branch (probe-locked on the live Gateway).
        _, drift_private_patch_record = self.writer.patch_bot_friend_profile(member, visibility="private")
        requests.append(drift_private_patch_record)
        private_create, private_record = self._create(self.driver_actor_id, member, expected=(500,), admit_error=True)
        private_error = self._error_projection(private_create, private_record)
        private_defect = {"defect": "PD-PRIVATE-CREATE-500", "http_status": private_error["http_status"], "code": private_error["code"], "error_code": private_error["data"]}
        if private_error["http_status"] != 500 or private_error["code"] != 50000 or private_error["data"] != {"error_code": "internal_error"}:
            raise RunStateError("private friend create drift is outside the observed defect contract")
        requests.append(private_record)
        self._pending_pair_absent(
            member=member, actor_id=self.driver_actor_id, requests=requests)
        _, public_record = self.writer.patch_bot_friend_profile(member, visibility="public")
        requests.append(public_record)

        first_data, first_record = self._create(self.driver_actor_id, member, expected=(201,))
        first_ids = self._valid_pending_create(first_data)
        duplicate_data, duplicate_record = self._create(self.driver_actor_id, member, expected=(201,))
        duplicate_ids = self._valid_pending_create(duplicate_data)
        # Live preprod matrix: a fresh pair mints [forward, reverse] on the
        # first create; an exact same-direction duplicate is idempotent but
        # projects only the stable forward ID, so the reviewed obligation is
        # "no NEW request IDs" (subset) plus the bounded pending-row count,
        # never a verbatim two-element list equality.
        if not duplicate_ids or not set(duplicate_ids).issubset(set(first_ids)):
            raise RunStateError("duplicate friend create minted new request IDs")
        sent_before, sent_record = self._read(self.reader, "friend.mutation.requests_sent.read", actor_id=self.driver_actor_id)
        pending_count = sum(
            row.get("from_actor") == self._actor(self.driver_actor_id)
            and row.get("to_actor") == self._actor(member)
            and row.get("status") == "pending"
            for row in self._page_rows(sent_before)
        )
        if pending_count != 1:
            raise RunStateError("duplicate friend create changed the pending row count")

        decision_data, decision_record = self._decide(
            action="accept", sender=self.driver_actor_id, receiver=member, request_id=first_ids[0], expected=(200,)
        )
        if decision_data.get("status") != "approved":
            raise RunStateError("the error-branch fixture acceptance failed")
        connected_data, connected_record = self._create(self.driver_actor_id, member, expected=(201,))
        requests.append(connected_record)
        if connected_data.get("status") != "approved" or connected_data.get("request_ids") != []:
            raise RunStateError("connected friend create was not idempotently approved")

        # Live bcs-pre precedence matrix (public target holding a mutual edge):
        # an edge-party Bot actor resolves edge_grant/ok, while the owning
        # human holds no bot-bot edge row and resolves public_default -
        # station visibility precedence applies to non-party actors. Record
        # the human observation and assert the edge-party Bot branch.
        human_public_edge = self._admission(bot_id=member, actor_id=human_actor, requests=requests)
        if human_public_edge.get("allowed") is not True or human_public_edge.get("reason") != "public_default":
            raise RunStateError("human non-party public admission precedence differs from the live contract")
        edge_admission = self._admission(bot_id=member, actor_id=self.driver_actor_id, requests=requests)
        if edge_admission.get("allowed") is not True or edge_admission.get("reason") != "ok" or edge_admission.get("source") != "edge_grant":
            raise RunStateError("edge-grant station admission differs from the contract")

        data = {
            "self_add": self_error,
            "unknown_bot": unknown_error,
            "unknown_request": unknown_request_error,
            "duplicate_request_ids": first_ids,
            "duplicate_pending_count": 1,
            "connected_create_status": "approved",
            "private_create_defect": private_defect,
            "admission_branches": {
                "unknown_bot": "bot_not_found", "protected_no_edge": "no_edge",
                "protected_no_edge_human": "no_edge",
                "human_public_default": "public_default",
                "human_public_with_edge": "public_default",
                "bot_party_edge": "edge_grant/ok",
            },
        }
        evidence = requests + [self_record, unknown_record, unknown_decision_record, first_record, duplicate_record, sent_record, decision_record]
        return self._finish(
            operation="friend.mutation.error_admission_branches",
            fixture_effect="REGISTERED_BOT_RETAINED", data=data, requests=evidence
        )

    def run_connect_private_discover(self) -> AdapterCall:
        member, requests = self._provision_member("connect-private-discover", visibility="private", strategy="OPEN")
        private_create, private_record = self._create(self.driver_actor_id, member, expected=(500,), admit_error=True)
        private_error = self._error_projection(private_create, private_record)
        private_defect = {"defect": "PD-PRIVATE-CREATE-500", "http_status": private_error["http_status"], "code": private_error["code"], "error_code": private_error["data"]}
        if private_error["http_status"] != 500 or private_error["code"] != 50000 or private_error["data"] != {"error_code": "internal_error"}:
            raise RunStateError("private friend target drift is outside the observed defect contract")
        self._pending_pair_absent(
            member=member, actor_id=self.driver_actor_id, requests=requests)
        no_edge = self._admission(bot_id=member, actor_id=self.driver_actor_id, requests=requests)
        if no_edge.get("allowed") is not False or no_edge.get("reason") != "no_edge":
            raise RunStateError("private member station admission differs from the contract")
        discover_empty_data, discover_empty_record = self._read(self.station_reader, "station.discover.collaborate.read", target_actor_id=member)
        requests.append(discover_empty_record)
        if discover_empty_data.get("bots") != [] or discover_empty_data.get("count") != 0:
            raise RunStateError("private collaborate-bot discovery was not empty")
        _, public_record = self.writer.patch_bot_friend_profile(member, visibility="public")
        discover_data, discover_record = self._read(self.station_reader, "station.discover.collaborate.read", target_actor_id=member)
        requests.append(discover_record)
        bots = discover_data.get("bots")
        # Local-twin parity: the reviewed obligation pins the directory SHAPE
        # (count/list agreement, bounded entry fields, requester self-exclusion),
        # never directory propagation timing - the station publishes the public
        # collaborate directory asynchronously. Driver presence is evidence, not
        # a gate, mirroring the local contract that never pins a specific peer.
        directory_rows = [row for row in bots if isinstance(row, dict)] if isinstance(bots, list) else []
        if (
            not isinstance(bots, list)
            or discover_data.get("count") != len(bots)
            or len(directory_rows) != len(bots)
            or any(row.get("bot_uuid") == member for row in directory_rows)
            or any(
                # provider_info is an optional projection key on a subset of
                # live directory rows; the bounded shape gates only the
                # bot_uuid / visibility / is_friend triple, mirroring the twin.
                not isinstance(row.get("bot_uuid"), str)
                or row.get("visibility") not in ("public", "protected")
                or not isinstance(row.get("is_friend"), bool)
                for row in directory_rows
            )
        ):
            raise RunStateError("public collaborate-bot discovery disagrees with the shape contract")
        driver_listed = any(row.get("bot_uuid") == self.driver_actor_id for row in directory_rows)
        role_data, role_record = self._read(self.station_reader, "station.discover.role_no_org.read", fail_open=True)
        role_error = self._error_projection(role_data, role_record)
        role_drift = {"defect": "PD-DISCOVER-BRANCH-DRIFT", **role_error}
        if (role_error["http_status"] != 400 or role_error["code"] != "invalid_operation"
                or "role_requires_organization_code" not in self._drift_text(role_error)):
            raise RunStateError("station role discovery drift differs from the observed contract")
        org_data, org_record = self._read(self.station_reader, "station.discover.org_no_requester.read", fail_open=True)
        org_error = self._error_projection(org_data, org_record)
        org_drift = {"defect": "PD-DISCOVER-BRANCH-DRIFT", **org_error}
        if (org_error["http_status"] != 403 or org_error["code"] != "forbidden"
                or "organization discovery requires a bot caller" not in self._drift_text(org_error)):
            raise RunStateError("station organization discovery differs from the observed contract")
        gateway_discover_data, gateway_discover_record = self._read(self.reader, "friend.mutation.discover_denied.read", target_actor_id=member, fail_open=True)
        gateway_error = self._error_projection(gateway_discover_data, gateway_discover_record)
        documented_gap = {"gap": "GATEWAY_HUMAN_DISCOVER_401", **gateway_error}
        if (gateway_error["http_status"], gateway_error["code"]) not in _GATEWAY_HUMAN_DISCOVER_REJECTIONS:
            raise RunStateError("Gateway human discovery gap differs from the observed contract")
        data = {
            "private_create_defect": private_defect, "private_discovery_empty": True,
            "public_discovery_count": discover_data["count"], "public_discovery_self_excluded": True,
            "public_directory_driver_listed": driver_listed,
            "role_no_org_drift": role_drift, "organization_no_requester_drift": org_drift,
            "gateway_human_discover": documented_gap,
        }
        requests.extend([private_record, public_record, gateway_discover_record])
        return self._finish(
            operation="friend.mutation.connect_private_discover",
            fixture_effect="REGISTERED_BOT_RETAINED;PRIVATE_MEMBER_RESTORED_PUBLIC", data=data, requests=requests
        )

    def run_revoke(self) -> AdapterCall:
        member, requests = self._provision_member("revoke", visibility="protected", strategy="APPROVAL")
        self._accept_pair(member=member, requests=requests)
        if not self._connections(actor_id=self.driver_actor_id, peer=member, requests=requests):
            raise RunStateError("pre-revocation driver connection is missing")
        if not self._connections(actor_id=member, peer=self.driver_actor_id, requests=requests):
            raise RunStateError("pre-revocation member connection is missing")
        gateway_revoke, gateway_revoke_record = self.writer.revoke_edge_friend_connection(member)
        if gateway_revoke.get("deleted") is not False:
            raise RunStateError("Gateway revoke did not expose the reviewed deleted:false asymmetry")
        legacy_revoke, legacy_record = self._station_write_client().station_legacy_revoke(self.driver_actor_id)
        revoked_edges = legacy_revoke.get("data", {}).get("revoked_edges")
        if legacy_revoke.get("success") is not True or not isinstance(revoked_edges, list) or len(revoked_edges) != 2 or any(type(value) is not int for value in revoked_edges):
            raise RunStateError("station legacy revoke did not revoke exactly both edges")
        requests.extend([gateway_revoke_record, legacy_record])
        if self._connections(actor_id=self.driver_actor_id, peer=member, requests=requests) or self._connections(actor_id=member, peer=self.driver_actor_id, requests=requests):
            raise RunStateError("revoked friendship remains visible")
        fresh_data, fresh_record = self._create(self.driver_actor_id, member, expected=(201,))
        fresh_ids = set(self._valid_pending_create(fresh_data))
        requests.append(fresh_record)
        sent_data, sent_record = self._read(self.reader, "friend.mutation.requests_sent.read", actor_id=self.driver_actor_id)
        received_data, received_record = self._read(self.reader, "friend.mutation.requests_received.read", actor_id=self.driver_actor_id)
        requests.extend([sent_record, received_record])
        if self._request_row(data=sent_data, candidates=fresh_ids, sender=self.driver_actor_id, receiver=member) is None:
            raise RunStateError("fresh forward pending recreation was incomplete")
        if self._request_row(data=received_data, candidates=fresh_ids, sender=member, receiver=self.driver_actor_id) is None:
            raise RunStateError("fresh reverse pending recreation was incomplete")
        admission = self._admission(bot_id=member, actor_id=self.driver_actor_id, requests=requests)
        if admission.get("allowed") is not False or admission.get("reason") != "no_edge":
            raise RunStateError("member admission did not return to no_edge after revoke")
        data = {"gateway_deleted": False, "station_revoked_edges": revoked_edges, "fresh_request_ids": sorted(fresh_ids)}
        return self._finish(
            operation="friend.mutation.revoke",
            fixture_effect="REGISTERED_BOT_RETAINED;EDGES_REVOKED;FRESH_MUTUAL_PENDING_RETAINED", data=data, requests=requests
        )

    def run_full_lifecycle(self) -> AdapterCall:
        member, requests = self._provision_member("full-lifecycle", visibility="protected", strategy="APPROVAL")
        accepted_request, edge_id = self._accept_pair(member=member, requests=requests)
        self._pending_absent(candidates={accepted_request}, actor_id=self.driver_actor_id, requests=requests)
        self._pending_absent(candidates={accepted_request}, actor_id=member, requests=requests)
        all_data, all_record = self._read(self.reader, "friend.mutation.requests_all.read", actor_id=self.driver_actor_id)
        requests.append(all_record)
        if self._request_row(data=all_data, candidates={accepted_request}) is not None:
            raise RunStateError("accepted friend request remains pending in all-direction read")
        if not self._connections(actor_id=self.driver_actor_id, peer=member, requests=requests) or not self._connections(actor_id=member, peer=self.driver_actor_id, requests=requests):
            raise RunStateError("full-lifecycle connection is not visible from both vantages")
        admission = self._admission(bot_id=member, actor_id=self.driver_actor_id, requests=requests)
        if admission.get("allowed") is not True or admission.get("reason") != "ok" or admission.get("source") != "edge_grant":
            raise RunStateError("full-lifecycle edge admission failed")
        legacy_revoke, legacy_record = self._station_write_client().station_legacy_revoke(self.driver_actor_id)
        if legacy_revoke.get("success") is not True or legacy_revoke.get("data", {}).get("revoked_edges") is None:
            raise RunStateError("full-lifecycle station revoke failed")
        requests.append(legacy_record)
        if self._connections(actor_id=self.driver_actor_id, peer=member, requests=requests) or self._connections(actor_id=member, peer=self.driver_actor_id, requests=requests):
            raise RunStateError("full-lifecycle friendship was not revoked")
        _, restore_record = self.writer.patch_bot_friend_profile(member, visibility="public")
        fresh_data, fresh_record = self._create(self.driver_actor_id, member, expected=(201,))
        fresh_ids = self._valid_pending_create(fresh_data)
        requests.append(fresh_record)
        if not fresh_ids:
            raise RunStateError("full-lifecycle did not leave fresh pending requests")
        data = {
            "accepted_request_id": accepted_request, "edge_id": edge_id,
            "station_revoked_edges": legacy_revoke["data"]["revoked_edges"],
            "visibility_restored": "public", "fresh_request_ids": fresh_ids,
        }
        return self._finish(
            operation="friend.mutation.full_lifecycle",
            fixture_effect="REGISTERED_BOT_RETAINED;VISIBILITY_RESTORED_PUBLIC;FRESH_MUTUAL_PENDING_RETAINED", data=data, requests=requests
        )

    def run_ensure_bot(self) -> AdapterCall:
        raise self._service_gap("run_ensure_bot")

    def _openapi_binding(self, operation: str, client: Any | None = None) -> dict:
        try:
            return require_edge_case_binding(
                (client or self.writer).profile, operation)
        except (ConfigError, ValueError) as error:
            raise RunStateError(str(error)) from None

    def _openapi_call(self, client: Any, *, operation: str, path: str,
                      body: dict, expected: tuple[int, ...],
                      admit_error: bool = False) -> tuple[dict, dict]:
        data, record = client._friend_mutation_request(
            operation=operation, binding_name=operation, path=path, body=body,
            expected=expected, admit_error=admit_error,
            response_mode="public-envelope",
        )
        if record.get("http_status") not in expected:
            raise RunStateError("Edge red-case transport status differs from the reviewed route")
        return data, record

    def _human_create(self, target_actor_id: str, *, expected: tuple[int, ...],
                      message: str, admit_error: bool = False) -> tuple[dict, dict]:
        operation = "friend_connection_mutation.create"
        binding = self._openapi_binding(operation)
        body = {"to_actor": self._actor(target_actor_id), "message": message}
        return self._openapi_call(
            self.writer, operation=operation, path=binding["path"], body=body,
            expected=expected, admit_error=admit_error,
        )

    def run_tc_friend_request_create_accept(self) -> AdapterCall:
        create, create_record = self._human_create(
            self.member_actor_id, expected=(201,), message="tc friend-auth-sync preprod")
        request_ids = self._valid_pending_create(create)
        if len(request_ids) != 1:
            raise RunStateError("TC friend create must return exactly one pending request")
        request_id = request_ids[0]
        operation = "friend_connection_mutation.accept"
        binding = self._openapi_binding(operation)
        path = binding["path"].replace(
            "{request_id}", quote(request_id, safe=""))
        accepted, accept_record = self._openapi_call(
            self.writer, operation=operation, path=path, body={},
            expected=(200,),
        )
        if (not isinstance(accepted, dict) or accepted.get("status") != "approved"
                or accepted.get("request_id") != request_id
                or type(accepted.get("edge_id")) is not int):
            raise RunStateError("TC friend acceptance did not return the exact approved edge")
        self.tc_friend_request = {"request_id": request_id, "member": self.member_actor_id}
        return self._finish(
            operation="friend.mutation.tc_friend_auth_sync_create_accept",
            fixture_effect="OWNED_MEMBER_EDGE_RETAINED",
            data={"request_id": request_id, "status": "approved",
                  "member_bot_id": self.member_actor_id},
            requests=[create_record, accept_record],
        )

    def run_tc_friend_auth_sync_readdress(self) -> AdapterCall:
        state = getattr(self, "tc_friend_request", None)
        if not isinstance(state, dict) or state.get("request_id") is None:
            raise RunStateError("TC sync obligation requires its completed create/accept step")
        # BCS does not expose the backend-bound FriendAuthSync JWT on any
        # public route.  A live PASS needs the reviewed work-order receiver's
        # bounded sync receipt (principal only).  Until the credential window
        # supplies that observation, the adapter fails closed instead of
        # pretending that an approved HTTP row proves the signing re-address.
        error = RunStateError(
            "TC friend-auth sync requires a bounded external work-order receipt")
        error.gap = {
            "gap": "TC_FRIEND_AUTH_SYNC_RECEIPT_OBSERVATION",
            "singlebox_closure": False,
            "request_id": state["request_id"],
            "tc_bot_id": state["member"],
            "required_receipt": "one grant principal with iss=bcs and aud=backend",
            "approver_identity": "authenticated human cookie principal",
            "station_bot_token_env": STATION_BOT_TOKEN_ENV,
        }
        raise error

    def run_dept_scope_mixed_owner_patch(self) -> AdapterCall:
        operation = "friend_connection_mutation.visibility.write"
        binding = self._openapi_binding(operation)
        path = binding["path"].replace(
            "{bot_id}", quote(self.member_actor_id, safe=""))
        body = {
            "user_visibility": "protected",
            "friend_check_in_strategy": "DEPT_FREE",
            "friend_ext": MIXED_SCOPE["friend_ext"],
        }
        patched, record = self._openapi_call(
            self.writer, operation=operation, path=path, body=body,
            expected=(200,),
        )
        friend_ext = patched.get("friend_ext") if isinstance(patched, dict) else None
        parsed = assert_dept_scope_parse(friend_ext, {
            "no_check_scope_friend_deps": ["F4858", "蚂蚁集团-信贷事业群"],
            "view_scope_user_friend_deps": [],
            "view_scope_agent_friend_deps": ["L3480"],
        })
        self.dept_scope_state = {"mixed": parsed, "member": self.member_actor_id}
        return self._finish(
            operation="friend.mutation.dept_scope_mixed_owner_patch",
            fixture_effect="OWNER_PATCHED_PROFILE",
            data={"bot_id": self.member_actor_id, "scopes": parsed["scopes"],
                  "dropped": parsed["dropped"]},
            requests=[record],
        )

    def run_dept_scope_human_no_check(self) -> AdapterCall:
        created, record = self._human_create(
            self.member_actor_id, expected=(201,),
            message="owned-local no-check object friend_ext parse",
        )
        ids = self._valid_pending_create(created)
        if len(ids) != 1:
            raise RunStateError("Human friend create must return exactly one pending request")
        return self._finish(
            operation="friend.mutation.dept_scope_human_no_check",
            fixture_effect="HUMAN_PENDING_REQUEST_RETAINED",
            data={"request_id": ids[0], "status": "pending",
                  "parser_path": "human-no-check"},
            requests=[record],
        )

    def _dept_create(self, *, client: Any, operation: str, body: dict,
                    expected: tuple[int, ...]) -> tuple[dict, dict]:
        binding = self._openapi_binding(operation, client)
        return self._openapi_call(
            client, operation=operation, path=binding["path"], body=body,
            expected=expected, admit_error=(403,) <= expected,
        )

    def run_dept_scope_human_user_scope(self) -> AdapterCall:
        operation = "friend_connection_mutation.visibility.write"
        binding = self._openapi_binding(operation)
        path = binding["path"].replace(
            "{bot_id}", quote(self.member_actor_id, safe=""))
        patch_body = {"friend_ext": SCOPED_SHAPE["friend_ext"]}
        patched, patch_record = self._openapi_call(
            self.writer, operation=operation, path=path, body=patch_body,
            expected=(200,),
        )
        parsed = assert_dept_scope_parse(patched.get("friend_ext", {}), {
            "no_check_scope_friend_deps": ["F4858", "蚂蚁集团-信贷事业群"],
            "view_scope_user_friend_deps": ["L3480"],
            "view_scope_agent_friend_deps": ["L3480"],
        })
        created, create_record = self._dept_create(
            client=self.writer,
            operation="friend_connection_mutation.create",
            body={"to_actor": self._actor(self.member_actor_id),
                  "message": "owned-local scoped friend_ext parse"},
            expected=(201, 403),
        )
        status = None
        if create_record["http_status"] == 201:
            ids = self._valid_pending_create(created)
            if len(ids) != 1:
                raise RunStateError("Human friend create must return exactly one pending request")
            status = ids[0]
        else:
            status = "out_of_scope"
        return self._finish(
            operation="friend.mutation.dept_scope_human_user_scope",
            fixture_effect="USER_SCOPE_PATCHED",
            data={"scoped_patch_status": patch_record["http_status"],
                  "human_create_http_status": create_record["http_status"],
                  "human_create_outcome": status, "scopes": parsed["scopes"],
                  "dropped": parsed["dropped"]},
            requests=[patch_record, create_record],
        )

    def run_dept_scope_bot_agent_scope(self) -> AdapterCall:
        created, record = self._dept_create(
            client=self.station_gateway_writer,
            operation="friend_connection_mutation.create_as_bot",
            body={"from_actor": self._actor(self.driver_actor_id),
                  "to_actor": self._actor(self.member_actor_id)},
            expected=(201, 403),
        )
        status = self._valid_pending_create(created)[0] if record["http_status"] == 201 else "out_of_scope"
        return self._finish(
            operation="friend.mutation.dept_scope_bot_agent_scope",
            fixture_effect="AGENT_SCOPE_PARSED",
            data={"bot_create_http_status": record["http_status"],
                  "bot_create_outcome": status, "caller_bot_id": self.driver_actor_id,
                  "target_bot_id": self.member_actor_id},
            requests=[record],
        )
