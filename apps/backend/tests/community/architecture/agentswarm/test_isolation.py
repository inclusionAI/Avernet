"""Architecture isolation test for ``core/agentswarm`` (OpenSpec 10.9).

Two invariants of the agentswarm swarm module:

1. Feature isolation (D17/D35): under ``core/agentswarm/**`` there is
   no import of the legacy task machinery or banned platform surfaces —
   ``core.task``, ``core.task_queue``, ``core.harness``,
   ``plugin_api.object_storage``, ``core.system_config`` — and no
   transport/test framework imports (``fastapi`` / ``httpx`` /
   ``pytest``) anywhere under the module: implementations moved to
   ``plugins/{local,community}/agentswarm`` (httpx is a plugin detail
   of the LLM client and the bot subagent; D18 keeps core
   transport-agnostic), so core must stay transport-free outright.

2. Typed boundary (D18, change task 1.2c): Protocol surface
   (``plugin_api/agentswarm/*``,
   ``core/agentswarm/service/protocols.py``) types closed-set vocabulary
   with the module StrEnums instead of bare ``str``, and bare-``str``
   fields that keep a string-literal default must be justified open
   vocabulary (tight allowlist). Transport DTOs
   (``adapters/http/agentswarm/schemas.py``) validate closed-set fields
   against the domain enums at the wire boundary.

Detection uses AST walks, not file-text regex.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[4]  # .../apps/backend
_AGENTCLAW_ROOT = _BACKEND_ROOT / "src" / "agentclaw"
_MODULE_ROOT = _AGENTCLAW_ROOT / "community" / "core" / "agentswarm"

# D17/D35: the swarm owns its own tables + runtime; both core.task and
# core.task_queue are banned (including each other's prefixes), plus the
# other feature-isolation walls.
_BANNED_PREFIXES: tuple[str, ...] = (
    "agentclaw.community.core.task",
    "agentclaw.community.core.task_queue",
    "agentclaw.community.core.harness",
    "agentclaw.community.plugin_api.object_storage",
    "agentclaw.community.core.system_config",
)

# D18: core stays transport-agnostic; httpx belongs only to plugins/.
_TRANSPORT_TOP_MODULES: frozenset[str] = frozenset({"fastapi", "httpx", "pytest"})

_SPI_FILES = tuple(sorted(
    (_AGENTCLAW_ROOT / "community" / "plugin_api" / "agentswarm").rglob("*.py")
))
_SERVICE_PROTOCOL = _MODULE_ROOT / "service" / "protocols.py"
_SCHEMAS = (
    _AGENTCLAW_ROOT / "community" / "adapters" / "http" / "agentswarm" / "schemas.py"
)

# Open-vocabulary str fields that may keep a string-literal default. Tight on
# purpose — every entry needs its reason:
#   provider/model — user-selected LLM identifiers: any string is valid wire
#     data, an open vocabulary, not a closed set;
#   instructions — free-form prose prompt text;
#   description — free-form prose on the provision spec (BotCreateSpec);
#   identity_file — a user-chosen soul/identity file name (provision input);
#   engine/bot_type — user-selected engine identifier and product bot type:
#     the host BotCreateContext types both as open str (bot_management
#     owns no enum for either), and the adapter derives the cluster from
#     engine, so a new engine never drifts a closed set here.
#   owner_id — the provisioning caller identity on the BotPlatform seam: an
#     open principal string owned by the auth layer; blank never falls
#     back to an ambient identity — the platform fails the call clear
#     and the factory gates on the run's authenticated submitter.
#   config_manifest — a caller-authored document, not vocabulary: the
#     full bot_config_manifest YAML string, whose grammar the platform's
#     own preflight owns; blank means "the host synthesizes the
#     identity-only persona document", so "" is a semantic default, not
#     one value of an open set.
_STR_DEFAULT_ALLOWLIST: frozenset[str] = frozenset({
    "provider", "model", "instructions", "description", "identity_file",
    "engine", "bot_type", "owner_id", "config_manifest",
})

# Closed-set parameter vocabulary must be typed with one of the module
# StrEnums in the Protocol/service path.
_ENUM_NAMES: frozenset[str] = frozenset({
    "RunStatus", "AgentStatus", "AgentKind", "WaitMode", "TerminationReason",
    "MessageRole", "GuardKind", "DispatchStatus", "BotState",
})
_CLOSED_SET_PARAMS: frozenset[str] = frozenset({
    "status", "kind", "mode", "termination_reason", "role",
})


def _relpath(file: pathlib.Path) -> str:
    return file.relative_to(_BACKEND_ROOT).as_posix()


def _iter_module_files() -> list[pathlib.Path]:
    return sorted(p for p in _MODULE_ROOT.rglob("*.py") if p.is_file())


def _collect_imports(tree: ast.AST) -> list[tuple[str, int]]:
    imports: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append((node.module, node.lineno))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, node.lineno))
    return imports


def _matches(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def _annotation_texts(annotation: ast.expr | None) -> set[str]:
    if annotation is None:
        return set()
    return {
        sub.id for sub in ast.walk(annotation) if isinstance(sub, ast.Name)
    }


def _defaulted_params(node: ast.FunctionDef | ast.AsyncFunctionDef):
    """(arg, default) pairs for params that carry a default value."""
    args = node.args
    pos_args = args.args
    pairs = [
        (pos_args[len(pos_args) - len(args.defaults) + i], default)
        for i, default in enumerate(args.defaults)
    ]
    pairs += list(zip(args.kwonlyargs, args.kw_defaults))
    return [(arg, default) for arg, default in pairs if default is not None]


def test_module_root_exists() -> None:
    """Path-resolver guard: a wrong root would silently skip every check."""
    assert _MODULE_ROOT.is_dir(), f"agentswarm module not found at {_MODULE_ROOT}"


def test_no_banned_feature_imports() -> None:
    violations: list[str] = []
    for file in _iter_module_files():
        tree = ast.parse(file.read_text(), filename=str(file))
        for name, lineno in _collect_imports(tree):
            if any(_matches(name, p) for p in _BANNED_PREFIXES):
                violations.append(f"{_relpath(file)}:{lineno} imports `{name}`")
    assert not violations, (
        "agentswarm is feature-isolated (D17/D35); banned imports:\n  "
        + "\n  ".join(violations)
    )


def test_no_transport_imports_in_core() -> None:
    violations: list[str] = []
    for file in _iter_module_files():
        rel = _relpath(file)
        tree = ast.parse(file.read_text(), filename=str(file))
        for name, lineno in _collect_imports(tree):
            if name.split(".")[0] in _TRANSPORT_TOP_MODULES:
                violations.append(f"{rel}:{lineno} imports `{name}`")
    assert not violations, (
        "transport/test frameworks are banned anywhere under core/agentswarm "
        "(D18; plugin impls live in plugins/{local,community}/agentswarm):\n  "
        + "\n  ".join(violations)
    )


@pytest.fixture()
def _protocol_trees() -> list[tuple[pathlib.Path, ast.AST]]:
    pairs = [
        (file, ast.parse(file.read_text(), filename=str(file)))
        for file in (*_SPI_FILES, _SERVICE_PROTOCOL, _SCHEMAS)
    ]
    for file, _ in pairs:
        assert file.is_file()
    return pairs


class TestD18TypedBoundary:

    def test_closed_set_parameters_use_str_enum(self, _protocol_trees) -> None:
        violations: list[str] = []
        for file, tree in _protocol_trees:
            if file == _SCHEMAS:
                continue  # transport DTOs validated against enums below
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                candidates = list(node.args.args) + list(node.args.kwonlyargs)
                for arg in candidates:
                    if arg.arg not in _CLOSED_SET_PARAMS:
                        continue
                    if not (_annotation_texts(arg.annotation) & _ENUM_NAMES):
                        annotation_src = (
                            ast.unparse(arg.annotation) if arg.annotation else "<none>"
                        )
                        violations.append(
                            f"{_relpath(file)}:{arg.lineno} `{node.name}` param "
                            f"`{arg.arg}` is `{annotation_src}`, expected a StrEnum"
                        )
        assert not violations, (
            "closed-set vocabulary must be StrEnum-typed in the Protocol/service "
            "path (D18):\n  " + "\n  ".join(violations)
        )

    def test_bare_str_string_defaults_are_justified(self, _protocol_trees) -> None:
        violations: list[str] = []
        for file, tree in _protocol_trees:
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    for stmt in node.body:
                        if (
                            isinstance(stmt, ast.AnnAssign)
                            and isinstance(stmt.annotation, ast.Name)
                            and stmt.annotation.id == "str"
                            and isinstance(stmt.value, ast.Constant)
                            and isinstance(stmt.value.value, str)
                            and isinstance(stmt.target, ast.Name)
                            and stmt.target.id not in _STR_DEFAULT_ALLOWLIST
                        ):
                            violations.append(
                                f"{_relpath(file)}:{stmt.lineno} class "
                                f"`{node.name}` field "
                                f"`{stmt.target.id}: str = \"...\"`"
                            )
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for arg, default in _defaulted_params(node):
                        if (
                            arg.arg not in _STR_DEFAULT_ALLOWLIST
                            and isinstance(arg.annotation, ast.Name)
                            and arg.annotation.id == "str"
                            and isinstance(default, ast.Constant)
                            and isinstance(default.value, str)
                        ):
                            violations.append(
                                f"{_relpath(file)}:{arg.lineno} `{node.name}` "
                                f"param `{arg.arg}` bare-str default"
                            )
        assert not violations, (
            "bare `str` keeping a string-literal default must be justified open "
            f"vocabulary (allowlist: {sorted(_STR_DEFAULT_ALLOWLIST)}):\n  "
            + "\n  ".join(violations)
        )

    def test_transport_dto_closed_sets_validate_enums(self, _protocol_trees) -> None:
        """The submit boundary must enum-validate the closed-set input fields.

        D18 places literal enum validation at the Pydantic boundary: input
        DTO fields carrying closed-set vocabulary must be typed by the domain
        StrEnum (`AgentSpecDTO.kind`) so the boundary rejects vocabulary
        drift; leaf/output DTOs may widen to plain wire strings.
        """
        tree = next(t for f, t in _protocol_trees if f == _SCHEMAS)
        spec_cls = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.ClassDef) and n.name == "AgentSpecDTO"
        )
        kind_field = next(
            s for s in spec_cls.body
            if isinstance(s, ast.AnnAssign)
            and isinstance(s.target, ast.Name)
            and s.target.id == "kind"
        )
        assert isinstance(kind_field.annotation, ast.Name) \
            and kind_field.annotation.id == "AgentKind", (
            "AgentSpecDTO.kind must stay AgentKind-typed so the HTTP boundary "
            "enum-validates agent kinds on submit (D18); annotation="
            f"{ast.unparse(kind_field.annotation)}"
        )