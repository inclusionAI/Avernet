# Per-Caller container TTL renewal exclusion

Status: implemented (2026-11-18).

## Context

Service-Bot Caller mode gives every caller its own ARCA container for privacy
isolation (`ac_expert_chat_instance`, see
`apps/backend/specs/2026-07-13-caller-instance/design.md`). The Caller binding
is written to `ac_entity_device_binding` with the stable marker
`apply_reason = caller_instance:<bot_id>`.

Both ARCA TTL renewal engines used to treat those containers as ordinary
renewable devices, so an idle caller container could be kept alive forever:

- legacy `DeviceTtlTimerTask` scans `ac_entity_device_binding` and
  `baas_device`;
- `DeadlineRenewalScheduler` discovers hot `baas_device` rows into
  `baas_bot_ttl_renewal_schedule`.

## Decision (short-term goal)

Caller containers are outside the TTL renewal universe: renewal skips them so
their own ARCA TTL can expire. The exclusion is derived from the existing
`apply_reason` contract; no new column, table or lifecycle is introduced.

Applied at every renewal entry point so an already-registered cold row also
stops renewing:

- `OrmTtlRenewalScheduleRepository.list_due_for_renewal`,
  `find_unregistered`, `count_hot_arca_devices`,
  `count_hot_arca_bindings`, and the covered-count helper for both hot sides.
- `OrmDeviceBindingRepository.list_bindings_by_id_asc` and
  `list_baas_devices_by_id_asc` (legacy engine / rollback path).

The `baas_device` side joins by `device_uuid = ac_entity_device_binding.device_id`,
which is the identity the Caller provisioning path already records.

## Non-goals (long-term goal)

Automatic liveness probing and activity-based reclaim of low-activity caller
containers (for example, unused for a day) is a separate follow-up. It needs an
activity signal and a destroy policy, and is intentionally not guessed here.

## Tests

- `tests/contract/arca_ttl/test_arca_ttl_repository_sqlite.py`
- `tests/contract/device_binding/test_device_ttl_caller_exclusion_sqlite.py`
