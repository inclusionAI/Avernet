"""Publish workflow enums.

Extracted from domain/model/publish.py as part of api/domain → api/{domain}/ refactoring.
"""

from enum import StrEnum


# ==================== Enums ====================
class PublishType(StrEnum):
    """Types of publish operations per D-06.

    Pipeline Configuration by Type:
    ┌──────────────┬─────────────────────────────────────────────────────────┬───────────────┐
    │ Type         │ Pipeline Stages                                         │ Approval Gates│
    ├──────────────┼─────────────────────────────────────────────────────────┼───────────────┤
    │ CREATE       │ PREPUB → GRAY → PROD_FIRST_BATCH → PROD_OTHER_BATCH    │ 3 gates       │
    │ UPDATE       │ PREPUB → GRAY → PROD_FIRST_BATCH → PROD_OTHER_BATCH    │ 3 gates       │
    │ RESTART      │ PROD_FIRST_BATCH → PROD_OTHER_BATCH                    │ 1 gate        │
    │ SCALE_UP     │ direct (single batch)                                   │ 0 gates       │
    │ SCALE_DOWN   │ direct (single batch)                                   │ 0 gates       │
    │ DESTROY      │ direct (single batch)                                   │ 0 gates       │
    │ STOP         │ direct (single batch)                                   │ 0 gates       │
    │ UPDATE_DEVICE│ direct (single batch)                                   │ 0 gates       │
    └──────────────┴─────────────────────────────────────────────────────────┴───────────────┘

    Notes:
    - CREATE/UPDATE use full 5-stage pipeline for progressive rollout
    - RESTART uses 2-stage pipeline (prod only)
    - SCALE_UP/DOWN/DESTROY/STOP/UPDATE_DEVICE bypass stage gates for immediate execution
    - Auto-compact reduces stages when device_count < stage_count
    """

    CREATE = "CREATE"
    UPDATE = "UPDATE"
    RESTART = "RESTART"
    SCALE_UP = "SCALE_UP"
    SCALE_DOWN = "SCALE_DOWN"
    DESTROY = "DESTROY"
    STOP = "STOP"
    UPDATE_DEVICE = "UPDATE_DEVICE"


class PublishStage(StrEnum):
    """Pipeline stages for publish workflow per D-01.

    - PREPUB: Pre-release validation (1-2 devices)
    - GRAY: Gray environment validation
    - PROD_FIRST_BATCH: First production batch, then PAUSE for approval
    - PROD_OTHER_BATCH: Remaining production batches
    - SUCCESS: All stages completed successfully
    """

    PREPUB = "PREPUB"
    GRAY = "GRAY"
    PROD_FIRST_BATCH = "PROD_FIRST_BATCH"
    PROD_OTHER_BATCH = "PROD_OTHER_BATCH"
    SUCCESS = "SUCCESS"


class PublishStatus(StrEnum):
    """Publish lifecycle status per D-02 (stage-gate approval).

    Status values:
    - PENDING: Initial state after creation, awaiting first approval
    - ACTIVE: Approved and executing stages
    - APPROVING: Paused at stage gate, awaiting next stage approval
    - REJECTED: Rejected by approver (terminal)
    - FAILED: Execution failed (terminal)
    - SUCCESS: All stages completed successfully (terminal)
    - REVOKED: Revoked after approval (terminal)

    State Transition Table:
    ┌────────────┬────────────────┬──────────────────────────────────────────┐
    │ From       │ Action         │ To                                       │
    ├────────────┼────────────────┼──────────────────────────────────────────┤
    │ PENDING    │ approve        │ ACTIVE (start execution)                 │
    │ PENDING    │ reject         │ REJECTED (terminal)                      │
    │ ACTIVE     │ stage_complete │ APPROVING (pause for gate approval)      │
    │ ACTIVE     │ all_complete   │ SUCCESS (terminal)                       │
    │ ACTIVE     │ fail           │ FAILED (terminal)                        │
    │ APPROVING  │ approve        │ ACTIVE (resume execution)                │
    │ APPROVING  │ reject         │ REJECTED (terminal)                      │
    │ APPROVING  │ revoke         │ REVOKED (terminal)                       │
    └────────────┴────────────────┴──────────────────────────────────────────┘

    Terminal States: REJECTED, FAILED, SUCCESS, REVOKED (no further transitions)

    See Also:
        PublishService.TRANSITIONS - Defines valid (status, action) → new_status mappings
    """

    PENDING = "PENDING"  # Created, awaiting initial approval
    ACTIVE = "ACTIVE"  # Approved, executing stages
    APPROVING = "APPROVING"  # Paused at stage gate, awaiting approval
    REJECTED = "REJECTED"  # Rejected by approver
    FAILED = "FAILED"  # Execution failed
    SUCCESS = "SUCCESS"  # All stages completed
    REVOKED = "REVOKED"  # Revoked after approval


class BatchStatus(StrEnum):
    """Batch execution status for publish batches.

    Per schema: baas_publish_batch.status
    - PENDING: Batch created, not started
    - RUNNING: Batch is currently executing
    - COMPLETED: Batch finished successfully
    - FAILED: Batch execution failed
    - ROLLED_BACK: Batch was rolled back due to failure
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


class PublishEventType(StrEnum):
    """Types of device-level operations in publish records.

    Per D-06: Different publish types trigger different device events.
    """

    CREATE = "CREATE"  # New device created (for CREATE/SCALE_UP)
    DESTROY = "DESTROY"  # Device destroyed (for SCALE_DOWN)
    RESTART = "RESTART"  # Device restarted
    UPDATE = "UPDATE"  # Device updated (destroy+create for UPDATE)
    UPDATE_DEVICE = "UPDATE_DEVICE"  # Device updated (destroy+create for UPDATE_DEVICE)
    START = "START"  # Device started
    STOP = "STOP"  # Device stopped


class PublishRecordResult(StrEnum):
    """Result status for publish record entries.

    - PENDING: Planned but not yet started (pre-created at create_publish)
    - PROCESSING: Operation initiated, result not yet known
    - SUCCESS: Operation completed successfully
    - FAILED: Operation failed

    Lifecycle: PENDING → PROCESSING → SUCCESS | FAILED
    """

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class RestartScope(StrEnum):
    """Scope for RESTART publish — controls which devices are restarted.

    - ALL: Restart all eligible devices (ACTIVE + FAILED)
    - UNHEALTHY: Restart only FAILED devices
    """

    ALL = "all"
    UNHEALTHY = "unhealthy"
