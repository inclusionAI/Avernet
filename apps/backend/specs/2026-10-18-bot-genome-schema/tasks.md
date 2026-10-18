# Tasks: Bot Genome schema and patch format (RSI-02)

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

## Task 1: Contract README and gene path grammar
- **Goal:** Write the normative prose the schemas rely on.
- **Files:** `apps/backend/api-contracts/genome/v1/README.md`
- **Done when:**
  - [x] Versioning rule (`genome_schema`, `patch_schema`, what bumps them).
  - [x] Gene path grammar, segment encoding, and segment-boundary prefix rule, with the path table.
  - [x] Canonicalisation: RFC 8785 subset (no floats, safe integers), list sort keys and uniqueness, revision id and every digest formula, `acm-tree-v1` rules and what it does not preserve.
  - [x] Recording rules: fetch details resolved away, inline content stored by digest, placeholders resolved once with the recording bot's values, first-revision capture from current state, the AGENTS.md reference block removed on capture with the full reason, `cli_tools` ignored, `engine_config` refused (#2858).
  - [x] Known limitations with issues: #2859, #2860; directory re-store cost (G-1).
- **Depends on:** —

## Task 2: Canonical JSON reference and vectors
- **Goal:** A test-only RFC 8785 canonicaliser and the vectors file it is checked against.
- **Files:** `apps/backend/tests/community/contracts/genome_canonical_json.py`, `apps/backend/api-contracts/genome/v1/canonical-json-vectors.json`
- **Done when:**
  - [x] `canonicalize` refuses floats and integers outside ±(2^53−1); sorts keys by UTF-16 code units.
  - [x] Vectors include the RFC 8785 §3.2.3 key-ordering example and string-escaping cases, with expected canonical text and SHA-256.
  - [x] `revision_id` and `tree_digest` helpers exist (the latter reuses `canonical_tree_bytes`).
- **Depends on:** Task 1

## Task 3: Revision schema
- **Goal:** `genome-revision.schema.json` for `spec`, `policy`, and `revision` metadata.
- **Files:** `apps/backend/api-contracts/genome/v1/genome-revision.schema.json`
- **Done when:**
  - [x] Spec entries follow key + value; `tools` has `mcp` only; `engine_config` is `{}`; `memory` is `null` or `{mode, items_digest}`; skills `origin` is `local`/`center`.
  - [x] Persona types are the identity whitelist minus `MEMORY.md`/`IDENTITY.md`.
  - [x] MCP `config` mirrors the Manifest's closed set and limits.
  - [x] Policy is `mutable_genes`, `pins`, `risk_overrides`; gene path pattern enforced.
  - [x] Revision metadata matches 01-genome.md §2.4 (provenance kinds `user`, `strategy_run`).
- **Depends on:** Task 1

## Task 4: Patch schema and risk tiers
- **Goal:** `genome-patch.schema.json` with every op, and `risk-tiers.json`.
- **Files:** `apps/backend/api-contracts/genome/v1/genome-patch.schema.json`, `apps/backend/api-contracts/genome/v1/risk-tiers.json`
- **Done when:**
  - [x] All 12 ops from plan.md, discriminated by `op`; `file.edit` has kinds `replace_section`, `insert_after`, `diff`.
  - [x] Targets use the gene path grammar; no op can target `tools`, `script`, `engine_config`, `policy`.
  - [x] `risk-tiers.json` has a rule for every op, the two raises, and the rewrite threshold as an integer ratio with the exact LCS definition in the README.
- **Depends on:** Task 1

## Task 5: Example records and blobs
- **Goal:** Real, verifiable examples for every schema.
- **Files:** `apps/backend/api-contracts/genome/v1/examples/{blobs,revisions,patches,invalid}/`
- **Done when:**
  - [x] Revisions cover a personal bot, a Center skill, a directory resource, MCP with and without config, a script, and the default policy.
  - [x] One valid patch per op plus one mixed patch.
  - [x] Every invalid case named in plan.md's Test Strategy, each with its expected failure.
  - [x] Every digest referenced exists under `examples/blobs/` and every revision id is real.
- **Depends on:** Tasks 2, 3, 4

## Task 6: Manifest v1 compatibility table and round-trip cases
- **Goal:** Account for every Manifest v1 field and work every example document through M → revision → M′.
- **Files:** `apps/backend/api-contracts/genome/v1/manifest-v1-compatibility.md`, `apps/backend/api-contracts/genome/v1/examples/manifest-v1/<case>/`
- **Done when:**
  - [x] Every top-level, source, and per-category field of Manifest v1 has one outcome: maps, resolved at record, provenance only, refused, ignored.
  - [x] Round-trip definition and the proposed content-store / Center-skill source syntax (marked "fixed by RSI-04").
  - [x] One case for the complete document (§1.2) and for each fragment in `docs/bot-config-manifest/examples.zh-CN.md` §2–§9, each with `manifest.yaml`, `revision.json`, `compiled.yaml`, `notes.md`.
- **Depends on:** Task 5

## Task 7: Contract test
- **Goal:** One suite that keeps the contract files consistent with each other and with the docs.
- **Files:** `apps/backend/tests/community/contracts/test_bot_genome_schema.py`
- **Done when:**
  - [x] All tests listed in plan.md pass; negative cases fail for their stated reason.
  - [ ] The design-doc tier-table check reads the marked tables (passes after Task 8).
  - [x] File stays under 1,000 lines.
- **Depends on:** Tasks 2–6

## Task 8: Update 01-genome.md and 08-promotion.md
- **Goal:** Make the two docs that define the format match the contract.
- **Files:** `apps/backend/docs/bot-evolution/01-genome.md`, `01-genome.zh-CN.md`, `08-promotion.md`, `08-promotion.zh-CN.md`
- **Done when:**
  - [ ] Every 01-genome.md edit listed in plan.md, including §7's note on `LESSONS.md`.
  - [ ] 08-promotion.md `PatchOpName`, tier table, and policy wording updated.
  - [ ] Tier tables wrapped in markers; English and zh-CN say the same.
- **Depends on:** Task 7

## Task 9: Update the other bot-evolution docs and work items
- **Goal:** Remove stale references elsewhere and record the RSI-04 additions.
- **Files:** `03-strategy.md`, `04-default-strategies.md`, `06-evolution-run.md`, `work-items.md`, and each `zh-CN` version
- **Done when:**
  - [ ] No `locked_genes`, dotted gene paths, or `tools.cli_tools` remain in bot-evolution docs.
  - [ ] RSI-02 marked done; RSI-04 gains the Center-skill source item and the #2162 dependency.
  - [ ] Spec dir links to the contract files.
- **Depends on:** Task 8

## Task 10: Verification
- **Goal:** Ensure the work meets every spec acceptance criterion.
- **Done when:**
  - [ ] Contract test passes; repo pre-push lint gate passes.
  - [ ] Every acceptance criterion in spec.md checks off.
- **Depends on:** Task 9

---

## Groups

- **Group A — Normative core:** Tasks 1, 2, 3, 4
  - Theme: the grammar, canonicalisation, and both schemas exist and agree.
- **Group B — Examples and mapping:** Tasks 5, 6, 7
  - Theme: verifiable examples, the Manifest v1 mapping, and the test that holds them together.
- **Group C — Design docs:** Tasks 8, 9
  - Theme: the design docs say what the contract says.
- **Group D — Verification:** Task 10
  - Theme: final spec acceptance check.
