# Tasks

Status: implementation and focused checks performed locally; see [delivery](delivery.md).
Unchecked tasks remain pending; no deployment is implied.

## Public implementation

- [x] Add `with_catalog_changes` to A2A registration and mutation cores.
- [x] Notify once after registration/replace/successful CAS; no signal on failure.
- [x] Add safe Core notification-call-site logs using only available fields.
- [x] Pass existing queue through public A2A assembly for both REST adapters.
- [x] Add shared queue bootstrap injection and extensions-capable startup wrapper.
- [ ] Cover default/embedded/disabled/V1 initialization and single-worker lifecycle.
- [x] Add actual Fuse HTTP start/success/failure summaries under `bcs_observation`.
- [x] Validate status/body acknowledgments without logging Card payloads or secrets.

## Minimal embedding-host integration

- [x] Follow user override: edit the specified existing host worktree after checking disk capacity.
- [ ] Update the host's pinned public revision to include the implementation.
- [x] Pass one shared queue through startup and independent A2A core assembly.
- [x] Keep RPC handlers, proto, mappings and business policy unchanged.
- [x] Run real RPC register/update/refresh integration with a consumed queue.

## Validation

- [x] V1 REST register, update, visibility-only update, explicit refresh -> Fuse.
- [x] Legacy REST registration/update/refresh smoke.
- [x] Explicit unchanged refresh and automatic no-write refresh semantics.
- [x] Auth/validation/duplicate/fetch/transaction/CAS failures -> no notification.
- [x] Successful commit followed by read-back/response failure still notifies.
- [x] PUT/DELETE ack, non-2xx, timeout, invalid JSON and negative ack logs.
- [x] Redaction, bounded strings and actual observation-file filter coverage.
- [x] Coalescing/retry, Group publication and disabled/V1 regressions.
- [x] No logging-only DB reads, extra HTTP calls or new schema changes.
- [ ] Focused public/host tests and architecture gates; record blocked/not-run items.
- [ ] Update owning docs and record source/build/deployment evidence separately.

## Initial specification-only work (historical)

- Checked public source and independent RPC assembly, and verified HTTP logging gap.
- Created isolated documentation worktree; no source edits or Rust builds.
- At specification creation, implementation and all validation were NOT_RUN.
  Subsequent results are recorded in delivery.md; deployment remains NOT_RUN.
