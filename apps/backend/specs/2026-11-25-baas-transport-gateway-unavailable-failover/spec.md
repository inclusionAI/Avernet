# BaaS transport: bounded device rotation on gateway-unavailable reads

- Date: 2026-11-25
- Scope: `apps/backend` — `agentclaw.community.core.devices.services.baas_invoke_transport`
- Type: resilience fix (recovery-rate improvement); the routing-side root cause is
  handed back separately (see "Out of scope" below)

> Incident-anonymization note: the original draft of this spec carried raw
> production evidence (real bot/device identifiers, trace ids, timestamps).
> Per the knowledge standard, production records stay in internal tracking;
> this public spec keeps only the anonymized fault scenario. Detailed
> evidence lives in the internal incident record.

## Problem

Anonymized fault scenario (shape observed in production; identifying
values replaced with synthetic examples):

- A bot has 3 sandbox devices registered `ACTIVE` in BaaS. One of them
  (`ARCA-SANDBOX-<example>@0`) answers 502 through the proxypass gateway —
  the ARCA sandbox gateway fails the connect ("Failed to connect sync
  sandbox") before any engine code runs.
- The BaaS auto-select (`select_active_device`) filters only on
  `status == "ACTIVE"`; it has no health signal that reaches selection (the
  periodic probe updates `provider_device_props.refresh_fail_count`, which
  selection does not read, and the sandbox's control-plane metadata stays
  responsive — TTL renewals even keep succeeding for the dead sandbox — so
  the probe never marks it unhealthy).
- Result: any read routed to that instance fails. Identity reads
  (`/api/file/read` via `BaasDeviceFileSystem`) logged
  `[IdentityService] publish read failed … 502 Bad Gateway` (a single burst
  of ERROR rows, all from one page load of identity files); ws-info selections
  hash onto the dead instance for sticky keys too. The caller-side API answers
  200 with degraded (fallback) content, so the failure surfaces only as ERROR
  logs / monitoring alerts.

End-to-end tracing showed the request failing at the sandbox gateway hop
with no downstream span and ≈0 gateway cost time: the verdict is "selected
device unreachable", not "request wrong".

## Solution

Bounded, read-only failover in `BaasInvokeTransport`:

- On a 502/503/504 response for `POST` on `/api/file/read` / `/api/file/list`
  (the idempotent engine read routes), when the transport resolved via BaaS
  auto-select (caller pinned no `device_uuid`) and knows its BaaS `bot_uuid`,
  it lists the bot's devices (`list_devices_by_bot_uuid`), picks another
  `ACTIVE` instance excluding the one that just failed (parsed from
  `http_info.target`), re-resolves `http_info` pinned to that instance, and
  retries exactly once.
- The rotated `http_info` replaces the per-path cache entry (same CAS
  discipline as the stale-token refresh), so the rest of a batch goes straight
  to the surviving device.
- Everything else keeps the existing contract:
  - mutating routes (`/api/file/upload|remove|rmtree`) never rotate — a replayed
    mutation could double-apply, against an instance the caller never named;
  - a caller-pinned `device_uuid` disables rotation (explicit targeting wins);
  - unparseable failed target / no other `ACTIVE` instance / list or resolve
    failure → the original gateway verdict propagates untouched;
  - 401 stale-token refresh and 403 device-answer passthrough are unchanged.
- Like `baas_device_header_updater`, the device-status comparison in the
  rotation path is case-normalized, so a BaaS reply whose status casing
  varies cannot silently disable rotation.

The resolver passes `bot_uuid=conn_info["paas_device_id"]` for provider `baas`
only; teclaw and desktop transports never rotate (their device ids are not
BaaS bot uuids; the desktop transparent proxy routes by bot, single VM).

Known, accepted gaps (noted for reviewers; follow-ups listed below):

- With ≥2 dead-but-`ACTIVE` instances ahead of a healthy one, consecutive
  failures can rotate between dead instances; each call still fails once and
  keeps the original verdict, but the stopgap does not converge. The single
  dead-instance shape observed in the incident is covered.
- Rotation runs after the 401 stale-token refresh branch: a 401-then-502
  sequence does not rotate, and a rotation replay that answers 401 does not
  trigger a further CAS refresh. Both verdicts remain observable to the
  caller; the contract is unchanged.

## Validation

- `tests/community/plugins/prod/test_baas_invoke_transport.py`: new cases for
  rotate-once success (all of 502/503/504), cache replacement for the batch,
  mutation paths never rotating, no-alternate / no-bot_uuid / pinned-device /
  unidentifiable-target passthrough, device-404 passthrough, resolver wiring,
  and status-case normalization of the device list.
- Local (`DEPLOY_PROFILE=test uv run pytest`): plugins/prod 121/121,
  core/devices/services 907/907, and the transport test file 43/43
  (including the status-case normalization case); 0 failures. The full
  gates run on the PR head in CI.

## Out of scope / follow-up (human handover)

- **BaaS-side selection root cause**: `select_active_device` (secbaas community)
  still routes to devices that are `ACTIVE` but unreachable, because no health
  signal reaches selection. Designing a health source for selection (e.g.
  persisting realtime probe results, or excluding devices by runtime failure
  feedback) is an architecture decision on the BaaS side, tracked on the
  internal incident work item rather than fixed here.
- **Stale sandbox data**: the dead sandbox instance remains attached and
  `ACTIVE` to its bot; its control plane accepts TTL renewals while the sync
  channel is dead. Releasing/cleaning that device row is an ops action on the
  production BaaS data, not a code change.
- ws-chat exposure: the same blind routing affects `get_ws_info`; the rotation
  here covers synchronous read transports only.
