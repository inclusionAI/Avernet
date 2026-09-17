#!/usr/bin/env python3
"""Generate the engine capability matrix straight from the code.

Every engine declares an ``EngineCapabilities`` (see
``core/engine/capability.py``): the set of capabilities it fully supports, a
map of *limited* ones with the caveat text, and a map of *fallback* ones with
the "do it another way" text. The matrix in
``docs/heterogeneous-engine-architecture.md`` §4.1 is hand-maintained and drifts
as engines gain or lose capabilities. This script derives the same table from
the declarations themselves, so it is never out of date.

Two collection modes:

* ``runtime`` — import ``engine.community.engines`` so every bundled engine
  self-registers, then read ``EngineCapabilities`` off each registered class.
  Authoritative, and respects ``ENGINE_PROFILE`` gating (community loads
  openclaw + community claude_code; corp additionally loads aicoding / hermes /
  deepseek_harness when that tree is present). Needs the engine's dependencies
  installed (``uv sync`` in ``src/engine``).
* ``static`` — parse the engine packages with ``ast`` and read the
  ``EngineCapabilities(...)`` literals without importing anything. No
  dependencies, works in any checkout, and lists *every* engine package on disk
  regardless of profile gating. Only handles literal declarations.

``auto`` (the default) tries runtime first and falls back to static.

``--target`` picks which matrix to build. The repo has two unrelated per-engine
declarations, and they do not share a vocabulary:

* ``engine`` (the default) — the backend ``EngineCapabilities`` described above.
* ``frontend`` — the ``BotFeatures`` record each adapter under
  ``src/frontend/src/adapters/engine/`` declares to gate the UI (which tabs to
  show, chat render mode, image upload, …). Covers every adapter the factory
  registers, including the engines whose backend packages are not in this tree.
  Parsed, not executed, so no npm install is needed; the frontend tree is absent
  from the engine-only dist, where this target reports that rather than failing.

Usage::

    # Markdown table on stdout
    python scripts/gen_capability_matrix.py

    # Refresh the checked-in doc
    python scripts/gen_capability_matrix.py -o docs/engine-capability-matrix.md

    # CI drift gate: fail when the doc no longer matches the code
    python scripts/gen_capability_matrix.py --check docs/engine-capability-matrix.md

    # Machine-readable
    python scripts/gen_capability_matrix.py --format json
    python scripts/gen_capability_matrix.py --format csv --engines openclaw,claude_code

    # The frontend adapters' BotFeatures matrix, and both at once
    python scripts/gen_capability_matrix.py --target frontend
    python scripts/gen_capability_matrix.py --target all
"""

from __future__ import annotations

import argparse
import ast
import csv
import io
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# .../src/engine/scripts/gen_capability_matrix.py -> .../src/engine
ENGINE_DIR = Path(__file__).resolve().parents[1]
#: Import root for the `engine` namespace package.
ENGINE_SRC = ENGINE_DIR / "src"
#: Capability vocabulary — the single source of truth for row order.
CAPABILITY_MODULE = ENGINE_SRC / "engine/community/core/engine/capability.py"
#: Engine package roots scanned in static mode (corp is absent from OSS trees).
ENGINE_PACKAGE_ROOTS = (
    ("community", ENGINE_SRC / "engine/community/engines"),
    ("corp", ENGINE_SRC / "engine/corp/engines"),
)

#: Frontend engine adapters — a second, unrelated capability vocabulary (see
#: `--target frontend`). Absent from the engine's standalone community dist,
#: which stages only the engine tree; the collector says so rather than failing.
FRONTEND_ADAPTER_DIR = ENGINE_DIR.parents[1] / "src/frontend/src/adapters/engine"
FRONTEND_ENGINE_TYPES = (
    ENGINE_DIR.parents[1] / "src/frontend/src/services/backend-api/BotController.ts"
)

MARK_SUPPORTED = "✅"
MARK_LIMITED = "⚠️"
MARK_FALLBACK = "↪"
MARK_UNSUPPORTED = "❌"
#: Frontend-only marks (see `--target frontend`).
MARK_CONDITIONAL = "*"
MARK_UNDECLARED = "–"

STATUS_MARKS = {
    "supported": MARK_SUPPORTED,
    "limited": MARK_LIMITED,
    "fallback": MARK_FALLBACK,
    "unsupported": MARK_UNSUPPORTED,
}

GENERATOR = "src/engine/scripts/gen_capability_matrix.py"


def warn(message: str) -> None:
    print(f"gen_capability_matrix: {message}", file=sys.stderr)


@dataclass
class EngineCaps:
    """One engine's declaration, flattened to capability *value* strings."""

    name: str
    version: str = ""
    profile: str = ""
    origin: str = ""
    supported: set[str] = field(default_factory=set)
    limited: dict[str, str] = field(default_factory=dict)
    fallback: dict[str, str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        """Column header — disambiguated when two profiles ship the same name."""
        return f"{self.name} ({self.profile})" if self.profile else self.name

    def status(self, capability: str) -> str:
        if capability in self.supported:
            return "supported"
        if capability in self.limited:
            return "limited"
        if capability in self.fallback:
            return "fallback"
        return "unsupported"

    def note(self, capability: str) -> str:
        return self.limited.get(capability) or self.fallback.get(capability) or ""

    def declared(self) -> set[str]:
        return self.supported | set(self.limited) | set(self.fallback)


@dataclass
class Matrix:
    """Everything a renderer needs: row vocabulary + engine columns."""

    source: str
    capabilities: list[str]
    engines: list[EngineCaps]


@dataclass
class FeatureValue:
    """One `BotFeatures` entry as an adapter declares it.

    `overrides` carries the `ConditionalFeature` branches verbatim — the
    condition source and the value it yields — because several adapters gate a
    feature on the bot's kind (desktop / service / default) rather than on the
    engine alone.
    """

    kind: str = "missing"  # bool | string | number | conditional | missing
    default: str = ""
    overrides: list[tuple[str, str]] = field(default_factory=list)

    @property
    def declared(self) -> bool:
        return self.kind != "missing"


@dataclass
class AdapterFeatures:
    """One frontend engine adapter's declaration."""

    engine_type: str
    display: str = ""
    beta: bool = False
    default_model: str = ""
    origin: str = ""
    values: dict[str, FeatureValue] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.engine_type

    def value(self, feature: str) -> FeatureValue:
        return self.values.get(feature, FeatureValue())


@dataclass
class FeatureMatrix:
    """The frontend counterpart of :class:`Matrix`."""

    source: str
    features: list[str]
    adapters: list[AdapterFeatures]


# ──────────────────────────────────────────────────────────────────────
# Collection — runtime
# ──────────────────────────────────────────────────────────────────────


def collect_runtime(profile: str | None = None) -> Matrix:
    """Import the engine packages and read the live declarations.

    Raises ImportError when the engine's dependencies are not installed, which
    is what ``--source auto`` catches to fall back to the static scan.
    """
    if profile:
        os.environ["ENGINE_PROFILE"] = profile
    if str(ENGINE_SRC) not in sys.path:
        sys.path.insert(0, str(ENGINE_SRC))

    from engine.community.core.engine.capability import (  # noqa: PLC0415
        Capability,
        EngineCapabilities,
    )
    from engine.community.core.engine.registry import (  # noqa: PLC0415
        DEFAULT_REGISTRY,
    )

    # Importing the package is what registers the bundled engines; the import
    # itself is profile-gated (see engines/__init__.py).
    import engine.community.engines  # noqa: F401,PLC0415

    active = (os.environ.get("ENGINE_PROFILE") or "community").strip().lower()
    return _matrix_from_registry(DEFAULT_REGISTRY, Capability, EngineCapabilities, active)


def _matrix_from_registry(registry, capability_enum, caps_type, profile: str) -> Matrix:
    """Flatten a populated registry into a Matrix.

    Split out from :func:`collect_runtime` so the registry walk is exercisable
    without importing the (dependency-heavy) engine packages.
    """
    engines: list[EngineCaps] = []
    for name in registry.names():
        engine_class = registry.get(name)
        caps = _runtime_caps(engine_class, caps_type)
        if caps is None:
            warn(f"skipping {name!r}: could not read its EngineCapabilities")
            continue
        engines.append(
            EngineCaps(
                name=name,
                version=str(getattr(engine_class, "version", "") or ""),
                profile=profile if _is_corp_class(engine_class) else "",
                origin=f"{engine_class.__module__}.{engine_class.__qualname__}",
                supported={c.value for c in caps.supported},
                limited={c.value: v for c, v in caps.limited.items()},
                fallback={c.value: v for c, v in caps.fallback.items()},
            )
        )

    return Matrix(
        source=f"runtime import (ENGINE_PROFILE={profile})",
        capabilities=[c.value for c in capability_enum],
        engines=sorted(engines, key=lambda e: e.label),
    )


def _is_corp_class(engine_class: type) -> bool:
    return engine_class.__module__.startswith("engine.corp.")


def _runtime_caps(engine_class: type, caps_type: type):
    """Read an engine's declaration without starting it, if at all possible.

    Convention is a class-level ``_CAPABILITIES``; engines that compute the
    declaration in ``__init__`` are constructed with an empty config as a last
    resort (the same escape hatch ``EngineRegistry`` uses for ``name``).
    """
    declared = getattr(engine_class, "_CAPABILITIES", None)
    if isinstance(declared, caps_type):
        return declared
    try:
        instance = engine_class({})
    except Exception as exc:  # noqa: BLE001 - diagnostic only
        warn(f"{engine_class.__name__}: zero-config construction failed ({exc!r})")
        return None
    candidate = getattr(instance, "capabilities", None)
    return candidate if isinstance(candidate, caps_type) else None


# ──────────────────────────────────────────────────────────────────────
# Collection — static (ast)
# ──────────────────────────────────────────────────────────────────────


def collect_static(engine_src: Path = ENGINE_SRC) -> Matrix:
    """Parse engine packages on disk; import nothing, install nothing."""
    capability_module = engine_src / CAPABILITY_MODULE.relative_to(ENGINE_SRC)
    members = _parse_capability_enum(capability_module)

    engines: list[EngineCaps] = []
    seen: dict[str, int] = {}
    for profile, root in (
        (profile, engine_src / path.relative_to(ENGINE_SRC))
        for profile, path in ENGINE_PACKAGE_ROOTS
    ):
        if not root.is_dir():
            continue
        for package in sorted(p for p in root.iterdir() if p.is_dir()):
            if package.name in {"tests", "__pycache__"}:
                continue
            caps = _parse_engine_package(package, members)
            if caps is None:
                continue
            caps.profile = profile
            caps.origin = str(package.relative_to(engine_src))
            seen[caps.name] = seen.get(caps.name, 0) + 1
            engines.append(caps)

    # Keep the column header short when a name is unambiguous; only engines
    # shipped by both profiles (claude_code) need the profile suffix.
    for caps in engines:
        if seen.get(caps.name, 0) < 2:
            caps.profile = ""

    return Matrix(
        source=f"static scan of `{_display_path(engine_src)}`",
        capabilities=list(members.values()),
        engines=sorted(engines, key=lambda e: e.label),
    )


def _display_path(path: Path) -> str:
    """Repo-relative when possible — keeps `--check` output machine-independent."""
    repo_root = ENGINE_DIR.parents[1]
    try:
        return str(path.resolve().relative_to(repo_root))
    except ValueError:
        return str(path)


def _parse_capability_enum(path: Path) -> dict[str, str]:
    """Return ``{"SESSION_LIST": "session.list", ...}`` in declaration order."""
    if not path.is_file():
        raise FileNotFoundError(f"capability vocabulary not found: {path}")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Capability":
            members: dict[str, str] = {}
            for item in node.body:
                if not isinstance(item, ast.Assign) or len(item.targets) != 1:
                    continue
                target = item.targets[0]
                value = _literal_str(item.value)
                if isinstance(target, ast.Name) and value is not None:
                    members[target.id] = value
            return members
    raise ValueError(f"no `class Capability` found in {path}")


def _parse_engine_package(package: Path, members: dict[str, str]) -> EngineCaps | None:
    """Find the engine class in a package and resolve its declaration.

    Handles both shapes in the tree: the literal inline in the class body
    (openclaw) and a module-level constant referenced by name, possibly from a
    sibling module such as ``capabilities.py`` (claude_code).
    """
    modules = [
        p
        for p in sorted(package.rglob("*.py"))
        if "tests" not in p.relative_to(package).parts
    ]

    constants: dict[str, ast.Call] = {}
    classes: list[tuple[str, str, ast.expr]] = []  # (name, version, caps expr)
    for module in modules:
        try:
            tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        except SyntaxError as exc:  # noqa: PERF203 - report and skip
            warn(f"{module}: {exc}")
            continue
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                name, value = _assignment(node)
                if name and _is_caps_call(value):
                    constants[name] = value  # type: ignore[assignment]
            elif isinstance(node, ast.ClassDef):
                found = _class_declaration(node)
                if found is not None:
                    classes.append(found)

    for name, version, expr in classes:
        call = expr if _is_caps_call(expr) else None
        if call is None and isinstance(expr, ast.Name):
            call = constants.get(expr.id)
        if call is None:
            warn(f"{package.name}: could not resolve _CAPABILITIES for {name!r}")
            continue
        supported, limited, fallback = _read_caps_call(call, members, package.name)
        return EngineCaps(
            name=name,
            version=version,
            supported=supported,
            limited=limited,
            fallback=fallback,
        )
    return None


def _assignment(node: ast.Assign | ast.AnnAssign) -> tuple[str | None, ast.expr | None]:
    if isinstance(node, ast.AnnAssign):
        target, value = node.target, node.value
    else:
        target = node.targets[0] if len(node.targets) == 1 else None
        value = node.value
    return (target.id if isinstance(target, ast.Name) else None), value


def _is_caps_call(node: ast.expr | None) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "EngineCapabilities"
    )


def _class_declaration(node: ast.ClassDef) -> tuple[str, str, ast.expr] | None:
    """Extract ``(name, version, _CAPABILITIES expr)`` from an engine class."""
    name = version = None
    caps: ast.expr | None = None
    for item in node.body:
        if not isinstance(item, (ast.Assign, ast.AnnAssign)):
            continue
        attr, value = _assignment(item)
        if attr == "name":
            name = _literal_str(value)
        elif attr == "version":
            version = _literal_str(value)
        elif attr == "_CAPABILITIES" and value is not None:
            caps = value
    if name and caps is not None:
        return name, version or "", caps
    return None


def _read_caps_call(
    call: ast.Call, members: dict[str, str], where: str
) -> tuple[set[str], dict[str, str], dict[str, str]]:
    supported: set[str] = set()
    limited: dict[str, str] = {}
    fallback: dict[str, str] = {}

    for keyword in call.keywords:
        if keyword.arg == "supported" and isinstance(keyword.value, ast.Set):
            for element in keyword.value.elts:
                value = _capability_value(element, members, where)
                if value:
                    supported.add(value)
        elif keyword.arg in {"limited", "fallback"} and isinstance(
            keyword.value, ast.Dict
        ):
            target = limited if keyword.arg == "limited" else fallback
            for key, note in zip(keyword.value.keys, keyword.value.values):
                value = _capability_value(key, members, where)
                if value:
                    target[value] = _literal_str(note) or ""
    return supported, limited, fallback


def _capability_value(
    node: ast.expr | None, members: dict[str, str], where: str
) -> str | None:
    """Resolve a ``Capability.SESSION_LIST`` attribute to ``"session.list"``."""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        if node.value.id == "Capability":
            value = members.get(node.attr)
            if value is None:
                warn(f"{where}: unknown Capability.{node.attr}")
            return value
    literal = _literal_str(node)
    if literal is not None:
        return literal
    warn(f"{where}: unsupported capability expression {ast.dump(node) if node else None}")
    return None


def _literal_str(node: ast.expr | None) -> str | None:
    """Constant string, tolerating parenthesised ``"a" + "b"`` concatenation."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal_str(node.left), _literal_str(node.right)
        if left is not None and right is not None:
            return left + right
    return None


# ──────────────────────────────────────────────────────────────────────
# Collection — frontend engine adapters (TypeScript)
# ──────────────────────────────────────────────────────────────────────
#
# The frontend keeps its own per-engine declaration, unrelated to the backend
# `Capability` enum: each adapter under src/frontend/src/adapters/engine/
# overrides `baseFeatures`, a `BotFeatures` record of UI-gating flags (which
# tabs to show, the chat render mode, image upload, …). It is parsed rather
# than executed: running it would mean an npm install and a TypeScript
# toolchain, and the declarations are plain object literals.


def collect_frontend(adapter_dir: Path = FRONTEND_ADAPTER_DIR) -> FeatureMatrix:
    """Parse the frontend engine adapters into a feature matrix."""
    if not adapter_dir.is_dir():
        raise FileNotFoundError(
            f"frontend adapters not found: {adapter_dir} "
            "(expected in the monorepo; absent from the engine-only dist)"
        )

    types_file = adapter_dir / "types.ts"
    features = _parse_interface_keys(types_file, "BotFeatures")
    engine_types = _parse_engine_type_map(FRONTEND_ENGINE_TYPES)

    adapters: dict[str, AdapterFeatures] = {}
    for path in sorted(adapter_dir.glob("*Adapter.ts")):
        if path.name == "BaseEngineAdapter.ts":
            continue  # the abstract default, not an engine
        parsed = _parse_adapter(path, engine_types, adapter_dir)
        if parsed is not None:
            adapters[path.stem] = parsed

    order = _parse_registration_order(adapter_dir / "EngineAdapterFactory.ts")
    ordered = [adapters.pop(name) for name in order if name in adapters]
    ordered.extend(adapters[name] for name in sorted(adapters))

    return FeatureMatrix(
        source=f"static scan of `{_display_path(adapter_dir)}`",
        features=features,
        adapters=ordered,
    )


def _parse_adapter(
    path: Path, engine_types: dict[str, str], adapter_dir: Path
) -> AdapterFeatures | None:
    text = _strip_ts_comments(path.read_text(encoding="utf-8"))

    engine_type = _class_field(text, "engineType")
    if engine_type is None:
        warn(f"{path.name}: no engineType field; skipped")
        return None
    # `ENGINE_TYPE.HERMES` → "hermes"; a bare literal stays as written.
    if engine_type.startswith("ENGINE_TYPE."):
        key = engine_type.split(".", 1)[1]
        resolved = engine_types.get(key)
        if resolved is None:
            warn(f"{path.name}: unknown ENGINE_TYPE.{key}")
        engine_type = resolved or key.lower()

    adapter = AdapterFeatures(
        engine_type=engine_type,
        display=_class_field(text, "displayName") or "",
        beta=_class_field(text, "isBeta") == "true",
        default_model=_class_field(text, "defaultModelId") or "",
        origin=_display_path(path),
    )

    body = _object_literal(text, "baseFeatures")
    if body is None:
        warn(f"{path.name}: no baseFeatures literal found")
        return adapter
    for key, raw in _object_entries(body):
        adapter.values[key] = _parse_feature_value(raw)
    return adapter


def _parse_feature_value(raw: str) -> FeatureValue:
    """Classify one `baseFeatures` value: literal or `ConditionalFeature`."""
    raw = raw.strip()
    if raw.startswith("{"):
        inner = raw[1 : _match_bracket(raw, 0)]
        default = ""
        overrides: list[tuple[str, str]] = []
        for key, value in _object_entries(inner):
            if key == "defaultValue":
                default = _scalar(value)
            elif key == "overrides" and value.strip().startswith("["):
                overrides = _parse_overrides(value.strip())
        return FeatureValue(kind="conditional", default=default, overrides=overrides)

    scalar = _scalar(raw)
    if scalar in {"true", "false"}:
        return FeatureValue(kind="bool", default=scalar)
    if scalar.lstrip("-").isdigit():
        return FeatureValue(kind="number", default=scalar)
    return FeatureValue(kind="string", default=scalar)


def _parse_overrides(raw: str) -> list[tuple[str, str]]:
    """Read `[{ when: ctx => …, value: X }, …]` into (condition, value) pairs."""
    elements = _split_top_level(raw[1 : _match_bracket(raw, 0)])
    pairs: list[tuple[str, str]] = []
    for element in elements:
        element = element.strip()
        if not element.startswith("{"):
            continue
        condition = value = ""
        for key, item in _object_entries(element[1 : _match_bracket(element, 0)]):
            if key == "when":
                # Keep only the predicate body: `(ctx: FeatureContext) => X` → `X`.
                condition = " ".join(item.split("=>", 1)[-1].split())
            elif key == "value":
                value = _scalar(item)
        if condition:
            pairs.append((condition, value))
    return pairs


def _scalar(raw: str) -> str:
    """Normalise a literal: drop `as const` / `as T` casts and quotes."""
    raw = raw.strip().rstrip(",").strip()
    raw = re.sub(r"\s+as\s+[A-Za-z_][\w.]*\s*$", "", raw).strip()
    if len(raw) >= 2 and raw[0] in "'\"`" and raw[-1] == raw[0]:
        return raw[1:-1]
    return raw


def _parse_interface_keys(path: Path, name: str) -> list[str]:
    """Top-level property names of a TS interface, in declaration order."""
    text = _strip_ts_comments(path.read_text(encoding="utf-8"))
    match = re.search(rf"\binterface\s+{re.escape(name)}\s*{{", text)
    if not match:
        raise ValueError(f"no `interface {name}` in {path}")
    start = match.end() - 1
    body = text[start + 1 : _match_bracket(text, start)]
    return [key for key, _ in _object_entries(body, separator=";")]


def _parse_engine_type_map(path: Path) -> dict[str, str]:
    """`ENGINE_TYPE` constant → `{"HERMES": "hermes", …}`."""
    if not path.is_file():
        warn(f"engine type map not found: {path}")
        return {}
    text = _strip_ts_comments(path.read_text(encoding="utf-8"))
    match = re.search(r"\bENGINE_TYPE\b[^=]*=\s*{", text)
    if not match:
        warn(f"no ENGINE_TYPE constant in {path}")
        return {}
    start = match.end() - 1
    body = text[start + 1 : _match_bracket(text, start)]
    return {key: _scalar(value) for key, value in _object_entries(body)}


def _parse_registration_order(path: Path) -> list[str]:
    """Adapter class names in the order the factory registers them."""
    if not path.is_file():
        return []
    text = _strip_ts_comments(path.read_text(encoding="utf-8"))
    return re.findall(r"register\(\s*new\s+(\w+)\s*\(", text)


def _class_field(text: str, name: str) -> str | None:
    """Value of a simple `readonly <name> = <literal>;` class field."""
    match = re.search(rf"\b{re.escape(name)}\s*=\s*([^;\n]+)", text)
    return _scalar(match.group(1)) if match else None


def _object_literal(text: str, name: str) -> str | None:
    """Body of `<name> ... = {...}`, braces excluded."""
    match = re.search(rf"\b{re.escape(name)}\b[^=;{{]*=\s*{{", text)
    if not match:
        return None
    start = match.end() - 1
    return text[start + 1 : _match_bracket(text, start)]


def _object_entries(body: str, separator: str = ",") -> list[tuple[str, str]]:
    """Split an object body into `(key, raw value)` at nesting depth zero."""
    entries: list[tuple[str, str]] = []
    for chunk in _split_top_level(body, separator):
        chunk = chunk.strip()
        if not chunk:
            continue
        key, sep, value = _split_key(chunk)
        if not sep:
            continue
        entries.append((key, value.strip()))
    return entries


def _split_key(chunk: str) -> tuple[str, bool, str]:
    """Split `key: value` on the first colon outside brackets and strings."""
    depth = 0
    for index, char in enumerate(_mask_strings(chunk)):
        if char in "{[(":
            depth += 1
        elif char in "}])":
            depth -= 1
        elif char == ":" and depth == 0:
            key = chunk[:index].strip().strip("'\"?")
            return key, True, chunk[index + 1 :]
    return chunk, False, ""


def _split_top_level(body: str, separator: str = ",") -> list[str]:
    """Split on `separator` at depth zero, ignoring separators in strings."""
    masked = _mask_strings(body)
    parts: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(masked):
        if char in "{[(":
            depth += 1
        elif char in "}])":
            depth -= 1
        elif char == separator and depth == 0:
            parts.append(body[start:index])
            start = index + 1
    parts.append(body[start:])
    return parts


def _match_bracket(text: str, start: int) -> int:
    """Index of the bracket closing the one at `start`."""
    pairs = {"{": "}", "[": "]", "(": ")"}
    opening = text[start]
    closing = pairs[opening]
    masked = _mask_strings(text)
    depth = 0
    for index in range(start, len(text)):
        if masked[index] == opening:
            depth += 1
        elif masked[index] == closing:
            depth -= 1
            if depth == 0:
                return index
    raise ValueError(f"unbalanced {opening!r} at offset {start}")


def _mask_strings(text: str) -> str:
    """Blank out string literals so brackets inside them don't count."""
    out = []
    quote = None
    escaped = False
    for char in text:
        if quote:
            out.append(" ")
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in "'\"`":
            quote = char
            out.append(" ")
        else:
            out.append(char)
    return "".join(out)


def _strip_ts_comments(text: str) -> str:
    """Remove `//` and `/* */` comments, leaving string literals intact."""
    out = []
    index = 0
    length = len(text)
    quote = None
    while index < length:
        char = text[index]
        if quote:
            out.append(char)
            if char == "\\" and index + 1 < length:
                out.append(text[index + 1])
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
        elif char in "'\"`":
            quote = char
            out.append(char)
            index += 1
        elif text.startswith("//", index):
            end = text.find("\n", index)
            index = length if end == -1 else end
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = length if end == -1 else end + 2
        else:
            out.append(char)
            index += 1
    return "".join(out)


# ──────────────────────────────────────────────────────────────────────
# Rendering
# ──────────────────────────────────────────────────────────────────────


def _domain(capability: str) -> str:
    return capability.split(".", 1)[0]


def render_markdown(matrix: Matrix, *, notes: bool = True) -> str:
    out = io.StringIO()
    labels = [e.label for e in matrix.engines]

    out.write("# Engine capability matrix\n\n")
    out.write(f"<!-- Generated by {GENERATOR}. Do not edit by hand. -->\n\n")
    out.write(
        "Each engine declares an `EngineCapabilities` (see "
        "`core/engine/capability.py`); this table is derived from those "
        "declarations.\n\n"
    )
    out.write(f"Source: {matrix.source}\n\n")

    if not matrix.engines:
        out.write("_No engines found._\n")
        return out.getvalue()

    # Per-engine note numbering, so cells stay narrow and the caveat text —
    # which is free-form and sometimes long — lives below the table.
    note_index: dict[tuple[str, str], int] = {}
    for engine in matrix.engines:
        counter = 0
        for capability in matrix.capabilities:
            if engine.note(capability):
                counter += 1
                note_index[(engine.label, capability)] = counter

    out.write("| Domain | Capability | " + " | ".join(labels) + " |\n")
    out.write("|---|---|" + "|".join([":--:"] * len(labels)) + "|\n")

    previous = None
    for capability in matrix.capabilities:
        domain = _domain(capability)
        shown = f"**{domain}**" if domain != previous else ""
        previous = domain
        cells = []
        for engine in matrix.engines:
            mark = STATUS_MARKS[engine.status(capability)]
            index = note_index.get((engine.label, capability))
            cells.append(f"{mark} ({index})" if index else mark)
        out.write(f"| {shown} | `{capability}` | " + " | ".join(cells) + " |\n")

    out.write(
        f"\n**Legend:** {MARK_SUPPORTED} supported · {MARK_LIMITED} limited "
        f"(caveat below) · {MARK_FALLBACK} unsupported, documented workaround "
        f"(HTTP 501 with the message) · {MARK_UNSUPPORTED} unsupported\n"
    )

    if notes and note_index:
        out.write("\n## Notes\n")
        for engine in matrix.engines:
            entries = [
                (index, capability)
                for (label, capability), index in note_index.items()
                if label == engine.label
            ]
            if not entries:
                continue
            out.write(f"\n### {engine.label}\n\n")
            for index, capability in sorted(entries):
                kind = MARK_LIMITED if capability in engine.limited else MARK_FALLBACK
                note = " ".join(engine.note(capability).split())
                out.write(f"{index}. {kind} `{capability}` — {note}\n")

    out.write("\n## Engines\n\n")
    out.write("| Engine | Version | Declared | Source |\n|---|---|--:|---|\n")
    for engine in matrix.engines:
        out.write(
            f"| {engine.label} | {engine.version or '—'} | "
            f"{len(engine.declared())} | `{engine.origin}` |\n"
        )
    return out.getvalue()


def render_json(matrix: Matrix) -> str:
    payload = {
        "generated_by": GENERATOR,
        "source": matrix.source,
        "capabilities": matrix.capabilities,
        "engines": [
            {
                "name": engine.name,
                "label": engine.label,
                "version": engine.version,
                "profile": engine.profile,
                "origin": engine.origin,
                "supported": sorted(engine.supported),
                "limited": dict(sorted(engine.limited.items())),
                "fallback": dict(sorted(engine.fallback.items())),
            }
            for engine in matrix.engines
        ],
        "matrix": {
            capability: {
                engine.label: engine.status(capability) for engine in matrix.engines
            }
            for capability in matrix.capabilities
        },
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def render_csv(matrix: Matrix) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["domain", "capability", "engine", "status", "note"])
    for capability in matrix.capabilities:
        for engine in matrix.engines:
            writer.writerow(
                [
                    _domain(capability),
                    capability,
                    engine.label,
                    engine.status(capability),
                    " ".join(engine.note(capability).split()),
                ]
            )
    return out.getvalue()


RENDERERS = {
    "markdown": lambda m: render_markdown(m),
    "json": render_json,
    "csv": render_csv,
}


def _feature_cell(value: FeatureValue) -> str:
    """One `BotFeatures` cell: the value `getFeatures()` returns by default."""
    if not value.declared:
        return MARK_UNDECLARED
    if value.kind == "bool":
        return MARK_SUPPORTED if value.default == "true" else MARK_UNSUPPORTED
    if value.kind == "conditional":
        mark = MARK_SUPPORTED if value.default == "true" else MARK_UNSUPPORTED
        return f"{mark}{MARK_CONDITIONAL}"
    return f"`{value.default}`"


def render_frontend_markdown(matrix: FeatureMatrix, *, notes: bool = True) -> str:
    out = io.StringIO()
    labels = [adapter.label for adapter in matrix.adapters]

    out.write("# Frontend engine feature matrix\n\n")
    out.write(f"<!-- Generated by {GENERATOR}. Do not edit by hand. -->\n\n")
    out.write(
        "Each engine adapter under `src/frontend/src/adapters/engine/` declares a "
        "`BotFeatures` record that gates the UI. This is a **separate vocabulary** "
        "from the engine's `Capability` enum — see "
        "`src/engine/docs/engine-capability-matrix.md` for that one.\n\n"
    )
    out.write(
        "Cells show the context-free default, i.e. what `getFeatures()` returns. "
        f"{MARK_CONDITIONAL} marks a `ConditionalFeature` whose value changes with "
        "the bot's kind; the branches are listed under Notes.\n\n"
    )
    out.write(f"Source: {matrix.source}\n\n")

    if not matrix.adapters:
        out.write("_No adapters found._\n")
        return out.getvalue()

    note_index: dict[tuple[str, str], int] = {}
    for adapter in matrix.adapters:
        counter = 0
        for feature in matrix.features:
            value = adapter.value(feature)
            if value.overrides or not value.declared:
                counter += 1
                note_index[(adapter.label, feature)] = counter

    out.write("| Feature | " + " | ".join(labels) + " |\n")
    out.write("|---|" + "|".join([":--:"] * len(labels)) + "|\n")
    for feature in matrix.features:
        cells = []
        for adapter in matrix.adapters:
            cell = _feature_cell(adapter.value(feature))
            index = note_index.get((adapter.label, feature))
            cells.append(f"{cell} ({index})" if index else cell)
        out.write(f"| `{feature}` | " + " | ".join(cells) + " |\n")

    out.write(
        f"\n**Legend:** {MARK_SUPPORTED} true · {MARK_UNSUPPORTED} false · "
        f"{MARK_CONDITIONAL} varies by bot context · {MARK_UNDECLARED} not "
        "declared by this adapter (a subclass `baseFeatures` replaces the base "
        "wholesale, so an omitted key resolves to `false`, or to `undefined` for "
        "`chatRenderMode` / `messagePageSize`, which are read directly)\n"
    )

    if notes and note_index:
        out.write("\n## Notes\n")
        for adapter in matrix.adapters:
            entries = [
                (index, feature)
                for (label, feature), index in note_index.items()
                if label == adapter.label
            ]
            if not entries:
                continue
            out.write(f"\n### {adapter.label}\n\n")
            for index, feature in sorted(entries):
                value = adapter.value(feature)
                if not value.declared:
                    out.write(f"{index}. {MARK_UNDECLARED} `{feature}` — not declared\n")
                    continue
                branches = "; ".join(
                    f"`{condition}` → `{result}`" for condition, result in value.overrides
                )
                out.write(
                    f"{index}. {MARK_CONDITIONAL} `{feature}` — "
                    f"default `{value.default}`, {branches}\n"
                )

    out.write("\n## Adapters\n\n")
    out.write(
        "| Engine type | Display name | Beta | Default model | Source |\n"
        "|---|---|:--:|---|---|\n"
    )
    for adapter in matrix.adapters:
        out.write(
            f"| {adapter.engine_type} | {adapter.display or '—'} | "
            f"{'yes' if adapter.beta else '—'} | "
            f"{adapter.default_model or '—'} | `{adapter.origin}` |\n"
        )
    return out.getvalue()


def render_frontend_json(matrix: FeatureMatrix) -> str:
    payload = {
        "generated_by": GENERATOR,
        "source": matrix.source,
        "features": matrix.features,
        "adapters": [
            {
                "engineType": adapter.engine_type,
                "displayName": adapter.display,
                "isBeta": adapter.beta,
                "defaultModelId": adapter.default_model,
                "origin": adapter.origin,
                "features": {
                    feature: {
                        "kind": adapter.value(feature).kind,
                        "default": adapter.value(feature).default,
                        "overrides": [
                            {"when": condition, "value": result}
                            for condition, result in adapter.value(feature).overrides
                        ],
                    }
                    for feature in matrix.features
                },
            }
            for adapter in matrix.adapters
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def render_frontend_csv(matrix: FeatureMatrix) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["feature", "engine", "kind", "default", "overrides"])
    for feature in matrix.features:
        for adapter in matrix.adapters:
            value = adapter.value(feature)
            writer.writerow(
                [
                    feature,
                    adapter.label,
                    value.kind,
                    value.default,
                    "; ".join(f"{c} -> {v}" for c, v in value.overrides),
                ]
            )
    return out.getvalue()


FRONTEND_RENDERERS = {
    "markdown": lambda m: render_frontend_markdown(m),
    "json": render_frontend_json,
    "csv": render_frontend_csv,
}


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────


def build_matrix(source: str, profile: str | None = None) -> Matrix:
    if source == "static":
        return collect_static()
    if source == "runtime":
        return collect_runtime(profile)
    try:
        return collect_runtime(profile)
    except Exception as exc:  # noqa: BLE001 - any import failure means fall back
        warn(f"runtime collection unavailable ({exc}); falling back to static scan")
        return collect_static()


def filter_engines(matrix: Matrix, wanted: list[str]) -> Matrix:
    selected = [e for e in matrix.engines if e.name in wanted or e.label in wanted]
    missing = sorted(set(wanted) - {e.name for e in selected} - {e.label for e in selected})
    if missing:
        available = ", ".join(e.label for e in matrix.engines) or "none"
        raise SystemExit(
            f"unknown engine(s): {', '.join(missing)} (available: {available})"
        )
    return Matrix(source=matrix.source, capabilities=matrix.capabilities, engines=selected)


def filter_adapters(matrix: FeatureMatrix, wanted: list[str]) -> FeatureMatrix:
    selected = [a for a in matrix.adapters if a.engine_type in wanted]
    missing = sorted(set(wanted) - {a.engine_type for a in selected})
    if missing:
        available = ", ".join(a.engine_type for a in matrix.adapters) or "none"
        raise SystemExit(
            f"unknown engine(s): {', '.join(missing)} (available: {available})"
        )
    return FeatureMatrix(
        source=matrix.source, features=matrix.features, adapters=selected
    )


def render_target(
    target: str, args: argparse.Namespace, wanted: list[str] | None
) -> str:
    """Collect and render one target."""
    if target == "engine":
        matrix = build_matrix(args.source, args.profile)
        if wanted:
            matrix = filter_engines(matrix, wanted)
        if args.format == "markdown":
            return render_markdown(matrix, notes=not args.no_notes)
        return RENDERERS[args.format](matrix)

    features = collect_frontend()
    if wanted:
        features = filter_adapters(features, wanted)
    if args.format == "markdown":
        return render_frontend_markdown(features, notes=not args.no_notes)
    return FRONTEND_RENDERERS[args.format](features)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the engine capability matrix from the code.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--format",
        "-f",
        choices=sorted(RENDERERS),
        default="markdown",
        help="output format (default: markdown)",
    )
    parser.add_argument(
        "--source",
        choices=("auto", "runtime", "static"),
        default="auto",
        help="read live declarations (runtime), parse the tree (static), "
        "or try runtime and fall back (default: auto)",
    )
    parser.add_argument(
        "--profile",
        choices=("community", "corp", "test"),
        help="ENGINE_PROFILE to collect under; runtime mode only",
    )
    parser.add_argument(
        "--target",
        choices=("engine", "frontend", "all"),
        default="engine",
        help="which matrix: the engines' EngineCapabilities (engine, the "
        "default), the frontend adapters' BotFeatures (frontend), or both "
        "(all; markdown and json only)",
    )
    parser.add_argument(
        "--engines",
        help="comma-separated engine names to include (default: all)",
    )
    parser.add_argument(
        "--no-notes",
        action="store_true",
        help="markdown only: omit the limitation/fallback notes section",
    )
    parser.add_argument("--output", "-o", type=Path, help="write to this file")
    parser.add_argument(
        "--check",
        type=Path,
        help="compare against this file and exit 1 when it is stale "
        "(CI drift gate; writes nothing)",
    )
    args = parser.parse_args(argv)

    if args.target == "all" and args.format == "csv":
        parser.error("--target all has no single csv shape; pick one target")

    wanted = (
        [name.strip() for name in args.engines.split(",") if name.strip()]
        if args.engines
        else None
    )

    if args.target == "all":
        if args.format == "json":
            rendered = json.dumps(
                {
                    "engine": json.loads(render_target("engine", args, wanted)),
                    "frontend": json.loads(render_target("frontend", args, wanted)),
                },
                indent=2,
                ensure_ascii=False,
            ) + "\n"
        else:
            rendered = (
                render_target("engine", args, wanted)
                + "\n---\n\n"
                + render_target("frontend", args, wanted)
            )
    else:
        rendered = render_target(args.target, args, wanted)

    if args.check:
        current = args.check.read_text(encoding="utf-8") if args.check.is_file() else ""
        if current != rendered:
            warn(
                f"{args.check} is out of date; regenerate with "
                f"`python {GENERATOR} --target {args.target} -o {args.check}`"
            )
            return 1
        print(f"{args.check} is up to date")
        return 0

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
