"""Run the Q7 legacy baseline without enabling any new Center feature."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.community.compatibility.legacy_skill_harness import (
    LEGACY_COMPATIBILITY_MATRIX,
    LegacySkillFixtureFactory,
)


@pytest.mark.parametrize("case", LEGACY_COMPATIBILITY_MATRIX, ids=lambda case: case.id)
def test_legacy_skill_baseline(case, tmp_path: Path) -> None:
    fixture = LegacySkillFixtureFactory(tmp_path).create(case)
    fixture.assert_legacy_baseline()
