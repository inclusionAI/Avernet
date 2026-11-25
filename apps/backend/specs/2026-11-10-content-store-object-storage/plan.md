# Plan: Content store on shared object storage

## Approach

`ContentStoreService` keeps everything it does today (digest checks, provenance
rows, hash-verified read) but stops owning *where the bytes live*. A small
`BlobStore` seam inside `core/content_store` gets two implementations: the
existing local blob tree, moved as-is, and a new one over the already-bound
`ObjectStoragePlugin`. The composition root picks one from a new config key,
defaulting to object storage, and gives the object-storage store the local
tree as a read-through fallback so blobs written by earlier versions are still
served and copied over on first read. Separately, the Manifest compiler's
"row exists, skip the write" shortcut is changed to ensure the bytes are
present, so a record whose bytes are missing heals on the next apply.

## Affected Components

- `apps/backend/src/agentclaw/community/core/content_store/blobs.py` (new) — the `BlobStore` seam and its two implementations.
- `apps/backend/src/agentclaw/community/core/content_store/service.py` — delegates blob IO to a `BlobStore`; gains `ensure_stored`.
- `apps/backend/src/agentclaw/community/core/content_store/service_protocol.py` — `ensure_stored` on `ContentStoreProtocol`.
- `apps/backend/src/agentclaw/community/core/content_store/settings.py` — parser for the new `content_store_backend` key.
- `apps/backend/src/agentclaw/community/di/config.py`, `di/modules/manifest_fetch_module.py`, `di/modules/content_store_module.py` — carry the key and wire the chosen store.
- `apps/backend/src/agentclaw/community/core/bot_config_manifest/compiler/entries/_fetch.py` — self-heal in `file_produced`.
- `apps/backend/src/agentclaw/community/configs/application.yaml`, `application-test.yaml` — the key, its comment, the test profile's choice.
- `apps/backend/tests/community/core/apply_engine/_fakes.py` (and any other fake `ContentStoreProtocol`) — the new method.
- Docs: `apps/backend/api-contracts/genome/v1/README.md` §3.3, `core/content_store/sql/2026_08_31_manifest_content.sql` header is frozen (not edited).

## Data Model Changes

None. `ac_manifest_content` rows, the digest format and the retention rule are unchanged.

Object keys (new, under the existing bucket), mirroring today's on-disk layout:

```text
content-store/blobs/<hex[:2]>/<hex>
# e.g. content-store/blobs/77/77cea85d7be48ac3c2d69fe6c77d607a381f0eb979717b3feb43f5cca4ac3d4a
```

No environment segment: the deployment's bucket/binding is the environment
boundary, as for every other consumer of the plugin.

## API / Interface Changes

No HTTP change. Internal interfaces:

```python
# core/content_store/blobs.py (new)
class BlobStore(Protocol):
    def write(self, digest: str, data: bytes) -> None:
        """Make ``data`` readable under ``digest``. No-op when already held. Raises ContentStoreFault."""
    def read(self, digest: str) -> bytes | None:
        """The stored bytes, unverified; None when the address holds nothing. Raises ContentStoreFault."""

class LocalBlobStore(BlobStore):            # today's _blob_path/_write_blob/_sweep_stale_tmp_files, moved
    def __init__(self, root: Path) -> None: ...

class ObjectStorageBlobStore(BlobStore):
    def __init__(self, storage: ObjectStoragePlugin, *, legacy: LocalBlobStore | None = None) -> None: ...
```

```python
# core/content_store/service_protocol.py — ContentStoreProtocol (new method)
def ensure_stored(self, data: bytes) -> str:
    """Make ``data`` readable under its digest and return the digest; files no provenance row.
    For a caller that holds bytes a row already answers for (self-heal). Raises ContentStoreFault."""
```

```diff
# core/content_store/service.py:200 — constructor (existing two-argument calls keep working)
-    def __init__(self, repository: ContentStoreRepositoryProtocol, root: Path) -> None:
+    def __init__(
+        self,
+        repository: ContentStoreRepositoryProtocol,
+        root: Path | None = None,
+        *,
+        blobs: BlobStore | None = None,   # exactly one of root / blobs
+    ) -> None:
```

Config (new key, beside `content_store_dir`):

```yaml
# configs/application.yaml — user_config.bot_config_manifest
bot_config_manifest:
  fetch_transport_allowlist: []
  content_store_backend: "object_storage"   # "object_storage" | "fs"
  content_store_dir: "./data/manifest_content"  # the "fs" root; with "object_storage", the read-through legacy tree
  teclaw_platform_managed: false
```

```yaml
# configs/application-test.yaml and configs/application-singlebox.yaml —
# both profiles bind the mock object storage, which keeps nothing
bot_config_manifest:
  content_store_backend: "fs"
```

## Key Files & Functions

```python
# core/content_store/blobs.py (new) — ObjectStorageBlobStore, the load-bearing lines
_KEY_PREFIX = "content-store/blobs"

def write(self, digest, data):
    key = _key(digest)
    if self._storage.get_etag(key) is not None:      # one HEAD; a re-apply uploads nothing
        return
    if isinstance(self._storage, ImmutableObjectStorageCapability):
        ok = self._storage.create_object_if_absent(key, data) is not ObjectCreateResult.FAILED
    else:
        ok = self._storage.put_object(key, data)     # same address = same bytes, so replace is harmless
    if not ok:
        raise ContentStoreFault(f"object storage refused the blob: {digest}")

def read(self, digest):
    found = self._read_object(_key(digest))          # read_object (3-state) when capable, else get_object
    if found is not None or self._legacy is None:
        return found
    data = self._legacy.read(digest)                 # pre-upgrade local tree
    if data is not None and _hashes_to(data, digest):
        self._copy_forward(digest, data)             # best effort; a failed copy logs, the read still answers
    return data
```

```diff
# core/content_store/service.py:373 — read(): storage-agnostic, verification stays here
-        blob = self._blob_path(digest)
-        if not blob.is_file():
-            raise ContentMissingError(f"no stored content for digest: {digest}")
-        ... chunked read + hash ...
+        data = self._blobs.read(digest)
+        if data is None:
+            raise ContentMissingError(f"no stored content for digest: {digest}")
+        if "sha256:" + hashlib.sha256(data).hexdigest() != digest:
+            raise ContentIntegrityError(f"stored blob fails its own digest: {digest}")
+        return data
```

```diff
# core/content_store/service.py:301, :349 — store() / store_produced()
-        self._write_blob(digest, data)
+        self._blobs.write(digest, data)
```

```diff
# core/bot_config_manifest/compiler/entries/_fetch.py:95 — file_produced._file
     def _file() -> None:
         if not content.is_reachable(session.scope, digest):
             _store_produced(content, data, scope=session.scope, origin=origin, ...)
+        else:
+            # The row answers for the digest; make sure the bytes do too (a row can outlive its blob).
+            _ensure_stored(content, data)   # ContentStoreFault -> EntryFetchError, as _store_produced maps it
```

```python
# di/modules/content_store_module.py:55 — provider
def content_store(self, repository, manifest_config, injector: Injector) -> ContentStoreProtocol:
    local = LocalBlobStore(Path(manifest_config.content_store_dir))
    if manifest_config.content_store_backend == "fs":
        return ContentStoreService(repository, blobs=local)
    storage = injector.get(ObjectStoragePlugin)      # resolved only when chosen
    return ContentStoreService(repository, blobs=ObjectStorageBlobStore(storage, legacy=local))
```

```python
# core/content_store/settings.py (new function, beside content_store_root_from_config)
DEFAULT_CONTENT_STORE_BACKEND = "object_storage"
def content_store_backend_from_config(settings: Mapping[str, Any]) -> str:
    """"object_storage" | "fs"; any other value raises ValueError at startup."""
```

```diff
# di/config.py:987 — BotConfigManifestConfig
     content_store_dir: str = "./data/manifest_content"
+    content_store_backend: str = "object_storage"
```

`file_single` (`_fetch.py:57`) needs no change: on the git road `delivery.file`
calls `store()`, which always writes the bytes; on the object road the bytes
were stored or read from the store in the same compile. Genome capture and
patch recording already call `store_produced` for every blob they name
(`bot_genome/registry.py:279`), which writes the bytes before deduping the
row, so a re-capture heals.

## Dependencies

None. `ObjectStoragePlugin` and its community (`fs`, `s3`), corp and test
implementations exist (`plugin_api/object_storage.py`).

## Risks & Mitigations

- **Risk:** A deployment's object-storage implementation lacks `ImmutableObjectStorageCapability` (the corp implementation is outside this repo).
  **Mitigation:** The store needs only the base surface (`get_etag`, `put_object`, `get_object`); the capability is used when present for the three-state read and atomic create. Without it, an unreachable store reads as "missing" (`get_object` returns `None` for both), which is today's 404 rather than a new failure mode; documented in `blobs.py`.
- **Risk:** The bucket has a lifecycle rule that expires objects under the new prefix.
  **Mitigation:** Spec open question; called out in the config comment and the PR's rollout notes. Not detectable from code.
- **Risk:** Whole-blob uploads of up to 200 MiB per entry.
  **Mitigation:** Bytes are already whole in memory on every path (fetch, read); the HEAD in `write` means an unchanged re-apply uploads nothing. Round trips per apply: one HEAD per entry, plus one PUT per new blob.
- **Risk:** Read latency rises from a disk read to an object GET.
  **Mitigation:** Reads are per apply/diff/content request, not a hot loop; accepted. A digest-keyed local cache is safe to add later (out of scope).
- **Risk:** Community installs change blob location (`./data/manifest_content` → `<fs_root>/content-store/blobs`).
  **Mitigation:** The legacy read-through serves and copies old blobs; no manual step. `content_store_backend: "fs"` restores the old behaviour exactly.
- **Risk:** An existing object is corrupt; `write` sees it exists and skips.
  **Mitigation:** `read` still hash-verifies and fails loudly. Object stores publish whole objects, so the torn-write case the local size check guarded against does not arise.
- **Risk:** Pods already replaced have lost their blobs.
  **Mitigation:** Not recoverable by code. Manifest content returns on the next apply (fetch roads rewrite; `file_produced` now heals); genome revisions return on re-capture.

## Alternatives Considered

- **Shared NAS path via config only** — no code, but depends on a mount on every pod and on a per-environment overlay line (the omission that caused this); weaker rename/fsync guarantees on the pooled mount. Rejected by the user in favour of object storage.
- **Default `fs`, opt in per deployment** — zero behaviour change by default, but reintroduces "works until someone forgets the overlay". Default is object storage; `fs` is the explicit opt-out.
- **Bulk migration CLI** — a sweep that uploads a pod's local tree. Dropped: pre/prod keep the tree on the container's root filesystem, so the deploy that ships this code discards it before the CLI could run; the read-through fallback covers every case where the tree does survive (community, singlebox, NAS-backed roots).
- **Always call `store_produced` in `file_produced`** — heals too, but files an extra provenance row whenever the existing row came from another producer; `ensure_stored` writes bytes only.
- **Dedicated bucket** — only needed if the lifecycle/access checks fail; the plugin binds one bucket, and a second binding is a larger change. Prefix in the existing bucket.

## Rollout

No flag and no migration. Order within the PR does not matter; one deploy.

```text
1. Operator confirms the bucket has no expiry rule covering content-store/ (spec open question).
2. Deploy. New writes land in object storage; reads fall back to a pod's local tree where one survives.
3. For content captured/applied before the deploy on pods since replaced:
   re-apply the Manifest / re-capture the bot (same content → same revision id).
```

A deployment that must stay on local disk sets `content_store_backend: "fs"` in its overlay.

## Test Strategy

Unit tests beside the existing suite; the object-storage store is tested against the real `CommunityFsObjectStorage` on `tmp_path` (it implements the immutable capability) and against a minimal base-surface fake.

```python
# tests/community/core/content_store/test_blobs.py (new)
def test_local_blob_store_round_trip_and_missing_is_none(): ...
def test_object_store_write_then_read_from_a_second_instance(): ...        # the cross-pod case
def test_object_store_write_skips_upload_when_object_exists(): ...
def test_object_store_write_failure_raises_fault(): ...
def test_object_store_read_failed_status_raises_fault_not_missing(): ...
def test_object_store_without_immutable_capability_uses_put_and_get(): ...
def test_legacy_read_through_serves_and_copies_forward(): ...
def test_legacy_blob_failing_its_digest_is_not_copied_forward(): ...
def test_key_layout(): ...

# tests/community/core/content_store/test_content_store.py (additions)
def test_service_over_object_storage_store_read_verify(): ...
def test_ensure_stored_writes_bytes_and_files_no_row(): ...
def test_store_fails_and_files_no_row_when_blob_write_faults(): ...

# tests/community/core/bot_config_manifest/compiler/ (addition, beside the file_produced tests)
def test_file_produced_restores_missing_bytes_when_row_exists(): ...

# tests/community/di/test_content_store_module_wiring.py (additions)
def test_backend_object_storage_wires_object_store_with_legacy_tree(): ...
def test_backend_fs_wires_local_store(): ...
def test_unknown_backend_fails_at_startup(): ...
```

Gate: `DEPLOY_PROFILE=test uv run pytest tests/community/core/content_store tests/community/core/bot_genome tests/community/core/bot_config_manifest tests/community/core/apply_engine tests/community/di -q`, then `./scripts/ci_test.sh` (changed-line coverage ≥ 80%).

## Revision after human review (2026-11-11)

Review on the PR changed four decisions above; the code follows this section where the two differ.

- **Wiring by profile, not by a config key.** `content_store_backend` is gone. `BlobStore` is the contract; `ContentStoreModule` binds the default (`ObjectStorageBlobStore`), and the test/singlebox column overrides it with the local tree (`di/modules/infrastructure/test/content_blob_store.py`). No `if` on a profile or a setting.
- **No legacy read-through.** `ObjectStorageBlobStore` reads and writes object storage only. Data left on instance disks is not carried over.
- **`LocalBlobStore` is test support.** It lives in `agentclaw/community/testing/content_blob_store.py`; `content_store_dir` is set only in the test and singlebox overlays.
- **`ensure_stored` is transitional.** Rows filed before the move have no object behind them; inline Manifest content has no source to re-fetch from. `ensure_stored` (used by `file_produced` and by the backfill below) stays until those rows are backfilled, then is removed.
- **Backfill endpoint (new).** `POST /api/system/content-store/manifest-inline/backfill`, super-admin only, takes an `ac_manifest_content` row (`entity_id`, `bot_id`, `digest`), re-reads the bot's stored Manifest and stores every inline body it declares (`core/bot_config_manifest/services/inline_backfill.py`).
