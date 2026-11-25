# ARCA lifecycle observation

## Symptom and distinction

A Bot recycle can remain `RELEASING`, then reach `NEEDS_CONFIRMATION`, even
when a logical device row is `RELEASED`. Compare the pinned provider ID and
its template/tenant suffix, the physical management-plane observation, and
the Bot/binding state. Logical deletion is not proof of physical destruction.

SDK connection operations commonly require `RUNNING`. Reading physical state
through `connect_sync_sandbox(...).get_info()` therefore cannot distinguish a
legitimately `DESTROYED` resource from an unavailable runtime. Do not classify
generic connection, authentication, transport errors, or gateway HTML 404s as
physical absence.

## Provider contract

Providers with a readiness-gated connect operation implement the optional
`ArcaSandboxInfoReader.get_sandbox_info(sandbox_id)` capability using their
management plane. It returns the existing `ArcaSandboxInfo` shape for non-running
resources too. Keep typed absence distinct from other failures and close any
management client on both success and failure. Existing providers without the
capability retain their connection-based behavior.

The existing PaaS device-info route preserves these physical states. Backend
recycle accepts either typed physical `DEVICE_NOT_FOUND` or an explicit
`DESTROYED` response whose sandbox identity matches the pinned target. Every
pinned physical target must satisfy this condition before normal transactional
completion sets the Bot to `RECYCLED` and releases its binding. `PAUSED`,
`FAILED`, `STOPPED`, logical `RELEASED`, and missing/mismatched identities do not
confirm destruction. No frontend change or new HTTP parameter is required.

## Rollout and limits

The shared PaaS capability and the configured SDK provider's implementation
must both be integrated. Updating only Backend's state classifier does not fix
a provider that still tries to connect to the destroyed runtime.

Already terminal `NEEDS_CONFIRMATION` operations are not automatically replayed
by this change. Verify their exact physical targets and follow the approved
reconciliation procedure; never repeat destroy or blindly write Bot status.

## Source and tests

- `src/secbaas/community/spi/sandbox/arca/_protocols.py`
- `src/secbaas/community/core/service/paas/_arca_paas_service.py`
- `tests/unit/core/service/paas/test_arca_device_inspection.py`
- Backend: `apps/backend/tests/community/core/bot_management/aicoding/test_recycle_runtime.py`
- Backend: `apps/backend/tests/community/core/bot_management/aicoding/test_recycle_task.py`
