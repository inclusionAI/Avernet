"""Shared helper: glob-import every case module under ``tests/endpoints/``.

Used by both ``tests/framework/conftest.py`` (so the coverage gate
sees the full registry when the framework tests are run alone, e.g.
from a PyCharm runner targeting only ``tests/framework/``) and
``tests/endpoints/conftest.py`` (so case files register before the
parametrized runner is collected).

Idempotent: the per-file ``sys.modules`` check guarantees calling
this twice in one session does not produce duplicate
``@endpoint_test`` registrations.

Underscored filename so pytest does not collect this as a test module.
"""
from __future__ import annotations

import importlib
import pathlib
import sys


def load_endpoint_case_modules(endpoints_dir: pathlib.Path | None = None) -> None:
    """Import every ``test_*.py`` under ``endpoints_dir`` (excluding the runner
    host and conftest siblings) so their ``@endpoint_test`` decorators populate
    ``ENDPOINT_CASES``.

    B11 (3.2): discovery is **per-tree**. Each tree's endpoints conftest passes
    its own ``endpoints_dir`` so the community runner only ever loads community
    cases and the corp runner only corp cases — the two run under different deploy
    profiles (``test`` vs ``corp_test``), so a corp case must not register into the
    community runner (it would execute under the corp-free ``test`` column and
    fail). This also keeps the community discovery corp-free by construction: it
    never references ``tests/corp``. Defaults to this helper's own
    ``tests/community/endpoints`` when called with no argument.
    """
    if endpoints_dir is None:
        # ``tests/community/framework/_case_discovery.py`` → parent.parent is
        # tests/community.
        endpoints_dir = pathlib.Path(__file__).resolve().parent.parent / "endpoints"
    endpoints_dir = endpoints_dir.resolve()
    if not endpoints_dir.exists():
        return
    # ``src/backend`` is on sys.path, so module names are ``tests.<side>.…``.
    # tests/<side>/endpoints → three parents up is tests/ → its parent is backend.
    backend_root = endpoints_dir.parent.parent.parent
    for path in sorted(endpoints_dir.rglob("test_*.py")):
        if (
            path.name == "conftest.py"
            or path.name.startswith("_")
            or path.name == "test_endpoint_runner.py"
        ):
            continue
        rel = path.relative_to(backend_root)
        module_name = ".".join(rel.with_suffix("").parts)
        if module_name in sys.modules:
            continue
        importlib.import_module(module_name)
