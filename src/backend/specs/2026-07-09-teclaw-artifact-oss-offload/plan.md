# Plan: Teclaw Config Artifact Offloading for Large Bots

## Approach
Do the offload transparently at the **repository persistence boundary** so none
of the ~20 service-layer sites that read/write `ext["config_artifact"]` change.
On write, if the serialized artifact exceeds a byte threshold, upload its JSON to
object storage under a deterministic per-record key and replace it in `ext` with
a small self-describing marker. On read, if the marker is present, fetch the JSON
back and re-inline it as `ext["config_artifact"]` so callers see the identical
shape they see today. Offloading is gated on the storage backend exposing a read
method; if it doesn't, we store inline (current behavior) and warn.

## Affected Components
- `src/backend/src/agentclaw/community/plugin_api/object_storage.py` — the
  `ObjectStoragePlugin` Protocol; add a `get_object` read method.
- `src/backend/src/agentclaw/community/plugins/community/object_storage.py` —
  the two real impls (filesystem + S3); implement `get_object`.
- `src/backend/src/agentclaw/community/plugins/local/oss_storage.py` — the
  `MockObjectStoragePlugin` test double; add a `get_object` mock handle.
- `src/backend/src/agentclaw/community/plugins/bot_publish_repository.py` — the
  unified `BotPublishRepository`; owns the offload/inload logic.
- `src/backend/tests/community/plugins/test_bot_publish_unified.py` — round-trip
  tests with an in-memory fake OSS.

Unchanged (deliberately): the ORM model / `to_record` at
`src/backend/src/agentclaw/community/core/service_bot/repository/models.py:161`
(the `ext = Column(Text, ...)` field) and every service-layer reader/writer of
`ext["config_artifact"]` (e.g. `bot_publish_service.py:383`,
`publish_flow_service.py` ~15 sites, `teclaw_provision_service.py:145`).

## Data Model Changes
None. No schema migration. The `ac_bot_publish.ext` `TEXT` column stays as-is;
we only change what we put *into* it when the artifact is large. Object-storage
key layout (deterministic, overwrite-on-update so no orphan accumulation):

    teclaw/{env}/bot_publish/{publish_id}/config_artifact.json

The marker stored inline under a new `ext` key `config_artifact_oss` (sibling of,
and mutually exclusive with, `config_artifact`):

```json
{
  "offloaded": true,
  "oss_key": "teclaw/dev/bot_publish/42/config_artifact.json",
  "size_bytes": 91234,
  "threshold_bytes": 61440,
  "note": "config_artifact (91234 bytes) exceeded the 61440-byte inline limit for the ac_bot_publish.ext TEXT column and was stored in object storage at oss_key; the repository re-inlines it as ext['config_artifact'] on read."
}
```

## API / Interface Changes
- **`ObjectStoragePlugin.get_object(key: str) -> bytes | None`** (new Protocol
  method). Returns the object's bytes, or `None` if missing / on swallowed
  transport error (matches the Protocol's "swallow errors, let caller decide"
  convention used by the other methods).
- **`BotPublishRepository.__init__`** gains an optional injected
  `oss: ObjectStoragePlugin | None = None` (keyword, default `None`). Default
  keeps direct construction in tests working; DI injects the real binding
  (bound via the `bot_oss_client` provider, `di/config.py:183`).
- No changes to `BotPublishRepositoryProtocol` (the offload is an impl detail).

## Key Files & Functions
- `plugin_api/object_storage.py` — add `get_object` to the Protocol (docstring:
  returns bytes or None, swallow errors).
- `plugins/community/object_storage.py`
  - `CommunityFsObjectStorage` (class @ `:31`) — `get_object` reads via
    `_safe_path` + `path.read_bytes()`, `None` if absent / `OSError`.
  - `CommunityS3ObjectStorage` (class @ `:148`) — `get_object` via
    `self._s3.get_object(...)["Body"].read()`, `None` on `self._errors`.
- `plugins/local/oss_storage.py` — add `self.get_object = MagicMock(return_value=None)`
  in `__init__` (`:46`) and a matching reset block in `reset_all_mocks` (`:63`).
- `plugins/bot_publish_repository.py`
  - Module constants: `_ARTIFACT_OSS_THRESHOLD_BYTES = 60 * 1024`,
    `_ARTIFACT_KEY = "config_artifact"`, `_ARTIFACT_OSS_MARKER = "config_artifact_oss"`.
  - `__init__` (`:58`) — accept/store `oss`; compute
    `self._offload_enabled = oss is not None and callable(getattr(oss, "get_object", None))`;
    warn once if `oss` present but `get_object` missing.
  - `_artifact_oss_key(env, publish_id)` — build the deterministic key.
  - `_serialize_ext(ext, publish_id, env) -> str | None` — dump `ext` to JSON,
    offloading `config_artifact` to OSS first when enabled and over threshold
    (measured as `len(json.dumps(artifact).encode("utf-8"))`); on `put_object`
    failure, raise (fail loud). Replaces `json.dumps(ext, ...)` at insert (`:67`)
    and `update_status_with_ext` (`:296`).
  - `_resolve_ext(ext: dict|None) -> dict|None` — if the marker is present,
    `get_object` → `json.loads` → set `config_artifact`, drop the marker; on
    fetch failure log error and leave the marker (callers degrade via existing
    `if config_artifact` guards).
  - `_to_record(row)` — `rec = row.to_record(); rec.ext = self._resolve_ext(rec.ext); return rec`.
    Replace every `row.to_record()` / `[r.to_record() ...]` in the read methods
    (`:104,:122,:140,:160,:176,:193,:210,:231,:249,:262`) with it.
  - `insert` (`:65`) — set `ext` *after* `db.flush()` (needs the new `publish_id`
    for the key): build row with `ext=None`, flush, then
    `row.ext = self._serialize_ext(ext, new_id, env)`, flush again.
  - `update_status_with_ext` (`:289`) — `ext_json = self._serialize_ext(ext, publish_id, get_current_env())`.
  - `delete` (`:352`) — before deleting, read the raw row `ext` JSON; if it holds
    the marker, capture `oss_key`; delete the row; then best-effort
    `self._oss.delete_object(oss_key)`.

## Dependencies
None new. Uses the existing `ObjectStoragePlugin` DI binding and `injector`.

## Risks & Mitigations
- **Risk:** Out-of-tree corp OSS impl lacks `get_object` → `AttributeError` in
  prod. **Mitigation:** `_offload_enabled` capability gate — offload only when
  `get_object` exists; otherwise inline + warn. No new crash path; fix activates
  when corp adds the method.
- **Risk:** OSS fetch fails on read → caller gets no artifact.
  **Mitigation:** log a clear error and leave the marker; existing readers guard
  with `if config_artifact`, so it degrades rather than raising. (Teclaw boot
  that truly needs it will fail visibly with the logged key, not a silent None.)
- **Risk:** Threshold too high → still overflow when sibling `ext` fields are
  large; too low → needless OSS traffic. **Mitigation:** 60 KB constant with
  ~4 KB headroom, documented and single-sourced for easy tuning.
- **Risk:** Re-stamping the artifact each publish stage leaves orphaned OSS
  objects. **Mitigation:** deterministic per-`publish_id` key → each write
  overwrites the same object.
- **Risk:** `insert` two-phase write (flush, set ext, flush) diverges from the
  documented "plain INSERT" parity. **Mitigation:** still a single INSERT within
  one session/transaction; the second flush only updates `ext` before commit.
  Covered by the existing insert parity test plus new offload tests.

## Alternatives Considered
- **Explicit ref threaded through the service layer** — every reader/writer of
  `config_artifact` handles the ref. Rejected: ~20 sites, high churn/risk vs. one
  choke point at the repo.
- **Always offload (no threshold)** — simpler branch, but an OSS round-trip on
  every read/write of every publish record, including tiny ones. Rejected per
  spec (branch by size).
- **`sign_url` + HTTP GET instead of `get_object`** — avoids a Protocol method
  but adds an HTTP dependency and presign edge cases inside the repo. Rejected:
  a direct read method is cleaner and the Protocol explicitly invites new methods
  "as a consumer lights up".
- **Resolve inside the ORM `to_record`** — would need OSS in the kernel/model
  layer (forbidden by layering) and would affect other repos. Rejected.

## Rollout
- No migration, no feature flag. Behavior is size-triggered and backward
  compatible: existing small records are untouched; already-stored inline
  artifacts keep working (no marker → no fetch). New large writes offload.
- Corp deployment gets the fix once its out-of-tree OSS impl implements
  `get_object`; until then it safely stores inline (unchanged from today).
- Backward read compat: a record written before this change has no marker, so
  `_resolve_ext` is a no-op.

## Test Strategy
Unit / integration (`test_bot_publish_unified.py`, SQLite + in-memory fake OSS):
- Small artifact → stored inline, no `put_object` call, no marker, round-trips.
- Large artifact (> threshold) → `put_object` called, `ext` holds the marker (no
  inline `config_artifact`), OSS holds the JSON; `get_by_id`/queries re-inline the
  full artifact; `config_artifact_oss` absent from the returned record.
- Offload via `insert` (upgrade-draft path carrying a large `prior_artifact`) —
  key uses the post-flush `publish_id`.
- Re-write same record twice → same key overwritten (one object).
- `delete` removes the OSS object.
- Capability gate: `oss=None` and `oss` without `get_object` → inline, no offload.
- Write-failure: `put_object` returns `False` on an over-threshold artifact →
  raises (fail loud).
Also add a small `get_object` test for `CommunityFsObjectStorage` (write→read→
missing key returns `None`).
