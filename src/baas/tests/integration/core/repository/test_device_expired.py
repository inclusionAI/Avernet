"""Integration tests for DeviceRepository.list_expired_paginated.

Covers derived-deadline selection: per-bot extra_config.deploy_config.ttl_in_minutes
as primary lifetime, fallback to default_ttl_minutes, ACTIVE/ARCA/non-null-sandbox
constraints, and keyset pagination.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from secbaas.community.bootstrap import get_container
from secbaas.community.core.repository.device import DeviceRepository
from secbaas.community.core.utils.env_utils import get_current_env

pytestmark = pytest.mark.integration

TEST_ENV = get_current_env()


def _uid() -> str:
    return uuid4().hex


def _insert_device(
    *,
    provider_type: str = "ARCA",
    sandbox_id: str = "sbx-expired",
    extra_config: dict | None = None,
    provider_device_props: dict | None = None,
) -> int:
    device_repo = get_container().repository.device_repository()
    props = {"sandbox_id": sandbox_id}
    if provider_device_props:
        props.update(provider_device_props)
    return device_repo.insert_device(
        device_uuid=_uid(),
        tenant="tenant-expire",
        env=TEST_ENV,
        domain="test",
        creator="tester",
        modifier="tester",
        status="ACTIVE",
        provider_type=provider_type,
        provider_device_id=sandbox_id,
        provider_device_props=props,
        extra_config=extra_config,
    )


class TestExpiredPagination:
    def _repo(self) -> DeviceRepository:
        return get_container().repository.device_repository()

    def test_excludes_non_arca_providers(self):
        repo = self._repo()
        _insert_device(provider_type="ARCA", sandbox_id="sbx-arca")
        _insert_device(provider_type="TECLAW", sandbox_id="sbx-teclaw")

        result = repo.list_expired_paginated(default_ttl_minutes=0, grace_seconds=3600)
        provider_types = {row["provider_type"] for row in result}
        assert "TECLAW" not in provider_types

    def test_zero_ttl_makes_recent_device_due(self):
        # default_ttl_minutes=0 → deadline = gmt_create ≤ now(+grace), so due.
        repo = self._repo()
        device_id = _insert_device(sandbox_id="sbx-due", extra_config=None)

        result = repo.list_expired_paginated(default_ttl_minutes=0, grace_seconds=3600)
        ids = {row["id"] for row in result}
        assert device_id in ids

    def test_large_ttl_excludes_recent_device(self):
        # default_ttl_minutes=10080 → deadline far in future, not due.
        repo = self._repo()
        device_id = _insert_device(sandbox_id="sbx-not-due", extra_config=None)

        result = repo.list_expired_paginated(default_ttl_minutes=10080, grace_seconds=0)
        ids = {row["id"] for row in result}
        assert device_id not in ids

    def test_expired_ttl_timestamp_due(self):
        # A past ttl_expiration_timestamp marks the device due, regardless of
        # gmt_create / default TTL.
        import time as _time

        past_ms = int((_time.time() - 3600) * 1000)
        repo = self._repo()
        device_id = _insert_device(
            sandbox_id="sbx-ts-past",
            provider_device_props={"ttl_expiration_timestamp": past_ms},
        )

        result = repo.list_expired_paginated(default_ttl_minutes=10080, grace_seconds=0)
        ids = {row["id"] for row in result}
        assert device_id in ids

    def test_future_ttl_timestamp_not_due(self):
        # A future ttl_expiration_timestamp must keep the device alive even when
        # gmt_create + default would otherwise be expired (timestamp wins).
        import time as _time

        future_ms = int((_time.time() + 3600) * 1000)
        repo = self._repo()
        device_id = _insert_device(
            sandbox_id="sbx-ts-future",
            provider_device_props={"ttl_expiration_timestamp": future_ms},
        )

        result = repo.list_expired_paginated(default_ttl_minutes=0, grace_seconds=3600)
        ids = {row["id"] for row in result}
        assert device_id not in ids

    def test_per_bot_ttl_overrides_default(self):
        # A usable per-bot lifetime overrides default_ttl_minutes. Here the
        # per-bot TTL is large (7 days) while the default would be due (0):
        # the per-bot value must win, so the device is NOT expired.
        repo = self._repo()
        device_id = _insert_device(
            sandbox_id="sbx-perbot",
            extra_config={"deploy_config": {"ttl_in_minutes": 10080}},
        )

        result = repo.list_expired_paginated(default_ttl_minutes=0, grace_seconds=3600)
        ids = {row["id"] for row in result}
        assert device_id not in ids

    def test_missing_or_zero_ttl_falls_back_to_default(self):
        # When extra_config.deploy_config.ttl_in_minutes is missing or zero, the
        # row is ineligible via the per-bot lifetime and must fall back to default.
        repo = self._repo()
        missing_id = _insert_device(sandbox_id="sbx-missing", extra_config=None)
        zero_id = _insert_device(
            sandbox_id="sbx-zero",
            extra_config={"deploy_config": {"ttl_in_minutes": 0}},
        )

        # default=0 → both fall back to 0 → deadline = gmt_create → due.
        result = repo.list_expired_paginated(default_ttl_minutes=0, grace_seconds=3600)
        ids = {row["id"] for row in result}
        assert missing_id in ids
        assert zero_id in ids

    def test_missing_sandbox_skipped(self):
        # provider_device_props with no sandbox_id must be excluded.
        repo = self._repo()
        device_repo = get_container().repository.device_repository()
        device_repo.insert_device(
            device_uuid=_uid(),
            tenant="tenant-expire",
            env=TEST_ENV,
            domain="test",
            creator="tester",
            modifier="tester",
            status="ACTIVE",
            provider_type="ARCA",
            provider_device_id=None,
            provider_device_props={},
            extra_config=None,
        )

        result = repo.list_expired_paginated(default_ttl_minutes=0, grace_seconds=3600)
        sandboxes = {row["provider_device_id"] for row in result}
        assert None not in sandboxes

    def test_keyset_pagination(self):
        repo = self._repo()
        prefix = f"sbx-page-{_uid()[:6]}"
        ids = []
        for i in range(5):
            ids.append(_insert_device(sandbox_id=f"{prefix}-{i}"))

        collected = []
        last_id = 0
        while True:
            page = repo.list_expired_paginated(
                last_id=last_id, limit=2, default_ttl_minutes=0, grace_seconds=3600
            )
            if not page:
                break
            collected.extend(int(row["id"]) for row in page)
            last_id = max(int(row["id"]) for row in page)

        # the specifically-inserted due devices must all be visited, in ascending id order.
        own = [i for i in ids if i in collected]
        assert own == sorted(own)
