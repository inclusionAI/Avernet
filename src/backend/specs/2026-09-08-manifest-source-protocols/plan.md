# Plan — Manifest source protocols + `resources` over git

Spec: `spec.md` in this directory.

## Shape of the change

Four independent pieces, in dependency order. Each is separately reviewable and
the first two carry no behaviour change on their own.

```
① support matrix  ──► one table; validator + capabilities both read it
        │
② source model   ──► `protocol:` parses into one internal SourceDecl
        │                 └─ entry subpath composes with source subpath  (D3)
        ├──► ③ oss road   ──► credential AK/SK + guarded object-store fetch (D4)
        └──► ④ resources  ──► consume GitEntrySource; drop unpack on git (D1)
```

## ① The support matrix — one table, two readers

**New:** `core/bot_config_manifest/support_matrix.py`

```python
class SourceKind(StrEnum):      # how an entry names its content
    CONTENT = "content"          # inline, no fetch
    OSS     = "oss"
    GIT     = "git"

#: Every (category, kind) cell. A cell is None (supported) or a refusal reason.
#: Exhaustive by construction — built by iterating both enums, so a category or
#: protocol added without a verdict raises at import, never resolves silently.
MATRIX: Mapping[tuple[ManifestCategory, SourceKind], str | None]
```

Cell values carry the *reason string* the caller sees, so the refusal a
validator emits and the reason the capabilities endpoint publishes are
literally the same object — they cannot drift.

Beside it, two rules the matrix references rather than duplicates:

```python
DIGEST_REQUIRED: frozenset[tuple[ManifestCategory, SourceKind]]   # skills/cli_tools × oss
ARCHIVE_FIELDS_BY_KIND: Mapping[SourceKind, frozenset[str]]       # unpack/strip → OSS only
```

**Readers.**

- `schema/entries.py` — `resolve_source()` replaces the ad-hoc
  `_UNDELIVERED_FORM_REASON` gate with a matrix lookup. The existing violation
  code `unsupported_source` is kept; only its population changes.
- `capabilities.py` — `resolve_capabilities()` gains a `source_matrix` tuple
  built from the same table, intersected with the engine/bot-type verdicts
  already computed (a desktop bot refuses every cell, as it refuses every
  construct today).

`SourceForm` (`url`/`git`/`named`/`content`) stays exactly as it is on the
existing `constructs` array — that array is published contract. `SourceKind` is
the new, orthogonal vocabulary; `named` is deliberately absent from it, because
a named source resolves to a protocol and the protocol's cell governs.

## ② One source model

**New:** `core/bot_config_manifest/schema/sources.py`

```python
@dataclass(frozen=True)
class SourceDecl:
    protocol: SourceKind          # GIT | OSS
    url: str
    ref: str | None               # git only — tag, branch, or commit SHA
    subpath: str | None
    auth: str | None
    mode: str                     # strict | non_strict
```

`parse_source(raw)` is the one place a source declaration is read, and
everything downstream — validator, capability checks, `EntryFetcher` — sees
`SourceDecl` only, never raw keys.

Rules: `protocol` required and one of `git`/`oss`; `url` required; the old
`git:`/`url:`-as-protocol-key spelling refused with a message naming the
`protocol` form; `ref` refused on `oss`; `unpack`/`strip_components` refused on
`git` (spec §"unpack/strip_components").

**`schema_version` stays `1`** — `SUPPORTED_SCHEMA_VERSIONS` is untouched. The
spelling changes in place; the feature is pre-release, so there is no installed
base and no compatibility road to build or test.

### Entry-level `subpath` (D3)

`apply/entry_fetch.py` — `fetch_declared()` currently refuses an entry
`subpath` on the git road. It instead **composes**: `source.subpath` joined
with `entry.subpath`, normalised and re-checked by the same
`relative_path_refusal` predicate the schema uses (no second, weaker rule), and
handed to `GitEntrySource.subpath`. One checkout per `(url, ref)` per apply is
already cached in the source session, so N entries off one source stay one
fetch and one `resolved_sha`.

## ③ The OSS road

**Credential (D4).** `credentials/models.py` gains `access_key_id` beside the
existing `secret_ciphertext`; `RESERVED_TYPES` drops `OSS_AKSK`, keeping
`BASIC`. `credentials/service.py` requires both values when
`type == oss_aksk` and `header_name` when `type == header`, refusing the
mismatch. DDL in `core/bot_config_manifest/sql/2026_09_08_source_credential_aksk.sql`
— nullable column, additive, no backfill (existing rows are `header` type).
The redacted record gains `access_key_id`; it is an identifier, not a secret,
and read-back needs it to be useful for rotation. The secret half stays
unreadable.

`adapters/http/openapi_v1/source_credentials/schemas.py`: `SourceCredentialWrite`
gains optional `access_key_id`; the `oss_aksk` description stops saying
"refused at write".

**Fetch.** `fetch/oss_source.py`, beside `git_source.py`, constructing a
per-call signed client from the tenant's credential — *not* the injected
`ObjectStoragePlugin`, which is the platform's own store bound to boto3's env
chain and must not learn about tenant credentials. It reuses
`guarded_fetcher`'s ceilings, timeout budget and redirect policy, and the
existing `allowed_prefixes` segment matching decides where a credential may be
presented.

`EntryFetcher.fetch_declared()` dispatches on `SourceDecl.protocol`: `GIT` →
today's `GitEntrySource`; `OSS` → the signed road when the credential is
`oss_aksk`, the existing header/anonymous HTTPS road otherwise. Both return
`FetchedEntry`, so no materialiser changes for OSS.

**D5** falls out here: the digest rule moves to `DIGEST_REQUIRED` keyed on
`(category, SourceKind)`, and a named git source is `SourceKind.GIT` like an
inline one — the `NAMED`-vs-`GIT` misclassification has nowhere left to live.

## ④ `resources` over git (D1)

`apply/materialisers/resources.py`, `resolve()` only. The write chain, the
plan/write split, `_delivery_refusal`, the `_DECLARED_TREE` marker and every
downstream consumer are untouched — this is what keeps the change small.

- **File entry** (`path` not ending `/`): call `fetch_declared`; on a
  `GitEntrySource` take `read_file()`, else `fetched.content` as today. One
  `Intent(path, bytes)`.
- **Directory entry** (`path` ends `/`): on a `GitEntrySource` take `files()`,
  which returns `list[tuple[str, bytes]]` — **the same `(rel, data)` shape**
  `_unpack_members` already produces. It feeds the existing member gate,
  marker intent and per-member intents unchanged. The `unpack` requirement is
  asked only on the OSS road.

`capabilities.py`'s `RESOURCES` comment about the undelivered pair goes away
with the pair.

## Files

| file | change |
| --- | --- |
| `core/bot_config_manifest/support_matrix.py` | **new** — the table + digest/archive rules |
| `core/bot_config_manifest/schema/sources.py` | **new** — `SourceDecl`, `parse_source` |
| `core/bot_config_manifest/fetch/oss_source.py` | **new** — signed object-store fetch |
| `core/bot_config_manifest/sql/2026_09_08_source_credential_aksk.sql` | **new** — DDL |
| `core/bot_config_manifest/schema/entries.py` | matrix lookup replaces the ad-hoc gate; `subpath`/`unpack` rules per protocol |
| `core/bot_config_manifest/capabilities.py` | publish `source_matrix`; drop the resources caveat |
| `core/bot_config_manifest/apply/entry_fetch.py` | protocol dispatch; compose source+entry `subpath` |
| `core/bot_config_manifest/apply/materialisers/resources.py` | consume `GitEntrySource` |
| `core/bot_config_manifest/apply/materialisers/cli_tools.py` | digest via `DIGEST_REQUIRED` |
| `core/bot_config_manifest/credentials/{models,service}.py` | AK/SK |
| `core/repository/{protocols,implementations}/bot/source_credential.py` | the new column |
| `adapters/http/openapi_v1/source_credentials/schemas.py` | `access_key_id` |
| `adapters/http/openapi_v1/bots/schemas_config_manifest.py` | `source_matrix` on the capabilities response |

## Tests

- **Matrix conformance** (`tests/community/endpoints/`): parametrised over
  every `MATRIX` cell, issues a real `PUT`, asserts accepted ⇔ the cell says
  supported and that a refusal carries the cell's own reason string. This is
  acceptance criterion 8 and the guard against the class of drift that produced
  D1–D5.
- **Old spelling refused**: a source using `git:`/`url:` as the protocol key is
  refused at `PUT` with a message naming the `protocol` form — not silently
  accepted, not half-parsed.
- **`subpath` composition**: two entries, one source, two paths, one
  `resolved_sha`; traversal attempts refused by the shared predicate.
- **resources × git**: file entry and directory entry, over the existing
  materialiser fakes that count writes.
- **OSS**: signing, `allowed_prefixes` refusal (including the
  `/team/content-secret` segment case), credential redaction on every read
  path, and that `oss_aksk` requires both halves.
- Existing suites — `tests/community/core/bot_config_manifest/` (865),
  the manifest endpoint cases, and the manual walkthrough flow — must stay
  green unchanged; any edit to them is a contract change needing its own line
  in the report.

## Risks

- **The capabilities response grows.** Additive, but it is published contract
  read by generated clients; `constructs` keeps its exact shape.
- **AK/SK signing is deployment-specific.** The community S3-compatible path
  (MinIO/S3 via boto3) is what this repo can test; a corp OSS variant may need
  its own signer behind the same seam. The port is shaped for that; only one
  implementation ships here.
- **Existing stored documents stop applying.** Accepted: the feature is
  pre-release and self-tested, so the only documents using the old spelling are
  our own test fixtures. Any fixture carrying it is updated in the same change,
  and the refusal message names the replacement.
