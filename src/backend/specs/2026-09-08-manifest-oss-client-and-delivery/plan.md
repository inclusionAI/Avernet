# Plan — object-store client, one delivery type, one fetcher per protocol

Spec: `spec.md` in this directory. Tasks: `tasks.md`.

## Shape of the change

Four groups, ordered so **each one is runnable and verifiable on its own**.
A, B and C change no behaviour — their correctness claim is that every existing
test passes untouched. D is where the published contract moves, and it lands
atomically because a half-applied vocabulary split would publish a matrix row
nothing serves.

| | group | behaviour change | how it is verified |
|---|---|---|---|
| A | `EntryDelivery` replaces the union | none | existing materialiser tests, unchanged |
| B | one fetcher per protocol | none | existing fetch tests, unchanged |
| C | the object-store client plugin | none (nothing consumes it yet) | new conformance test |
| D | the vocabulary split + `oss` on the plugin | **yes** | new tests + the matrix cell test |
| E | documentation | — | — |
| F | verification | — | full suite, lint, type check |

## ① `EntryDelivery` — one return type (group A)

New module `apply/delivery.py`. `FetchedEntry` and `GitEntrySource` stay, demoted
to the *payloads* two deliveries wrap; nothing outside `apply/` imports them
after this group.

```python
class EntryDelivery(Protocol):
    """What a source delivered, and how the category reads it."""

    def members(self, *, unpack: str | None,
                strip_components: int) -> list[tuple[str, bytes]] | str: ...
    def single(self) -> bytes: ...
    def note(self) -> str | None: ...
    def source_url(self) -> str | None: ...
    def receipt_url(self) -> str: ...
    def digest(self) -> str: ...
```

Three pieces of logic move out of `materialisers/resources.py` and behind the
seam — this is what closes D2's split obligation:

| moves from | to |
|---|---|
| `resources._unpack_members` | `BlobDelivery.members` |
| `resources._git_members` / `_git_file` | `GitDelivery.members` / `.single` |
| `resources._decode_tree_bytes` / `_canonical_tree_bytes` | `delivery.py`, private |

`GitDelivery` needs `file_bytes` to file the canonical tree with the content
store — the duty `_git_members` performs today. It takes the `EntryFetcher`'s
`file_bytes` as a constructor callable rather than the fetcher itself, so
`delivery.py` does not import `entry_fetch.py` and the direction stays one-way.

**Eight sites lose their `isinstance`:** `resources.py` ×4, `skills.py` ×2,
`identity.py` ×1, `cli_tools/service.py` ×1.

The keep-last tree case deserves a note, because it is the one place the two
deliveries genuinely differ in a way `members()` must absorb: when `keep_last`
stands in for a failed *git* fetch, the store hands back a `_TREE_MAGIC`-prefixed
canonical tree as **bytes** — so it arrives as a `BlobDelivery`. Hence
`BlobDelivery.members` checks the magic first and decodes, before considering
`unpack`. That is the same three-way decision `resources.py` makes today, moved
intact.

## ② One fetcher per protocol (group B)

New package `apply/fetchers/`. `EntryFetcher` keeps `fetch` (the raw URL
entrypoint `cli_tools/service.py:647` still calls directly), `file_bytes`, and
`fetch_declared` — but `fetch_declared`'s body becomes parse → look up → call.

```python
class SourceFetcher(Protocol):
    def fetch(self, ctx, *, decl, entry, category,
              entry_identity) -> EntryDelivery: ...

_FETCHERS: Mapping[SourceKind, SourceFetcher] = MappingProxyType({
    SourceKind.GIT:   GitSourceFetcher(),
    SourceKind.HTTPS: HttpsSourceFetcher(),      # group D renames it
    SourceKind.OSS:   ObjectStoreFetcher(),      # group D adds it
})
```

In group B the table has two rows and `SourceKind.OSS` still means "URL", so the
`HttpsSourceFetcher` body is exactly today's non-git branch. The rename lands in
D. Exhaustiveness gets the same treatment the support matrix already has: a
module-level check that `set(_FETCHERS) == set(SourceKind) - {CONTENT}`, so a
protocol added without a fetcher raises at import rather than `KeyError`-ing at
apply time.

The shared preamble stays in `fetch_declared` where all three fetchers need it:
session lookup, the `needs_session` refusal, `keep_last` resolution, budget
checks, and `declared_protocol`'s no-fetch gate.

## ③ The object-store client plugin (group C)

`plugin_api/object_store_client.py` — the contract in `spec.md`
(`ObjectStoreTarget`, `ObjectFetchStatus`, `ObjectFetchResult`,
`ObjectStoreClient`, `ObjectStoreClientFactory`).

Per `docs/arch/protocol-contract-tests.md`, a plugin protocol needs a conformance
test shape every implementation runs. Ours asserts the five statuses and the
streaming cap.

| impl | file | notes |
|---|---|---|
| community | `plugins/community/object_store_client.py` | boto3 S3; `CommunityS3ObjectStorage` is the template. AK/SK passed explicitly to `boto3.client`, **not** the env chain. |
| test | `plugins/local/object_store_client.py` | in-memory, per-key scriptable status |
| corp | *not in this repo* | binds `oss2`; the contract + conformance test here are what it satisfies |

DI: `di/modules/infrastructure/community/object_store_client.py`, bound
**non-singleton** — the factory is the singleton, the clients it returns are not.

Two details the community impl must get right, both carried from the guarded
fetcher:

- **the cap is enforced while streaming.** boto3's `get_object` returns a
  `StreamingBody`; read in chunks against `byte_limit` and return `TOO_LARGE`
  without draining. Never `body.read()` unbounded.
- **errors are classified, not swallowed.** `ClientError` with `NoSuchKey` /
  404 → `NOT_FOUND`; `AccessDenied` / `InvalidAccessKeyId` /
  `SignatureDoesNotMatch` / 403 → `DENIED`; `BotoCoreError`, timeouts, 5xx →
  `UNAVAILABLE`. This is the opposite of `ObjectStoragePlugin`'s contract and the
  reason it is a separate protocol.

`detail` is report-safe by construction: it is composed from the bucket, the key
and the status, never from the SDK's message (which echoes endpoints and
sometimes request signatures) — the same ruling `git_source.py` applies to git's
stderr.

## ④ The vocabulary split and the `oss` road (group D)

The atomic behaviour change.

**`support_matrix.py`** — `SourceKind` gains `HTTPS`. `_VERDICTS` gains six
rows; the matrix becomes 24 cells, still built by iterating both enums.
`DIGEST_REQUIRED` gains `(SKILLS, HTTPS)` and `(CLI_TOOLS, HTTPS)` — today's
`(…, OSS)` pairs follow the URL road and move with it; the new `oss` road keeps
them too (a bucket read is as digest-worthy as a URL read).
`ARCHIVE_FIELDS_BY_KIND` gains `HTTPS: {unpack, strip_components}` and keeps the
same for `OSS`.

**`schema/sources.py`** — the key tables become per-protocol:

```python
_KEYS_BY_PROTOCOL = {
    GIT:   {"protocol", "url", "auth", "ref", "mode", "subpath"},
    HTTPS: {"protocol", "url", "auth"},
    OSS:   {"protocol", "auth", "bucket", "key"},
}
_REQUIRED_BY_PROTOCOL = {GIT: {"url"}, HTTPS: {"url"}, OSS: {"bucket"}}
```

`SourceDecl.url` becomes `str | None`; `bucket` and `key` are added. The
`misplaced`-set discipline from #2019 is kept: a field refused as
not-valid-for-protocol suppresses its own shape check, so one mistake yields one
violation. `url` on an `oss` source gets a message naming `bucket`/`key`
explicitly — this is the refusal a migrating author will actually hit.

`DECLARABLE_PROTOCOLS` gains `HTTPS`. The inline string form
(`source: "https://…"`) resolves to `HTTPS`.

**`schema/entries.py`** — entry-level `key`, composed source-first by a
`_compose_key` that is `_compose_subpath` generalised (same
`relative_path_refusal` re-check). Refused on a non-`oss` entry, with the
existing per-protocol-field message shape.

**`credentials/`** — `endpoint` added to the row, the record, the repository
protocol and impl, and `REQUIRED_FIELDS_BY_TYPE[OSS_AKSK]`.
`EXCLUSIVE_FIELDS_BY_TYPE` gains it so a `header` credential carrying `endpoint`
is refused rather than silently dropped — the rule
`_check_mechanism_fields` already enforces both ways.
`allowed_prefixes` becomes **optional and ignored** for `oss_aksk` (spec D-4)
and stays mandatory for `header`; `validate_prefixes`' non-empty rule moves
behind that branch.

`headers_for` loses its `oss_aksk` branch and returns to one line.
`signing.py` and its test are deleted.

**DDL** — `sql/2026_09_09_source_credential_endpoint.sql`:
`ALTER TABLE ac_source_credential ADD COLUMN endpoint varchar(512) NULL`.
Nullable, no backfill — the same shape as the `access_key_id`/`region` migration,
and the DDL contract test already parses `ALTER TABLE … ADD COLUMN` since #2019.

**`capabilities.py`** — `source_matrix` publishes 24 cells. This is a visible API
change; its test asserts the full cell set, not a subset.

## Files

**New (9)**

```
plugin_api/object_store_client.py
plugins/community/object_store_client.py
plugins/local/object_store_client.py
di/modules/infrastructure/community/object_store_client.py
core/bot_config_manifest/apply/delivery.py
core/bot_config_manifest/apply/fetchers/{__init__,git,https,oss}.py
sql/2026_09_09_source_credential_endpoint.sql
```

**Modified (13)**

```
core/bot_config_manifest/apply/entry_fetch.py
core/bot_config_manifest/apply/materialisers/{resources,skills,identity}.py
core/bot_config_manifest/cli_tools/service.py
core/bot_config_manifest/schema/{sources,entries}.py
core/bot_config_manifest/support_matrix.py
core/bot_config_manifest/capabilities.py
core/bot_config_manifest/credentials/{models,service,service_protocol}.py
core/repository/{protocols,implementations}/bot/source_credential.py
```

**Deleted (2)** — `credentials/signing.py` and its test.

## Tests

| what | where | asserts |
|---|---|---|
| delivery seam | `apply/test_delivery.py` | both impls satisfy `EntryDelivery`; `GitDelivery` ignores `unpack`; `BlobDelivery` decodes `_TREE_MAGIC` before unpacking |
| fetcher table | `apply/test_entry_fetch.py` | `set(_FETCHERS) == set(SourceKind) - {CONTENT}`; dispatch picks by protocol |
| **no fetch on the oss road** | `apply/test_entry_fetch.py` | guarded-fetcher stub `.calls == []` when protocol is `oss` — the #2019 discipline |
| plugin conformance | `tests/…/plugin_api/test_object_store_client.py` | five statuses; cap enforced without draining |
| status → outcome | `apply/test_entry_fetch.py` | `keep_last` stands in for `UNAVAILABLE` only; the other three are refusals |
| matrix | `test_support_matrix.py`, `test_capabilities.py` | 24 cells; exhaustive-by-construction still raises on a missing/stale verdict |
| schema | `schema/test_sources.py` | `url` on `oss` refused naming `bucket`; missing `bucket` refused; one mistake → one violation |
| credential | `credentials/test_service.py` | `endpoint` required for `oss_aksk`, refused on `header`; readable back; secret still absent from the record |
| regression | the whole existing suite | groups A–C change nothing |

## Risks

**R1 — the community client cannot reach `oss-alipay` either.** boto3 signs
SigV4. Stated in the spec, not hidden: the corp `oss2` binding is what closes
D1, and this repo ships the contract it satisfies. A community deploy pointing
`oss` at a native-only Aliyun endpoint gets `DENIED`, which is a correct and
legible outcome rather than a silent one.

**R2 — group A is a large mechanical diff across four materialisers.** Mitigated
by landing it first and alone, with the claim that *no existing test changes*.
Any test that needs editing in group A is a behaviour change that was not
supposed to happen — treat it as a finding, not as churn.

**R3 — `allowed_prefixes` becoming conditional is a validation rule that now
branches on credential type.** The `_check_mechanism_fields` precedent already
handles per-mechanism fields in both directions; this follows it rather than
adding a second mechanism-shaped rule elsewhere.

**R4 — corp binds the real client, so nothing in CI proves `oss2` works.** The
conformance test is the contract; the corp binding is tested corp-side. Called
out so it is a known boundary rather than an assumed coverage.
