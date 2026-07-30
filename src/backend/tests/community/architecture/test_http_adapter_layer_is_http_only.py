"""R8 arch gate: ``adapters/http/`` contains only HTTP delivery code.

After Tasks 4–7.5, every router lives under ``adapters/http/<module>/``
and depends on ``api/<service>.py`` Protocols (never core service
classes) for service resolution. This test pins three invariants the
incremental migration established:

1. Every router file imports either ``fastapi`` or
   ``agentclaw.community.adapters.http.dependencies``. (Auth helpers,
   middleware, the composition root, and dispatch dependencies are
   exempt — they are HTTP infra, not endpoints.)

2. No router under ``adapters/http/`` imports a concrete service
   class from ``agentclaw.community.core.<module>.services.<name>``. Routers
   resolve services through ``Injected(<X>Protocol)`` instead.
   Exception types and helper functions from
   ``core/<module>/services/`` are allowed — those are part of the
   core's public surface, not the service-instance surface.

3. The legacy ``api/<module>/`` directories no longer exist (R8
   Task 8 cleanup checkpoint).

Together these pin the post-R8 layout: HTTP adapters depend on api/
Protocols and (limited) core domain types; no service classes leak
through the HTTP boundary.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest


_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]  # .../src/backend
_AGENTCLAW_ROOT = _BACKEND_ROOT / "src" / "agentclaw"
_HTTP_ROOT = _AGENTCLAW_ROOT / "community" / "adapters" / "http"
_API_ROOT = _AGENTCLAW_ROOT / "community" / "api"


# Files that live under adapters/http/ but are not router endpoints.
# These are HTTP infrastructure (composition root, middleware,
# shared request-context, dispatch dependencies) — they don't have
# to import fastapi or get_request_context themselves.
_NON_ENDPOINT_FILES: frozenset[str] = frozenset({
    "app.py",
    "sofa_app.py",
    "middleware.py",
    "dependencies.py",
    "__init__.py",
})

# File-name patterns that legitimately live under adapters/http/<m>/
# but don't drive endpoints themselves — schemas, response models,
# router-local dependencies. Match by name only (not full path).
#
# These exempt a file from invariant 1 ONLY (must touch the HTTP stack).
# Invariant 2 — no concrete core service imports — scans every file under
# adapters/http/ regardless, so a name appearing here can never become a hole
# in the layering guard. Matching by stem is deliberately loose (any module's
# ``schemas.py`` qualifies); that looseness is only safe because it cannot
# disable the import check.
_NON_ENDPOINT_NAME_PATTERNS: tuple[str, ...] = (
    "schemas",       # schemas.py, schemas_publish.py, etc.
    "dependencies",  # router-local DI helpers
    "converter",     # domain-model → API Response transforms
    "models",        # adapter-owned identity / response dataclasses
    "errors",        # adapter-owned error types (kept import-light on purpose)
    "clusters",      # public-API domain rule (engine ↔ cluster bijection)
    "principal",     # caller-identity extraction from the principal seam
)


def _is_endpoint_file(file: pathlib.Path) -> bool:
    """Whether ``file`` must touch the HTTP stack (invariant 1 only).

    Deliberately NOT used to scope the core-service import check: helpers are
    exempt from *looking like* endpoints, never from the layering rule.
    """
    if file.name in _NON_ENDPOINT_FILES:
        return False
    stem = file.stem
    for pattern in _NON_ENDPOINT_NAME_PATTERNS:
        if stem == pattern or stem.startswith(pattern + "_"):
            return False
    return True


def _core_service_import_offenders() -> list[str]:
    """Every ``adapters/http/`` import of a non-allow-listed core service name."""
    offenders: list[str] = []
    for file in _iter_http_files():
        try:
            tree = ast.parse(file.read_text(), filename=str(file))
        except SyntaxError as exc:  # pragma: no cover
            offenders.append(f"{file} SyntaxError {exc.msg}")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            mod = node.module or ""
            if not _CORE_SERVICE_IMPORT_RE.match(mod):
                continue
            if any(mod.endswith(suffix) for suffix in _CORE_SERVICE_MODULE_EXEMPT_SUFFIX):
                continue
            for alias in node.names:
                if alias.name in _CORE_SERVICE_NAMES_OK:
                    continue
                rel = file.relative_to(_HTTP_ROOT)
                offenders.append(
                    f"{rel}:{node.lineno} imports `{alias.name}` from `{mod}`"
                )
    return offenders


def _iter_http_files() -> list[pathlib.Path]:
    return [
        p for p in _HTTP_ROOT.rglob("*.py")
        if "__pycache__" not in p.parts
    ]


def _collect_imported_modules(tree: ast.AST) -> set[str]:
    """All module names referenced via ``import X`` / ``from X import ...``."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
    return names


# Regex anchored to ``agentclaw.community.core.<module>.services.<file>``. The
# allow-list below names a few legitimate non-service imports from
# that namespace (exception classes, helper functions, engine
# resolvers) that adapters need to catch / call directly.
# The second alternative covers ``core.services.<file>`` — services that live
# directly under ``core/services/`` rather than under a module. The first
# alternative requires a module segment before ``.services.``, so those files
# matched nothing and their imports skipped the check entirely.
_CORE_SERVICE_IMPORT_RE = re.compile(
    r"^agentclaw\.(?:community\.)?core\."
    r"(?:[a-z_]+(?:\.[a-z_]+)*\.services\.[a-z_]+|services\.[a-z_]+)$"
)

# ``services/repositories.py`` is a quirk of the skill_center layout —
# the file holds repository Protocols (not service instances), so
# adapters depending on it is the same as depending on the standard
# ``core/<m>/repository/protocol.py``. Exempt it wholesale.
_CORE_SERVICE_MODULE_EXEMPT_SUFFIX: tuple[str, ...] = (
    ".services.repositories",
    # Engine-health helpers live under ``core/devices/services/`` but
    # are pure functions (no service class) consumed by aicoding
    # routers as utility calls. They behave like helpers, not service
    # instances, so the whole module is exempt.
    ".services.engine_health",
)

# Names that legitimately live under ``core/<m>/services/<file>`` and
# are imported directly by adapters. Three classes of exception:
#   * Errors — adapters catch and translate to HTTP status codes.
#   * Domain types — context dataclasses, records, reports, results.
#     These are pure data, not service instances; passing them across
#     the boundary is not a service-implementation leak.
#   * Helpers — pure functions used to resolve / probe / generate.
_CORE_SERVICE_NAMES_OK: frozenset[str] = frozenset({
    # Errors raised by services that the router translates to HTTP:
    "FileTooLargeError",
    "BotServiceError", "BotInvalidLifecycleStateError", "BotNotFoundError", "BotPermissionError",
    "BotLimitExceededError", "BotNameExistsError", "BotNameInvalidError",
    "BotOperationNotAllowedError",
    "DeviceAllocationError", "DeviceLimitError",
    # Multi-instance entry-resolution errors raised by DeviceServiceRouter and
    # translated to HTTP by the devices router (§1/§2/§3):
    "BindingNotFoundError", "BotPublishNotFoundError",
    "BotBuildServiceError", "BotBuildMigrationError",
    "BotPublishServiceError", "PublishFlowServiceError",
    "BotAlreadyServiceTypeError", "BotTypeNotSupportedError",
    "PublishAlreadyExistsError", "PublishNotFoundError",
    "PublishStatusInvalidError", "InvalidTransitionError",
    "BotPublicServiceError", "BotNotPublicError",
    "DesktopBotServiceError",
    "BaasServiceError",
    "CollaboratorServiceError", "PermissionDeniedError",
    "CollaboratorNotFoundError", "CollaboratorAlreadyExistsError",
    "CannotRemoveSelfError", "BotNotServiceTypeError",
    "LockNotHeldError", "LockReleaseDeniedError",
    "PatchEngineError",
    # Domain dataclasses / context records (not service instances):
    "OperatorContext", "ChannelRecord",
    "BatchSyncReport", "SyncResult",
    # data-proxy result type — the router branches buffered-vs-streamed
    # on it to render a StreamingResponse for SSE upstreams.
    "StreamingForwardResult",
    # Pure-function helpers / generators:
    "generate_bot_id", "validate_bot_name", "resolve_engine_for_bot",
    "filter_passport_mcp_codes",
    # Pure functions in core/mcp/services/_defaults that build the passport
    # resource scope (default MCP server codes / default CLI items) from
    # engine-scoped module constants. Read-only helpers, not service instances;
    # parallel to filter_passport_mcp_codes above.
    "get_default_cli_items",
    "generate_report",
    # ContentScanner static helpers used directly by harness router:
    "ContentScanner",
    # DeviceContextResolver — 全仓唯一 provider 解析点(spec §6.2.2)。
    # 是 stateless helper class,通过 Injected(...) 注入,语义等同于
    # ``resolve_engine_for_bot`` 的 class 版本,不是普通业务 service。
    "DeviceContextResolver",
    # DeviceSyncDispatcher / DeviceFilesystemDispatcher — core routing holders
    # (B6): thin per-bot dispatchers used purely as DI keys via Injected(...),
    # like DeviceContextResolver. The vendor construction lives behind injected
    # resolver fns; these classes carry no service logic the router could misuse.
    "DeviceSyncDispatcher",
    "DeviceFilesystemDispatcher",
    # DeviceContext typed result + its resolve errors — pure domain types
    # (a frozen dataclass and exception classes), not services. Routers read
    # ``ctx.provider`` and catch resolve failures when reading a published
    # bot's stage binding via ``resolve_for_binding``.
    "DeviceContext",
    "DeviceNotBoundError",
    "UnknownProviderError",
    "ConnInfoBuildError",
    # ── Surfaced by widening the regex to cover ``core/services/<file>`` (R11/F43)
    # Those imports previously matched nothing and skipped this check entirely.
    #
    # Domain types and helpers — the documented categories above:
    "IdentityFileContent", "IdentityFileResponse", "IdentityFileUpdateResponse",
    "IdentityFileListResponse", "BotIdentityFileResponse",
    "BotIdentityFileUpdateResponse",
    "VALID_ENTITY_TYPES",
    "is_readonly",
    "_HIDDEN_BASENAMES", "_HIDDEN_DIRNAMES",
    "_POOL_SKILLS_LOCAL_RELPATH", "_SKILLS_LOCAL_RELPATH",
    # Genuine pre-existing violations, NOT endorsements. Both are concrete
    # service classes injected directly by routers that predate this guard
    # covering their path; the engine-config service in the same directory was
    # migrated to `api/engine_config_service.py` in R11/F43, and these two want
    # the same treatment. Listed so the widened check reports the *new* ones
    # instead of failing on day one — remove each as it is migrated.
    "IdentityService",
    "ResourceFileService",
    # aicoding data-proxy errors, caught by the app-level handler in app.py to
    # render the {"detail": {"error", "op"}} shape aixharness expects. Same
    # "errors an adapter translates" category as the entries above; surfaced
    # once the import check stopped skipping non-endpoint files.
    "DataProxyError", "EngineUnreachable", "EngineUrlNotConfigured",
    # Governance action result — a plain domain enum/record referenced by the
    # economy request/response models, not a service instance.
    "TicketActionOutcome",
    # Per-request helper instantiated inline (not via DI) because it
    # depends on a request-local ArcaVerifyClient. Moving it under DI
    # would require a request-scoped factory; tracked as separate
    # cleanup, allowed for now.
    "SkillSymlinkVerifyService",
})

# Legacy api/ subpackages still present in the current codebase.
# This test only guards against newly introduced api/ subpackages.
_LEGACY_API_SUBDIR_ALLOWLIST: frozenset[str] = frozenset({
    "access",
    "aicoding",
    "antcode",
    "antprocess",
    "approvals",
    "auth",
    "bot_chat",
    "bot_collaborator",
    "bot_management",
    "bot_public",
    "bot_render_screen",
    "channel",
    "claudecode",
    "cron",
    "desktop",
    "devices",
    "enums",
    "expert_chat",
    "harness",
    "identity",
    "local",
    "mcp",
    "models",
    "resources",
    "service_bot",
    "skill_center",
    "system",
    "system_config",
    "task",
    "token_exchange",
})


def test_http_root_exists() -> None:
    assert _HTTP_ROOT.is_dir(), f"adapters/http/ root not found at {_HTTP_ROOT}"


def test_legacy_api_subdirectories_are_gone() -> None:
    """Routers have all moved to adapters/http/. No directories left under api/."""
    subdirs = [
        p.name for p in _API_ROOT.iterdir()
        if (
            p.is_dir()
            and p.name != "__pycache__"
            and p.name not in _LEGACY_API_SUBDIR_ALLOWLIST
        )
    ]
    assert not subdirs, (
        "api/ must be flat. The following subdirectories remain after R8:\n  "
        + "\n  ".join(subdirs)
        + "\nMove each router into adapters/http/<module>/ instead."
    )


@pytest.mark.unit
def test_router_files_use_http_machinery() -> None:
    """Every router file must touch the HTTP stack (fastapi or request context)."""
    offenders: list[str] = []
    for file in _iter_http_files():
        if not _is_endpoint_file(file):
            continue
        try:
            tree = ast.parse(file.read_text(), filename=str(file))
        except SyntaxError as exc:  # pragma: no cover
            offenders.append(f"{file} SyntaxError {exc.msg}")
            continue
        imports = _collect_imported_modules(tree)
        uses_fastapi = any(m == "fastapi" or m.startswith("fastapi.") for m in imports)
        uses_request_ctx = "agentclaw.community.adapters.http.dependencies" in imports
        uses_auth_dep = "agentclaw.community.adapters.http.auth.dependencies" in imports
        if not (uses_fastapi or uses_request_ctx or uses_auth_dep):
            rel = file.relative_to(_HTTP_ROOT)
            offenders.append(str(rel))
    assert not offenders, (
        "adapters/http/ files should be FastAPI router endpoints. The "
        "following imported neither fastapi nor an adapter helper — "
        "they look like they don't belong here:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.unit
def test_routers_do_not_import_core_service_classes() -> None:
    """Adapters depend on api/ Protocols, never core/<m>/services/ classes.

    Allow-listed non-service names (exception types, helpers) match
    the documented R8 layering rule: adapters may catch and re-raise
    domain exceptions, and may call pure helpers, but must resolve
    service instances through ``Injected(<X>Protocol)``.

    Scans **every** file under ``adapters/http/``, endpoint or not. The
    non-endpoint exemptions above answer "must this look like a router?", which
    is a different question from "may this reach past the layer boundary?" —
    a ``schemas.py`` or ``errors.py`` has no more business importing a concrete
    service than a router does. Sharing one predicate between the two checks
    would mean any file whose stem matched an exempt pattern silently left the
    layering guard as well.
    """
    offenders = _core_service_import_offenders()
    assert not offenders, (
        "Adapters must not import concrete service classes from core. "
        "Resolve services through `Injected(<X>Protocol)` instead.\n"
        "If a name is a legitimate domain type (exception class, "
        "engine-resolver helper), add it to `_CORE_SERVICE_NAMES_OK`.\n"
        "Violations:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.unit
def test_core_service_import_check_covers_non_endpoint_files(monkeypatch) -> None:
    """The layering guard must not be scoped by the endpoint exemptions (R7/F34).

    ``_NON_ENDPOINT_NAME_PATTERNS`` matches by filename stem across the whole
    adapter tree. While one predicate gated both checks, adding a stem there
    also removed every file with that name from the core-service import check —
    so a future ``errors.py`` or ``schemas.py`` in any adapter could import a
    concrete service and CI would stay green.

    Proven by dropping a name from the allow-list and asserting the scan then
    reports the **non-endpoint** file that imports it. If the scan were still
    filtered by ``_is_endpoint_file`` this would find nothing.
    """
    probes = {
        "app.py": "DataProxyError",                  # _NON_ENDPOINT_FILES
        "economy/schemas.py": "TicketActionOutcome",  # _NON_ENDPOINT_NAME_PATTERNS
    }
    for rel, name in probes.items():
        assert not _is_endpoint_file(_HTTP_ROOT / rel), f"{rel} is no longer exempt"
        monkeypatch.setattr(
            "tests.community.architecture."
            "test_http_adapter_layer_is_http_only._CORE_SERVICE_NAMES_OK",
            _CORE_SERVICE_NAMES_OK - {name},
        )
        offenders = _core_service_import_offenders()
        assert any(o.startswith(rel) and name in o for o in offenders), (
            f"the import guard did not reach {rel} — it is being skipped as a "
            f"non-endpoint file, which is exactly the hole this pins shut"
        )


@pytest.mark.unit
def test_core_service_import_check_covers_flat_core_services(monkeypatch) -> None:
    """Services under ``core/services/`` are in scope too (R11/F43).

    The original regex required a module segment before ``.services.``
    (``core.<module>.services.<file>``), so everything under ``core/services/``
    — engine-config, identity, resource files — matched nothing and skipped the
    layering check entirely. That is how a concrete ``EngineConfigService``
    injection sat in three routers without CI noticing.

    Proven the same way as the endpoint-scope test: drop a name from the
    allow-list and assert the scan then reports the flat-path import.
    """
    assert _CORE_SERVICE_IMPORT_RE.match(
        "agentclaw.community.core.services.engine_config"
    ), "the flat core/services/ path is not matched — the hole is back"

    monkeypatch.setattr(
        "tests.community.architecture."
        "test_http_adapter_layer_is_http_only._CORE_SERVICE_NAMES_OK",
        _CORE_SERVICE_NAMES_OK - {"is_readonly"},
    )
    offenders = _core_service_import_offenders()
    assert any("core.services.resource_file_service" in o for o in offenders), (
        "imports from core/services/ are still invisible to the layering guard"
    )
