"""Service API for the one-shot secbaas → gateway API-key migration.

The contract between the core migrator and whichever delivery layer exposes it.
Lives in ``api`` rather than ``core`` because the web adapter must read the
outcome, and adapters may not import ``core``.
"""

from ._models import (
    MigratedApp,
    MigratedGrant,
    MigrationOutcome,
    MigrationResult,
)

__all__ = [
    "MigratedApp",
    "MigratedGrant",
    "MigrationOutcome",
    "MigrationResult",
]
