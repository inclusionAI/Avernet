# Tasks — object-store client, one delivery type, one fetcher per protocol

Spec: `spec.md`. Plan: `plan.md`.

Ordering rule: **A → B → C → D → E → F.** A, B and C change no behaviour and
land with every existing test untouched. D moves the published contract and
lands atomically.

---

## Group A — `EntryDelivery` replaces the union (no behaviour change)

- [x] **A1** — `apply/delivery.py`: `EntryDelivery` Protocol, `BlobDelivery`,
      `GitDelivery`. `GitDelivery` takes a `file_bytes` callable, not the
      fetcher, so `delivery.py` never imports `entry_fetch.py`.
- [x] **A2** — move `_canonical_tree_bytes` / `_decode_tree_bytes` out of
      `materialisers/resources.py` into `delivery.py` (private). `BlobDelivery.members`
      checks `_TREE_MAGIC` **before** considering `unpack`.
- [x] **A3** — move `resources._unpack_members` into `BlobDelivery.members`, and
      `resources._git_members` / `_git_file` into `GitDelivery.members` / `.single`.
      Refusals stay strings, not exceptions — `resolve`'s currency is unchanged.
- [x] **A4** — `fetch_declared` returns `EntryDelivery`. `FetchedEntry` and
      `GitEntrySource` become internal payloads.
- [x] **A5** — remove all eight `isinstance(…, GitEntrySource)` sites:
      `resources.py` ×4, `skills.py` ×2, `identity.py` ×1, `cli_tools/service.py` ×1.
      `skills` keeps one branch on `is_tree()` (two validators by design);
      `cli_tools` keeps one *condition* on it (a tree is not an archive, so the
      "subpath without unpack" refusal must not fire there). `resources` and
      `identity` end with neither.
- [x] **A6** — drop `identity`'s own git-without-subpath check (spec D-9);
      `GitCheckout.read_file` already refuses the case. Update the one test whose
      expected message changes.
- [x] **A7** — `apply/test_delivery.py`. **Gate: the existing suite passes with
      exactly one test edited (A6).** Any other test needing a change is a
      finding (R2), not churn.

> **Group A landed.** Two test doubles were found lying, in the same shape:
> `identity`'s `_StaticGit.read_file` answered where the real checkout refuses,
> and `cli_tools`' `FakeGitEntrySource` returns a *real* `GitEntrySource` to
> guard its dispatch — a guard defeated because the fetcher's return type
> changed rather than the dispatch, leaving the service broken in production
> and green in tests. Both doubles now answer in the seam's currency. Worth
> expecting more of these in B and D: a double that impersonates the thing
> being refactored is where a refactor hides.

## Group B — one fetcher per protocol (no behaviour change)

- [x] **B1** — `apply/fetchers/`: `SourceFetcher` Protocol, `GitSourceFetcher`
      (today's git branch), `ObjectStoreFetcher` (today's non-git branch, still
      a guarded fetch by URL — group D swaps its body).
- [x] **B2** — `fetch_declared` becomes parse → look up → call. The shared
      preamble stays: session lookup, the `needs_session` refusal, `keep_last`
      resolution, budget checks, `declared_protocol`'s no-fetch gate.
- [x] **B3** — module-level exhaustiveness check on `_FETCHERS` (raise at import,
      never `KeyError` at apply), mirroring `support_matrix._build_matrix`.
- [x] **B4** — test the table and the dispatch. **Gate: existing suite untouched.**

## Group C — the object-store client plugin (nothing consumes it yet)

- [x] **C1** — `plugin_api/object_store_client.py`: `ObjectStoreTarget`,
      `ObjectFetchStatus`, `ObjectFetchResult`, `ObjectStoreClient`,
      `ObjectStoreClientFactory`. Document *why* it is not `ObjectStoragePlugin`
      (singleton, env-chain credentials, swallowing error contract).
- [x] **C2** — `tests/community/contracts/test_object_store_client.py`: the five
      statuses and the cap enforced **without draining** the body, against the
      local impl as spec. The Rule 25 *consumer* half lands in D9 — the consumer
      is `ObjectStoreFetcher` and it has no bucket/key to read until D2. Split
      rather than exempted: `EXEMPT_PROTOCOLS` is a set each commit drains.
- [x] **C3** — `plugins/local/object_store_client.py`: in-memory, per-key
      scriptable status.
- [x] **C4** — `plugins/community/object_store_client.py`: boto3 S3. AK/SK passed
      explicitly (**not** the env chain). Chunked read against `byte_limit`.
      Error classification: `NoSuchKey`/404 → `NOT_FOUND`;
      `AccessDenied`/`InvalidAccessKeyId`/`SignatureDoesNotMatch`/403 → `DENIED`;
      `BotoCoreError`/timeout/5xx → `UNAVAILABLE`. `detail` composed from
      bucket + key + status, never from the SDK message.
- [x] **C5** — DI module; factory singleton, clients **not** singletons.

## Group D — the `oss` road, and the URL road removed (behaviour change, atomic)

- [x] **D1** — `schema/sources.py`: `_KEYS_BY_PROTOCOL` / `_REQUIRED_BY_PROTOCOL`
      per protocol; `SourceDecl.url` → `str | None`, `+bucket`, `+key`.
      `url` on `oss` refused with a message naming `bucket`/`key`. Keep the
      `misplaced`-set discipline: one mistake, one violation.
      `SourceKind` and the 18-cell matrix are **unchanged** — only the docstrings
      that describe `oss` as a URL road.
- [x] **D2** — `schema/entries.py`: the bare-string `source: "https://…"` branch
      becomes a refusal naming the two protocols and the mapping form;
      `SourceForm.URL` → `SourceForm.OSS`; entry-level `key`, composed
      source-first and re-checked by `relative_path_refusal`, refused on a
      non-`oss` entry. `subpath` keeps its shipped meaning on both roads.
- [x] **D3** — `capabilities.py`: `SourceForm.URL` → `SourceForm.OSS` in the enum
      and the `constructs` verdict map. The test asserts the full construct set.
- [x] **D4** — DDL `sql/2026_09_09_source_credential_endpoint.sql`
      (`ADD COLUMN endpoint varchar(512) NULL`); row, record, repository protocol
      and impl; `REQUIRED_FIELDS_BY_TYPE` / `EXCLUSIVE_FIELDS_BY_TYPE`.
- [x] **D5** — `allowed_prefixes` optional-and-ignored for `oss_aksk`, still
      mandatory for `header` (the mechanism git sources use).
- [x] **D6** — `ObjectStoreFetcher`'s body swaps to the plugin: reads by bucket +
      composed key; status → outcome per the spec table; sha256 +
      declared-digest stays in the fetcher.
- [x] **D7** — **delete** `credentials/signing.py` and its test; `headers_for`
      back to one line.
- [x] **D8** — migrate ~77 bare-string source usages across 8 test files
      (41 `test_resources_materialiser.py`, 26 `test_manifest_schema.py`, the rest
      in ones and threes) to `oss` bucket/key, or to refusal assertions where the
      test is *about* the URL form.
- [x] **D9** — the guarded fetcher is **not called** on the `oss` road
      (`stub.calls == []`) — and a test that it **is** still called by
      `cli_tools`' API-driven install, which keeps the URL transport.

## Group E — documentation

- [x] **E1** — `manifest-schema.zh-CN.md`: two declarable protocols (`git`,
      `oss`); bucket/key addressing; `key` vs `subpath` on an `oss` entry; the
      bare-string source form is gone. Say plainly that HTTPS remains the wire
      for both roads and stops being a source protocol.
- [x] **E2** — `examples.zh-CN.md`: an `oss` example that is actually accepted;
      refresh the stale caveat header.
- [x] **E3** — credential docs: `oss_aksk` now stores `endpoint`; no
      `allowed_prefixes`; the secret is never presented on the wire.
- [x] **E4** — `work-items.zh-CN.md`: record that W3's signing road is replaced
      by the plugin seam.

## Group F — verification

- [x] **F1** — full backend suite; compare failures against merge-base so
      pre-existing ones are not attributed here.
- [x] **F2** — lint + type check.
- [x] **F3** — architecture gates (`docs/arch/ci.enforce.md`): plugin protocol
      conformance, context-boundary metadata on the new modules.
- [x] **F4** — confirm every acceptance criterion in `spec.md` has a test that
      **fails against the unfixed code**, the #2019 discipline.


---

## Implementation notes — where the plan bent

**The Rule 25 suite split across C and D.** Group C as written created a
Plugin Protocol with no consumer, and Rule 25 defines conformance as
*consumer ↔ Protocol*. Rather than take an `EXEMPT_PROTOCOLS` entry — a set
the arch test says each commit drains — C landed the local impl's spec and D
added the consumer half. Recorded in `plan.md` §③.

**The test migration was ~47 cases, not ~77.** The plan counted *usages* of a
bare-URL source. Most live in materialiser tests that stub the fetcher and
never reach the schema, so they migrated for free. The real surface was
`test_manifest_schema.py` (26 documents), the credential service (12), and
ones and threes elsewhere.

**Four defects the migration found that the design had missed**, each fixed
where it belonged rather than worked around:

1. `auth` was required by the fetcher and not by `PUT` — the surface
   accepting what it cannot apply, introduced in this same change set. Now a
   schema rule, with the fetcher's check demoted to a belt.
2. `${BOT_*}` substitution applied to a source URL and nothing replaced it on
   the bucket/key road, so a per-env bucket would have been silently inert.
3. `check_https_url` ran against `decl.url` unconditionally and answered
   "source URL must be a string" to a document that correctly declared none.
4. One mistake produced two violations: writing `url` on an oss source got
   both "not valid here" and "you must declare bucket", though the first
   already names the replacement.

**Two test doubles were lying, and both hid real breakage** (group A).
`identity`'s `_StaticGit.read_file` answered where the real checkout refuses;
`cli_tools`' `FakeGitEntrySource` returns a *real* `GitEntrySource` to guard
its dispatch, a guard defeated because the fetcher's return type changed
rather than the dispatch — leaving the service broken in production and green
in tests. Both now answer in the seam's currency.

**A test asserted a message where it should have asserted a ruling.** The
consumer-side "a refusal is never masked by keep_last" test passed against a
mutation that made `TOO_LARGE` maskable, because with nothing in the store
there was nothing to mask either way. It now files a receipt first, and the
mutation fails it.

**The branch count was wrong twice, in the same direction.** Zero → one
(after reading `skills._build_package`) → one branch and one condition (after
implementing `cli_tools`). Each time the claim was cleaner than the code
supported.

**CI caught what local scopes did not.** `test_modules_for_community_is_isolated`
pins the exact set of DI modules the community profile composes; group C added
one without updating it. The manifest suite, the architecture gates and the
contract suites do not reach `tests/community/di`.

**The fix for the endpoint SSRF finding overshot, and two endpoint tests said
so.** The first version refused an endpoint whose host would not resolve —
carried over wholesale from the fetch road, where resolution failure states a
fact about a hop happening now. At a *write* it is a prediction: the pod that
stores a credential is not the pod that later reads with it. Two
production-graph tests broke on hosts (`objects.example.test`,
`objects.example-corp.com`) that never resolve by design, and the same rule
would have made split-horizon DNS or a minute's outage enough to refuse a
rotation. The tempting move — script DNS into the endpoint world, or swap the
fixtures for literal IPs — would have kept a rule that also buys nothing:
whoever controls a name can answer publicly at write time and link-locally at
read time, so the strict form was TOCTOU either way. Resolution failure is now
logged; a literal address needs no DNS, so `https://169.254.169.254/` and any
name that *does* resolve somewhere private stay refused. The scripted-DNS
double had to learn the same thing — `getaddrinfo` answers a numeric host from
the string, and a double returning `[]` there would have reported the guard
passing a case the real resolver refuses.

**The prose correction claimed four places and changed three.** `03acbc72`
listed `spec.md`, the DDL comment, the Chinese schema doc and the
`ObjectStoreTarget` docstring; its diff touched three files, leaving the DDL
comment still arguing the endpoint holds "by construction". Corrected here.
The commit message was checked against its own diff only after the fact.
