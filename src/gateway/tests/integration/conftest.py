from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client() -> Generator[TestClient, None, None]:
    from gateway.community.adapters.web.app import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c
