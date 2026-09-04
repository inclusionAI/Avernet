"""Pytest configuration: run the suite under the test config overlays.

Sets ``DEPLOY_ENV=test`` (merges ``config/application-test.yaml``) and
``SOFAPY_CONFIG_OVERLAY=e2e-sqlite`` (merges
``config/overlays/e2e-sqlite.yaml``) so ``load_config`` resolves an in-memory
SQLite + stub LLM and tests never touch the real database or network,
mirroring BAAS's ``configs/`` + ``configs/overlays/`` convention.
"""

from __future__ import annotations

import os

os.environ.setdefault("DEPLOY_ENV", "test")
os.environ.setdefault("SOFAPY_CONFIG_OVERLAY", "e2e-sqlite")
