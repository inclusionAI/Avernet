# Device creation context

`api.device_manage.creation_context` carries an internal business operation
identity from the lifecycle service to a synchronous provider. It is not a
request schema field and is never reconstructed from caller metadata.

`CreationScopedDeviceService` wraps the existing start/update methods. It
resolves one non-deleted device record within the requested tenant/environment,
then binds its device UUID, previous provider ID and an operation ID. A publish
uses a stable per-device publish ID; an explicit operation without a publish ID
gets a fresh identity. Ambiguous records explicitly disable recovery. Both
normal and schedule-aware bootstrap variants use this service. The underlying
lifecycle and persistence behavior stays in the existing implementation.

The scope resets on success and failure, isolates concurrent asyncio tasks and
propagates into `asyncio.to_thread`. Absence of scope means normal creation is
allowed, but automatic termination of an occupant is not authorized.

A provider implementing exclusive NAS recovery must:

- capture the binding before the first creation attempt;
- act only on the exact storage-conflict failure, not a generic timeout;
- match the occupant's device, tenant, environment and storage to the trusted
  scope, refusing incomplete or conflicting identity;
- stamp newly created managed instances with the server operation identity,
  overriding caller values for reserved identity fields;
- protect instances from the same operation and new instances not matching
  the operation's expected previous provider ID;
- fix the release target to the original snapshot, reread before release,
  and never chase a changed binding;
- retain the allocation and files, wait for a confirmed detach, and retry
  creation at most once within the caller's budget;
- propagate query/release/recreate failures, with no storage deletion fallback.

The snapshot and operation marker make racing recoveries safe without a new
distributed lock or persistence ledger: they may contend to release the same
obsolete ID, but can never retarget its replacement. A later explicitly
authorized replacement can target the provider ID already persisted on its
device record. An uncommitted creation or a replay of the same publish is
protected. An unknown creation outcome requires reconciliation, not another
termination attempt.

This optional infrastructure behavior does not require local or other sandbox
providers to emulate remote NAS recovery. Provider tests must exercise a real
consumer and assert both resource outcomes and the actual adapter calls.

ARCA update retirement runs inside the same trusted scope. The factory selects
an ARCA adapter that defers NAS retirement until create can validate the pinned
binding and operation marker. Its destroy-phase acknowledgement does not mean
the NAS or instance has been deleted. Explicit deletion outside that scope keeps
its existing semantics; non-NAS replacements retain their existing retirement.
Unknown storage or mismatched retirement identity cannot authorize deletion.

Only plugins explicitly declaring `supports_nas_replacement_recovery = True`
opt into deferred NAS retirement; absence retains the existing lifecycle. Opt-in
requires implementing the ownership, replay and binding checks described above.
