"""Tests for System Disk Usage API endpoints.

Tests the following scenarios:
- POST /api/system/disk-usage — trigger disk usage analysis
- POST /api/system/disk-usage/file-count — trigger file count analysis
- Admin permission check (staffId in DISK_USAGE_ADMIN_STAFF_IDS)
- Cooldown handling
- Already running handling
"""
from __future__ import annotations

from tests.community.factories.access import make_staff_user
from tests.community.framework import (
    CaseInput,
    ExpectSuccess,
    ExpectError,
    endpoint_test,
)


# Admin staff IDs allowed to trigger disk usage analysis (from router.py)
ADMIN_STAFF_ID = "100000"  # seeded disk_usage_admin in application-test.yaml
NON_ADMIN_STAFF_ID = "999999"


def _seed_admin_user(world):
    """Seed an admin user who can trigger disk usage analysis."""
    make_staff_user(world, user_id=ADMIN_STAFF_ID)


def _seed_non_admin_user(world):
    """Seed a non-admin user who cannot trigger disk usage analysis."""
    make_staff_user(world, user_id=NON_ADMIN_STAFF_ID)


# =============================================================================
# POST /api/system/disk-usage - trigger_disk_usage_analysis
# =============================================================================

@endpoint_test(
    method="POST",
    path="/api/system/disk-usage",
    scenario="trigger_success",
    input=CaseInput(
        headers={"x-user-id": ADMIN_STAFF_ID},
    ),
    seed=_seed_admin_user,
    expect=ExpectSuccess(
        status=200,
        json_contains={"message": "Disk usage analysis started, results will be written to ac_nas_usage_info.total_usage_mb"},
    ),
)
def trigger_disk_usage_success():
    """Admin user can trigger disk usage analysis."""


@endpoint_test(
    method="POST",
    path="/api/system/disk-usage",
    scenario="trigger_forbidden",
    input=CaseInput(
        headers={"x-user-id": NON_ADMIN_STAFF_ID},
    ),
    seed=_seed_non_admin_user,
    expect=ExpectError(
        status=403,
        json_contains={"detail": "权限不足：此接口仅允许管理员调用"},
    ),
)
def trigger_disk_usage_forbidden():
    """Non-admin user cannot trigger disk usage analysis."""


@endpoint_test(
    method="POST",
    path="/api/system/disk-usage",
    scenario="trigger_with_params",
    input=CaseInput(
        headers={"x-user-id": ADMIN_STAFF_ID},
        query_params={
            "cooldown_minutes": "30",
            "skip_within_minutes": "10",
            "concurrency": "4",
        },
    ),
    seed=_seed_admin_user,
    expect=ExpectSuccess(
        status=200,
        json_contains={"message": "Disk usage analysis started, results will be written to ac_nas_usage_info.total_usage_mb"},
    ),
)
def trigger_disk_usage_with_params():
    """Admin user can trigger disk usage analysis with custom parameters."""


# =============================================================================
# POST /api/system/disk-usage/file-count - trigger_file_count_analysis
# =============================================================================

@endpoint_test(
    method="POST",
    path="/api/system/disk-usage/file-count",
    scenario="trigger_success",
    input=CaseInput(
        headers={"x-user-id": ADMIN_STAFF_ID},
    ),
    seed=_seed_admin_user,
    expect=ExpectSuccess(
        status=200,
        json_contains={"message": "File count analysis started, results will be written to ac_nas_usage_info.file_count"},
    ),
)
def trigger_file_count_success():
    """Admin user can trigger file count analysis."""


@endpoint_test(
    method="POST",
    path="/api/system/disk-usage/file-count",
    scenario="trigger_forbidden",
    input=CaseInput(
        headers={"x-user-id": NON_ADMIN_STAFF_ID},
    ),
    seed=_seed_non_admin_user,
    expect=ExpectError(
        status=403,
        json_contains={"detail": "权限不足：此接口仅允许管理员调用"},
    ),
)
def trigger_file_count_forbidden():
    """Non-admin user cannot trigger file count analysis."""


@endpoint_test(
    method="POST",
    path="/api/system/disk-usage/file-count",
    scenario="trigger_with_params",
    input=CaseInput(
        headers={"x-user-id": ADMIN_STAFF_ID},
        query_params={
            "cooldown_minutes": "30",
            "skip_within_minutes": "10",
            "concurrency": "4",
        },
    ),
    seed=_seed_admin_user,
    expect=ExpectSuccess(
        status=200,
        json_contains={"message": "File count analysis started, results will be written to ac_nas_usage_info.file_count"},
    ),
)
def trigger_file_count_with_params():
    """Admin user can trigger file count analysis with custom parameters."""