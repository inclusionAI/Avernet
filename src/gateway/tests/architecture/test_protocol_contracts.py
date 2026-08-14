"""Architecture enforcement: Protocol contract checks via mypy.

Runs mypy over ``check_protocols/`` to verify that concrete
implementations structurally-satisfy their ``typing.Protocol``
definitions.  This turns a CI-only static check into a pytest test
that also appears in architecture test output.

Derived from the Microkernel Architecture Constitution:

- **Rule 3 / Rule 5** — Contracts are separate from implementations;
  Protocol definitions in ``spi/*/_protocols.py`` must be
  structurally compatible with their concrete implementations in
  ``plugins/``.
"""

import subprocess
import sys
from pathlib import Path

import pytest

CHECK_DIR = Path(__file__).parent / "check_protocols"


def test_protocols_satisfy_contracts() -> None:
    """Mypy structural subtype check: all implementations must satisfy
    their Protocol.

    mypy analyses each ``check_*.py`` file and verifies that the
    assigned concrete class is structurally compatible with the
    annotated Protocol type.  A non-zero exit code means at least
    one implementation diverges from its contract.
    """
    if not CHECK_DIR.is_dir():
        pytest.skip(
            "check_protocols directory does not exist yet — "
            "create src/gateway/tests/architecture/check_protocols/ "
            "and add structural subtype check files to enable this test"
        )

    result = subprocess.run(
        [sys.executable, "-m", "mypy", str(CHECK_DIR)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"Protocol contract check failed:\n"
        f"--- stdout ---\n{result.stdout}"
        f"--- stderr ---\n{result.stderr}"
    )
