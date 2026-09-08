# Manifest sources: an object-store client, one delivery type, one fetcher per protocol

Follow-up to `2026-09-08-manifest-source-protocols` (#2019, merged). That change
made the source axis explicit and enumerable; this one makes the **fetch layer**
match it. Plan: `plan.md` in this directory.

## Summary

Three changes with one theme. The fetch layer answers *how content travels* in
three different places today, and none of them scales past the two protocols it
already has:

1. **`credentials/signing.py` is deleted.** A `protocol: oss` source is read
   through an **object-store client obtained from a plugin**, allocated per
   credential rather than wired once at startup. The hand-rolled request signer
   goes away entirely.
2. **The fetch layer returns one type.** `FetchedEntry | GitEntrySource` becomes
   `EntryDelivery`, an interface with two implementations, and every
   `isinstance` branch in every materialiser goes with it.
3. **One fetcher per protocol**, and **the plain-URL source road is removed.**
   The `if decl.protocol is not SourceKind.GIT:` branch in `fetch_declared`
   becomes a table of `SourceFetcher` implementations. A manifest may declare
   exactly two protocols — `git` and `oss` — and can no longer hand the
   platform a raw URL to GET.

(1) is the user-visible fix — the shipped `oss` road cannot reach the object
store it was built for. (2) and (3) are what make (1) a small change rather than
a third branch bolted onto two existing ones.

## Motivation

**D1 — the shipped `oss` road cannot authenticate against Aliyun OSS.**
`signing.py` emits **AWS SigV4** (`botocore.auth.S3SigV4Auth`, service `s3`,
credential scope `.../aws4_request`, `x-amz-*` headers). Aliyun OSS's native API
requires **`OSS4-HMAC-SHA256`**: service `oss`, scope `.../aliyun_v4_request`,
`x-oss-*` headers, and a signing key chained from the literal prefix
`aliyun_v4`. The two are not variants of one scheme.

The decisive detail is not the header names — it is that Aliyun's canonical URI
is built from **structured bucket and key inputs**, never parsed out of the URL:

```python
# alibabacloud_oss_v2/signer/v4.py, Aliyun's official SDK
uri = '/'
if signing_ctx.bucket is not None:
    uri = uri + signing_ctx.bucket + '/'
if signing_ctx.key is not None:
    uri = uri + signing_ctx.key
canonical_uri = quote(uri, safe='/')
```

A signer handed only a URL cannot reconstruct that split reliably — virtual-host
style puts the bucket in the hostname, path style puts it in the path, and
nothing in the URL says which. **`sign_headers(url=...)` has the wrong
signature, not just the wrong algorithm.**

Verified by reading Aliyun's published signer, not by a live request against a
production bucket. The conclusion — a SigV4 signature will not authenticate
against `*.oss-alipay.aliyuncs.com` — follows from the algorithms differing at
every step (key derivation, scope suffix, signed headers, canonical URI).

**D2 — the fetch layer returns a union, so every consumer branches on type.**
`fetch_declared` returns `FetchedEntry | GitEntrySource`. `resources.py` alone
asks "which one did I get?" four times (`:192`, `:204`, `:284`, plus the
`_decode_tree_bytes` keep-last case) to answer one question: *what files did
this entry deliver?*

The union has also let the two roads drift on an obligation they share. On the
blob road the **fetcher** files bytes with the content store; on the git road the
**materialiser** does (`_git_members` calls `self._fetcher.file_bytes`). Same
duty, two homes — and the next fetching category will copy whichever one its
author happens to read.

**D3 — protocol dispatch is a single `if`.** The whole of it:

```python
if decl.protocol is not SourceKind.GIT:
    return self.fetch(ctx, source_url=decl.url, ...)
```

Adding a protocol means editing that branch *and* every `isinstance` chain
downstream. D2 and D3 are the same defect seen from two ends.

**D4 — `oss` names two different things, and one of them has no users.**
Today `SourceKind.OSS` covers both "a plain HTTPS GET of a URL the tenant wrote"
and "a read from a private object store". They do not share a threat model:

| | plain URL | object store |
|---|---|---|
| host chosen by | the tenant's document | the stored credential |
| needs URL-shape refusal | yes | no URL exists |
| needs address validation + pinning | yes | no tenant-supplied host |
| needs redirect re-validation | yes | the SDK owns the transport |
| needs a streaming byte cap | yes | yes |
| needs sha256 + declared-digest check | yes | yes |

Four of the guarded fetcher's six protections exist *because* the tenant supplies
the URL. Collapsing both roads into one name means either the object-store road
carries machinery it does not need, or the URL road loses machinery it does.

Two resolutions were available: split the name (add an `https` protocol), or drop
the road. **The road is dropped** — no end user fetches manifest content from a
plain URL, so the capability is paying for its threat model with no traffic. See
decision D-7.

**D5 — the endpoint is derived from the manifest, not from the credential.**
`headers_for(url)` signs whatever URL it is handed, and that URL comes from the
tenant's document. This is *not* currently a hole: `allowed_prefixes` is
mandatory and non-empty (`validate_prefixes` refuses `[]`), and
`CanonicalPrefix.allows` pins scheme, host and port exactly. So the credential is
host-constrained today — **by policy**.

Under an SDK client the endpoint is a property of the credential, so the same
constraint holds **by construction**: there is no tenant-supplied host for a
policy to have to constrain. That is why `allowed_prefixes` becomes meaningless
for `oss` (decision D-4 below) — the field it exists to guard no longer exists on
that road.

## The vocabulary

`SourceKind` is **unchanged** — `content`, `oss`, `git` — and the support matrix
stays 6×3 = 18 cells. What changes is what `oss` means and how it is spelled.

| | before | after |
|---|---|---|
| `content` | inline text on the entry | unchanged |
| `git` | repository over HTTPS | unchanged |
| `oss` | an HTTPS GET of a URL, optionally signed | **a bucket + key read through an object-store client** |

**HTTPS does not disappear from the wire** — the git CLI still speaks HTTPS to a
remote, and the object-store SDK still speaks HTTPS to an endpoint. What goes
away is HTTPS as a *source protocol*: a manifest can no longer hand the platform
a raw URL to GET.

`SourceForm` (the published `constructs` spelling) loses `url` and gains `oss`,
because the spelling it named no longer exists: an `oss` source is now written as
a mapping or referenced by name, never as a bare string.

**The guarded fetcher stays.** It has a second consumer that is not a manifest
source at all: `cli_tools/service.py`'s API-driven install takes a plain URL from
an API *caller* — "no manifest and no `sources` map to resolve against". That
road keeps the fetcher, its DI wiring, `FetchedObject` and the content service
exactly as they are. Removing the URL *source protocol* is not removing the URL
*transport*.

## Behaviour changes

### The object-store client is a plugin, allocated per credential

A new plugin kind in `plugin_api/object_store_client.py`. It is deliberately
**not** `ObjectStoragePlugin`, for three reasons that each rule it out:

- `ObjectStoragePlugin` is a **singleton bound to one bucket** at DI wiring time
  (`CommunityObjectStorageModule.object_storage`). A manifest names a different
  bucket per source.
- Its credentials come from boto3's **standard env chain, never from config** —
  by explicit design. Ours come from a tenant's stored credential row.
- Its error contract **swallows everything** into `False` / `None` / `[]`. The
  fetch path must tell a missing object from a denied one from an unreachable
  store, because `keep_last` may mask exactly one of those three.

```python
@dataclass(frozen=True)
class ObjectStoreTarget:
    endpoint: str          # from the credential row
    region: str | None     # from the credential row
    bucket: str            # from the source declaration
    access_key_id: str     # from the credential row
    secret_access_key: str # decrypted at the call, never stored on the object


class ObjectFetchStatus(StrEnum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    DENIED = "denied"
    TOO_LARGE = "too_large"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ObjectFetchResult:
    status: ObjectFetchStatus
    content: bytes | None = None
    #: Report-safe. Names the bucket, the key and the status — never the
    #: endpoint's query string, never the credential's value.
    detail: str = ""


@runtime_checkable
class ObjectStoreClient(Protocol):
    def get(self, key: str, *, byte_limit: int) -> ObjectFetchResult: ...


@runtime_checkable
class ObjectStoreClientFactory(Plugin, Protocol):
    def client_for(self, target: ObjectStoreTarget) -> ObjectStoreClient: ...
```

**The status → outcome mapping is the load-bearing part**, and it follows W5's
existing ruling that `keep_last` may mask a *failure* but never a *refusal*:

| status | outcome | may `keep_last` stand in? |
|---|---|---|
| `NOT_FOUND` | refusal — the document names an object that is not there | **no** |
| `DENIED` | refusal — the credential is not authorized for this bucket | **no** |
| `TOO_LARGE` | refusal — over the category's `FETCH_ENTRY_LIMITS` cap | **no** |
| `UNAVAILABLE` | failure — transport, DNS, 5xx | **yes** |

A denied credential silently serving last apply's bytes for a year is exactly the
outcome that ruling exists to prevent.

`byte_limit` is enforced **while streaming**, so `TOO_LARGE` comes back without
the object ever being fully buffered — this is guarded-fetcher protection (4),
carried over. Protection (5), the sha256 and declared-digest check, stays in the
fetcher: the plugin has no business knowing what a manifest digest is.

Implementations, by deploy profile:

- **corp** — `oss2`, the Aliyun native SDK. This is the one that reaches
  `*.oss-alipay.aliyuncs.com`.
- **community** — boto3 against an S3-compatible endpoint (MinIO, S3, R2,
  Aliyun's S3-compatible OSS endpoints). `CommunityS3ObjectStorage` is the
  working template; boto3 is already a community dependency (`boto3>=1.34`).
- **test** — an in-memory client with scriptable per-key results, so every row
  of the status table above is testable without a network.

**An honest note, stated here because it is the whole point of the seam:**
deleting the SigV4 signer does not by itself make ant-internal OSS reachable.
boto3 signs SigV4 too — the community client will not authenticate against
`oss-alipay` either. What makes it reachable is that the corp deployment binds an
`oss2`-backed client. **The plugin seam is the fix; the SDK is the payload.**

### `oss` addresses a bucket and a key, not a URL

```yaml
sources:
  prod-data:
    protocol: oss
    auth: oss-prod                       # endpoint + region + AK/SK live here
    bucket: antsys-agentclaw-prod
    key: aidesktop/aidesktop_prod/       # optional prefix
manifest:
  resources:
    - path: workspace/data/
      from: prod-data
      key: bolt_data/staff_272471/x.tar.gz   # composes onto the source's prefix
      unpack: tar.gz
```

- On a **source**: `bucket` required, `key` optional (a prefix). `url` is
  refused, with a message naming `bucket`/`key`.
- On an **entry**: `key` composes onto the source's, source-first, re-checked by
  `relative_path_refusal` — the identical rule and the identical code path
  `subpath` already uses for git.
- `subpath` stays **git-only on a source**, and keeps its shipped entry-level
  meaning of *inside the fetched archive*. For an `oss` entry the two are
  different jobs and both stay meaningful: `key` picks the object, `subpath`
  picks inside the archive it unpacks to. `check_source_subpath`'s
  `subpath_without_archive` refusal is unchanged.

### The plain-URL source road is removed

A manifest declares `git` or `oss`. Everything else is refused at `PUT`:

- the **bare-string form** `source: "https://example.com/x.tar.gz"` — today's
  `entries.py:370-376`, which classifies a string as `SourceForm.URL` /
  `SourceKind.OSS`. It is refused with a message naming the two protocols and
  the mapping form.
- `url:` on an `oss` source, refused with a message naming `bucket`/`key`.

`url` remains a required field on a **git** source — that is a repository
address, not a fetch target.

Consequences, stated rather than discovered later:

- `SourceForm.URL` → `SourceForm.OSS`, a published `constructs` rename.
- Roughly 77 usages across 8 test files move to bucket/key or become refusal
  assertions. Mechanical, but it is the bulk of the diff in group D.
- A deployment with no git remote and no object store can no longer fetch
  manifest content at all. That is the intended trade: the URL road's threat
  surface was being paid for with no traffic (D4).

### The fetch layer returns one type

```python
class EntryDelivery(Protocol):
    """What a source delivered, and how the category reads it."""

    def members(self, *, unpack: str | None,
                strip_components: int) -> list[tuple[str, bytes]] | str: ...
    def single(self) -> bytes: ...
    def note(self) -> str | None: ...
    def digest(self) -> str: ...
    def source_url(self) -> str | None: ...
    def content_type(self) -> str | None: ...
    def receipt_url(self) -> str: ...
    #: Did the source deliver a *tree* or a single object? See below — this
    #: is the one discriminator that survives, and it is a capability
    #: question, not a type check.
    def is_tree(self) -> bool: ...
```

| | `GitDelivery` (wraps a checkout) | `BlobDelivery` (wraps bytes) |
|---|---|---|
| `members()` | walk the tree under the composed subpath, file the canonical blob, return pairs | `_TREE_MAGIC` → decode a stored tree; else unpack the archive |
| `single()` | `read_file()` off the checkout | the bytes |
| `note()` | `moved_note()` | `fallback_reason` |
| `is_tree()` | `True` | `False` |

`GitDelivery.members()` ignores `unpack`/`strip_components` — legitimately, and
provably: `ARCHIVE_FIELDS_BY_KIND[GIT]` is empty, so a git source carrying either
was refused at `PUT` and can never reach a delivery.

**The category keeps its authority.** The materialiser still reads
`entry["unpack"]` and passes the value down; the delivery is told, never asked.
The rule `entry_fetch.py:177` states — *what "the entry's bytes" are is a
category question the fetch layer must not answer* — is preserved exactly. What
changes is that the mechanism moves behind the seam: `_unpack_members`,
`_git_members`, `_git_file` and `_decode_tree_bytes` move out of `resources.py`,
which fixes D2's split obligation as a side effect.

**One branch survives, and `skills` is why.** Reading all four callers, three of
them (`resources`, `identity`, `cli_tools`) reduce to `members()` / `single()`
with no branch at all. `skills` does not, and the reason is deliberate design
rather than accident: its two roads run **two different validators**. A fetched
zip with no `subpath` goes byte-for-byte through `validate_zip` — the same
validator the manual upload service runs, *so that limits and layout are one
rule* (`skills.py:437-441`) — while a git tree goes through `validate_directory`.
Forcing those into one `members()` call would either discard the byte-for-byte
zip road or make `members()` mean two things.

So `skills` keeps one branch, on `is_tree()` rather than
`isinstance(…, GitEntrySource)`. That is a smaller thing than it looks: a class
check names *this* implementation, while `is_tree()` names a property of what
arrived, so a third protocol that delivers a tree slots into the existing branch
instead of adding a third arm to it.

**Net: 8 `isinstance` sites become 1 branch and 1 condition**, not zero.
`skills` branches on `is_tree()` to pick a validator; `cli_tools` uses it as a
*condition* — its "subpath without unpack" refusal is about a missing archive,
and a tree is not an archive (its subpath already selected the file `single()`
returned). Stated honestly here because the first draft of this spec claimed
zero: reading `skills._build_package` corrected it to one, and implementing
`cli_tools` corrected it again.

### One fetcher per protocol

```python
class SourceFetcher(Protocol):
    def fetch(self, ctx, *, decl, entry, category,
              entry_identity) -> EntryDelivery: ...

_FETCHERS: Mapping[SourceKind, SourceFetcher]   # git | oss
```

`fetch_declared` parses the declaration, looks up the fetcher, and calls it. No
`if`. Adding a protocol is a new row in two tables (the matrix and this one) and
one new class; no materialiser changes.

## Acceptance criteria

1. `credentials/signing.py` does not exist. No module imports `sign_headers`.
   `SourceCredentialBinding.headers_for` is `{row.header_name: secret}` again,
   with no branch.
2. A `protocol: oss` source reads through `ObjectStoreClientFactory`. A test
   asserts the guarded fetcher is **not called** on that road (`stub.calls == []`),
   the way D1's fetch-before-validation defect was pinned in #2019.
3. Each of the five `ObjectFetchStatus` values produces the outcome in the table
   above, asserted per row. In particular: `keep_last` stands in for
   `UNAVAILABLE` and does **not** for `NOT_FOUND` / `DENIED` / `TOO_LARGE`.
4. `byte_limit` is enforced without buffering the whole object: a client handed
   an oversized object returns `TOO_LARGE` having read no more than the cap plus
   one chunk.
5. `fetch_declared` returns `EntryDelivery`. No `isinstance(..., GitEntrySource)`
   remains in any materialiser; `skills` carries the one surviving branch, on
   `is_tree()`. Every existing materialiser test passes unchanged **except**
   `identity`'s git-without-subpath message (see D-9) — this is a refactor, and
   its correctness claim is that behaviour did not move.
6. `SourceKind` is unchanged and the matrix is still 18 cells, exhaustive by
   construction (a missing verdict raises at import, a stale one raises
   `RuntimeError`). `SourceForm.URL` is gone and `SourceForm.OSS` is published
   in its place.
7. An `oss` source declaring `url` is refused at `PUT` with a message naming
   `bucket`/`key`. An `oss` source with no `bucket` is refused. A bare-string
   `source: "https://…"` is refused, naming the two protocols. Entry-level `key`
   composes and is re-checked by `relative_path_refusal`.
8. The credential surface: `endpoint` is required for `oss_aksk`, stored, and
   readable back (it is an identifier, not a secret — the same ruling
   `access_key_id` already has). The secret half still has no representation in
   `SourceCredentialRecord`.
9. Docs corrected: `manifest-schema.zh-CN.md`, `examples.zh-CN.md` and the
   `oss_aksk` credential docs describe bucket/key addressing and the `https`
   protocol. (Deferred from #2019 by explicit agreement.)

## Out of scope

- **Listing a bucket prefix.** An `oss` directory entry still requires `unpack`
  — `_archive_refusal` is unchanged. Adding `list()` to the plugin surface would
  be mirroring an SDK ahead of a consumer, which `plugin_api/object_storage.py`
  explicitly warns against. It is also the natural home for the "Avernet-side
  OSS data processing component" discussed separately, and should be designed
  with that consumer in front of it.
- **Writing to an object store from a manifest.** Read only.
- The two git-road items in
  [#2045](https://github.com/inclusionAI/Avernet/issues/2045) — the unbounded
  pack transfer and the orphan-checkout sweep. Independent of this change.

## Decisions taken

- **D-1: bucket and key are separate fields, not an `oss://…` URL.** The
  official signer takes them as structured inputs (D1), so a URL form would be
  parsed apart again at the first call. Settled with the user.
- **D-2: the endpoint lives on the credential, not on the source.** It is issued
  with the AK/SK and belongs to the same trust boundary; putting it in the
  document is what forced `allowed_prefixes` to do host-pinning by policy (D5).
- **D-3: `bucket` lives on the source, not on the credential.** One credential
  commonly reads several buckets in one account, and the bucket is the part a
  manifest author legitimately chooses.
- **D-4: no `allowed_prefixes` for `oss`.** With no tenant-supplied host there is
  nothing for a prefix to constrain. Settled with the user; see D-8 for where
  the constraint lands instead.
- **D-5: `schema_version` stays `1`, and the old `oss` spelling is refused
  rather than translated.** Same reasoning #2019 recorded: the feature is
  pre-release, so no installed base is protected by a compatibility road, and a
  dual spelling is two vocabularies to keep honest forever.
- **D-9: `identity` loses its own git-without-subpath message.** Today
  `identity.py:185-196` checks `decl.subpath is None` before reading and emits a
  category-worded refusal. Asking that question requires exposing a git-only
  field on the delivery, which is the seam leaking. `GitCheckout.read_file`
  already refuses the same case with *"read_file: the source's subpath must name
  a single file"*, so the check is dropped and the generic message stands. One
  existing test's expected string changes — the single sanctioned test edit in
  group A, and it is a wording change, not a behaviour change.
- **D-6: `EntryDelivery` is a `Protocol`, not a base class.** It is a seam two
  unrelated things satisfy, matching how `FetchContext` and the plugin
  capabilities are already declared in this module.
- **D-7: no `https` protocol, and the plain-URL source road is removed.** The
  alternative was to split the name so the two threat models stopped sharing
  one; the road is dropped instead because no end user fetches manifest content
  from a plain URL. Settled with the user. The URL *transport* survives for
  `cli_tools`' API-driven install, which is not a manifest source.
- **D-8: `allowed_prefixes` is mandatory for `header` credentials only.** Stated
  by protocol it is "required when the source is git", which is the same rule:
  after D-7, `header` is the mechanism git sources use and `oss_aksk` is the
  mechanism object stores use. The constraint moves rather than being forced
  onto a road it does not fit. Settled with the user.
