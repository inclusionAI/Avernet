"""Architecture enforcement: no new files in secbaas.infra.

The ``infra/`` layer is deprecated — all infrastructure implementations
should be in ``plugins/`` or ``core/``.  Only ``__init__.py`` is allowed
to remain for backward-compatibility re-exports during migration.

Adding files to ``infra/`` reintroduces the core→infra coupling that
has been eliminated.  New infrastructure code must go in:
- ``plugins/``  — middleware-specific implementations
- ``core/``     — shared utilities migrated from infra
"""

from pathlib import Path

INFRA = Path(__file__).resolve().parents[2] / "src" / "secbaas" / "infra"

# Known file allowed to remain
_KNOWN_FILES = {"__init__.py"}


def _collect_new_infra_files() -> list[str]:
    """Scan infra/ for .py files not in the known allowlist."""
    if not INFRA.is_dir():
        return []  # infra dir removed entirely — even better

    new_files: list[str] = []
    for entry in sorted(INFRA.rglob("*.py")):
        rel = entry.relative_to(INFRA.parent)
        name = entry.name
        if name in _KNOWN_FILES:
            continue
        new_files.append(str(rel))
    return new_files


def test_no_new_python_files_in_infra():
    """Fail if any new .py file has been added to secbaas.infra."""
    new_files = _collect_new_infra_files()
    if new_files:
        raise AssertionError(
            f"\n{len(new_files)} new Python file(s) found in secbaas.infra:\n"
            + "\n".join(f"  {f}" for f in new_files)
            + "\n\ninfra/ is deprecated. Move new code to plugins/ or core/."
        )
