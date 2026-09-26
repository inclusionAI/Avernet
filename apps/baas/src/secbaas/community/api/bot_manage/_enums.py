"""Bot domain enums."""

from enum import StrEnum


class SlaGrade:
    """SLA Grade Constants"""

    STANDARD = "standard"
    ENTERPRISE = "enterprise"


class BotDeviceStatus(StrEnum):
    """Aggregate device status for a bot.

    Describes the health of all devices attached to a bot, not the bot's
    lifecycle state (see BotStatus for that).

    Values:
    - ALL_ONLINE: Every device attached to the bot is ACTIVE
    - ALL_OFFLINE: Every device is FAILED, or no devices exist
    - PARTIAL_ONLINE: Mixed device statuses (any combination)
    """

    ALL_ONLINE = "ALL_ONLINE"
    ALL_OFFLINE = "ALL_OFFLINE"
    PARTIAL_ONLINE = "PARTIAL_ONLINE"


class BotStatus(StrEnum):
    """Bot status enumeration for baas_bot table.

    Status values:
    - PENDING: Initial state after bot creation (devices not yet started)
    - ACTIVE: Bot is operational with at least one active device
    - STOPPING: Bot stop in progress (stop publish created)
    - STOPPED: Bot has been stopped (provider devices destroyed, records preserved)
    - DESTROYING: Bot is being destroyed (destroy publish in progress)
    - FAILED: Bot creation or operation failed
    - RELEASED: Bot has been destroyed (terminal state)

    State Transition Table:
    ┌────────────┬─────────────┬──────────────────────────────────────┐
    │ From       │ To          │ Trigger                              │
    ├────────────┼─────────────┼──────────────────────────────────────┤
    │ PENDING    │ ACTIVE      │ CREATE publish SUCCESS               │
    │ PENDING    │ FAILED      │ CREATE publish FAILURE               │
    │ ACTIVE     │ STOPPING    │ stop_bot() API call                  │
    │ FAILED     │ STOPPING    │ stop_bot() API call                  │
    │ STOPPING   │ STOPPED     │ STOP publish SUCCESS                 │
    │ STOPPING   │ ACTIVE      │ STOP publish FAILURE (restore)       │
    │ ACTIVE     │ DESTROYING  │ destroy_bot() API call               │
    │ ACTIVE     │ FAILED      │ Operation failure                    │
    │ DESTROYING │ RELEASED    │ DESTROY publish SUCCESS              │
    │ DESTROYING │ DESTROYING  │ DESTROY publish retry (after reject) │
    └────────────┴─────────────┴──────────────────────────────────────┘

    Notes:
    - STOPPING/STOPPED: bot records preserved; STOPPED devices can be restarted/upgraded
    - DESTROYING is a retryable state: failed/rejected DESTROY publishes can be re-approved
    - RELEASED is terminal: bot is soft-deleted and cannot be recovered
    - FAILED is set when CREATE publish fails: bot transitions from PENDING to FAILED
    """

    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    DESTROYING = "DESTROYING"
    FAILED = "FAILED"
    RELEASED = "RELEASED"
