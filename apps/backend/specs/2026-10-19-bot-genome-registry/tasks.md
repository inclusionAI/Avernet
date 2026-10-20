# Tasks: Genome Registry in Backend (RSI-03)

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

## Task 1: Package skeleton, contract loader, canonical JSON and rewrite measure
- **Goal:** Create `core/bot_genome/` with the pure building blocks the rest depends on, moving the two test-only reference modules into core.
- **Files:** `core/bot_genome/{__init__,contract,canonical,rewrite,gene_paths,models,errors,limits}.py`, `core/bot_genome/contract/*.json`, `core/bot_genome/README.md`; `tests/community/contracts/{genome_canonical_json,genome_rewrite}.py` (removed), `tests/community/contracts/test_bot_genome_schema.py` (imports); `tests/community/core/bot_genome/{test_canonical,test_rewrite,test_gene_paths,test_contract_copies}.py`
- **Done when:**
  - [ ] `canonical.py` and `rewrite.py` pass every vector in `canonical-json-vectors.json` and `rewrite-vectors.json`; the contract test imports them from core and still passes.
  - [ ] `gene_paths.py` covers encode/decode, canonical-spelling refusal, `covers`, `overlaps`, and builders for every op's gene path (README §2, §6.1).
  - [ ] `contract.py` loads the schema pair and `risk-tiers.json` from the package copies; a test pins the copies byte-equal to `api-contracts/genome/v1/`.
  - [ ] `README.md` carries the `## Context Boundary` block; `test_module_boundaries` passes with `core.bot_genome` added to the significant set.
- **Depends on:** —

## Task 2: Text edits, patch ops, policy and risk
- **Goal:** Apply a patch to a base spec exactly as the contract defines, and assess it.
- **Files:** `core/bot_genome/text_edits.py`, `core/bot_genome/patch/{__init__,ops,policy,risk}.py`; `tests/community/core/bot_genome/{test_text_edits,test_patch_ops,test_policy_and_risk}.py`
- **Done when:**
  - [ ] `replace_section`, `insert_after` and the strict unified-diff kind behave per README §6.2 (fenced headings skipped, exactly-one anchor, no fuzz, `\ No newline at end of file`).
  - [ ] Every `examples/patches/*.json` applies to its base; `00-mixed-r1-to-r2.json` reproduces `02-support-bot-candidate.json`'s spec; `delete-skill-md`, `unknown-op`, `op-on-mcp`, memory ops on `memory: null` are refused with their codes.
  - [ ] Policy checks yield `gene_not_mutable`, `pinned_item`, `gene_not_allowed`; `missing_rationale` and missing evidence per op kind.
  - [ ] Risk: every rule in `risk-tiers.json`, `description_only`, the rewrite raise measured base→result once per file, replacement (remove+add) flagged, overrides by overlap raise only.
- **Depends on:** Task 1

## Task 3: Spec checks and diff
- **Goal:** Refuse at record time what apply would refuse, and diff two specs.
- **Files:** `core/bot_genome/spec_checks.py`, `core/bot_genome/diff.py`; `tests/community/core/bot_genome/{test_spec_checks,test_diff}.py`
- **Done when:**
  - [ ] Every `examples/invalid` case that names `utf8`, `skill-package`, `admission`, `resource-paths`, `tree`, `order` or `canonical-path` is refused by the matching predicate; MCP URL/header rules, `check_mcp_permission_detail` and `validate_bot_override` are exercised with fakes; engine-legal persona types checked.
  - [ ] `diff_specs` reports added/removed/modified per gene, unified diffs for UTF-8 text (persona, file resources, tree members, memory items), `None` otherwise, and the diff's tier and rewrite flags.
- **Depends on:** Task 2

## Task 4: Content store produced-content write path and reachability
- **Goal:** Let the registry file produced bytes with genome provenance and answer whether a digest belongs to a bot.
- **Files:** `core/bot_config_manifest/content/{service_protocol,service}.py`, `core/repository/protocols/bot/manifest_content.py`, `core/repository/implementations/bot/manifest_content.py`; `tests/community/core/bot_config_manifest/content/test_content_store.py`, `tests/community/repository/bot/test_manifest_content_repository.py` (or the existing file for that repository)
- **Done when:**
  - [ ] `store_produced` writes the blob once, files a row with `produced://<origin>` URLs and the category/entry linkage, and is idempotent by digest.
  - [ ] `is_reachable` / `has_digest` answer true only for the bot that holds a row for the digest.
- **Depends on:** —

## Task 5: Tables, ORM, repositories
- **Goal:** Persistence for revisions, refs (CAS), ref events, patches and promotions.
- **Files:** `core/bot_genome/sql/2026_10_19_bot_genome.sql`, `core/bot_genome/repository/{__init__,models}.py`, `core/repository/protocols/bot/genome.py` (+ `__init__` exports), `core/repository/implementations/bot/genome.py`, `core/schema.py`; `tests/community/repository/bot/test_genome_repository.py`
- **Done when:**
  - [ ] The five tables are created by `create_all`; the DDL matches the ORM.
  - [ ] Inserting an existing `revision_id` returns the existing row without a new `seq`; a `seq` collision retries; `list` filters by status/parent newest-seq-first with totals.
  - [ ] `move` is CAS on the expected revision (rowcount semantics), `move_pair` moves `active`+`previous` in one transaction, each move appends an event; `candidate_count(run)` supports numbering.
  - [ ] Promotion rows round-trip; `by_apply_reference` finds the promotion for an apply id.
  - [ ] `test_repository_contracts` passes (abstract members, declared protocols).
- **Depends on:** Task 1

## Task 6: Manifest recorder, capture and compile
- **Goal:** Record a revision from a Manifest document with pinned sources and first-revision capture; compile a revision back.
- **Files:** `core/bot_genome/recorder/{__init__,manifest_recorder,capture,bot_state}.py`, `core/bot_genome/compile.py`; `tests/community/core/bot_genome/{test_manifest_recorder,test_capture,test_compile}.py`
- **Done when:**
  - [ ] Every `examples/manifest-v1/*` case with a `revision.json` records to exactly its spec through the real `DeclaredSourceResolver` over `FakeObjectStore` and a scripted git client; `01-complete` and `04-engine-config` are refused by the Manifest validator; `09-cli-tools` ignores `cli_tools`.
  - [ ] Omitted categories come from the parent, or from `capture_spec` for a first revision; the AGENTS.md reference block is removed and `rstrip()+"\n"` applied; a repo (`git://`) skill, a non-Manifest-path resource and a platform-managed teclaw bot raise `CaptureRefused` naming the item.
  - [ ] `PlatformBotStateReader` reads persona, skills (local files via package storage, Center versions), resources, MCP servers with bot overrides, and the script through the named services, with a fake-driven unit test per category.
  - [ ] `compile_spec` reproduces every `compiled.yaml`; `document_digest` is the digest of the canonical JSON of the document and is unchanged by YAML formatting.
- **Depends on:** Tasks 3, 4

## Task 7: Registry service
- **Goal:** The in-process `GenomeRegistryProtocol` implementation tying recorder, patch, checks, content and repositories together.
- **Files:** `core/bot_genome/genome_registry_protocol.py`, `core/bot_genome/registry.py`, `api/bot_genome_registry_service.py`; `tests/community/core/bot_genome/test_registry_service.py`; `tests/community/architecture/test_service_api_conformance.py` (pair registered)
- **Done when:**
  - [ ] `record_patch` runs the README §6.4 order, stores produced blobs, the patch and the revision atomically, is idempotent (same revision, same seq), and names `candidate/<run>/<n>` for run candidates (idempotent on retry).
  - [ ] `record_manifest` records a draft with the YAML kept as provenance; `get_revision` resolves id, `r<seq>` and ref names; `list_revisions` pages; `set_status` updates metadata only.
  - [ ] `move_ref` enforces the mover table (`MoverNotAllowed`), CAS (`RefConflict` with the current revision), and logs events; `read_content` is reachability-checked and hash-verified; `put_content` returns the digest and whether it existed, refusing over the limit.
  - [ ] `compile` returns the compiled document; `applied_revision` joins `last_apply` to the promotion that started it and is `None` otherwise.
- **Depends on:** Tasks 5, 6

## Task 8: Promotion service and applier seam
- **Goal:** Move `active`/`previous`, record the promotion, hand the compiled document to Manifest apply.
- **Files:** `core/bot_genome/promotion/{__init__,promotion_service_protocol,applier,service}.py`, `api/bot_genome_promotion_service.py`; `tests/community/core/bot_genome/test_promotion_service.py`; conformance pair registered
- **Done when:**
  - [ ] `promote` follows the planned order; `NotAppliable`/`PromotionUnsupported` from `preflight` leaves refs and statuses untouched; `Drifted` refuses non-owner actors only; `going_back` is set when the target was promoted before; a failed `start` records `apply_failed` with refs moved.
  - [ ] `ManifestRevisionApplier` validates through `BotConfigManifestService.validate`, stores through `put` and starts `start_apply(trigger="genome_promotion")`; the service-bot path raises `PromotionUnsupported`.
  - [ ] `get_promotion` / `list_promotions` read back rows.
- **Depends on:** Task 7

## Task 9: DI wiring
- **Goal:** One module binds repositories, services and the applier; the composition root installs it.
- **Files:** `di/modules/bot_genome_module.py`, `di/container.py`; `tests/community/di/test_bot_genome_module_wiring.py`
- **Done when:**
  - [ ] `build_injector(profile=TEST)` resolves `GenomeRegistryProtocol` and `GenomePromotionServiceProtocol`; the state reader is a thunk so boot's lifecycle walk builds no device graph.
  - [ ] Architecture gates pass: `test_build_injector_composition_root_only`, `test_no_user_config_outside_di`, `test_lifecycle_discovery`.
- **Depends on:** Tasks 7, 8

## Task 10: HTTP surface
- **Goal:** The `/openapi/v1/bots/{bot_id}/genome` router with envelopes, bars, admission and error mapping.
- **Files:** `adapters/http/openapi_v1/bots/{genome,schemas_genome}.py`, `adapters/http/openapi_v1/{errors_genome,authorization_rules_genome,admission_genome_routes}.py`, `adapters/http/openapi_v1/{__init__,responses,authorization_rules,admission_routes}.py` (merges); `tests/community/adapters/http/openapi_v1/test_genome_routes.py`, `tests/community/adapters/http/openapi_v1/test_config_manifest_apply_bars.py`
- **Done when:**
  - [ ] All eleven operations of the plan's table are mounted with the stated levels and `GRANT_CHECKED_ADDRESSED_BOT`; `test_authorization_inventory` and the admission tests pass; the promotion row satisfies the apply-bars dominance test.
  - [ ] `{rev:path}` routing serves `candidate/<run>/<n>` for get and diff; content GET returns `application/octet-stream`; PUT content enforces the size cap before reading the whole body.
  - [ ] Every `GenomeError` maps to its documented status with `data` carrying reasons or the current ref; 201 vs 200 on an existing revision/upload.
- **Depends on:** Task 9

## Task 11: Docs and work items
- **Goal:** Make the design docs say what shipped.
- **Files:** `docs/bot-evolution/01-genome.md` (+ `zh-CN`), `docs/bot-evolution/work-items.md` (+ `zh-CN`), `api-contracts/genome/v1/README.md` and `manifest-v1-compatibility.md` (the `document_digest` rule), `core/bot_genome/README.md`
- **Done when:**
  - [ ] D-6 recorded as closed (DB + content store) with the table names; §11 matches the shipped Protocol (async record methods, `RunScope`, `RefMover`, `record_manifest` without `base`); §12's document request drops `base` (parent is `active`); §5.5/§12 note the apply-id join until RSI-04.
  - [ ] RSI-03 status set with the deferred legs listed under RSI-04 (promotion becomes appliable; service-bot publish; teclaw platform-managed capture; repo skills).
  - [ ] English and zh-CN say the same.
- **Depends on:** Task 10

## Task 12: Verification
- **Goal:** Ensure the work meets every spec acceptance criterion.
- **Done when:**
  - [ ] `DEPLOY_PROFILE=test uv run pytest tests/community -q` passes; architecture tests pass; every touched source file is under 1,000 lines.
  - [ ] The singlebox story (record from a document → patch → diff → move `draft` → promote) is run against a local backend and the promotion is refused as `not_appliable` with the Manifest's reasons, refs untouched; the result is recorded in the PR's Validation section.
  - [ ] Every acceptance criterion in `spec.md` checks off, with the RSI-04-dependent leg stated.
- **Depends on:** Task 11

---

## Groups

- **Group A — Pure core:** Tasks 1, 2, 3
  - Theme: canonicalisation, gene paths, patch application, risk, checks and diff work on the contract's examples with no database.
- **Group B — Storage:** Tasks 4, 5
  - Theme: produced content in the manifest store and the five genome tables with CAS.
- **Group C — Recording and services:** Tasks 6, 7, 8
  - Theme: a revision can be recorded from a document or a patch, compiled, promoted and traced back from an apply.
- **Group D — Wiring and surface:** Tasks 9, 10
  - Theme: the registry is reachable through DI and the public API with the right bars.
- **Group E — Docs and verification:** Tasks 11, 12
  - Theme: design docs match the code; final spec acceptance check.
