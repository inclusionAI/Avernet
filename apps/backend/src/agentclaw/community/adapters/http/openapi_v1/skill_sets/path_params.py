"""Validated public path parameters for SkillSet identifiers."""

from typing import Annotated

from fastapi import Path
from pydantic import AfterValidator


_MAX_SKILL_SET_ID = "2147483647"
_MAX_DECIMAL_ID_LENGTH = 4300


def _within_skill_set_id_range(value: str) -> str:
    significant = value.lstrip("0") or "0"
    if len(significant) > len(_MAX_SKILL_SET_ID) or (
        len(significant) == len(_MAX_SKILL_SET_ID)
        and significant > _MAX_SKILL_SET_ID
    ):
        raise ValueError("SkillSet identifier exceeds the supported integer range")
    return value


SetIdPath = Annotated[
    str,
    Path(
        max_length=_MAX_DECIMAL_ID_LENGTH,
        pattern=r"^[0-9]+$",
        description="Decimal SkillSet identifier.",
    ),
    AfterValidator(_within_skill_set_id_range),
]
