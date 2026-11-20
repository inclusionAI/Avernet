# Configured catalog labels validation record

Date: 2026-11-19. Status: **LOCAL_TESTED; NOT_PUBLISHED; NOT_DEPLOYED**.

## Planning-time checks (historical)

- Refreshed the authoritative development ref and created the dedicated `codex/configured-catalog-labels` worktree; main checkout WIP and submodule state were not changed by spec editing.
- Inspected owning source/contracts and recorded exact source baselines in proposal/design. OCB's new worktree leaves the old pinned submodule uninitialized; no integration/build is claimed.
- `openspec validate add-configured-catalog-labels --strict`: **PASS** for this repository's local change.
- `openspec status --change add-configured-catalog-labels`: **4/4 artifact groups complete** (proposal/design/specs/tasks). This means artifact readiness, not task execution.
- Requirements inventory: **10 requirements, 28 scenarios** across capability specs; **32 unchecked implementation/release tasks**.
- App-owned artifact copy compared byte-for-byte with the local OpenSpec artifacts; local Markdown links, scenario shape, whitespace and dictionary example structure checked separately before delivery.

## At the planning checkpoint (superseded by local evidence below)

- No Rust/Java implementation, config-file rollout, schema DDL or gitlink update.
- No Cargo/Maven regression, real MySQL/OceanBase upgrade, old migration-runner test, HTTP/Triple wire smoke, CI or coverage run.
- No commit, push, PR, merge or deployment.
- No bcngw implementation or Group Triple/Java extension.
- No Fuse server incremental/full-replacement behavior change or downstream stale-label-removal acceptance.

At implementation, append actual commands, denominators, SHAs and gaps here; never convert these documentation checks into runtime proof.


## Implementation baseline and compatibility inventory (2026-11-19)

- Worktree `/Users/shenlifang/work/codes/datamarket/teamclaw/Avernet/.worktree/configured-catalog-labels`, branch `codex/configured-catalog-labels`, HEAD/fetched GitHub origin/dev/merge-base `8fc2e5f7693fc5caca97e5b5591148885d38ff10`; ahead/behind 0/0 at start. Existing spec/index changes retained. No submodules or active Cargo/Rust builds at start; 109 GiB available. Rustup stable selected, incremental disabled, build jobs 2.
- Canonical Agent HTTP is implemented twice (V1 API adapter and legacy HTTP adapter). Named Agent and Provider-admin legacy routes have independent DTOs. Scoped-token registration uses bcs-app-register and its own atomic admission Core; all must carry labels rather than treating the canonical endpoint as the only writer.
- Group OpenAPI DTO -> V1 Group facade -> legacy GroupManagement -> GroupCore -> GroupRepo; PATCH -> eventful mutation/CAS. Legacy/browser create aliases and legacy PATCH have independent request DTOs. Chat/DM nonempty labels rejected; idempotent reuse must not reinitialize labels.
- New public struct fields break external Rust literals. Service API version advances to 7.0.0. Old Agent update methods remain and retain omission-clears semantics; new `UpdateAgentCommand`/`update_agent` and `register_provider_bot_with_labels` defaults fail explicitly. `GroupCoreService`/`GroupRepoPort::upsert_with_catalog_labels` defaults refuse explicit replacements when unsupported. Legacy upsert preserves labels.
- New schema: MySQL/OceanBase `048_configured_catalog_labels.sql`, SQLite `046_configured_catalog_labels.sql`, nullable JSON/TEXT on both owning tables. No historical migration body/name/checksum changed.
- Only local implementation/testing is authorized. Tasks 6.4 and 7.x remain release/deployment gates; implementation SHA is not yet assigned and no public availability or downstream integration is claimed.


## Public API and OCB source handoff

This is an **uncommitted local implementation**, not a published dependency. HEAD remains the baseline `8fc2e5f7693fc5caca97e5b5591148885d38ff10`; **do not pin that SHA as the feature implementation**. Implementation SHA is NOT_ASSIGNED. A separately authorized commit/publication must precede final OCB gitlink integration, with authoritative GitHub and internal mirror verification.

### Shared types and assembly

- `bcs_domain::catalog_labels::CatalogLabelsInput` is a transport-neutral `BTreeMap<String, Option<Vec<String>>>` write-intent newtype, also re-exported from `bcs_service_api::types::catalog_labels`. `present_input` distinguishes absence from forbidden outer null. It is not the normalized stored type.
- `bcs_service_api::types::catalog_labels::CatalogLabels` is the normalized named value; its map is private, `as_map() -> &BTreeMap<String, Vec<String>>`, `is_empty() -> bool`, serde encodes an object, and `from_storage(Option<&str>) -> ServiceResult<CatalogLabels>` maps SQL NULL to empty and rejects malformed non-null data. It does not read files or filter retained data through a newer dictionary.
- `CatalogLabelRule { allowed_values: Vec<String>, allow_multiple: bool }`; `CatalogLabelPolicy::new(BTreeMap<String, CatalogLabelRule>) -> ServiceResult<Self>`; `normalize(CatalogLabelsInput) -> ServiceResult<CatalogLabels>`. Policy rules are immutable after construction. No business dictionary literal lives in the validator.
- `BcsConfig.catalog_label_policy: Arc<CatalogLabelPolicy>` is skipped by serde. File-loading paths load the standalone selected-directory policy once, then Agent management, scoped Register service, legacy Group management and V1 Group facade receive the same configured policy. Embedded hosts must inject/load this policy deliberately; legacy programmatic constructors default to an empty, fail-closed dictionary, never a permissive fallback.
- Builders: `A2aBotManagement::with_catalog_label_policy`, `RegisterServiceImpl::with_catalog_label_policy`, `GroupManagement::with_catalog_label_policy`, `GroupServiceImpl::with_catalog_label_policy`, each accepting `Arc<CatalogLabelPolicy>`.

### Agent contracts

Service API version is **7.0.0** (Cargo.lock contains only this intended package-version delta). Required new public Rust fields mean downstream literals/constructors rebuild together:

- `RegisterAgentCommand.catalog_labels`, `RegisterNamedA2aBotCommand.catalog_labels`, `ConfigureA2aBotCommand.catalog_labels`: `CatalogLabelsInput` (legacy omitted requests are translated to empty intent).
- V1 `RegisterA2aAgent`, `RegisterNamedA2aAgent`, `UpdateA2aAgent`, scoped `RegisterBot`: `catalog_labels: Option<CatalogLabelsInput>`; adapters reject explicit null.
- `A2aBotRecord.catalog_labels`, `A2aRegistrationCard.catalog_labels`, `ManagedA2aCard.catalog_labels`, `NamedA2aCard.catalog_labels`: normalized `CatalogLabels`. Record serde accepts older snapshots lacking the field as empty; views emit `{}` explicitly.
- `UpdateAgentCommand { provider_admin_token: String, bot_id: String, source: Option<A2aCardSource>, visibility: Option<A2aRegistrationVisibility>, catalog_labels: Option<CatalogLabelsInput> }`.
- `A2aBotManagementService::update_agent(UpdateAgentCommand) -> ServiceResult<ManagedA2aCard>` is the new complete update entry. `None` labels clears on an otherwise-valid update; `Some(empty)` is a valid label-only clear. No source/visibility/explicit-label object still yields UPDATE_REQUIRED. Legacy `update_agent_by_id` and `update_agent_by_id_with_visibility` remain, with omission-clearing semantics. Unsupported defaults only forward genuinely omitted legacy intent; explicit new metadata is not silently dropped.
- `register_provider_bot_with_labels(RegisterProviderBotCommand, A2aCardSource, i64, CatalogLabelsInput) -> ServiceResult<RegisterProviderBotOutcome>` carries labels through atomic Provider admission; old entry remains for omitted/empty intent.
- `configure_catalog_labels(&str provider_id, &str token, &str bot_id, CatalogLabelsInput, Option<u64> expected_revision) -> ServiceResult<A2aBotRecord>` supports the existing Provider-ID legacy label-only configure route without requiring a public Provider slug or fetching a URL. It preserves source/Card with identity/revision CAS, retries only local races when no expected revision was supplied, and rejects unsupported implementations explicitly.
- Existing `A2aBotRepoPort`/`A2aBotCoreService` write signatures stay record-based. Record labels participate in the same Bot/Card transaction and management revision fence. Successful commit invalidates registry metadata and emits catalog identity notification; refresh snapshots are fenced against newer label-only writes.

### Group contracts

- `CreateCollaborationGroup`, `CreateDirectMessageGroup`, `GroupPatch`: `catalog_labels: Option<CatalogLabelsInput>` at the V1 application boundary. Group PATCH None preserves; explicit object replaces. Unsupported Chat/DM normalized-nonempty intent is rejected.
- `GroupCreateCommand.catalog_labels: Option<CatalogLabels>` and `GroupMutableFieldsPatch.catalog_labels: Option<CatalogLabels>` carry normalized preserve/replace intent internally.
- `GroupManagementService::normalize_catalog_labels(CatalogLabelsInput) -> ServiceResult<CatalogLabels>` validates legacy create input before definition/provisioning writes. The runtime-cleanup wrapper forwards this policy method.
- `GroupCoreService::upsert_with_catalog_labels(Group, Option<CatalogLabels>) -> ServiceResult<()>` and the same `GroupRepoPort` method atomically write Group/template/explicit labels. Legacy `upsert` preserves. Defaults refuse unsupported explicit label writes.
- `GroupCardSnapshot`, legacy `GroupDetailResult`, V1 `CollaborationGroupDetail` and `DirectMessageGroupDetail` expose normalized labels. List summaries do not gain per-result label queries. SQL snapshot remains one joined Group/Card read; memory snapshots hold the same group/template/label lock order as writes.
- Explicit labels use the existing Group transaction/event/CAS path. Template maintenance updates display fields only and preserves labels; a newly materialized eligible template initializes `{}` and explicit writes create missing legacy templates transactionally.
- No Group Triple/Java API, Group Card upload route, new label-filter API or auth/envelope change was added.

### Migration and config handoff

- New MySQL/OceanBase migration: `apps/bcs/migrations/mysql/048_configured_catalog_labels.sql`, SHA-256 `8276b566b52db7500db269cebfeeda58b1671a88c6572d1b7483cd9454b00006`.
- New SQLite migration: `apps/bcs/migrations/sqlite/046_configured_catalog_labels.sql`, SHA-256 `f64dd3c29808e9bcdd31f4082979bf56a3b0747cf211fc92376bc97346e7a3af`.
- Both add nullable `catalog_labels` to `bcs_bots` and `bcs_group_agent_cards`; JSON on MySQL/OceanBase, TEXT on SQLite. Historical numbered SQL and earlier SQLite migration bodies/names/checksums are unchanged. Existing NULL/old inserts remain readable; new explicit writes and new eligible template inserts encode normalized empty as `{}`.
- Public policy: `apps/bcs/configs/catalog-labels.toml`, SHA-256 `9fa6a75b3fe45281c413cc80e7ebed024dc0bdb329a214d5af921bc6122c4b66`. Initial lifecycle values are draft/trial/running/offline, `allow_multiple=false`; dictionary must be identical initially across profiles/products but files remain independent regular files. Singlebox and generated test configs stage the file explicitly. No symlink/include coupling is introduced.

```toml
[labels.lifecycle]
allowed_values = ["draft", "trial", "running", "offline"]
allow_multiple = false
```

### Publication and release boundary

Fuse V2 payloads contain full custom + resource-specific system labels, outside `card`. Clear emits system-only labels, never tombstones. Identity-queued retries re-read committed current state. Fuse server full-replacement/stale-key removal is **NOT_VERIFIED** and externally owned.

Expand schema -> provision policy -> roll all server replicas -> verify persisted nonempty readback -> enable clients. Old Agent writers can clear labels by omission; new REST writers and strict old response readers can fail against the opposite version. Disable new writers first for rollback, retain columns/data/history, and control old writer/publication degradation. No remote DDL, runtime rollout, mirror publication, gitlink update or destructive downgrade was performed.


## Final local validation — 2026-11-19

All commands below ran inside this dedicated worktree's `apps/bcs`, unless a repository-root command is explicitly indicated. Rustup `rustc 1.98.0` / `cargo 1.98.0` were verified. Common environment:

```sh
export PATH="$HOME/.rustup/toolchains/stable-aarch64-apple-darwin/bin:$PATH"
export CARGO_INCREMENTAL=0 CARGO_BUILD_JOBS=2 CARGO_PROFILE_DEV_DEBUG=0
```

### Executed commands and denominators

```sh
cargo test -p bcs-service-api -p bcs-db-migrations -p bcs-app-a2a-bot   -p bcs-group-store -p bcs-bot-store -p bcs-app-group -p bcs-app-register   -p bcs-fusion -p bcs-bot -p bcs-group -p bcs-api-http -p bcs-http   --no-fail-fast -- --test-threads=2

cargo test -p bcs --lib --test integration_catalog_labels   --test integration_fuse_cards --test api_auth_config   --no-fail-fast -- --test-threads=1

cargo test -p bcs --test integration_catalog_labels -- --test-threads=1
cargo check --workspace --all-targets --keep-going
bash scripts/ci/check-config-validation.sh
bash ../../singlebox/verity/test_singlebox_bcs_runtime_config.sh
```

| Verification | Final result | Evidence boundary |
| --- | --- | --- |
| 12 affected package suites, including doc tests | **2729 passed, 0 failed, 11 ignored** across 204 result groups; exit 0 | Ignored tests require external/native database targets; not MySQL proof |
| BCS bootstrap library | **364 passed, 0 failed, 1 ignored** | Excludes a nested filtered child-test report from the total, so it is not counted twice; ignored inventory/llvm-cov case remains ignored |
| `api_auth_config` | **25 passed** | Full-file parse/value-domain/overlay compatibility |
| `integration_fuse_cards` | **2 passed** | Existing eligibility, delete behavior, bounded latest-state retries and no startup scan |
| `integration_catalog_labels` | **1 passed** (latest focused rerun also exit 0) | Real local HTTP Card source + canonical Agent API + Group REST + real application/Core + file-backed SQLite + Fuse mock; restart/clear/retry payload assertions |
| BCS aggregate above | **392 passed, 0 failed, 1 ignored** | Latest focused HTTP test adds actual changed/unchanged URL refresh assertions without counting the same test twice |
| Additional focused A2A package rerun | **63 passed, 0 failed** | Changed and unchanged manual/background refresh preservation, label-only CAS races, scoped/named/Provider paths |
| Additional focused Group packages rerun | **387 passed, 0 failed, 3 ignored** | Memory/SQLite preserve/replace, eventful rollback, joined snapshot, application/authorization paths |
| Whole BCS Cargo workspace/all targets | **PASS**, exit 0 | Compilation only, not a whole-workspace runtime/CI or deployment claim |
| Config validation gate | **161 passed**, exit 0 | Bootstrap config/loader filter; policy is required and cannot be overridden inline/with environment-suffixed files |
| Singlebox runtime-config shell suite | **PASS**, exit 0 | Independent regular policy copy with exact bytes; local/dev/mock/coverage fixture regeneration. A task-local ignored Backend venv with httpx/tomlkit was prepared to run the existing coverage fixture helper; no Backend source changed |
| YAML syntax/schema refs, Python fixture syntax, shell syntax, `git diff --check` | **PASS** | Static checks, not native DB or runtime rollout |
| OpenSpec strict validation (from repository root) | **PASS** | Formal app-owned and CLI mirror artifacts remain byte-identical |

Local ignored logs are `.catalog-labels-tests-final.log`, `.catalog-labels-bootstrap-final.log`, `.catalog-labels-http-final.log`, `.catalog-labels-a2a-final.log`, `.catalog-labels-group-final.log`, `.catalog-labels-check-final.log`, `.catalog-labels-config-gate-final.log`, `.catalog-labels-package-tests-final.log`, and `.catalog-labels-gates-final.log` under `apps/bcs`. They are local diagnostic artifacts, not deliverable source or CI artifacts.

The first broad run exposed obsolete six-field management-view expectations, minimal test schemas that stopped at migration 039, and fixed migration-tail assertions. These were updated to the new contract, then the complete affected suites were rerun successfully. A first HTTP Card fixture used an intentionally nonresolving example host and correctly failed outbound-address validation; the final test uses an explicitly allowed loopback Card source and asserts that Provider Authorization never reaches that host. No outbound security check was weakened in production.

### Spec-to-test coverage

| Local requirement | Executed evidence |
| --- | --- |
| Generic config dictionary, exact enums and distinct multiplicity | Service API `types::catalog_labels::tests::{generic_dictionary_normalizes_and_rejects_entire_invalid_input,dictionary_is_fail_closed}`; alternate `stage` key and multi-valued dictionary need no production branch |
| Outer null/type/deletion normalization and strict shapes | Service API `invalid_types_and_legacy_storage`; HTTP Agent `write_dto_presence_and_type_matrix`; Group DTO `group_patch_labels_preserve_presence_and_reject_invalid_types` and `normal_and_dm_creation_use_the_same_strict_label_shape` |
| Independent startup file and fail-closed policy | Bootstrap `required_file_strict_dictionary_and_no_interpolation`, `single_file_requires_policy_and_rejects_inline_overlay_override`; config gate and Singlebox file-copy checks |
| Agent register/Get/Find/update/clear/refresh | Application `public_management::catalog_labels::register_get_find_replace_clear_and_refresh_persist_labels`, `named_and_provider_admin_entries_store_the_same_labels`, and real HTTP integration |
| Agent invalid/failing combined writes and post-commit notification | Application `invalid_mixed_updates_and_storage_failure_are_atomic`, `invalid_labels_precede_network_access_and_registration_mutation`, `label_only_notifications_follow_commit_and_never_follow_invalid_or_failed_writes` |
| Refresh fences against label-only writes | `public_management::by_id::label_only_replacement_fences_manual_and_background_url_refresh`; changed/unchanged manual and background readback; real HTTP URL refresh followed by complete Fuse payload assertions |
| Group create/PATCH/preserve/public transition/unsupported kinds | Application `catalog_labels::{create_patch_omission_replacement_and_public_transition,chat_rejects_nonempty_labels_and_invalid_combined_creation_creates_nothing}`; actual Group REST create/PATCH/Get and invalid combined requests |
| Group persistence, missing template, SQL NULL/corruption, concurrency, eventful failure atomicity | Group store `catalog_labels::{memory_preserve_replace_and_version_fence,sqlite_preserve_replace_and_version_fence,legacy_missing_template_null_corruption_and_failure_rollback}`; failure trigger covers both direct and eventful combined writes |
| Migration chain/history and old-column SQL compatibility | Migration `catalog_labels_tests::upgrade_retains_history_and_accepts_legacy_queries_inserts_and_runner`; full SQLite fresh/upgraded chains and frozen history checks. The pre-change known-version/checksum runner algorithm is exercised against expanded schema; this is not an executed old binary |
| Query/access cost | Bot store `labels_have_constant_read_and_atomic_write_round_trips`: 0/1/64 keys retain one pre-write read + a two-step transaction, Get/Find each one query; Group `snapshot_is_one_current_env_scoped_read_and_never_hides_errors_or_backfills` now carries multiple custom keys and remains one joined query. Group explicit writes add one whole-map statement, not per-key statements; list summaries remain unchanged |
| Full Agent/Group publication and latest-state retry | Real integration `agent_http_persistence_retry_restart_and_full_payload`: register -> Get/Find -> replace/clear -> unchanged/changed URL refresh -> full payload -> failing/retried latest-state payload -> persistence restart. Agent retains both system dimensions; Group retains only its visibility. Existing Fuse eligibility/deletion/retry suites pass |
| Compatibility/release requirement | Legacy request/new-server paths and pre-change closed request/strict response shapes tested; exact old-binary and deployed mixed-replica acceptance remain pending below |

### Architecture gates — no baseline weakening

- **PASS (exit 0):** `check-port-purity.sh`, `check-store-boundaries.sh`, `check-forbidden-symbols.sh`, `check-protocol-compat.sh`, and the 161-test config gate.
- **FAIL (existing baseline findings retained):** `check-deps.sh` reports three existing dependency allowlist conflicts: bcs-protocol -> bcs-domain and bcs-service-api -> bcs-config-api / bcs-storage-api. HEAD manifests already contain these edges; this change introduces no dependency edge and does not edit the allowlists.
- `check-import-rules.sh` reports **82 existing matched import lines**; each matched line was verified present in the baseline HEAD source, with **zero newly introduced matches**.
- `check-trait-naming.sh` reports **11 existing trait names**, each verified present at baseline HEAD, including the existing GroupEventSubscriptionProvisioner. No gate was disabled or renamed to hide findings.
- cargo-machete is unavailable and was reported SKIP by the dependency gate. No coverage percentage, remote CI success or broad Singlebox live-stack acceptance is inferred from these checks.
- All **131 touched/new Rust source/test files** were checked against the 1000-line ceiling; none exceeds it. The large Group test suite's shared request/projection fixtures were split into `v1_group_service/commands.rs`; no global formatter was used.

### Final Git evidence and drift

- Worktree and branch remain the requested dedicated path and `codex/configured-catalog-labels`; HEAD/merge-base remain `8fc2e5f7693fc5caca97e5b5591148885d38ff10`. Changes are uncommitted and unstaged; existing spec/index work was retained.
- Final fetched authoritative GitHub `origin/dev`: `d60b5488526f1957066f2eb9ea654969c26112c0`; ahead/behind is **0/4**. The four new commits affect frontend/gateway/engine, not BCS. Baseline and final dev BCS tree objects are both **`65bff44012ea7f16069a0160f48278f1eeca9823`**. No automatic rebase/reset occurred.
- Final dev still ends at MySQL 047 and SQLite 045, so the new 048/046 allocation does not collide with that fetched ref. No historical SQL migration was modified.
- No submodules/gitlinks, OCB/bcngw sources, primary checkout, other worktrees, commit/push/PR/merge or remote deployment were changed by this task. Final disk availability was about 91 GiB; no unrelated process was killed or cleaned up.

### Remaining acceptance / authorization gates

OpenSpec progress is **25/32 tasks complete**. The remaining unchecked tasks are intentional and are not claimed complete:

- **3.7 — partial:** full SQLite chain, history, old-column queries/inserts and baseline known-version/checksum algorithm pass. Real **MySQL/OceanBase: NOT_RUN** because no isolated target is configured (`BCS_TEST_MYSQL_URL` / OceanBase target absent). New native conformance fixtures carry nonempty multi-key labels and the full 048 chain, but remain ignored until an empty disposable target is supplied. Actual pre-change binary/runner execution against that target is NOT_RUN.
- **6.2 — partial compatibility matrix:** old payload/new-server and old closed DTO/strict response decode contracts, malformed policy and expanded SQLite SQL behavior are tested. Exact old binary rollout/downgrade and live mixed-backend routing remain **NOT_VERIFIED**; source-shape tests must not be called deployment proof.
- **6.4 — not authorized:** no implementation commit SHA exists, no push/PR/merge or GitHub/internal-mirror publication verification was performed. OCB may read the local handoff but must not finalize its gitlink against this uncommitted baseline.
- **7.1–7.4 — not authorized / NOT_RUN:** target schema expansion, all-instance policy hashes, all-replica server rollout, client enablement, deployed smoke and rollback rehearsal. No online DDL or destructive down migration was executed.
- Fuse server stale-custom-key removal/full-replacement application is **external NOT_VERIFIED**; BCS payload completeness is proven locally, downstream application is not.


## PR #4256 review revision: omitted Agent labels preserve

This section supersedes the omission-clears contract descriptions above. Earlier
counts remain historical evidence for their recorded snapshots, not proof of this
local revision. The user explicitly accepted the review feedback and requested
local validation only; no commit, push, PR edit, merge or deployment is authorized.

### Corrected contract and implementation

- Public `UpdateAgentCommand.catalog_labels = None` preserves the current labels
  on every CAS attempt. `Some(object)` replaces the whole collection; `{}` and
  known-key deletion objects clear it. Source/visibility/label absence still fails
  `UPDATE_REQUIRED`. Registration omission still initializes empty labels.
- Provider legacy `ConfigureA2aBotCommand.catalog_labels` is now
  `Option<CatalogLabelsInput>`; HTTP forwards presence instead of defaulting
  omission to an empty object. Omitted labels use at most three local CAS attempts,
  re-reading labels, authorization and binding on each attempt, with one Card load
  outside the loop. An explicit expected revision never retries. Explicit label
  objects retain the existing atomic replace/single-CAS paths.
- Each preserve attempt adds a bounded Card read and binding/authorization checks;
  no per-label query, new cache, extra URL fetch, migration or transaction spanning
  network I/O is introduced. Persistence failures still propagate.

### Fresh local evidence

All commands run inside the dedicated configured-catalog-labels worktree, based on
HEAD `27e13268a16fc380ad29a21db8fa7e694b1befe6`; Rust is pinned to 1.94.0,
matching the GitHub BCS workflow. Uncommitted source hashes are recorded in
`apps/bcs/testresult/pr4256-omission-source-manifest.json` (ignored local artifact).

- Red regression: the updated existing lifecycle test failed on the pre-fix
  implementation because visibility-only omission lost `stage: trial`.
- `cargo test --locked -p bcs-service-api -p bcs-app-a2a-bot -p bcs-http -p bcs-api-http`:
  **1518 passed, 0 failed, 0 ignored**, including doctests.
- `cargo test --locked -p bcs --test integration_catalog_labels`:
  **1 passed, 0 failed**. Real HTTP -> application/Core -> SQLite -> full Fuse mock
  payload, persistence/restart, omitted-label preservation and explicit clear/replace
  through `/api/v1/collaboration/a2a`, root `/a2a` (its existing unwrapped response)
  and Provider-ID legacy routes. The test preserves each route's existing envelope.
- Deterministic race tests insert real competing SQLite CAS commits immediately
  before the tested commit; both public/legacy retry paths preserve the newly
  committed labels. Explicit replacements keep their requested intent, continuous
  races stop after three attempts, supplied revisions never retry, and URL-fetch
  races fetch once without restoring earlier labels.
- OpenSpec strict validation and `git diff --check`: passed. All ten modified/new
  Rust files remain below the 1000-line ceiling.
- Architecture checks remain failed on baseline findings: 3 dependency edges and
  83 matched imports are already in fetched `origin/dev`; both affected Cargo
  manifests are byte-identical to that baseline. No gate/allowlist was weakened.
- Supplementary `test_bcs_cov_gate.py`: 9 passed, 1 error during temporary `.git`
  directory cleanup (`OSError: Directory not empty`), not a product assertion.
  This unsuccessful run is retained, not counted as an all-green gate.

### Pending acceptance boundaries

Fresh full-workspace unit coverage is running with
`bash scripts/ci_test.sh --coverage --reuse-build`, nextest retries=0 and 2 test
threads. Required gate: pass rate 100%, overall lines >87%, PR changed lines >85%
when at least 51 instrumentable lines map to the diff against the fetched dev
merge base. The runner's outer success alone is not gate proof; the final JUnit,
Cobertura and `scripts/cov_gate.py` result must be inspected.

Singlebox shared-stack acceptance/coverage and native MySQL/OceanBase contracts
are NOT_RUN for this local revision: Docker daemon is unavailable, no local Qdrant
Server listens on 6333, and the local Backend environment lacks qdrant-client.
Do not substitute the already-green remote HEAD's evidence for this unpushed
revision or declare all remote requirements satisfied.


## PR #4256 review revision: legacy Group create error mapping

The second P2 review comment is addressed locally. `GroupUseCaseError::Service`
now maps only `A2aManagement(InvalidRequest)` errors whose field is
`catalog_labels` or `catalog_labels.*` to structured HTTP 400. The response
retains `field`, `reason`, and the stable `invalid_request` parameter code.
Other A2A management invalid requests continue through the existing internal
error fallback, so unrelated error semantics are not widened. The validation is
performed before Group creation or inline event-subscription provisioning.

Fresh local evidence:

- Group HTTP suite: **129 passed, 0 failed, 0 ignored**.
- Focused translation regression: **1 passed, 0 failed**.
- New regression covers unknown key, unknown deletion key, unknown value, and
  single-value multiplicity violations through both no-subscription and inline-
  subscription request shapes; invalid requests return 400 and do not create a
  Group or provision subscriptions.
- `git diff --check`: passed. No commit, push, PR edit, or remote write performed.


## PR #4256 final local rerun and publication authorization (2026-11-20)

This section supersedes the pending-run wording above. After reviewing the local
results, the user explicitly authorized committing and pushing these review fixes.
The authorization does not make unsuccessful validation pass or authorize merging,
deployment, gate relaxation, or dropping tests.

Final local runs used Rustup Rust 1.94.0, `CARGO_INCREMENTAL=0`, and one test
thread in the dedicated configured-catalog-labels worktree:

- `cargo test --locked -p bcs-service-api -p bcs-app-a2a-bot -p bcs-http -p bcs-api-http -- --test-threads=1`:
  **1519 passed, 0 failed, 0 ignored**, including doctests.
- `cargo test --locked -p bcs-http --test suite_groups -- --test-threads=1`:
  **129 passed, 0 failed, 0 ignored**. These tests also appear in the package run
  above; the two counts must not be added as unique coverage.
- `cargo test --locked -p bcs --test integration_catalog_labels -- --test-threads=1`:
  **1 passed, 0 failed**.
- `cargo test --workspace --locked --no-fail-fast -- --test-threads=1`:
  exit **101**, **8935 passed, 79 failed, 74 ignored** across the reported test
  and doctest results; **8935 / 9014 executed = 99.1236%**, not the required 100%.
  Nine test targets failed. Eight BCS startup cases explicitly report child
  `SIGKILL`; the other 71 failures are in bcs-cli subprocess suites, reporting
  interrupted processes, absent exit codes/output, or downstream assertions.
  Direct shell execution of the same bcs-cli `--help` and `--version` succeeds.
  This is evidence of a local subprocess-execution problem, not proof that every
  failure is environmental or that the complete suite passes elsewhere.
- The clean nextest round2 build completed, but its test-list subprocess phase
  did not complete normally and was interrupted. It is not a passing test run.
- No final LLVM coverage report was regenerated. Earlier intermediate-source
  coverage percentages are historical only and do not certify this revision.
- OpenSpec strict validation and `git diff --check` passed. All 13 changed/new
  Rust source and test files are below the 1000-line limit.

Ignored local logs are retained under `apps/bcs/testresult/`:
`pr4256-focused-packages-final.log`, `pr4256-suite-groups-final.log`,
`pr4256-integration-catalog-labels-final.log`, and
`pr4256-full-unit-no-fail-fast.log`, with matching exit receipts. They are not
committed as generated artifacts. Only stale build output from this worktree was
cleaned during earlier testing; no active target was deleted. Disk availability
at publication preflight was approximately 42 GiB.

The normal repository pre-push hook remains enabled. Its default lint-only mode
and credential scan do not constitute full CI, 100% tests, coverage, Singlebox
acceptance, or deployment proof. Fresh remote checks on the new commit remain
necessary. No test, coverage threshold, or allowlist was weakened.


The user subsequently confirmed that local security software affects some test
subprocesses and explicitly accepted these local failures as non-blocking for
this push. The user also authorized updating the PR description to match the
final commit. The raw failures above remain recorded; neither this confirmation
nor publication is a claim that all local tests or final coverage passed.


## PR #4256 third review revision (2026-11-20)

Baseline HEAD for this revision: `c34dc36ad91e99a3b0103ecffc652807be36a581`.
The user authorized fixing the first three new comments, local regression,
commit and push. Work stays in the dedicated configured-catalog-labels worktree;
no rebase, main-checkout edit, online DDL, deployment or OCB integration occurs.

### Scope and safety

1. Public Agent updates now perform read-only Provider authentication, label
   normalization, scoped target validation and Card validation before reserving
   the Provider slug. Reservation occurs once before the first CAS write, not
   once per retry; the store still rejects a concurrent slug change. Other public
   management entries retain their existing reservation semantics. Invalid label,
   target, empty-update and Card input cannot newly pin a mutable slug. This does
   not claim that later commit/storage failures roll back a separate reservation.
   Successful-path query count is unchanged: the existing reservation is moved,
   not duplicated; failed pre-validation requests avoid its DB write/read/cache
   invalidation. Existing bounded Card CAS retries and single URL fetch remain.
2. Gateway Group create/PATCH/detail schemas now contain all 13 generated label
   property fragments, copied from a fresh BCS OpenAPI dump. Full regeneration
   also includes 151 historical semantic differences, so only the label fragments
   were applied. A structural before/after comparison confirms exactly 13 property
   additions, no removals or unrelated changes; the existing compatibility checker
   reports no breaking changes. New tests regenerate the authoritative dump and
   compare every Group label schema fragment, including optional PATCH clear `{}`.
3. Legacy Group create catches JSON data rejections only when the structured
   serde path begins with the `catalog_labels` field. It returns structured 400
   with `invalid_request`, precise field/list index, and `INVALID_TYPE`. It uses
   the existing workspace `serde_path_to_error` dependency without changing its
   version; the lockfile adds only the bcs-http dependency edge. Other DTO errors
   remain 422, syntax errors 400, unsupported content types 415 and oversized
   bodies 413. No parsing error reaches Group or subscription writes.

### Executed evidence

- Red regression against the pre-fix implementation: unknown label key mutated
  Provider config by adding `a2a_name_pinned`; the label-type HTTP regression
  returned 422 instead of 400; both Gateway schema tests failed on missing fields.
- `cargo check --manifest-path apps/bcs/Cargo.toml -p bcs-http --tests`: PASS.
- Rustup Rust 1.94.0, `CARGO_INCREMENTAL=0`, `CARGO_BUILD_JOBS=2`:
  `cargo test --manifest-path apps/bcs/Cargo.toml --locked -p bcs-app-a2a-bot -p bcs-http -- --test-threads=1`:
  **885 passed, 0 failed, 0 ignored**, including the 131-case Group HTTP suite.
- The new Provider tests cover eight rejected input/auth/target cases without
  Provider config or Agent record mutation and verify actual slug rename remains
  allowed; valid label updates still pin the name, and a deterministic slug change
  during URL fetch rejects the Agent write without pinning the changed name.
- Group HTTP tests cover seven invalid label type shapes through both subscription
  modes, precise JSON field/index details and no writes; separate negative controls
  preserve unrelated 422, syntax 400, content-type 415 and body-limit 413.
- `cargo test --manifest-path apps/bcs/Cargo.toml --locked -p bcs --test integration_catalog_labels -- --test-threads=1`:
  **1 passed, 0 failed** (actual HTTP/SQLite/restart/Fuse mock payload chain).
- In `apps/gateway`, after `uv sync --frozen`, `PYTHONPATH=src .venv/bin/python -m pytest tests/unit -q`:
  **917 passed, 1 skipped, 0 failed**. Initial invocation from the repository root
  could not collect app-bootstrap tests because config lookup used the wrong cwd;
  the complete run from the Gateway module succeeded. The first Rust test build
  also caught a missing test-only trait import, corrected before the passing run.
- Ruff check and format check on the new Gateway test: PASS.
- Full BCS workspace tests, final LLVM coverage and Singlebox runtime coverage are
  NOT_RUN for this revision. Earlier local full-suite environmental failures remain
  historical and must not replace these focused results or the new remote CI.
  No test or coverage threshold, retry, timeout, allowlist or platform was weakened.

Ignored Rust logs and exit receipts are under `apps/bcs/testresult/` with prefix
`pr4256-round3-`; Python logs are temporary local artifacts. Disk preflight had
about 38 GiB available, with no active Cargo process. Final checks will be recorded
in the publication report; no build-output cleanup was needed in this round.
