# src/backend/tests/framework/flow_coverage.py
"""E2 coverage matrix + E3 exempt list (Plan B).

covered_modules(): union of every FlowCase.covers.
all_core_modules(): the core business modules under agentclaw/core/.
SINGLEBOX_E2E_EXEMPT: modules explicitly NOT yet covered by an e2e flow,
each with a reason. First pass: ALL core modules are exempt (no real flows
yet); Plan C removes one each time a flow covers it. Drain to zero = full
e2e coverage. Exempt = "we explicitly decided not to run this in single box
(yet)", NOT "covered".
"""
from __future__ import annotations

import pkgutil
from pathlib import Path

import agentclaw.community.core as _core_pkg
from tests.community.framework.flow import FlowCase


def covered_modules(flows: list[FlowCase]) -> set[str]:
    out: set[str] = set()
    for f in flows:
        out.update(f.covers)
    return out


# Structural packages under ``core/`` that are not business modules and so are
# not part of the E3 denominator. ``repository`` holds the Protocol contracts and
# the ORM bodies shared by every business module; it carries no flow of its own,
# and its bodies are exercised by the flows of the modules that consume it. It is
# excluded here rather than added to SINGLEBOX_E2E_EXEMPT because exempt means
# "not covered yet" — these ARE covered, just not nameable as a `covers` entry.
#
# ``bot_config_surface`` is the same shape and is here for the same reason. It is
# an index: it names the checks each bot-config category enforces, every one of
# which is defined in the package that owns that category's domain and imported
# by reference. Its README states outright that it must not grow logic, so there
# is no behaviour of its own for a flow to drive — the resources, mcp and
# skill-centre flows already exercise every object it names, through the routers
# that call them. Adding it to SINGLEBOX_E2E_EXEMPT instead would claim it is
# uncovered, and would need a "drain when…" reason that nothing could ever
# satisfy: no flow can cover an index directly. If this module ever does grow
# behaviour of its own, that is the moment it stops belonging here.
#
# ``ports`` is the third of this shape: outbound port contracts, owned by the
# caller and published where both caller and implementer can reach them. Every
# member is a ``Protocol`` of ``@abstractmethod`` stubs with no bodies, so there
# is nothing for a flow to drive — what a port describes is executed by whichever
# module implements it, under that module's own flow. Exempting it instead would
# claim it is uncovered and demand a "drain when…" reason no flow could satisfy.
_STRUCTURAL_NON_BUSINESS: frozenset[str] = frozenset(
    {"repository", "bot_config_surface", "ports"}
)


def all_core_modules() -> set[str]:
    """Top-level business module package names under agentclaw/core/."""
    regular_packages = {
        mi.name
        for mi in pkgutil.iter_modules(_core_pkg.__path__)
        if mi.ispkg and not mi.name.startswith("_")
    }
    namespace_packages = {
        child.name
        for root in _core_pkg.__path__
        for child in Path(root).iterdir()
        if child.is_dir()
        and not child.name.startswith("_")
        and child.name not in regular_packages
    }
    return (regular_packages | namespace_packages) - _STRUCTURAL_NON_BUSINESS


# First pass: all core modules exempt (no real e2e flows exist yet, those
# are Plan C). Each entry: module → reason. Plan C removes one per flow.
#
# This is an EXPLICIT, auditable manifest — NOT derived from
# all_core_modules() at import time. Deriving it would make Task 5's
# covered-or-exempt guard tautological and defeat the drain mechanism.
# Plan C edits this dict by hand, deleting one key per flow it lands.
#
# 这些 key 必须覆盖 all_core_modules() 中尚无真业务流的模块。
_EXEMPT_REASON = "Plan B 基建期：尚无真业务流覆盖，待 Plan C 排水"
_TASK_QUEUE_EXEMPT_REASON = (
    "Internal infra component with no HTTP/router surface (in-process worker + repo); "
    "covered by unit/integration tests, not an e2e flow."
)
_GATEWAY_PRINCIPAL_EXEMPT_REASON = (
    "Verifies a token only the gateway can mint, so an e2e flow needs a gateway "
    "in the box signing with the shared key; singlebox runs the backend alone and "
    "the public surface it guards answers 401 by design until that exists. Covered "
    "by unit tests that mint tokens with the gateway's own claim shape plus the "
    "HTTP seam tests. Drain this when singlebox fronts the backend with a gateway."
)
_SESSION_RESOURCES_EXEMPT_REASON = (
    "Requires the Frontend upload, BaaS transfer, and Engine callback chain; "
    "the unpublished BaaS pull contract remains fail-closed and this change "
    "covers the module with core and HTTP API tests."
)

_RUNTIME_BINDING_EXEMPT_REASON = (
    "Read-only binding selection used only by the Session File OpenAPI upload "
    "intent. A real flow requires a signed OpenAPI principal and the same "
    "Frontend/BaaS/Engine upload lifecycle as session_resources; covered by "
    "core resolver and HTTP endpoint tests. Drain when that lifecycle can run "
    "in singlebox."
)

_TASK_FRAMEWORK_EXEMPT_REASON = (
    "Task goal-driven execution framework skeleton (core/task). No HTTP/router or DI surface is wired yet (no adapters/http/openapi_v1/task/, no di/modules/task_module.py), so there is no endpoint for an e2e flow to drive. Covered by domain/unit tests on the graph, planner, dispatcher, runner, and harness as each lands. Drain this when a router + DI provider expose the TaskService facade over a real singlebox stack."
)

_ENGINE_RUNTIME_EXEMPT_REASON = (
    "TEMPORARY, and blocked twice over. FIRST, on the auth workstream — not on "
    "this module: every /openapi/v1 route answers 401 in singlebox, so a flow "
    "could only assert 401s and would prove nothing. The verifier has landed "
    "and require_principal is real; what is still missing is a minter. It "
    "accepts only a token signed with the gateway's key, singlebox runs the "
    "backend without a gateway, and singlebox ships no local signing key "
    "there — which the verifier treats as deny. That is the same blocker, and "
    "deliberately the same answer, as _GATEWAY_PRINCIPAL_EXEMPT_REASON above: "
    "the auth workstream chose to wait for a gateway in the box rather than "
    "have tests forge tokens against a shared key, and this module follows that "
    "ruling rather than inventing a second one. SECOND, on the simulator behind "
    "the seam. Review corrected an earlier version of this note that named auth "
    "as the only blocker: singlebox binds InMemoryDeviceAdapterTransport, and "
    "cron does have a real flow over that same transport, but the simulator "
    "implements exactly the skills-layout probe and the /api/cron family — "
    "every other path falls through to {'success': False, 'unhandled path'} "
    "(plugins/local/device_adapter_transport.py). So once the minter lands, all "
    "16 routes would relay a failure envelope and answer 502 rather than "
    "exercising anything. Draining therefore needs BOTH: a gateway in the box, "
    "and either engine routes added to the simulator or a live transport bound "
    "in that profile. Covered meanwhile by relay/connection unit tests, "
    "endpoint tests per group, and a 16-route cross-tenant isolation sweep. "
    "Drain the auth half together with _GATEWAY_PRINCIPAL_EXEMPT_REASON, which "
    "is the same event that unblocks the whole track's definition of done."
)

_BOT_APP_GRANT_EXEMPT_REASON = (
    "TEMPORARY, and blocked on the same single thing as "
    "_GATEWAY_PRINCIPAL_EXEMPT_REASON above rather than on anything in this "
    "module. All four of its routes are /openapi/v1 and require a signed "
    "principal; two of them require an App identity as well, which only a "
    "gateway can mint. Singlebox runs the backend without a gateway and ships "
    "no local signing key, so a flow here could assert 401s and nothing else "
    "— and for the grant and application-view routes it could not even "
    "construct the App half of the request. Following the auth workstream's "
    "ruling that tests do not forge tokens against a shared key, rather than "
    "inventing a second answer for this module. Covered meanwhile by "
    "tests/community/core/bot_app_grant/test_grant_service.py (service and "
    "repository over a real database, including the grant/withdraw/grant/"
    "withdraw cycle and both scoping dimensions of the application's view), "
    "tests/community/adapters/http/openapi_v1/authorized_apps/test_router.py "
    "(all four operations end to end), the principal-seam assertions in "
    "test_principal_seam.py, and src/gateway/tests/unit/core/authn/"
    "test_route_security.py against the shipped config. Drain this together "
    "with _GATEWAY_PRINCIPAL_EXEMPT_REASON — one gateway in the box unblocks "
    "both."
)

_SPACES_FAMILY_EXEMPT_REASON = (
    "TEMPORARY, and blocked on the same single thing as "
    "_GATEWAY_PRINCIPAL_EXEMPT_REASON above: the spaces / market-favorites / "
    "work-orders family serves /openapi/v1 routes requiring a signed "
    "principal only a gateway can mint (the one exception, the internal "
    "personal-space batch query, is a trusted-integration seam with no user "
    "story of its own), and singlebox runs the backend without a gateway — a "
    "flow here could assert 401s and nothing else. Covered meanwhile by "
    "per-endpoint happy + error cases in "
    "tests/community/endpoints/test_spaces_router.py and "
    "test_work_orders_router.py (real services over a real database behind "
    "the same DI graph), plus the core service tests of each module. Drain "
    "these three together with _GATEWAY_PRINCIPAL_EXEMPT_REASON — one "
    "gateway in the box unblocks them all."
)

SINGLEBOX_E2E_EXEMPT: dict[str, str] = {
    "aicoding": _EXEMPT_REASON,
    "spaces": _SPACES_FAMILY_EXEMPT_REASON,
    "market_favorites": _SPACES_FAMILY_EXEMPT_REASON,
    "work_orders": _SPACES_FAMILY_EXEMPT_REASON,
    "bot_app_grant": _BOT_APP_GRANT_EXEMPT_REASON,
    "engine_runtime": _ENGINE_RUNTIME_EXEMPT_REASON,
    # antcode relocated to agentclaw/corp/core (B11 T3.3) — no longer a core module.
"bot_dormant": _EXEMPT_REASON,
    "bot_inventory": (
        "New public inventory/local Bot aggregation module. Covered by HTTP endpoint, "
        "service conformance, and architecture tests in this change; drain when "
        "singlebox has a signed /openapi/v1 principal flow for the new routes."
    ),
    "approval": _EXEMPT_REASON,
    "auth": _EXEMPT_REASON,
    "bot_public": _EXEMPT_REASON,
    "bot_config_manifest": (
        "Blocked on the same missing minter as _GATEWAY_PRINCIPAL_EXEMPT_REASON "
        "above — every route in the group is /openapi/v1 and needs a "
        "gateway-signed principal, which singlebox has no way to produce, the "
        "manifest routes and the apply routes (W4, #1472) alike. The "
        "machine-part waves — W2 fetch + unpack (#1470), the W11 content "
        "store (#1510), and W5's skills/identity materialisers (#1473) — add "
        "only transport/storage/materialisation behavior with no "
        "singlebox-visible flow of their "
        "own, covered by the security matrix in "
        "tests/community/core/bot_config_manifest/fetch/ and the store "
        "contract tests in "
        "tests/community/core/bot_config_manifest/content/ (plus its "
        "repository over a real database in "
        "tests/community/repository/bot/test_manifest_content_repository.py), "
        "by the materialiser suites in "
        "tests/community/core/bot_config_manifest/apply/ (the two fetching "
        "categories over fakes that count every write, and the entry-fetch "
        "pipeline's pinned/unpinned/keep_last policy), "
        "and by W1's own cover meanwhile: "
        "tests/community/repository/bot/test_bot_config_manifest_repository.py "
        "(repository over a real database), "
        "tests/community/core/bot_config_manifest/ (the schema rules, the "
        "capability resolver, and the absent-is-empty contract), and "
        "tests/community/endpoints/test_openapi_config_manifest.py (the four "
        "manifest routes through the assembled app) plus the apply routes in "
        "tests/community/endpoints/test_openapi_config_manifest_apply.py. "
        "Drain this when singlebox fronts the backend with a gateway."
    ),
    "bot_startup_script": (
        "The script's effect is only observable inside a provisioned container: "
        "it is appended to the start sequence the backend composes and runs "
        "there, so an end-to-end flow needs a real BaaS device to assert "
        "anything beyond storage. Covered meanwhile by "
        "tests/community/repository/bot/test_bot_startup_script_repository.py "
        "(repository over a real database), "
        "tests/community/core/bot_startup_script/ (the size cap and the "
        "absent-is-empty contract the payload path depends on), and the "
        "start-command composition tests in "
        "tests/community/core/service_bot/services/test_baas_service_start_cmd.py "
        "(byte-identical output without a script, and the exit-status guard "
        "with one). Drain this when singlebox can provision a container."
    ),
    "channel": _EXEMPT_REASON,
    "caller_identity": (
        "Agent Principal and BaaS outbound-rule calls require remote credentials; "
        "covered by local API/core tests until a singlebox-compatible external seam exists."
    ),
    "common_config": _EXEMPT_REASON,
    "config": _EXEMPT_REASON,
    "config_compose": _EXEMPT_REASON,
    "desktop_bot": _EXEMPT_REASON,
    "task": _TASK_FRAMEWORK_EXEMPT_REASON,
    "economy": _EXEMPT_REASON,
    "events": _EXEMPT_REASON,
    "gateway_principal": _GATEWAY_PRINCIPAL_EXEMPT_REASON,
    "group_chat": _EXEMPT_REASON,
    "grt_chat": _EXEMPT_REASON,
    "models": _EXEMPT_REASON,
    "notify": _EXEMPT_REASON,
    "nas_usage": _EXEMPT_REASON,
    "service_bot": _EXEMPT_REASON,
    "runtime_binding": _RUNTIME_BINDING_EXEMPT_REASON,
    "session_resources": _SESSION_RESOURCES_EXEMPT_REASON,
    "services": _EXEMPT_REASON,
    "skill_center": _EXEMPT_REASON,
    "skills_pool": (
        "Internal migration control-plane component with no HTTP/router surface in "
        "Issue #367; covered by repository, rollout-gate, claim-service, DI, and "
        "local-bootstrap contract tests."
    ),
    "storage": _EXEMPT_REASON,
    "system_config": _EXEMPT_REASON,
    "task_queue": _TASK_QUEUE_EXEMPT_REASON,
    "utils": _EXEMPT_REASON,
    "workspace": _EXEMPT_REASON,
}
