# Backend runtime targeting

This map covers Backend requests that address an existing Bot runtime, with Cron
as a concrete example. It is not a universal session or provisioning algorithm:
each API retains its own identity, stage and device-selection contract.

## Participants and ownership

| Participant | Responsibility and source |
| --- | --- |
| Backend HTTP adapter | Decode the request and establish its authenticated identity; see [Backend interfaces](../../apps/backend/src/agentclaw/community/api/README.md). |
| Owning core domain | Apply business authorization and target semantics; [Cron](../../apps/backend/src/agentclaw/community/core/cron/README.md) is the example here. |
| Runtime binding resolver | Resolve a trusted request to an existing binding; see [boundary](../../apps/backend/src/agentclaw/community/core/runtime_binding/README.md) and [implementation](../../apps/backend/src/agentclaw/community/core/runtime_binding/service.py). |
| Device service and transport | Resolve the provider/device context and invoke the selected runtime through the [transport contract](../../apps/backend/src/agentclaw/community/plugin_api/device_adapter_transport.py). |
| Provider and runtime adapter | Supply the actual endpoint and runtime operation; selection is made by the checkout's DI/configuration, not by this diagram. |

## Cron request path

```text
HTTP request + authenticated user
  → Cron authorization and requested target
  → existing runtime binding
  → provider-specific device target / transport
  → selected runtime's Cron operation
  → target result or explicit failure
```

1. Keep the Bot owner, authenticated operator and requested Caller separate.
   Read the Cron boundary for permission and stage rules before choosing a target.
2. For resolver-backed selection, the binding resolver distinguishes personal,
   service draft/verify/online and Caller instances. It checks binding existence
   and ACTIVE status. It does not provision a runtime or select a physical device.
3. Cron's [runtime target handling](../../apps/backend/src/agentclaw/community/core/cron/services/cron_runtime_targets.py)
   expands the binding according to provider behavior. An explicit device UUID
   is validated against the selected runtime before forwarding; see
   [targeted operations](../../apps/backend/src/agentclaw/community/core/cron/services/cron_targeted_operations.py).
4. The transport invokes the selected target. Preserve target identity when
   interpreting results; partial list failures are not an empty successful list.

## Failure boundaries

- A missing Bot, stage binding or ready Caller instance is a target-resolution
  failure. It does not prove that a downstream request was made.
- ACTIVE means the binding passed a metadata check; it does not prove that the
  adapter is listening or that a business operation succeeds.
- A transport timeout does not by itself establish whether a write took effect.
  Inspect the specific operation before retrying it.
- A replacement container can have different container-local resources. A Cron
  identifier must be interpreted with its runtime target, not only its Bot ID.

## Verification and next reading

Use the [runtime-target diagnostic](../../apps/backend/docs/diagnostics/runtime-targeting.md)
for missing resources, wrong-instance results and timeouts. Existing focused
checks include [binding resolution](../../apps/backend/tests/community/core/runtime_binding/test_service.py),
[targeted Cron operations](../../apps/backend/tests/community/core/cron/services/test_cron_targeted_operations.py)
and the [transport contract](../../apps/backend/tests/community/contracts/test_device_adapter_transport.py).
These are verification entry points, not a statement that a live provider path
has been tested. Host integrations must also check their own bindings and build.
