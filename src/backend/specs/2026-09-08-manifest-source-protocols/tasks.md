# Tasks — Manifest source protocols + `resources` over git

Spec: `spec.md` · Plan: `plan.md`

Groups run in order; tasks within a group may be done together. Each group ends
green — the suite passes before the next one starts.

---

## Group A — the matrix, as one table (no behaviour change)

- [ ] **A1** Add `core/bot_config_manifest/support_matrix.py`: `SourceKind`
      (`content`/`oss`/`git`), `MATRIX` covering every (category, kind) cell
      with `None` or a refusal reason, `DIGEST_REQUIRED`, and
      `ARCHIVE_FIELDS_BY_KIND`. Build the mapping by iterating both enums so a
      missing verdict raises at import.
- [ ] **A2** Unit-test A1: every cell present; a category or kind added without
      a verdict fails; reason strings non-empty exactly where unsupported.
- [ ] **A3** Point `schema/entries.py`'s source gate at `MATRIX`, replacing
      `_UNDELIVERED_FORM_REASON`. Keep the `unsupported_source` violation code.
      **The existing suites must pass unchanged** — at this point the table
      still encodes today's behaviour, resources × git included.
- [ ] **A4** Publish the matrix from `capabilities.py` as `source_matrix`,
      intersected with the existing engine/bot-type verdicts (a desktop bot
      refuses every cell). Surface it on the capabilities response schema.
- [ ] **A5** Matrix-conformance test: parametrised over every `MATRIX` cell,
      issue a real `PUT` and assert accepted ⇔ supported, and that a refusal
      carries the cell's own reason string. Green against **current** behaviour
      now; it is the acceptance gate for every later group.

## Group B — one source model

- [ ] **B1** Add `schema/sources.py`: `SourceDecl` and `parse_source(raw)`,
      reading `protocol: git|oss` + `url` into one model.
- [ ] **B2** Unit-test B1: both protocols parse; `protocol` missing or unknown
      refused; the old `git:`/`url:`-as-protocol-key spelling refused with a
      message naming the `protocol` form; `ref` refused on `oss`; `ref`
      accepting a tag, a branch and a full commit SHA.
- [ ] **B3** Route `schema/entries.py` and `apply/entry_fetch.py` through
      `SourceDecl` so no consumer reads `git`/`url` keys directly.
- [ ] **B4** Update every in-repo manifest fixture and doc snippet using the
      old spelling. `schema_version` stays `1` and
      `SUPPORTED_SCHEMA_VERSIONS` is untouched — assert that in a test so a
      later drive-by bump is caught.
- [ ] **B5** **D3** — compose `source.subpath` with entry `subpath` in
      `fetch_declared()`, re-checked by `relative_path_refusal`. Delete the
      "entry-level 'subpath' is not supported on a git source" refusal.
- [ ] **B6** Test B5: two entries off one named git source with different
      `subpath`s both resolve; the report shows **one** `sources[]` row and one
      `resolved_sha`; traversal (`../`) refused with the schema's own message.
- [ ] **B7** Refuse `unpack`/`strip_components` on a git source at `PUT` via
      `ARCHIVE_FIELDS_BY_KIND`, naming the field and the protocol. Test both
      fields.
- [ ] **B8** End-to-end refusal test: a stored-shaped document using the old
      spelling is refused at `PUT`, and the violation names the `protocol`
      form.

## Group C — the OSS road

- [ ] **C1** DDL `sql/2026_09_08_source_credential_aksk.sql`: nullable
      `access_key_id`, additive, no backfill.
- [ ] **C2** `credentials/models.py` — carry `access_key_id`; drop `OSS_AKSK`
      from `RESERVED_TYPES`, keep `BASIC`. Repository protocol + implementation
      read/write the column.
- [ ] **C3** `credentials/service.py` — require both halves for `oss_aksk` and
      `header_name` for `header`; refuse the mismatch. Test both directions,
      and that read-back exposes `access_key_id` but never the secret.
- [ ] **C4** `source_credentials/schemas.py` — optional `access_key_id`;
      correct the `oss_aksk` description. Endpoint cases for register, rotate
      and masked read.
- [ ] **C5** Add `fetch/oss_source.py`: per-call signed client built from the
      tenant credential, under `guarded_fetcher`'s ceilings, timeout budget and
      redirect policy. Not the injected `ObjectStoragePlugin`.
- [ ] **C6** Dispatch `fetch_declared()` on `SourceDecl.protocol`: `GIT` as
      today; `OSS` → signed road for `oss_aksk`, header/anonymous HTTPS
      otherwise. Both yield `FetchedEntry`.
- [ ] **C7** Test C5/C6: signing; `allowed_prefixes` refusal including the
      `/team/content` vs `/team/content-secret` segment case; size and timeout
      ceilings; the credential **name** and never a value in errors and report
      rows.
- [ ] **C8** **D5** — move the digest rule to `DIGEST_REQUIRED` keyed on
      `(category, SourceKind)`. Test: `cli_tools` + named git accepted with no
      digest and applies; `cli_tools`/`skills` + `oss` still refused without
      one.

## Group D — `resources` over git

- [ ] **D1** File entry: `fetch_declared` → `read_file()` on a
      `GitEntrySource`, `.content` otherwise. One `Intent`.
- [ ] **D2** Directory entry: `files()` on a `GitEntrySource` feeding the
      existing member gate, `_DECLARED_TREE` marker and per-member intents
      unchanged; ask `unpack` only on the OSS road.
- [ ] **D3** Flip the resources row of `MATRIX` to supported for `git` — A5
      then proves the surface agrees.
- [ ] **D4** Materialiser tests over the existing write-counting fakes: a file
      entry writes one file at `path`; a directory entry replaces the tree
      under `path` **recursively, nested subdirectories included**, and a file
      removed upstream disappears on the next apply; a member refused by
      admission aborts the category **with the existing tree still standing**.
- [ ] **D5** End-to-end: the `examples.zh-CN.md` document (protocol spelling) is
      accepted and applies — the D2 defect, closed.

## Group E — documentation

- [ ] **E1** `docs/bot-config-manifest/manifest-schema.zh-CN.md`: the protocol
      axis replacing the `git:`/`url:` keys, and §7's undelivered list reduced
      to what is actually undelivered.
- [ ] **E2** `user-manual.zh-CN.md`: §5.3 resources sources; Appendix C's
      resources and `cli_tools` rows; §B.7 enum tables; **§10 gains the
      2048-char source-URL limit it omits today**.
- [ ] **E3** `examples.zh-CN.md`: correct the document to the protocol
      spelling, show an `oss` entry beside the git ones, and fix its stale
      caveat header (`cli_tools`, `from`/git are open).
- [ ] **E4** `docs/bot-config-manifest/README.zh-CN.md` term table + the
      work-items entry for this follow-up.

## Group F — verification

- [ ] **F1** Full backend suite green: `uv run pytest tests/community -q`.
- [ ] **F2** `tests/community/core/bot_config_manifest/` (865) and the manifest
      endpoint cases pass with **no behavioural edits** — updating a fixture's
      source spelling (B4) is expected; changing an assertion is a contract
      change and gets its own line in the PR body.
- [ ] **F3** A5 green over the final matrix — the surface and the table agree
      cell for cell.
- [ ] **F4** PR body records: the matrix as shipped, the in-place spelling change, and
      the four defects closed (D1, D3, D4, D5) with the one deferred
      (`aborted` semantics).

---

**Counts.** 6 groups, 30 tasks. Group A is behaviour-neutral scaffolding. Group
B changes one user-visible thing — the source spelling — and everything else
lands in C or D, all of it behind the A5 conformance test.
