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
"""

from __future__ import annotations

import argparse
import ast
import csv
import io
import json
import os
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

MARK_SUPPORTED = "✅"
MARK_LIMITED = "⚠️"
MARK_FALLBACK = "↪"
MARK_UNSUPPORTED = "❌"

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

    matrix = build_matrix(args.source, args.profile)
    if args.engines:
        wanted = [name.strip() for name in args.engines.split(",") if name.strip()]
        matrix = filter_engines(matrix, wanted)

    if args.format == "markdown":
        rendered = render_markdown(matrix, notes=not args.no_notes)
    else:
        rendered = RENDERERS[args.format](matrix)

    if args.check:
        current = args.check.read_text(encoding="utf-8") if args.check.is_file() else ""
        if current != rendered:
            warn(
                f"{args.check} is out of date; regenerate with "
                f"`python {GENERATOR} -o {args.check}`"
            )
            return 1
        print(f"{args.check} is up to date ({len(matrix.engines)} engines)")
        return 0

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(
            f"wrote {args.output} ({len(matrix.engines)} engines, "
            f"{len(matrix.capabilities)} capabilities)",
            file=sys.stderr,
        )
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
