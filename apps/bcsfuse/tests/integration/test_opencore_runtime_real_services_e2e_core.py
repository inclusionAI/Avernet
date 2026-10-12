"""Opt-in HTTP acceptance for a user-selected deployment.

Run:
  BCSFUSE_RUN_EXTERNAL_ACCEPTANCE=1 BCSFUSE_ACCEPTANCE_URL=https://selected-host \
  BCSFUSE_AUTH_TOKEN=... .venv/bin/python -m pytest \
  tests/integration/test_opencore_runtime_real_services_e2e_core.py -q

No default localhost target, process restart, direct database access, or broad
cleanup. The same HTTP scenarios run automatically against isolated real stores
in test_isolated_runtime_acceptance.py. Model-provider smoke is separate.
"""

import os
from urllib.parse import urlparse

import httpx
import pytest

from tests.fixtures.runtime_acceptance import (
    acceptance_run, exercise_invalid_requests, exercise_lifecycle,
)


@pytest.fixture
def external_acceptance():
    if os.getenv("BCSFUSE_RUN_EXTERNAL_ACCEPTANCE") != "1":
        pytest.skip(
            "External deployment not selected: set BCSFUSE_RUN_EXTERNAL_ACCEPTANCE=1 "
            "and BCSFUSE_ACCEPTANCE_URL; isolated lifecycle acceptance runs separately"
        )
    url = os.getenv("BCSFUSE_ACCEPTANCE_URL", "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        pytest.fail("Explicit BCSFUSE_ACCEPTANCE_URL with http(s) scheme is required")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        pytest.fail("Acceptance URL must not contain credentials, query, or fragment")
    token = os.getenv("BCSFUSE_AUTH_TOKEN", "")
    if not token:
        pytest.fail("BCSFUSE_AUTH_TOKEN is required for authenticated acceptance")
    with httpx.Client(base_url=url.rstrip("/"), timeout=60, follow_redirects=False) as client:
        with acceptance_run(client, token) as acceptance:
            yield acceptance


def test_external_lifecycle_and_gateway_contract(external_acceptance):
    exercise_lifecycle(external_acceptance)


def test_external_auth_and_invalid_requests(external_acceptance):
    exercise_invalid_requests(external_acceptance)
