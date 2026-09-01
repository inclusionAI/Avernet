# Tasks: the Two Fetching Categories (`skills` + `identity`, W5)

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

Groups run in order. Within a group, tasks are independent.

Two invariants, as in W4's task list:

- **No existing assertion is edited.** Scaffolding may move (construction
  sites gain arguments; one fake absorbs its skill-pair twin; the
  no-materialiser demo names the still-sparse `resources`), but every
  pre-existing assertion keeps its meaning. Each deliberate adjustment is
  recorded beside its task.
- **Nothing existing is destroyed by a failure.** Every fetch, package and
  legality refusal lands in `resolve`, before any write — the §3.2
  all-or-nothing rule holds by construction.

---

## Group A — Seams

## [x] Task 1: W11 `latest_receipt`
- **Goal:** the per-source receipt lookup; the audit read bounds at
  DEFAULT_RECORD_LIMIT and returns every source, so it cannot answer the
  pipeline's question on a busy bot.
- **Files:** repository protocol + implementation, service protocol +
  implementation, their tests.
- **Done when:** newest-first for one source URL; exact-equality source
  matching (a sibling path is another source); other bots and other tenants
  answer `None`; repository exercised over real SQLite.

## [x] Task 2: refuse `content` on a skills entry
- **Goal:** a skill is a package (SKILL.md + what it names); inline text
  cannot be one, and W1's rule forbids accepting an inappliable construct.
- **Files:** `schema/entries.py::validate_skill_entry`,
  `manifest-schema.zh-CN.md` §3.3 (the same-PR doc rule), the schema tests.
- **Done when:** `manifest.skills[0].content` / `content_not_a_skill_package`;
  identity keeps both forms (pinned by the same test); the schema doc gains
  the note in the same change.

---

## Group B — The pipeline

## [x] Task 3: `apply/entry_fetch.py`
- **Goal:** one funnel — substitute, consult the platform's copy, fetch
  under the named credential, file the receipt.
- **Done when:** each policy branch is its own test — substitution reaches
  the transport first; a pinned entry with a matching receipt is served from
  the store with zero network; a mismatched receipt refetches (sources
  rotate); unpinned re-fetches; `keep_last` reads the receipt only when it
  may (no receipt, or one disagreeing with the pin, refuses); transport and
  credential errors carry names, never values; `store()` receives the
  substituted URL, the credential NAME, and the actor as modifier.

---

## Group C — The materialisers

## [x] Task 4: `IdentityMaterialiser`
- **Done when:** the router's own write path (`identity_coords_from_record`
  + `update_bot_file`); both source forms; legality re-asked per the bot's
  engine; reserved names refused in `resolve` AND subtracted from removals
  (both halves of the guarantee, each in its own test); removals are empty
  writes (absent ≡ empty is the domain's own contract); convergence is zero
  further writes; one failed fetch aborts the category, nothing written; a
  structural test holds the module off restart/device plumbing.

## [x] Task 5: `SkillsMaterialiser`
- **Done when:** declaration conflicts with the area are asked *before* the
  fetch; no-subpath zips travel the exact manual-upload road (the same
  validator, `upload_local_skill`, direct activation); tar.gz/subpath go
  through the guarded unpacker and re-pack canonically; the package's
  front-matter name must equal the entry's; oversized packages fail in
  `resolve`; the area is the active set with Set-governed members neither
  declarable nor removable; `skills: []` removes the directly-active
  skills, sorted; re-apply converges with zero uploads, zero activations,
  zero fetches; a structural import test keeps the module off the storage
  and repository internals of the upload flow.

## [x] Task 6: registration + engine integration
- **Done when:** `build_materialisers` returns four; the W4 safety nets
  (bar dominance, admission width) re-run over the new categories unchanged;
  a four-category document applies in `APPLY_ORDER` order (identity before
  skills), one category's fetch outage aborts only that category, and the
  report summarises `PARTIAL`. Deliberate adjustment recorded: the
  no-materialiser demo names `resources` (skills were its W4-era subject;
  W5 materialised them — the move the wave always implied).

---

## Group D — Wiring and docs

## [x] Task 7: composition
- **Done when:** `ManifestFetchModule` owns the config cluster (through
  config_module's one public seam — the sofa read stays in its sanctioned
  file), guarded fetcher, content store, the one `EntryFetcher`, and the
  five lazy factories (identity keyed by the narrow port, so the device
  graph is never imported eagerly); the apply service's five new providers
  are distinct typed keys; a full-graph smoke resolves every key to its real
  singleton; the architecture gates run green (module boundaries + the
  1000-line cap — the cap is what placed the wiring in its own module).

## [x] Task 8: README, flow coverage, this spec set
- **Done when:** the module README's narrative, `provides`, `consumes` and
  `internal_dependencies` cover the W5 surface (three new declared imports);
  the flow-coverage exemption names the W5 machine parts; the spec/plan/task
  docs exist in this directory.
