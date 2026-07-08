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
_NON_ENDPOINT_NAME_PATTERNS: tuple[str, ...] = (
    "schemas",       # schemas.py, schemas_publish.py, etc.
    "dependencies",  # router-local DI helpers
    "converter",     # domain-model → API Response transforms
    "models",        # adapter-owned identity / response dataclasses
)


def _is_endpoint_file(file: pathlib.Path) -> bool:
    if file.name in _NON_ENDPOINT_FILES:
        return False
    stem = file.stem
    for pattern in _NON_ENDPOINT_NAME_PATTERNS:
        if stem == pattern or stem.startswith(pattern + "_"):
            return False
    return True


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
_CORE_SERVICE_IMPORT_RE = re.compile(
    r"^agentclaw\.(?:community\.)?core\.[a-z_]+(?:\.[a-z_]+)*\.services\.[a-z_]+$"
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
    "BotServiceError", "BotNotFoundError", "BotPermissionError",
    "BotLimitExceededError", "BotNameExistsError", "BotNameInvalidError",
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
    # Per-request helper instantiated inline (not via DI) because it
    # depends on a request-local ArcaVerifyClient. Moving it under DI
    # would require a request-scoped factory; tracked as separate
    # cleanup, allowed for now.
    "SkillSymlinkVerifyService",
    # Governance notification builder helpers — pure functions that construct
    # card/notification payloads. Not service classes; called inline in
    # scan-and-deliver endpoint to build TC card data before sending.
    "build_card_notification_data",
    "build_governance_reason",
    "build_tc_card_detail_link",
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
    """
    offenders: list[str] = []
    for file in _iter_http_files():
        if not _is_endpoint_file(file):
            continue
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
                name = alias.name
                if name in _CORE_SERVICE_NAMES_OK:
                    continue
                rel = file.relative_to(_HTTP_ROOT)
                offenders.append(f"{rel}:{node.lineno} imports `{name}` from `{mod}`")
    assert not offenders, (
        "Adapters must not import concrete service classes from core. "
        "Resolve services through `Injected(<X>Protocol)` instead.\n"
        "If a name is a legitimate domain type (exception class, "
        "engine-resolver helper), add it to `_CORE_SERVICE_NAMES_OK`.\n"
        "Violations:\n  " + "\n  ".join(offenders)
    )
