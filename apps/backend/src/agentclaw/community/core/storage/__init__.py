"""Storage Path — Core layer storage utilities.

Temporary forwarding to services.storage during migration.
Full migration will implement proper Protocol-based abstraction.

According to README.md:
- core/ is the business logic layer
- Temporary forwarding during architecture migration
"""

# Forward import from services.storage (intermediate step during migration)
from agentclaw.community.core.storage import path

__all__ = ["path"]
