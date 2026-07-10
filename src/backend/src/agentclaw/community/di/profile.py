"""Deployment profile — the single wiring driver.

``DeployProfile`` replaces the old two-dimension ``RuntimeConfig``
(``RuntimeMode`` × ``DatabaseMode``). It is a **pure selector**: the four
values name the meaningful points of the old cross-product, plus the new
community bucket. "What is wired" is answered by *which modules*
``modules_for(profile)`` installs — not by any derivation method on this
enum (no ``is_local`` / ``database`` properties: those would re-encode the
retired two-dimension model).

| Profile      | Runtime infrastructure         | Database |
| ------------ | ------------------------------ | -------- |
| ``corp``     | prod (Buservice / ZCache / …)  | ZDAS     |
| ``singlebox``| LOCAL stubs (corp-free)        | SQLite   |
| ``test``     | LOCAL stubs (corp-free)        | SQLite   |
| ``corp_test``| LOCAL stubs + corp doubles     | SQLite   |
| ``community``| community implementations      | (own)    |

``singlebox`` and ``test`` share the same corp-free local doubles except for two
explicit Profile-selected bindings: ``test`` keeps the base real policy service
and overrides HTTP with no-network ``LocalHttpClient`` doubles, while
``singlebox`` installs the all-open ``LocalPolicyService`` and deliberately
consumes the base real HTTP clients for its local services.

``corp_test`` is a **monorepo-only test profile**: the same fixed no-network HTTP
doubles and local stubs as ``test`` plus the corp-flavored modules (the corp reuse
column + the corp-flavored ``Test*`` doubles, e.g. the MagicMock ARCA sandbox
factory). It exists so the corp test suite (``tests/corp``) keeps its corp bindings
while ``test``/``singlebox`` ship corp-free. A community distribution never selects
it (it has no ``tests/corp`` and no ``agentclaw.corp``).

The profile is selected by the ``DEPLOY_PROFILE`` env var and read **once**
at the composition root. It is **mandatory** — an unset or unknown value is
a hard error, never a silent default.
"""
from __future__ import annotations

import os
from enum import Enum


class DeployProfile(Enum):
    CORP = "corp"
    SINGLEBOX = "singlebox"
    TEST = "test"
    CORP_TEST = "corp_test"
    COMMUNITY = "community"

    @classmethod
    def detect(cls) -> DeployProfile:
        """Resolve the profile from ``DEPLOY_PROFILE``.

        Mandatory: raises if the env var is unset or holds an unknown
        value. Call this exactly once, at the composition root.
        """
        raw = os.getenv("DEPLOY_PROFILE")
        if raw is None:
            raise RuntimeError(
                "DEPLOY_PROFILE must be set (corp|singlebox|test|corp_test|community)"
            )
        try:
            return cls(raw.strip().lower())
        except ValueError:
            raise RuntimeError(
                f"Unknown DEPLOY_PROFILE={raw!r}; "
                "expected one of corp|singlebox|test|corp_test|community"
            ) from None
