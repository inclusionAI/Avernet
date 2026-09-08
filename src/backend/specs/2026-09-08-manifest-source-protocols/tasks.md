# Tasks — Manifest source protocols + `resources` over git

Spec: `spec.md` · Plan: `plan.md`

Groups run in order; tasks within a group may be done together. Each group ends
green — the suite passes before the next one starts.

---

## Group A — the matrix, as one table (no behaviour change)

- [x] **A1** Add `core/bot_config_manifest/support_matrix.py`: `SourceKind`
      (`content`/`oss`/`git`), `MATRIX` covering every (category, kind) cell
      with `None` or a refusal reason, `DIGEST_REQUIRED`, and
      `ARCHIVE_FIELDS_BY_KIND`. Build the mapping by iterating both enums so a
      missing verdict raises at import.
- [x] **A2** Unit-test A1: every cell present; a category or kind added without
      a verdict fails; reason strings non-empty exactly where unsupported.
- [x] **A3** Point `schema/entries.py`'s source gate at `MATRIX`, replacing
      `_UNDELIVERED_FORM_REASON`. Keep the `unsupported_source` violation code.
      **The existing suites must pass unchanged** — at this point the table
      still encodes today's behaviour, resources × git included.
- [x] **A4** Publish the matrix from `capabilities.py` as `source_matrix`,
      intersected with the existing engine/bot-type verdicts (a desktop bot
      refuses every cell). Surface it on the capabilities response schema.
- [x] **A5** Matrix-conformance test: parametrised over every `MATRIX` cell,
      issue a real `PUT` and assert accepted ⇔ supported, and that a refusal
      carries the cell's own reason string. Green against **current** behaviour
      now; it is the acceptance gate for every later group.

## Group B — one source model

- [x] **B1** Add `schema/sources.py`: `SourceDecl` and `parse_source(raw)`,
      reading `protocol: git|oss` + `url` into one model.
- [x] **B2** Unit-test B1: both protocols parse; `protocol` missing or unknown
      refused; the old `git:`/`url:`-as-protocol-key spelling refused with a
      message naming the `protocol` form; `ref` refused on `oss`; `ref`
      accepting a tag, a branch and a full commit SHA.
- [x] **B3** Route `schema/entries.py` and `apply/entry_fetch.py` through
      `SourceDecl` so no consumer reads `git`/`url` keys directly.
- [x] **B4** Update every in-repo manifest fixture and doc snippet using the
      old spelling. `schema_version` stays `1` and
      `SUPPORTED_SCHEMA_VERSIONS` is untouched — assert that in a test so a
      later drive-by bump is caught.
- [x] **B5** **D3** — compose `source.subpath` with entry `subpath` in
      `fetch_declared()`, re-checked by `relative_path_refusal`. Delete the
      "entry-level 'subpath' is not supported on a git source" refusal.
- [x] **B6** Test B5: two entries off one named git source with different
      `subpath`s both resolve; the report shows **one** `sources[]` row and one
      `resolved_sha`; traversal (`../`) refused with the schema's own message.
- [x] **B7** Refuse `unpack`/`strip_components` on a git source at `PUT` via
      `ARCHIVE_FIELDS_BY_KIND`, naming the field and the protocol. Test both
      fields.
- [x] **B8** End-to-end refusal test: a stored-shaped document using the old
      spelling is refused at `PUT`, and the violation names the `protocol`
      form.

## Group C — the OSS road

- [x] **C1** DDL `sql/2026_09_08_source_credential_aksk.sql`: nullable
      `access_key_id`, additive, no backfill.
- [x] **C2** `credentials/models.py` — carry `access_key_id`; drop `OSS_AKSK`
      from `RESERVED_TYPES`, keep `BASIC`. Repository protocol + implementation
      read/write the column.
- [x] **C3** `credentials/service.py` — require both halves for `oss_aksk` and
      `header_name` for `header`; refuse the mismatch. Test both directions,
      and that read-back exposes `access_key_id` but never the secret.
- [x] **C4** `source_credentials/schemas.py` — optional `access_key_id`;
      correct the `oss_aksk` description. Endpoint cases for register, rotate
      and masked read, **one per credential type** (`header` for git,
      `oss_aksk` for the object store), plus the three refused shapes:
      `header` with no `header_name`, `oss_aksk` missing either half, and
      `access_key_id` sent on a `header` credential.
- [x] **C5** Add `fetch/oss_source.py`: per-call signed client built from the
      tenant credential, under `guarded_fetcher`'s ceilings, timeout budget and
      redirect policy. Not the injected `ObjectStoragePlugin`.
- [x] **C6** Dispatch `fetch_declared()` on `SourceDecl.protocol`: `GIT` as
      today; `OSS` → signed road for `oss_aksk`, header/anonymous HTTPS
      otherwise. Both yield `FetchedEntry`.
- [x] **C7** Test C5/C6: signing; `allowed_prefixes` refusal including the
      `/team/content` vs `/team/content-secret` segment case; size and timeout
      ceilings; the credential **name** and never a value in errors and report
      rows.
- [x] **C8** **D5** — move the digest rule to `DIGEST_REQUIRED` keyed on
      `(category, SourceKind)`. Test: `cli_tools` + named git accepted with no
      digest and applies; `cli_tools`/`skills` + `oss` still refused without
      one.

## Group D — `resources` over git

- [x] **D1** File entry: `fetch_declared` → `read_file()` on a
      `GitEntrySource`, `.content` otherwise. One `Intent`.
- [x] **D2** Directory entry: `files()` on a `GitEntrySource` feeding the
      existing member gate, `_DECLARED_TREE` marker and per-member intents
      unchanged; ask `unpack` only on the OSS road.
- [x] **D3** Flip the resources row of `MATRIX` to supported for `git` — A5
      then proves the surface agrees.
- [x] **D4** Materialiser tests over the existing write-counting fakes: a file
      entry writes one file at `path`; a directory entry replaces the tree
      under `path` **recursively, nested subdirectories included**, and a file
      removed upstream disappears on the next apply; a member refused by
      admission aborts the category **with the existing tree still standing**.
- [x] **D5** End-to-end: the `examples.zh-CN.md` document (protocol spelling) is
      accepted and applies — the D2 defect, closed.

## Group E — documentation

- [x] **E1** `docs/bot-config-manifest/manifest-schema.zh-CN.md`: the protocol
      axis replacing the `git:`/`url:` keys, and §7's undelivered list reduced
      to what is actually undelivered.
- [x] **E2** `user-manual.zh-CN.md`: §5.3 resources sources; Appendix C's
      resources and `cli_tools` rows; §B.7 enum tables; **§10 gains the
      2048-char source-URL limit it omits today**.
- [x] **E3** `examples.zh-CN.md`: correct the document to the protocol
      spelling, show an `oss` entry beside the git ones, and fix its stale
      caveat header (`cli_tools`, `from`/git are open).
- [x] **E4** `docs/bot-config-manifest/README.zh-CN.md` term table + the
      work-items entry for this follow-up.

## Group F — verification

- [x] **F1** Full backend suite green: `uv run pytest tests/community -q`.
- [x] **F2** `tests/community/core/bot_config_manifest/` (865) and the manifest
      endpoint cases pass with **no behavioural edits** — updating a fixture's
      source spelling (B4) is expected; changing an assertion is a contract
      change and gets its own line in the PR body.
- [x] **F3** A5 green over the final matrix — the surface and the table agree
      cell for cell.
- [x] **F4** PR body records: the matrix as shipped, the in-place spelling change, and
      the four defects closed (D1, D3, D4, D5) with the one deferred
      (`aborted` semantics).

---

**Counts.** 6 groups, 30 tasks. Group A is behaviour-neutral scaffolding. Group
B changes one user-visible thing — the source spelling — and everything else
lands in C or D, all of it behind the A5 conformance test.

---

## Implementation notes — where the plan bent

Recorded here rather than silently: each of these was found while building and
each changes something the plan asserted.

**1. Group order.** A → B → C → D as written is not runnable. Group A's task A3
points the validator at the matrix, which *opens* `resources` × git — but the
resources materialiser cannot serve it until group D. Running A3 first would
ship a window where the surface accepts what apply cannot deliver, which is the
one rule this feature rests on. Shipped order: A1–A2 (the table, pure data) →
B (the source model) → D1–D2 (the materialiser) → A3–A5 (wire the matrix, which
is the flip point) → C → E → F.

**2. `subpath` on the `oss` road does not compose into the URL.** The plan and
`manifest-schema.zh-CN.md` §2.3 both said a URL source's `url` is a prefix that
an entry's `subpath` appends to. Nothing ever implemented that, and the shipped
skills/`cli_tools` behaviour is the opposite: `subpath` selects *inside the
fetched archive*. Two meanings for one word, split by category, is what this
change exists to remove — so the rule shipped is the single one: **`subpath`
selects within what the source delivered.** Git delivers a tree, so the
source's and the entry's compose (D3, as planned). `oss` delivers one object,
so its `url` addresses that object and two objects are two sources. The docs now
say this, and say plainly that declare-once-reference-many is git's property.

**3. No `fetch/oss_source.py`.** The plan called for a signed object-store
transport beside `git_source.py`. Unnecessary: AK/SK signing produces *headers*,
and `headers_for(url)` is already the seam the guarded fetcher calls. Signing
lives in `credentials/signing.py` (botocore's `SigV4Auth`, not hand-rolled) and
the one guarded road keeps its ceilings, timeout budget, redirect policy and
per-hop re-authorization. A parallel transport would have been a second copy of
every limit to keep in step with the first.

**4. `region` joins `access_key_id` in the DDL.** SigV4 binds a signature to a
region string. One nullable column more, same migration.

**5. `cli_tools` needed the same fix `resources` did (not in the plan).** Its
materialiser read `decl.source_url`, which for a `from:` entry holds the source
*name*, and put that on the wire as a URL — the one construct that passed `PUT`
and failed at apply. Opening the matrix cell without fixing it would have
shipped "accepted" and "appliable" as different sets. Two consequences followed:
its resolve-time digest belt had to key on the protocol (a belt refusing what
the surface accepts is the same defect wearing the other hat), and an unpinned
(git-sourced) declaration now has **no convergence key**, so it re-acquires every
apply — `("", subpath)` would have made every git tool at one path compare equal
and a moved ref would have survived as `unchanged`.

**6. `mode` is git-only now.** A `url` source accepted `mode` and nothing read
it. Same argument as acceptance criterion 4: a field that reads as configuration
and governs nothing is refused, not ignored.

**7. Contract changes to existing tests** (F2's "gets its own line"):
- `test_named_and_git_sources_are_refused_for_resources_entries` — replaced by
  its inverse. It asserted defect D1.
- `test_entry_level_subpath_on_a_git_source_is_refused` — replaced by the
  composition tests. It asserted defect D3.
- `test_reserved_types_are_refused_at_write[oss_aksk]` — `oss_aksk` is
  implemented; `basic` still holds the case.
- the two `mode` cases moved from a url source to a git source (see 6).
- `cli_tools` fixtures writing `from: <a URL>` corrected to `source:` — they
  only ever worked because a source name was being fetched as a URL.
