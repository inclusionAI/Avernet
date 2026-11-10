# Tasks: Content store on shared object storage

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

## Task 1: Extract the local blob tree behind a `BlobStore` seam
- **Goal:** Move today's blob IO out of `ContentStoreService` into `LocalBlobStore` with no behaviour change, and make the service delegate to a `BlobStore`.
- **Files:** `src/agentclaw/community/core/content_store/blobs.py` (new), `core/content_store/service.py`, `core/content_store/__init__.py`, `tests/community/core/content_store/test_blobs.py` (new), `tests/community/di/test_content_store_module_wiring.py`
- **Done when:**
  - [x] `BlobStore` protocol (`write`, `read`) and `LocalBlobStore(root)` exist; the atomic write, size shortcut and stale-tmp sweep are moved unchanged.
  - [x] `ContentStoreService(repository, root)` still works; `ContentStoreService(repository, blobs=...)` is accepted; passing both or neither raises.
  - [x] `read` verifies the digest in the service for any `BlobStore`; missing → `ContentMissingError`, mismatch → `ContentIntegrityError`.
  - [x] Existing `tests/community/core/content_store`, `bot_genome`, `bot_config_manifest`, `apply_engine` suites pass, with only the assertions that reached into moved internals updated.
  - [x] `service.py` and `blobs.py` are each under 1,000 lines.
- **Depends on:** —

## Task 2: Object-storage blob store with legacy read-through
- **Goal:** Add `ObjectStorageBlobStore` over `ObjectStoragePlugin`, including the fallback to a pre-upgrade local tree.
- **Files:** `core/content_store/blobs.py`, `tests/community/core/content_store/test_blobs.py`, `tests/community/core/content_store/test_content_store.py`
- **Done when:**
  - [x] Keys are `content-store/blobs/<hex[:2]>/<hex>`.
  - [x] `write` skips the upload when the object exists, uses `create_object_if_absent` when the plugin has the immutable capability and `put_object` otherwise, and raises `ContentStoreFault` on failure.
  - [x] `read` returns `None` for a missing object and raises `ContentStoreFault` when a capable plugin reports `FAILED`; a plugin without the capability uses `get_object`.
  - [x] A blob present only in the legacy local tree is returned and copied to object storage; one that fails its digest is not copied; a failed copy does not fail the read.
  - [x] Two service instances over one object storage and one database, with separate local roots: content stored by the first is read by the second (spec criterion 1).
  - [x] A failed blob write fails `store` / `store_produced` and files no row.
- **Depends on:** Task 1

## Task 3: Configuration and wiring
- **Goal:** Select the blob store from config, defaulting to object storage, with the test profile on `fs`.
- **Files:** `core/content_store/settings.py`, `di/config.py`, `di/modules/manifest_fetch_module.py`, `di/modules/content_store_module.py`, `configs/application.yaml`, `configs/application-test.yaml`, `tests/community/di/test_content_store_module_wiring.py`, `tests/community/core/content_store/` (settings tests)
- **Done when:**
  - [x] `user_config.bot_config_manifest.content_store_backend` accepts `object_storage` (default) and `fs`; any other value fails at startup with a message naming the key.
  - [x] `object_storage` wires `ObjectStorageBlobStore` with the `content_store_dir` tree as legacy; `fs` wires `LocalBlobStore` and never resolves `ObjectStoragePlugin`.
  - [x] `application.yaml` documents the key, what `content_store_dir` means under each backend, and the no-expiry requirement on the bucket prefix; `application-test.yaml` sets `fs`.
  - [x] The community profile boots with its default `fs` object storage and round-trips a blob (wiring test).
- **Depends on:** Task 2

## Task 4: Self-heal in the Manifest compiler
- **Goal:** A row whose bytes are missing is repaired by the next apply.
- **Files:** `core/content_store/service_protocol.py`, `core/content_store/service.py`, `core/bot_config_manifest/compiler/entries/_fetch.py`, `tests/community/core/apply_engine/_fakes.py` (and any other `ContentStoreProtocol` fake), `tests/community/core/content_store/test_content_store.py`, `tests/community/core/bot_config_manifest/compiler/` (file_produced test)
- **Done when:**
  - [x] `ContentStoreProtocol.ensure_stored(data) -> digest` writes bytes only and files no provenance row.
  - [x] `file_produced` calls it when the row already exists; a store fault surfaces as that entry's `EntryFetchError`.
  - [x] Test: file inline content, delete its blob, compile again → the materialiser's read succeeds and the row count is unchanged.
  - [x] Test: re-recording a genome capture whose blob was deleted restores the bytes (pins the existing behaviour the spec relies on).
- **Depends on:** Task 1

## Task 5: Documentation
- **Goal:** The docs say where content lives and what an operator must check.
- **Files:** `apps/backend/api-contracts/genome/v1/README.md` (§3.3 sentence on the content store), `apps/backend/docs/bot-evolution/01-genome.md` and `.zh-CN.md` only where they state the storage location, `core/content_store/service.py` / `settings.py` docstrings
- **Done when:**
  - [x] No doc or docstring still says blobs live under a NAS path or local root without naming the backend key.
  - [x] The frozen migration file `core/content_store/sql/2026_08_31_manifest_content.sql` is untouched.
- **Depends on:** Task 3

## Task 6: Tests & Verification
- **Goal:** Ensure the feature meets the spec acceptance criteria.
- **Files:** —
- **Done when:**
  - [x] `DEPLOY_PROFILE=test uv run pytest tests/community/core/content_store tests/community/core/bot_genome tests/community/core/bot_config_manifest tests/community/core/apply_engine tests/community/di -q` passes.
  - [x] `./scripts/ci_test.sh` passes (changed-line coverage ≥ 80%, total ≥ 75%).
  - [x] Every spec acceptance criterion maps to a passing test, or is stated in the PR as not verifiable locally (cross-instance behaviour on a real bucket; instance replacement).
  - [x] No file over 1,000 lines; no unrelated formatting changes in the diff.
- **Depends on:** Tasks 1–5

---

## Groups

- **Group A — Storage seam:** Tasks 1, 2
  - Theme: The content store can keep its bytes in object storage, with the old local tree as a fallback; nothing is wired to use it yet.
- **Group B — Switch-over and self-heal:** Tasks 3, 4
  - Theme: Deployments use object storage by default, and a record with missing bytes repairs itself on the next apply.
- **Group C — Docs and verification:** Tasks 5, 6
  - Theme: Documentation matches the new storage, and the spec's acceptance criteria are checked.
