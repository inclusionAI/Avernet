"""Employee-number compatibility at the internal handoff input boundary."""

from agentclaw.community.utils.avernet_tenant import (
    DEFAULT_AVERNET_TENANT,
    get_current_avernet_tenant,
)
from agentclaw.community.utils.work_no import normalize_work_no_for_lookup


def owner_user_id(value: str) -> str:
    """Canonicalize only internal short ASCII employee numbers, never Bot keys.

    Other tenants, opaque IDs and longer numeric IDs retain exact identity.
    The same conversion is used for journal intent comparisons, without HR I/O.
    """
    if (
        get_current_avernet_tenant() == DEFAULT_AVERNET_TENANT
        and value.isascii() and value.isdigit() and len(value) <= 6
    ):
        return value.lstrip("0") or "0"
    return value


def directory_work_no(value: str) -> str:
    """Convert the internal employee ID only for staff-directory lookup."""
    if (
        get_current_avernet_tenant() == DEFAULT_AVERNET_TENANT
        and value.isascii() and value.isdigit() and len(value) <= 6
    ):
        return normalize_work_no_for_lookup(value)
    return value
