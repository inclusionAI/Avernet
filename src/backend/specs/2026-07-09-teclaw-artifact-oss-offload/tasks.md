# Tasks: Teclaw Config Artifact Offloading for Large Bots

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

## Task 1: [x] Add `get_object` to the object-storage abstraction
- **Goal:** Give `ObjectStoragePlugin` a read method so offloaded artifacts can be
  fetched back.
- **Files:** `src/backend/src/agentclaw/community/plugin_api/object_storage.py`
- **Done when:**
  - [x] Protocol declares `get_object(self, key: str) -> bytes | None` with a
        docstring matching the "swallow transport errors, return None" convention.
- **Depends on:** —

## Task 2: Implement `get_object` in the real object-storage backends
- **Goal:** Both deployable impls can read an object's bytes.
- **Files:** `src/backend/src/agentclaw/community/plugins/community/object_storage.py`
- **Done when:**
  - [ ] `CommunityFsObjectStorage.get_object` returns bytes via `_safe_path` +
        `read_bytes()`, and `None` for a missing key / escaping key / `OSError`.
  - [ ] `CommunityS3ObjectStorage.get_object` returns the body bytes, and `None`
        on `self._errors` (incl. missing-key `ClientError`).
- **Depends on:** Task 1

## Task 3: Add `get_object` to the mock object-storage double
- **Goal:** Tests and the local runtime satisfy the extended Protocol.
- **Files:** `src/backend/src/agentclaw/community/plugins/local/oss_storage.py`
- **Done when:**
  - [ ] `MockObjectStoragePlugin.__init__` adds `self.get_object = MagicMock(return_value=None)`.
  - [ ] `reset_all_mocks` resets `get_object` (return_value `None`, clears side_effect/history).
- **Depends on:** Task 1

## Task 4: Add offload helpers + constants to the publish repository
- **Goal:** Introduce the size threshold, marker shape, key builder, and the
  serialize/resolve helpers — without wiring them into read/write paths yet.
- **Files:** `src/backend/src/agentclaw/community/plugins/bot_publish_repository.py`
- **Done when:**
  - [ ] Module constants: `_ARTIFACT_OSS_THRESHOLD_BYTES = 60 * 1024`,
        `_ARTIFACT_KEY = "config_artifact"`, `_ARTIFACT_OSS_MARKER = "config_artifact_oss"`.
  - [ ] `__init__` accepts injected `oss: ObjectStoragePlugin | None = None`,
        stores it, and sets `self._offload_enabled` (True only when `oss` is
        present AND exposes a callable `get_object`); warns once when `oss` is
        present but lacks `get_object`.
  - [ ] `_artifact_oss_key(env, publish_id)` returns
        `teclaw/{env}/bot_publish/{publish_id}/config_artifact.json`.
  - [ ] `_serialize_ext(ext, publish_id, env) -> str | None`: offloads
        `config_artifact` to OSS when `_offload_enabled` and the artifact's UTF-8
        JSON byte length > threshold; replaces it with the self-describing marker;
        raises on `put_object` failure (fail loud); returns `json.dumps(ext)` (or
        `None` when `ext is None`). Inline path unchanged when under threshold /
        offload disabled.
  - [ ] `_resolve_ext(ext) -> ext`: when the marker is present, `get_object` +
        `json.loads` → set `config_artifact`, drop the marker; on fetch failure,
        log an error and return `ext` with the marker intact.
- **Depends on:** Task 1

## Task 5: Wire offload/inload into the repository read & write paths
- **Goal:** Make persistence transparently offload large artifacts and re-inline
  them on read; clean up on delete.
- **Files:** `src/backend/src/agentclaw/community/plugins/bot_publish_repository.py`
- **Done when:**
  - [ ] `_to_record(row)` helper applies `_resolve_ext` to `row.to_record().ext`;
        every read method returns records through it (get_by_id, the `get_*`
        queries, and the `list_*` comprehensions).
  - [ ] `insert` sets `ext` after `db.flush()` (so the key uses the new
        `publish_id`): build row with `ext=None`, flush, `row.ext =
        _serialize_ext(ext, new_id, env)`, flush again — still one INSERT / one
        transaction.
  - [ ] `update_status_with_ext` builds `ext_json` via
        `_serialize_ext(ext, publish_id, get_current_env())`.
  - [ ] `delete` reads the raw row `ext` first; if it holds the marker, captures
        `oss_key`, deletes the row, then best-effort `delete_object(oss_key)`.
  - [ ] `update_status` (no ext) and other non-ext updates are unchanged.
- **Depends on:** Task 4

## Task 6: Tests & Verification
- **Goal:** Prove the spec's acceptance criteria with an in-memory fake OSS.
- **Files:** `src/backend/tests/community/plugins/test_bot_publish_unified.py`
  (and a small `get_object` check for the fs backend, colocated or in the
  existing object-storage test module if one exists)
- **Done when:**
  - [ ] Small artifact → inline, no `put_object`, no marker, round-trips unchanged.
  - [ ] Large artifact → `put_object` called, `ext` holds only the marker, OSS
        holds the JSON; reads (`get_by_id` + a query + a `list_*`) re-inline the
        full artifact and the marker key is absent from the returned record.
  - [ ] `insert` with an over-threshold `prior_artifact` offloads under a key
        containing the post-flush `publish_id`.
  - [ ] Two writes of the same record reuse/overwrite one OSS key.
  - [ ] `delete` removes the OSS object.
  - [ ] Capability gate: `oss=None` and an `oss` lacking `get_object` both store
        inline (no offload, no crash).
  - [ ] Over-threshold artifact with `put_object` → `False` raises (fail loud).
  - [ ] `CommunityFsObjectStorage.get_object` write→read→missing-key(None) passes.
  - [ ] Existing `test_bot_publish_unified.py` suite still green.
- **Depends on:** Task 5

---

## Groups

- **Group A — Storage read capability:** Tasks 1, 2, 3
  - Theme: `ObjectStoragePlugin` can read objects across the Protocol and all
    three impls (fs, S3, mock). Self-contained; no behavior change yet.
- **Group B — Repository offload:** Tasks 4, 5
  - Theme: `BotPublishRepository` transparently offloads oversized artifacts to
    OSS and re-inlines them, with delete-time cleanup — the core feature.
- **Group C — Verification:** Task 6
  - Theme: Round-trip + edge-case tests proving every spec acceptance criterion.
