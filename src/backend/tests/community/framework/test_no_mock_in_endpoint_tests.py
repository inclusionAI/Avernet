"""Forbid ``unittest.mock`` / monkey-patching inside ``tests/*/endpoints/``.

The endpoint-test framework exists so a case exercises the **real**
request path: real router, real dependency graph, real services, real
in-memory database. A case that reaches for ``unittest.mock.patch`` or
``monkeypatch`` stops testing that path — it asserts against a stand-in
the author wrote, so the test keeps passing while production code rots.

Sibling rule: :mod:`test_no_mock_on_world_get` forbids overwriting
attributes on an instance handed out by ``world.get(...)``. This module
covers the other half — reaching for the mock library (or ``setattr``)
at all, whatever the target.

What is forbidden in ``tests/*/endpoints/``:

1. **Mock-library imports** — ``from unittest.mock import patch``,
   ``from unittest import mock``, ``import mock``, including
   function-local imports, which are the usual way this sneaks back in.
2. **Mock-library usage** — ``patch(...)``, ``patch.object(...)``,
   ``MagicMock()``, ``AsyncMock()``, ``mock.patch(...)``,
   ``pytest.MonkeyPatch()``.
3. **The ``monkeypatch`` / ``mocker`` fixtures** — requested as a test
   or helper parameter.
4. **``setattr`` / ``delattr``** — an ``x.attr = ...`` patch spelled as
   a function call to dodge the sibling scanner.

What to do instead:

* **Seed through the real components.** ``world.get(SomeService)`` and
  the ``tests/*/factories/`` object-mothers write real rows, so the
  request under test reads real state.
* **Drive a system boundary through its DI seam.** Boundary plugins
  under ``plugins/local/`` inherit ``MockSeam``, so
  ``world.get(SomeBoundaryPlugin).set_response("method", value)`` /
  ``.set_override("method", fn)`` substitutes the *edge* of the system
  (an HTTP upstream, a device link) while everything inside it stays
  real. That is the sanctioned substitution point — it is bound through
  the injector, so the handler resolves it exactly as production does.
* **Bind a substitute in a testing DI module** when the seam does not
  exist yet.
* **Test an error path that the system can actually reach** — a denied
  permission, a missing row, a malformed body — rather than forcing an
  exception out of a patched method that no real input could produce.

If a boundary genuinely has no seam, add one to ``plugins/local/`` (or
a helper under ``tests/*/framework/``); do not re-open the mock door in
the case tree.

Detection is AST-only — comments, docstrings, and strings that merely
mention ``MagicMock`` are not violations.
"""
from __future__ import annotations

import ast
import pathlib

import pytest


_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]                # .../src/backend
# Endpoint cases live in the community and corp test trees; the rule is
# universal, so scan whichever of the two exists.
_ENDPOINTS_ROOTS = (
    _BACKEND_ROOT / "tests" / "community" / "endpoints",
    _BACKEND_ROOT / "tests" / "corp" / "endpoints",
)

# Modules whose whole purpose is fabricating stand-ins.
_MOCK_MODULES = frozenset({"unittest.mock", "mock"})

# Callables that build or install a stand-in, wherever they came from.
_MOCK_CALLABLES = frozenset({
    "patch",
    "MagicMock",
    "Mock",
    "AsyncMock",
    "NonCallableMock",
    "NonCallableMagicMock",
    "PropertyMock",
    "create_autospec",
    "mock_open",
    "seal",
    "MonkeyPatch",
})

# Fixtures that hand a case a patching API.
_MOCK_FIXTURES = frozenset({"monkeypatch", "mocker"})

# Builtins that perform an attribute patch without an ``x.attr = ...``
# assignment the sibling scanner would see.
_PATCH_BUILTINS = frozenset({"setattr", "delattr"})

_GUIDANCE = (
    "Endpoint cases must exercise the real request path end to end.\n"
    "Instead of a mock:\n"
    "  - seed real state via world.get(Service) / tests/*/factories/;\n"
    "  - substitute a system boundary through its DI seam —\n"
    "    world.get(SomeBoundaryPlugin).set_response(...) / .set_override(...);\n"
    "  - or assert an error path the system can actually reach.\n"
    "See tests/*/framework/README.md."
)


def _dotted_name(node: ast.AST) -> str:
    """Render ``a.b.c`` from an ``Attribute``/``Name`` chain (else ``\"\"``)."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return ""
    parts.append(node.id)
    return ".".join(reversed(parts))


class _Scanner(ast.NodeVisitor):
    """Collect ``(lineno, message)`` for every forbidden construct."""

    def __init__(self) -> None:
        self.hits: list[tuple[int, str]] = []

    # ---- imports ----------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name in _MOCK_MODULES:
                self.hits.append((
                    node.lineno, f"import {alias.name}  (mock library import)"
                ))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if module in _MOCK_MODULES:
            names = ", ".join(a.name for a in node.names)
            self.hits.append((
                node.lineno, f"from {module} import {names}  (mock library import)"
            ))
        elif module == "unittest":
            for alias in node.names:
                if alias.name == "mock":
                    self.hits.append((
                        node.lineno,
                        "from unittest import mock  (mock library import)",
                    ))
        self.generic_visit(node)

    # ---- calls ------------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        fn = node.func
        # ``patch.object(...)`` / ``mock.patch(...)`` / ``pytest.MonkeyPatch()``
        dotted = _dotted_name(fn)
        head = dotted.split(".", 1)[0] if dotted else ""
        tail = dotted.rsplit(".", 1)[-1] if dotted else ""
        if isinstance(fn, ast.Name):
            if fn.id in _MOCK_CALLABLES:
                self.hits.append((node.lineno, f"{fn.id}(...)  (builds a mock)"))
            elif fn.id in _PATCH_BUILTINS:
                self.hits.append((
                    node.lineno,
                    f"{fn.id}(...)  (patches an attribute at runtime)",
                ))
        elif dotted:
            if head in _MOCK_CALLABLES or tail in _MOCK_CALLABLES:
                self.hits.append((node.lineno, f"{dotted}(...)  (builds a mock)"))
        self.generic_visit(node)

    # ---- fixtures ---------------------------------------------------

    def _check_params(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        args = node.args
        for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
            if arg.arg in _MOCK_FIXTURES:
                self.hits.append((
                    node.lineno,
                    f"def {node.name}(..., {arg.arg}, ...)  "
                    f"(requests the {arg.arg} patching fixture)",
                ))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_params(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_params(node)
        self.generic_visit(node)


def scan_source(source: str, *, filename: str = "<case>") -> list[str]:
    """Return one ``"file:line  message"`` string per violation in ``source``.

    Exposed (rather than inlined into the test) so the scanner itself is
    testable against known-good and known-bad snippets.
    """
    tree = ast.parse(source, filename=filename)
    scanner = _Scanner()
    scanner.visit(tree)
    return [f"{filename}:{lineno}  {msg}" for lineno, msg in sorted(scanner.hits)]


def _scan_file(path: pathlib.Path) -> list[str]:
    try:
        source = path.read_text()
    except OSError:  # pragma: no cover — unreadable case file
        return []
    try:
        return scan_source(source, filename=path.relative_to(_BACKEND_ROOT).as_posix())
    except SyntaxError:  # pragma: no cover — collected elsewhere as an error
        return []


@pytest.mark.unit
def test_no_mock_or_patch_in_endpoint_tests() -> None:
    """No endpoint case may import or use a mocking/patching facility.

    The framework's guarantee is that a declared ``(method, path)`` was
    genuinely exercised. That guarantee is worth little if the code
    behind the route was swapped out for a stand-in before the request
    was issued.
    """
    roots = [r for r in _ENDPOINTS_ROOTS if r.is_dir()]
    assert roots, "no endpoints/ test dir found under tests/community or tests/corp"

    violations: list[str] = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            violations.extend(_scan_file(path))

    assert not violations, (
        f"Mocking/patching found in endpoint tests.\n{_GUIDANCE}\n"
        "Violations:\n  " + "\n  ".join(violations)
    )


# ── Scanner self-tests ────────────────────────────────────────────────
# The guard is only as trustworthy as its detector: a scanner that
# silently matches nothing would keep passing forever. These pin both
# directions.


@pytest.mark.unit
@pytest.mark.parametrize(
    "source",
    [
        pytest.param("from unittest.mock import patch\n", id="from_import"),
        pytest.param("from unittest import mock\n", id="from_unittest_import_mock"),
        pytest.param("import unittest.mock\n", id="plain_import"),
        pytest.param("import mock\n", id="third_party_mock"),
        pytest.param(
            "def seed(world):\n"
            "    from unittest.mock import patch\n"
            "    patch.object(X, 'y').start()\n",
            id="function_local_import",
        ),
        pytest.param("m = MagicMock()\n", id="magicmock_call"),
        pytest.param("m = AsyncMock(return_value=1)\n", id="asyncmock_call"),
        pytest.param("mock.patch('a.b')\n", id="dotted_patch"),
        pytest.param("patch.object(Svc, 'run')\n", id="patch_object"),
        pytest.param("pytest.MonkeyPatch().setattr(X, 'y', 1)\n", id="monkeypatch_cls"),
        pytest.param("def test_x(monkeypatch):\n    pass\n", id="monkeypatch_fixture"),
        pytest.param("def test_x(mocker):\n    pass\n", id="mocker_fixture"),
        pytest.param("setattr(svc, 'run', lambda: 1)\n", id="setattr_call"),
        pytest.param("delattr(svc, 'run')\n", id="delattr_call"),
    ],
)
def test_scanner_flags_forbidden_source(source: str) -> None:
    assert scan_source(source), f"scanner missed a violation in:\n{source}"


@pytest.mark.unit
@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            '"""Uses real DI services — no MagicMock / unittest.mock."""\n',
            id="docstring_mentioning_mocks",
        ),
        pytest.param("# patch.object(Svc, 'run') was removed\n", id="comment"),
        pytest.param(
            "def seed(world):\n"
            "    world.get(HttpClient).set_response('get', _resp())\n",
            id="di_seam_set_response",
        ),
        pytest.param(
            "def seed(world):\n"
            "    world.get(HttpClient).set_override('post', _fail)\n",
            id="di_seam_set_override",
        ),
        pytest.param(
            "def seed(world):\n"
            "    make_staff_user(world, user_id='u1')\n"
            "    world._seeded = 'u1'\n",
            id="real_seeding",
        ),
        pytest.param(
            "class _FakeFs:\n"
            "    def __init__(self):\n"
            "        self.files = {}\n",
            id="hand_written_stub_class",
        ),
        pytest.param("def test_x(world, async_client):\n    pass\n", id="real_fixtures"),
    ],
)
def test_scanner_allows_sanctioned_source(source: str) -> None:
    assert not scan_source(source), f"scanner false-positived on:\n{source}"
