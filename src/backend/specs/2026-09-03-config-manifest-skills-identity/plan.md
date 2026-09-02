# Plan: the Two Fetching Categories

## Approach

Backend-only, one module family (`core/bot_config_manifest/apply/`) plus its
wiring. No new tables, no new routes, no new categories in the vocabulary:
the engine, the ordering table and the area rules are W4's, untouched. Five
moves:

1. **The entry-fetch pipeline as one funnel** (`apply/entry_fetch.py`).
   Every fetch-consuming category fetches through it, because §2.8's audit
   and `keep_last` both read *the same* log: a category that bypassed the
   funnel would acquire unrecorded bytes. The pipeline is where the
   pinned/unpinned/keep_last policy lives, once.

2. **The two materialisers honour the three-stage contract they inherit.**
   Fetch and package validation land in `resolve` — the registry's contract
   says that is where a category's failures are collected before anything is
   written, so a fetch or package failure aborts the whole category by
   construction (the §3.2 all-or-nothing guarantee, satisfied by structure
   rather than by discipline). `plan` classifies against the area and
   computes removals; `write` only executes.

3. **Parity by reuse.** Identity addresses through the router's own
   coordinates and writes through `update_bot_file`; removals are empty
   writes because the domain's own contract reads absent and empty as one
   state. Skills validate with the upload path's own package gate and
   install through `upload_local_skill` + direct activation; convergence is
   observed through W11 receipts (an active name plus a store-served pin
   writes nothing).

4. **Narrowing before writing, mcp-precedent shape.** A skill one of the
   bot's SkillSets supplies is neither declarable nor removable — the write
   refuses it (R1), so `resolve` refuses the declaration and `plan` refuses
   the removal, exactly where `McpMaterialiser` narrows by
   `platform_default_mcp_codes`. The known corner (an excluded default-set
   member the flush's `member_skill_ids` does not name) is recorded in the
   module docstring as the same class W4-mcp carries for user Sets: an
   honest `partially_written` abort, never a silent one.

5. **Wiring that keeps every import lazy where the graph requires it.**
   `ManifestFetchModule` owns the fetch-side machine parts (typed config
   cluster through config_module's one public seam, the guarded fetcher, the
   content store, the one `EntryFetcher`, five lazy factories). The identity
   service is keyed by a narrow port (`apply/identity_port.py`) — the real
   service has no Protocol (one implementation; the router's waiver), and
   the port exists so a lazy provider can be keyed without importing the
   device dispatcher graph.

## Task groups

- **A. Seams first** — W11 `latest_receipt` (repository over real SQLite +
  service), the `content`-on-skills PUT refusal (validator + schema doc,
  same-PR doc rule).
- **B. The pipeline** — `apply/entry_fetch.py` with the pinned/unpinned/
  keep_last policy, tested over fakes that model W2's digest verification
  (the doctrine: a fake that forgets a rule the real service enforces lets
  a materialiser pass here and fail in production).
- **C. The materialisers** — identity, then skills, then the registry
  registration and the engine-level integration case (four categories, one
  document, one category's outage aborting only itself).
- **D. Wiring and docs** — `ManifestFetchModule`, the container
  registration, the README (narrative + Context Boundary), the
  flow-coverage exemption wording, this spec set.

## Validation

Per-task `pytest` runs from `src/backend` (cwd mattering is a recorded trap),
the architecture gates (module boundaries picked up three new declared
imports; the 1000-line cap is what drove the wiring split — config_module
lands at exactly 1000), a full-DI-graph smoke resolving every new key to its
real singleton, and the two W4 safety nets (apply-bar dominance and
admission-mode width) re-running automatically over the two newly
registered categories.
