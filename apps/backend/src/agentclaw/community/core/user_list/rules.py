"""Subject and Bot scope validation shared by writes and eligibility checks."""
from __future__ import annotations

class MembershipConflictError(ValueError):
    """The existing unique-key collation equates distinct department paths."""


SUBJECT_TYPES = frozenset({"user", "department"})
RULE_BOT_TYPES = frozenset({"service", "personal", "all"})
ACTUAL_BOT_TYPES = frozenset({"service", "personal"})


def normalize_department_path(raw_path: str) -> str:
    path = raw_path.strip().rstrip("/")
    if (
        not path
        or len(path) > 1024
        or "*" in path
        or any(not segment.strip() for segment in path.split("/"))
    ):
        raise ValueError("Invalid department path")
    return path


def department_path_candidates(raw_path: str) -> tuple[str, ...]:
    parts = normalize_department_path(raw_path).split("/")
    return tuple("/".join(parts[:index]) for index in range(1, len(parts) + 1))


def validate_membership(entity_id: str, subject_type: str, bot_type: str) -> str:
    if subject_type not in SUBJECT_TYPES or bot_type not in RULE_BOT_TYPES:
        raise ValueError("Invalid membership subject or Bot type")
    if not entity_id or len(entity_id) > 1024:
        raise ValueError("Invalid membership entity")
    return normalize_department_path(entity_id) if subject_type == "department" else entity_id
