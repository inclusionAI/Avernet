# Tasks — object-store client, one delivery type, one fetcher per protocol

Spec: `spec.md`. Plan: `plan.md`.

Ordering rule: **A → B → C → D → E → F.** A, B and C change no behaviour and
land with every existing test untouched. D moves the published contract and
lands atomically.

---

## Group A — `EntryDelivery` replaces the union (no behaviour change)

- [ ] **A1** — `apply/delivery.py`: `EntryDelivery` Protocol, `BlobDelivery`,
      `GitDelivery`. `GitDelivery` takes a `file_bytes` callable, not the
      fetcher, so `delivery.py` never imports `entry_fetch.py`.
- [ ] **A2** — move `_canonical_tree_bytes` / `_decode_tree_bytes` out of
      `materialisers/resources.py` into `delivery.py` (private). `BlobDelivery.members`
      checks `_TREE_MAGIC` **before** considering `unpack`.
- [ ] **A3** — move `resources._unpack_members` into `BlobDelivery.members`, and
      `resources._git_members` / `_git_file` into `GitDelivery.members` / `.single`.
      Refusals stay strings, not exceptions — `resolve`'s currency is unchanged.
- [ ] **A4** — `fetch_declared` returns `EntryDelivery`. `FetchedEntry` and
      `GitEntrySource` become internal payloads.
- [ ] **A5** — remove all eight `isinstance(…, GitEntrySource)` sites:
      `resources.py` ×4, `skills.py` ×2, `identity.py` ×1, `cli_tools/service.py` ×1.
- [ ] **A6** — `apply/test_delivery.py`. **Gate: the existing suite passes with
      no test edited.** A test that needs changing here is a finding (R2).

## Group B — one fetcher per protocol (no behaviour change)

- [ ] **B1** — `apply/fetchers/`: `SourceFetcher` Protocol, `GitSourceFetcher`
      (today's git branch), `HttpsSourceFetcher` (today's non-git branch).
- [ ] **B2** — `fetch_declared` becomes parse → look up → call. The shared
      preamble stays: session lookup, the `needs_session` refusal, `keep_last`
      resolution, budget checks, `declared_protocol`'s no-fetch gate.
- [ ] **B3** — module-level exhaustiveness check on `_FETCHERS` (raise at import,
      never `KeyError` at apply), mirroring `support_matrix._build_matrix`.
- [ ] **B4** — test the table and the dispatch. **Gate: existing suite untouched.**

## Group C — the object-store client plugin (nothing consumes it yet)

- [ ] **C1** — `plugin_api/object_store_client.py`: `ObjectStoreTarget`,
      `ObjectFetchStatus`, `ObjectFetchResult`, `ObjectStoreClient`,
      `ObjectStoreClientFactory`. Document *why* it is not `ObjectStoragePlugin`
      (singleton, env-chain credentials, swallowing error contract).
- [ ] **C2** — conformance test per `docs/arch/protocol-contract-tests.md`: the
      five statuses, and the cap enforced **without draining** the body.
- [ ] **C3** — `plugins/local/object_store_client.py`: in-memory, per-key
      scriptable status.
- [ ] **C4** — `plugins/community/object_store_client.py`: boto3 S3. AK/SK passed
      explicitly (**not** the env chain). Chunked read against `byte_limit`.
      Error classification: `NoSuchKey`/404 → `NOT_FOUND`;
      `AccessDenied`/`InvalidAccessKeyId`/`SignatureDoesNotMatch`/403 → `DENIED`;
      `BotoCoreError`/timeout/5xx → `UNAVAILABLE`. `detail` composed from
      bucket + key + status, never from the SDK message.
- [ ] **C5** — DI module; factory singleton, clients **not** singletons.

## Group D — the vocabulary split and the `oss` road (behaviour change, atomic)

- [ ] **D1** — `support_matrix.py`: `SourceKind.HTTPS`; six new verdict rows;
      24 cells; `DIGEST_REQUIRED` and `ARCHIVE_FIELDS_BY_KIND` extended.
- [ ] **D2** — `schema/sources.py`: `_KEYS_BY_PROTOCOL` / `_REQUIRED_BY_PROTOCOL`
      per protocol; `SourceDecl.url` → `str | None`, `+bucket`, `+key`;
      `DECLARABLE_PROTOCOLS` gains `HTTPS`; inline string → `HTTPS`.
      `url` on `oss` refused with a message naming `bucket`/`key`. Keep the
      `misplaced`-set discipline: one mistake, one violation.
- [ ] **D3** — `schema/entries.py`: entry-level `key`, composed source-first,
      re-checked by `relative_path_refusal`. Refused on a non-`oss` entry.
      `subpath` keeps its shipped meaning on both roads.
- [ ] **D4** — DDL `sql/2026_09_09_source_credential_endpoint.sql`
      (`ADD COLUMN endpoint varchar(512) NULL`); row, record, repository protocol
      and impl; `REQUIRED_FIELDS_BY_TYPE` / `EXCLUSIVE_FIELDS_BY_TYPE`.
- [ ] **D5** — `allowed_prefixes` optional-and-ignored for `oss_aksk`, still
      mandatory for `header`.
- [ ] **D6** — `ObjectStoreFetcher` in the table; reads via the plugin; status →
      outcome per the spec table; sha256 + declared-digest stays in the fetcher.
- [ ] **D7** — **delete** `credentials/signing.py` and its test; `headers_for`
      back to one line.
- [ ] **D8** — `capabilities.py` publishes 24 cells; the cell test drives a real
      request per cell and asserts observed == table.
- [ ] **D9** — the guarded fetcher is **not called** on the `oss` road
      (`stub.calls == []`).

## Group E — documentation

- [ ] **E1** — `manifest-schema.zh-CN.md`: the four protocols; bucket/key
      addressing; `key` vs `subpath` on an `oss` entry.
- [ ] **E2** — `examples.zh-CN.md`: an `oss` example that is actually accepted;
      refresh the stale caveat header.
- [ ] **E3** — credential docs: `oss_aksk` now stores `endpoint`; no
      `allowed_prefixes`; the secret is never presented on the wire.
- [ ] **E4** — `work-items.zh-CN.md`: record that W3's signing road is replaced
      by the plugin seam.

## Group F — verification

- [ ] **F1** — full backend suite; compare failures against merge-base so
      pre-existing ones are not attributed here.
- [ ] **F2** — lint + type check.
- [ ] **F3** — architecture gates (`docs/arch/ci.enforce.md`): plugin protocol
      conformance, context-boundary metadata on the new modules.
- [ ] **F4** — confirm every acceptance criterion in `spec.md` has a test that
      **fails against the unfixed code**, the #2019 discipline.
