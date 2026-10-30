"""Read-only, bounded HTTP transport. Never follow authentication redirects."""
from __future__ import annotations

import hashlib
import json
import os
import ssl
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, ProxyHandler, HTTPSHandler, build_opener

from .correlation import record_response_id
from .profile import ConfigError, browser_headers, identifier, origin, unique_keys
from .route_bindings import require_edge_case_binding
from .identity_binding import (identity_request, pilot_execution_blocker, project_authorized_human_view,
                               HUMAN_VIEW, resolve_identity_binding, human_actor_path, require_human_actor)


class ObservationError(RuntimeError):
    pass


def tls_context() -> ssl.SSLContext:
    """Build a fail-closed HTTPS trust context for local Python installations.

    macOS framework Python can point OpenSSL at an ``etc/openssl/cert.pem`` that
    was never installed. Prefer the standard OpenSSL trust store, then certifi,
    then the system CA bundle. No candidate ever disables certificate or
    hostname verification.
    """
    context = ssl.create_default_context()
    paths: list[Path] = []
    environment_path = os.environ.get("SSL_CERT_FILE")
    if environment_path:
        paths.append(Path(environment_path))
    defaults = ssl.get_default_verify_paths()
    if defaults.openssl_cafile:
        paths.append(Path(defaults.openssl_cafile))
    try:
        import certifi
        paths.append(Path(certifi.where()))
    except ImportError:
        pass
    paths.extend((Path("/etc/ssl/cert.pem"), Path("/private/etc/ssl/cert.pem")))
    for path in dict.fromkeys(paths):
        if path.is_file():
            context.load_verify_locations(cafile=str(path))
            return context
    raise ObservationError("No readable TLS CA bundle found; refusing to disable verification")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_EDGE_ADMISSION_READS = {"edge.admission.human_actor.read", "edge.admission.actor_kind.read",
                         "station.admission.read"}


_WEBHOOK_PRINCIPAL_READS = {"webhook.subscription.list", "webhook.subscription.read",
                             "webhook.subscription.deliveries.list", "webhook.delivery.read"}


@dataclass(frozen=True)
class Observation:
    data: dict
    evidence: dict


_HUMAN_COOKIE_ONLY_READS = {"environment.read", "identity.read", "identity.actor.read",
                            "network.bot_mine.read", "network.actor_list.read",
                            "network.actor_search.read", "network.bots_paged.read",
                            "network.actor_status.read",
                            "channel.bindings.list_before", "channel.bindings.by_target_empty",
                            "channel.bindings.list_after",
                            "edge.bot_search_name.read", "edge.bot_search_empty.read",
                            "structured.templates.list", "structured.template.detail",
                            "structured.group.read", "structured.definition.read",
                            "structured.run.read", "structured.run.graph.read",
                            "structured.run.node.read",
                            "structured.run.pending.human.nodes",
                            "friend.decision.member_requests.read",
                            "friend.decision.driver_requests.read",
                            "friend.decision.driver_connections.read",
                            "friend.public.driver_connections.read",
                            "friend.mutation.requests_sent.read",
                            "friend.mutation.requests_received.read",
                            "friend.mutation.requests_all.read",
                            "friend.mutation.connections.read",
                            "friend.mutation.bot_detail.read",
                            "friend.mutation.discover_denied.read", "station.register_token.read",
                            "station.bot_detail.read", "station.discover.collaborate.read",
                            "station.discover.role_no_org.read",
                            "station.discover.org_no_requester.read",
                            "register.token.read", "register.bot.readback"}

_MUTATION_READ_BINDINGS = {
    "friend.mutation.requests_sent.read": "friend_connection_mutation.requests_sent.read",
    "friend.mutation.requests_received.read": "friend_connection_mutation.requests_received.read",
    "friend.mutation.requests_all.read": "friend_connection_mutation.requests_all.read",
    "friend.mutation.connections.read": "friend_connection_mutation.connections.read",
    "friend.mutation.bot_detail.read": "friend_connection_mutation.bot_detail.read",
    "friend.mutation.discover_denied.read": "friend_connection_mutation.discover_denied.read",
    "station.register_token.read": "friend_connection_mutation.register_token.read",
    "station.admission.read": "friend_connection_mutation.station_admission.read",
    "station.discover.collaborate.read": "friend_connection_mutation.station_discover_collaborate.read",
    "station.discover.role_no_org.read": "friend_connection_mutation.station_discover_role_no_org.read",
    "station.discover.org_no_requester.read": "friend_connection_mutation.station_discover_org_no_requester.read",
    "station.bot_detail.read": "friend_connection_mutation.bot_detail.read",
}


class ReadClient:
    def __init__(self, profile: dict, *, local_auth=None):
        self.profile = profile
        self.base = origin(profile["entrypoint"]["origin"], profile["environment"]["tier"])
        self.local_auth = local_auth
        owned_local = profile["auth"] == {"kind": "owned-local"}
        if owned_local or local_auth is not None:
            from .local_auth import require_local_auth
            require_local_auth(profile, local_auth)
        self.headers = browser_headers(profile)
        for header, ref in profile["auth"].get("header_env", {}).items():
            value = os.environ.get(ref, "")
            if not value or not value.isascii() or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ConfigError("auth: missing or invalid credential environment reference")
            self.headers[header] = value
        self.opener = build_opener(ProxyHandler({}), HTTPSHandler(context=tls_context()), NoRedirect()) if owned_local else build_opener(HTTPSHandler(context=tls_context()), NoRedirect())
        self.evidence: list[dict] = []

    def read(self, operation: str, bot_id: str = "", *, policy_authority=None,
             direction: str = "", group_id: str = "", run_id: str = "",
             subscription_id: str = "", actor_id: str = "",
             target_actor_id: str = "", delivery_id: str = "",
             fail_open: bool = False) -> Observation:
        # No general URL or HTTP method parameter: Preflight cannot call arbitrary endpoints.
        envelope = True
        if operation == "identity.read":
            if resolve_identity_binding(self.profile) == HUMAN_VIEW:
                # A human_ prefix alone is not a type/ownership proof. Check the
                # exact existing actor before testing the exclusive perspective.
                self.read("identity.actor.read")
            binding = identity_request(self.profile, owned_local=self.local_auth is not None)
            path = binding["path"]
            envelope = binding["response"] != "plain-user-info"
        elif operation == "identity.actor.read":
            if resolve_identity_binding(self.profile) != HUMAN_VIEW:
                raise ObservationError("Human actor read requires the reviewed self-view binding")
            from .profile import require_resolved
            require_resolved({'human': self.profile['human'],
                              'expected_env': self.profile['environment']['expected_bcs_env']})
            path = human_actor_path(self.profile)
        elif operation == "delivery.policy.read":
            from .delivery_policy_check import PolicyReadReview, PATH
            if type(policy_authority) is not PolicyReadReview or policy_authority.profile != self.profile:
                raise ObservationError('policy read requires explicit authority for this exact native profile')
            policy_authority.validate()
            path, envelope = PATH, False
        elif operation == "environment.read":
            envelope = self.profile["entrypoint"]["manifest_face"] == "internal-v1"
            path = "/api/v1/collaboration/manifest" if envelope else "/manifest"
        elif operation == "bot.read":
            blocker = pilot_execution_blocker(self.profile)
            if blocker:
                raise ObservationError(blocker)
            allowed = {a["bot_id"] for a in self.profile["actors"].values()}
            if bot_id not in allowed:
                raise ObservationError("unbound Bot target refused")
            if self.profile["entrypoint"]["kind"] == "bcs-direct":
                # Station legacy face deploys the un-prefixed bot detail route
                # (live-verified 200 with the reviewed ACE trio); the
                # public-v1 route resolves through the gateway ACE domain.
                path = "/bots/" + quote(bot_id, safe="")
                envelope = False
            else:
                path = "/openapi/v1/collaboration/bots/" + quote(bot_id, safe="")
        elif operation in {"edge.admission.human_actor.read", "edge.admission.actor_kind.read"}:
            binding_name = ("edge_admission_human_actor.read"
                             if operation == "edge.admission.human_actor.read"
                             else "edge_admission_actor_kind.read")
            binding = require_edge_case_binding(self.profile, binding_name)
            path = binding["path"]
            envelope = False
            self.edge_admission_binding = binding
        elif operation == "edge.friend_requests.read":
            if direction not in {"received", "sent", "all"}:
                raise ObservationError("unsupported friend-request direction")
            binding = require_edge_case_binding(self.profile, "friend_requests.read")
            if self.profile["environment"]["tier"] == "preprod":
                path = (binding["path"]
                        + "?actor_type=bot&actor_id="
                        + binding["caller_actor_id"]
                        + "&direction=" + direction
                        + "&page=1&page_size=20")
            else:
                path = binding["path"] + "?direction=" + direction
        elif operation in {"friend.decision.member_requests.read",
                           "friend.decision.driver_requests.read",
                           "friend.decision.driver_connections.read"}:
            binding_name = {
                "friend.decision.member_requests.read": "friend_connection_decision.member_requests.read",
                "friend.decision.driver_requests.read": "friend_connection_decision.driver_requests.read",
                "friend.decision.driver_connections.read": "friend_connection_decision.driver_connections.read",
            }[operation]
            binding = require_edge_case_binding(self.profile, binding_name)
            path = binding["path"]
        elif operation == "friend.public.driver_connections.read":
            binding = require_edge_case_binding(
                self.profile, "friend_connection_public_accept.driver_connections.read"
            )
            path = binding["path"]
        elif operation == "edge.friend_connections.read":
            binding = require_edge_case_binding(self.profile, "friend_connections.read")
            path = (binding["path"]
                    + "?actor_type=bot&actor_id="
                    + binding["caller_actor_id"]
                    + "&page=1&page_size=100")
        elif operation == "network.bot_mine.read":
            binding = require_edge_case_binding(self.profile, "network_bot_mine.read")
            path = binding["path"]
        elif operation == "network.actor_list.read":
            binding = require_edge_case_binding(self.profile, "network_actor_list.read")
            path = binding["path"]
        elif operation == "network.actor_search.read":
            binding = require_edge_case_binding(self.profile, "network_actor_search.read")
            path = binding["path"]
        elif operation == "network.bots_paged.read":
            binding = require_edge_case_binding(self.profile, "network_bots_paged.read")
            path = binding["path"]
        elif operation == "network.actor_status.read":
            binding = require_edge_case_binding(self.profile, "network_actor_status.read")
            path = binding["path"]
            if binding.get("target_actor_id") != self.profile["actors"]["driver"]["bot_id"]:
                raise ObservationError("actor-status read target is not the dedicated driver Bot")
            self.actor_status_binding = binding
        elif operation in {"channel.bindings.list_before", "channel.bindings.list_after"}:
            binding = require_edge_case_binding(self.profile, "channel_bindings.list")
            path = binding["path"]
        elif operation == "channel.bindings.by_target_empty":
            binding = require_edge_case_binding(self.profile, "channel_bindings.by_target")
            path = binding["path"]
            if binding.get("target_actor_id") != self.profile["actors"]["driver"]["bot_id"]:
                raise ObservationError("channel-binding target is not the dedicated driver Bot")
            self.channel_binding_target = binding
        elif operation in {"structured.templates.list", "structured.template.detail"}:
            binding = require_edge_case_binding(self.profile, (
                "structured_templates.list"
                if operation == "structured.templates.list"
                else "structured_templates.detail"
            ))
            path = binding["path"]
        elif operation in {"structured.group.read", "structured.definition.read"}:
            binding = require_edge_case_binding(self.profile, operation)
            if not isinstance(group_id, str) or not group_id:
                raise ObservationError("structured group route requires a bound group ID")
            path = binding["path"].replace("{group_id}", quote(group_id, safe=""))
            envelope = binding["response"] == "public-envelope"
        elif operation in {"structured.run.read", "structured.run.graph.read",
                           "structured.run.node.read",
                           "structured.run.pending.human.nodes"}:
            binding = require_edge_case_binding(self.profile, operation)
            if not isinstance(run_id, str) or not run_id:
                raise ObservationError("structured run route requires a bound run ID")
            path = binding["path"].replace("{run_id}", quote(run_id, safe=""))
            envelope = binding["response"] == "public-envelope"
        elif operation == "edge.bot_search_name.read":
            binding = require_edge_case_binding(self.profile, "edge_bot_search_name.read")
            path = binding["path"]
        elif operation == "edge.bot_search_empty.read":
            binding = require_edge_case_binding(self.profile, "edge_bot_search_empty.read")
            path = binding["path"]
        elif operation == "register.token.read":
            binding = require_edge_case_binding(self.profile, "register.token.read")
            path = binding["path"]
        elif operation == "register.bot.readback":
            binding = require_edge_case_binding(self.profile, "register.bot.readback")
            if not isinstance(bot_id, str) or not bot_id:
                raise ObservationError("registered Bot readback requires a bound Bot ID")
            path = binding["path"].replace("{bot_uuid}", quote(bot_id, safe=""))
        elif operation in _MUTATION_READ_BINDINGS:
            # These are frozen route templates: substitution happens only after
            # the profile binding has matched the exact literal template.
            binding = require_edge_case_binding(self.profile, _MUTATION_READ_BINDINGS[operation])
            path = binding["path"]
            envelope = binding["response"] == "public-envelope"
            if "{actor_id}" in path:
                identifier(actor_id, f"{operation}.actor_id")
                path = path.replace("{actor_id}", quote(actor_id, safe=""))
            if "{target_actor_id}" in path:
                identifier(target_actor_id, f"{operation}.target_actor_id")
                path = path.replace("{target_actor_id}", quote(target_actor_id, safe=""))
            if "{bot_uuid}" in path:
                if operation == "station.admission.read" and target_actor_id:
                    bound_bot_id = identifier(target_actor_id, f"{operation}.target_actor_id")
                else:
                    bound_bot_id = identifier(bot_id, f"{operation}.bot_id")
                path = path.replace("{bot_uuid}", quote(bound_bot_id, safe=""))
            if "{bot_id}" in path:
                bound_bot_id = identifier(bot_id, f"{operation}.bot_id")
                path = path.replace("{bot_id}", quote(bound_bot_id, safe=""))
        elif operation in _WEBHOOK_PRINCIPAL_READS:
            # The reviewed public-v1 Event Subscription read core. Every
            # route is declared in the profile's opt-in cross-environment
            # Case bindings and authenticated with an exact Gateway
            # Principal JWT, never with a Cookie or a Bot token.
            binding = require_edge_case_binding(self.profile, operation)
            path = binding["path"]
            if "{group_id}" in path:
                if not isinstance(group_id, str) or not group_id:
                    raise ObservationError("webhook subscription list requires a bound group ID")
                path = path.replace("{group_id}", quote(group_id, safe=""))
            if "{subscription_id}" in path:
                if not isinstance(subscription_id, str) or not subscription_id:
                    raise ObservationError("webhook subscription route requires a bound subscription ID")
                path = path.replace("{subscription_id}", quote(subscription_id, safe=""))
            if "{delivery_id}" in path:
                if not isinstance(delivery_id, str) or not delivery_id:
                    raise ObservationError("webhook delivery read requires a bound delivery ID")
                path = path.replace("{delivery_id}", quote(delivery_id, safe=""))
            self.webhook_subscription_binding = binding
        else:
            raise ObservationError("unsupported read-only operation")
        request_id = "e2e-check-" + uuid.uuid4().hex
        if operation in _WEBHOOK_PRINCIPAL_READS:
            from .local_auth import minted_principal_headers
            auth_headers = minted_principal_headers(self.profile, self.local_auth)
        else:
            auth_headers = self.local_auth.headers(self.profile) if self.local_auth is not None else self.headers
        human_cookie_read = operation in _HUMAN_COOKIE_ONLY_READS
        if operation in _EDGE_ADMISSION_READS:
            if binding["auth"] == "human_cookie":
                human_cookie_read = True
        elif (operation in {"edge.friend_requests.read", "edge.friend_connections.read"}
              and binding["auth"] == "human_cookie"):
            # Station Gateway admission happens through the browser context; the
            # exact actor query remains the dedicated-driver scope. Do not send
            # a Bot Authorization bearer to these public read routes.
            human_cookie_read = True
        if human_cookie_read:
            auth_headers = {header: value for header, value in auth_headers.items()
                            if header != "Authorization"}
        unauthenticated = (operation in _EDGE_ADMISSION_READS
                            and binding["auth"] == "unauthenticated")
        request = Request(self.base + path, method="GET", headers={
            **({} if unauthenticated else auth_headers),
            "Accept": "application/json", "X-Request-ID": request_id,
        })
        start = time.monotonic()
        record = {"operation": operation, "method": "GET", "path": path,
                  "submitted_request_id": request_id, "http_status": None}
        if unauthenticated:
            record["credentials_sent"] = False
            record["authorization_sent"] = False
        elif human_cookie_read:
            record["credentials_sent"] = "Cookie" in auth_headers
            record["authorization_sent"] = "Authorization" in auth_headers
            record["identity_kind"] = "human_cookie"
        if operation in {"structured.group.read", "structured.definition.read"}:
            record["bound_group_id"] = group_id
        elif operation in {"structured.run.read", "structured.run.graph.read",
                           "structured.run.node.read",
                           "structured.run.pending.human.nodes"}:
            record["bound_run_id"] = run_id
        if operation == "network.actor_status.read":
            record["target_actor_id"] = self.actor_status_binding["target_actor_id"]
        if operation == "channel.bindings.by_target_empty":
            record.update({
                "target_actor_id": self.channel_binding_target["target_actor_id"],
                "requested_channel_type": "e2e-uninstalled-channel",
            })
        if operation == "register.bot.readback":
            record["bound_bot_id"] = bot_id
        if operation in _MUTATION_READ_BINDINGS:
            if actor_id:
                record["bound_actor_id"] = actor_id
            if target_actor_id:
                record["bound_target_actor_id"] = target_actor_id
            if (operation in {"friend.mutation.bot_detail.read", "station.bot_detail.read"}
                    or (operation == "station.admission.read" and not target_actor_id)):
                record["bound_bot_id"] = bot_id
        if operation in _WEBHOOK_PRINCIPAL_READS:
            record["principal_sent"] = True
            record["credentials_sent"] = False
            record["authorization_sent"] = False
            if operation == "webhook.subscription.list":
                record["bound_group_id"] = group_id
            elif operation in {"webhook.subscription.read",
                                 "webhook.subscription.deliveries.list"}:
                record["bound_subscription_id"] = subscription_id
            elif operation == "webhook.delivery.read":
                record["bound_delivery_id"] = delivery_id
        self.evidence.append(record)
        try:
            try:
                response = self.opener.open(request, timeout=self.profile["policy"]["timeout_seconds"])
            except HTTPError as error:
                response = error
            with response:
                record["http_status"] = response.code
                echoed = response.headers.get("x-request-id", "")
                # A server response cannot smuggle credentials or arbitrary strings into reports.
                record["request_id_echoed"] = echoed == request_id
                record_response_id(record, echoed, source='HEADER', sensitive_values=auth_headers.values())
                if response.code != 200:
                    if (not fail_open or operation not in _MUTATION_READ_BINDINGS
                            or not 400 <= response.code <= 599):
                        raise ObservationError(f"HTTP {response.code}; no redirect or route fallback attempted")
                    # Explicit fail-open is an assertion mode, not general error
                    # recovery. It admits only the reviewed negative mutation
                    # reads and returns their bounded JSON diagnostic.
                    raw = response.read(self.profile["policy"]["max_response_bytes"] + 1)
                    if len(raw) > self.profile["policy"]["max_response_bytes"]:
                        raise ObservationError("response exceeds configured size limit")
                    from .profile import unique_keys as strict_unique_keys
                    body = json.loads(raw, object_pairs_hook=strict_unique_keys)
                    if not isinstance(body, dict):
                        raise ObservationError("fail-open diagnostic must be a JSON object")
                    if envelope:
                        if type(body.get("code")) is not int:
                            raise ObservationError("public fail-open envelope is invalid")
                        # A reviewed negative gateway diagnostic must carry
                        # the code; its data projection is optional (the live
                        # 401 human-discovery gap projects null data).
                        data = body.get("data")
                        if data is not None and not isinstance(data, dict):
                            raise ObservationError("public fail-open data must be an object when present")
                        response_id = body.get("request_id")
                        if isinstance(response_id, str) and response_id:
                            if not record_response_id(record, response_id, source="ENVELOPE",
                                                        sensitive_values=auth_headers.values()):
                                raise ObservationError("public fail-open response request ID is invalid")
                        projected = {
                            "code": body["code"],
                            "message": body.get("message", ""),
                            "data": data,
                        }
                        error_code = data.get("error_code") if isinstance(data, dict) else None
                        if isinstance(error_code, str) and error_code:
                            projected["error"] = error_code
                        body = projected
                    return Observation(body, record)
                limit = self.profile["policy"]["max_response_bytes"]
                raw = response.read(limit + 1)
                if len(raw) > limit:
                    raise ObservationError("response exceeds configured size limit")
                if operation == 'delivery.policy.read':
                    from .profile import unique_keys
                    body = json.loads(raw, object_pairs_hook=unique_keys)
                else:
                    body = json.loads(raw)
                pending_human_nodes = operation == "structured.run.pending.human.nodes"
                if not (isinstance(body, dict)
                        or (pending_human_nodes and isinstance(body, list))):
                    raise ObservationError("expected JSON object")
                if envelope:
                    if (type(body.get("code")) is not int or body["code"] != 20000
                            or not isinstance(body.get("message"), str)
                            or not isinstance(body.get("request_id"), str)
                            or not body.get("request_id") or "data" not in body):
                        raise ObservationError("public response envelope is invalid")
                    if not record_response_id(record, body["request_id"], source='ENVELOPE',
                                              sensitive_values=auth_headers.values()):
                        raise ObservationError('public response request ID is invalid')
                    body = body.get("data")
                if pending_human_nodes:
                    # The reviewed bot-only workflow must expose an empty
                    # pending-Human array on both public-envelope and station-direct
                    # faces; a bare array is projected so the pipeline keeps one
                    # response shape, and any entry is drift.
                    if not isinstance(body, list) or body:
                        raise ObservationError(
                            "pending-human-nodes response must be an empty array")
                    body = {"items": []}
                if not isinstance(body, dict):
                    raise ObservationError("expected object in response data")
                if operation == "station.admission.read" and binding["response"] == "admission-json":
                    # Live bcs-pre shape carries reason_code and per-grant source;
                    # project the reviewed canonical reason/source keys without
                    # dropping any wire field.
                    grants = body.get("grants")
                    grant_source = (
                        grants[0].get("source")
                        if isinstance(grants, list) and grants
                        and isinstance(grants[0], dict)
                        and isinstance(grants[0].get("source"), str)
                        else None
                    )
                    body = {**body, "reason": body.get("reason_code"), "source": grant_source}
                    record["admission_projection"] = "grants-normalized"
                if operation == "identity.actor.read":
                    require_human_actor(self.profile, body)
                    body = {key: body[key] for key in ('bot_id', 'kind', 'created_by', 'env')}
                if operation == "identity.read" and binding["response"] == "authorized-human-view":
                    body = project_authorized_human_view(self.profile, body)
                    record["identity_basis"] = "server-authorized-human-view"
                    record["identity_returned_directly"] = False
                    record["human_actor_checked"] = True
                if (operation == "identity.read" and "identity_binding" in self.profile
                        and binding["response"] == "plain-user-info"):
                    user_id = body.get("user_id")
                    if (not isinstance(user_id, str) or not user_id.strip() or len(user_id) > 128
                            or any(ord(char) < 32 or ord(char) == 127 for char in user_id)):
                        raise ObservationError("native identity response requires a bounded top-level user_id")
                if operation == 'delivery.policy.read':
                    from .delivery_policy_projection import project_policy
                    body = project_policy(body, self.profile)
                    policy_authority.validate()
                return Observation(body, record)
        except (OSError, URLError, ValueError, UnicodeError):
            # Never include exception strings, response bodies, URLs with credentials, or headers.
            raise ObservationError("network, TLS, timeout or JSON decoding error") from None
        finally:
            record["elapsed_ms"] = round((time.monotonic() - start) * 1000, 2)
    def read_platform_asset(self, target: str) -> Observation:
        """Fetch one pre-bound JavaScript asset without credentials or redirects.

        The caller supplies a route binding that already selects either the station
        internal-assets path or an explicit HTTPS CDN URL. The manifest response
        must independently equal that binding before this method is called.
        """
        if (not isinstance(target, str) or not target or len(target) > 2048
                or any(ord(char) < 32 or ord(char) == 127 for char in target)):
            raise ObservationError("platform asset target is invalid")
        binding = require_edge_case_binding(self.profile, "network_platform_asset.read")
        if target != binding["path"]:
            raise ObservationError("platform asset target is not the pre-bound route")
        try:
            parsed = urlsplit(target)
        except ValueError:
            raise ObservationError("platform asset target is invalid") from None
        if target.startswith("/"):
            if (not target.startswith("/api/v1/collaboration/assets/bcsPanel/")
                    or ".." in target or target.endswith("/") or parsed.query or parsed.fragment):
                raise ObservationError("platform asset target is an unbound route")
            url = self.base + target
        else:
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                    or parsed.password is not None or parsed.query or parsed.fragment
                    or not parsed.path.endswith(".js") or ".." in parsed.path):
                raise ObservationError("platform asset target is an unbound HTTPS URL")
            url = target
        request_id = "e2e-check-" + uuid.uuid4().hex
        request = Request(url, method="GET", headers={
            "Accept": "application/javascript", "X-Request-ID": request_id,
        })
        start = time.monotonic()
        record = {"operation": "network.platform_asset.read", "method": "GET",
                  "path": target, "submitted_request_id": request_id,
                  "http_status": None, "credentials_sent": False}
        self.evidence.append(record)
        try:
            try:
                response = self.opener.open(request, timeout=self.profile["policy"]["timeout_seconds"])
            except HTTPError as error:
                response = error
            with response:
                record["http_status"] = response.code
                if response.code != 200:
                    raise ObservationError(f"HTTP {response.code}; no redirect or route fallback attempted")
                content_type = response.headers.get("Content-Type", "").lower().strip()
                media_type, separator, parameters = content_type.partition(";")
                if media_type != "application/javascript" or (
                        separator and parameters.strip() not in {"charset=utf-8"}):
                    raise ObservationError("platform asset media type differs from the reviewed contract")
                raw = response.read(self.profile["policy"]["max_response_bytes"] + 1)
            if (len(raw) < 1024 or len(raw) > self.profile["policy"]["max_response_bytes"]):
                raise ObservationError("platform asset size is outside the reviewed bounds")
            digest = hashlib.sha256(raw).hexdigest()
            data = {"asset_url": target, "content_type": content_type,
                    "size": len(raw), "sha256": digest}
            record.update({"content_type": content_type, "size": len(raw),
                            "sha256": digest, "request_id_echoed": False})
            return Observation(data, record)
        except (OSError, URLError, ValueError, UnicodeError):
            raise ObservationError("network, TLS, timeout or decoding error") from None
        finally:
            record["elapsed_ms"] = round((time.monotonic() - start) * 1000, 2)

    def attempt_channel_binding_create(self) -> Observation:
        """Attempt only the reviewed uninstalled-channel create and export its 400.

        This is not a general write helper. A 201 response is a contract failure and
        is never retried, silently accepted, or routed to another face.
        """
        binding = require_edge_case_binding(self.profile, "channel_bindings.create_denied")
        driver = self.profile["actors"]["driver"]["bot_id"]
        if binding.get("target_actor_id") != driver:
            raise ObservationError("channel-binding target differs from the dedicated driver Bot")
        if binding["path"] != "/openapi/v1/collaboration/channels/bindings":
            raise ObservationError("channel-binding create route differs from its contract")
        request_id = "e2e-channel-" + uuid.uuid4().hex
        auth_headers = {header: value for header, value in self.headers.items()
                        if header != "Authorization"}
        data = json.dumps({
            "channel_type": "e2e-uninstalled-channel",
            "account_ref": "e2e-uninstalled-channel-" + request_id,
            "target": {"bot": {"bot_id": driver}},
            "outbound_visibility": "full_transcript",
            "config": {"mode": "story"},
        }, separators=(",", ":")).encode()
        request = Request(self.base + binding["path"], method="POST", data=data,
                          headers={**auth_headers, "Accept": "application/json",
                                   "Content-Type": "application/json",
                                   "X-Request-ID": request_id})
        start = time.monotonic()
        record = {"operation": "channel.bindings.create_denied", "method": "POST",
                  "path": binding["path"], "target_actor_id": driver,
                  "requested_channel_type": "e2e-uninstalled-channel",
                  "submitted_request_id": request_id, "http_status": None,
                  "credentials_sent": "Cookie" in auth_headers,
                  "authorization_sent": "Authorization" in auth_headers}
        self.evidence.append(record)
        try:
            try:
                response = self.opener.open(request, timeout=self.profile["policy"]["timeout_seconds"])
            except HTTPError as error:
                response = error
            with response:
                record["http_status"] = response.code
                echoed = response.headers.get("x-request-id", "")
                record["request_id_echoed"] = echoed == request_id
                record_response_id(record, echoed, source="HEADER",
                                   sensitive_values=auth_headers.values())
                if response.code != 400:
                    raise ObservationError(f"HTTP {response.code}; expected reviewed rejection without fallback")
                raw = response.read(self.profile["policy"]["max_response_bytes"] + 1)
            if len(raw) > self.profile["policy"]["max_response_bytes"]:
                raise ObservationError("response exceeds configured size limit")
            payload = json.loads(raw, object_pairs_hook=unique_keys)
            if (not isinstance(payload, dict)
                    or type(payload.get("code")) is not int or payload["code"] != 40000
                    or not isinstance(payload.get("message"), str)
                    or not isinstance(payload.get("request_id"), str)
                    or not payload["request_id"] or "data" not in payload):
                raise ObservationError("public channel error envelope is invalid")
            if not record_response_id(record, payload["request_id"], source="ENVELOPE",
                                       sensitive_values=auth_headers.values()):
                raise ObservationError("public channel error request ID is invalid")
            body = payload.get("data")
            if (not isinstance(body, dict)
                    or not isinstance(body.get("error_code"), str)
                    or body["error_code"] not in {"invalid_request", "invalid_channel_params"}):
                raise ObservationError("channel-binding rejection reason is outside the public contract")
            return Observation({"error_code": body["error_code"]}, record)
        except (OSError, URLError, ValueError, UnicodeError):
            raise ObservationError("network, TLS, timeout or JSON decoding error") from None
        finally:
            record["elapsed_ms"] = round((time.monotonic() - start) * 1000, 2)

    def read_bot_query(self, operation: str) -> Observation:
        """Execute the single POST-based read operation declared by a Case."""
        if operation != "network.bot_query.read":
            raise ObservationError("unsupported POST-based read operation")
        binding = require_edge_case_binding(self.profile, "network_bot_query.read")
        path = binding["path"]
        targets = binding["target_actor_ids"]
        if (not isinstance(targets, list) or len(targets) != 2
                or any(not isinstance(target, str) or not target for target in targets)
                or len(set(targets)) != 2):
            raise ObservationError("bot query target binding is invalid")
        request_id = "e2e-check-" + uuid.uuid4().hex
        auth_headers = {header: value for header, value in self.headers.items()
                        if header != "Authorization"}
        data = json.dumps({"bot_ids": targets}, separators=(",", ":")).encode()
        request = Request(
            self.base + path, method="POST", data=data,
            headers={**auth_headers, "Accept": "application/json",
                     "Content-Type": "application/json", "X-Request-ID": request_id},
        )
        start = time.monotonic()
        record = {"operation": operation, "method": "POST", "path": path,
                  "submitted_request_id": request_id, "http_status": None}
        self.evidence.append(record)
        try:
            try:
                response = self.opener.open(request, timeout=self.profile["policy"]["timeout_seconds"])
            except HTTPError as error:
                response = error
            with response:
                record["http_status"] = response.code
                echoed = response.headers.get("x-request-id", "")
                record["request_id_echoed"] = echoed == request_id
                record_response_id(record, echoed, source='HEADER', sensitive_values=auth_headers.values())
                if response.code != 200:
                    raise ObservationError(f"HTTP {response.code}; no redirect or route fallback attempted")
                limit = self.profile["policy"]["max_response_bytes"]
                raw = response.read(limit + 1)
                if len(raw) > limit:
                    raise ObservationError("response exceeds configured size limit")
                body = json.loads(raw, object_pairs_hook=unique_keys)
                if (not isinstance(body, dict)
                        or type(body.get("code")) is not int or body["code"] != 20000
                        or not isinstance(body.get("message"), str)
                        or not isinstance(body.get("request_id"), str)
                        or not body.get("request_id") or "data" not in body):
                    raise ObservationError("public response envelope is invalid")
                if not record_response_id(record, body["request_id"], source='ENVELOPE',
                                          sensitive_values=auth_headers.values()):
                    raise ObservationError('public response request ID is invalid')
                data = body.get("data")
                if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                    raise ObservationError("bot query response is not a bounded object page")
                if len(data["items"]) > 100:
                    raise ObservationError("bot query response exceeds its bounded page contract")
                return Observation(data, record)
        except (OSError, URLError, ValueError, UnicodeError):
            raise ObservationError("network, TLS, timeout or JSON decoding error") from None
        finally:
            record["elapsed_ms"] = round((time.monotonic() - start) * 1000, 2)
