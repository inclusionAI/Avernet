"""Shared test data for E2E tests — common bot names and device configs."""

from typing import Any

DEFAULT_BOT_OPERATOR = "e2e-test"
DEFAULT_DEVICE_COUNT = 1
DEFAULT_CALLBACK_TIMEOUT = 120

DEFAULT_PAAS_DEVICE_CONFIG: dict[str, Any] = {
    "name": "e2e-test-device",
    "ttl_in_minutes": 60,
}

VALID_PAAS_DEVICE_CONFIGS: dict[str, dict[str, Any]] = {
    "ARCA": {
        "cpu": "2",
        "memory": "4Gi",
        "image": "test-image:latest",
    },
    "LOCAL": {
        "resource_dir": "/tmp/e2e-test",
    },
}

STANDARD_HTTP_METHODS = ["GET", "POST", "PUT", "DELETE"]
