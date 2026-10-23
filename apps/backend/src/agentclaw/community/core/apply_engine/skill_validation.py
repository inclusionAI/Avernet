"""One place that turns the package validator's refusals into an entry's reason.

The Manifest compiler asks it of a package it just fetched; the skills
materialiser asks it again of the stored tree before uploading. One helper so
the two refusals read the same and cannot drift.
"""

from __future__ import annotations

from typing import Any, Callable

from agentclaw.community.core.skill_center.skill_package import (
    SkillPackageInvalidError,
    SkillPackageTooLargeError,
    ValidatedSkillPackage,
)

__all__ = ["PackageRefusal", "validate_package"]


class PackageRefusal(Exception):
    """These bytes can never become a skill package — refuse the entry with ``str(exc)``."""


def validate_package(call: Callable[[Any], ValidatedSkillPackage], payload: Any) -> ValidatedSkillPackage:
    """One validator call, refused as an entry failure rather than a mid-write surprise."""
    try:
        return call(payload)
    except SkillPackageInvalidError as exc:
        raise PackageRefusal(f"the skill is not a valid package ({exc.reason})") from exc
    except SkillPackageTooLargeError:
        raise PackageRefusal("the skill package is over the upload package limits") from None
