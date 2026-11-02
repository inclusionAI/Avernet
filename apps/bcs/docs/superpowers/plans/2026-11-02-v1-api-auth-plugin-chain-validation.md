# V1 API Auth Plugin Chain — Rollout, Regression and Delivery Validation

> Date: 2026-11-03
> Status: 本地实现已完整回归；发布/回滚以演练记录交付（无生产环境存在）
> [Spec](../specs/2026-11-02-v1-api-auth-plugin-chain-design.md) ·
> [Plan](2026-11-02-v1-api-auth-plugin-chain.md)

## 1. Problem

The pre-2026-11 V1 authentication surface accepted a single unconditional
OAuth session writer (`update_token`): a browser could complete an unbound
callback, a refresh or logout could lose a race silently, and a repository
failure could be folded into a quiet denial. Rollout and rollback therefore had
no safe shared-writer procedure: mixing old and new writer binaries against
one shared session store could resurrect revoked sessions, and no drill
existed that demonstrated the safe order or the safe reverse.

Task 15 of the plan is the delivery gate: run the full targeted regression,
walk the rollout and rollback drills with cited repo evidence, attempt the
unified singlebox coverage gate, sweep line-count/diff hygiene, and record —
without adding any runtime functionality — what was actually executed, what
was not, and why.

## 2. Solution

No runtime functionality was added by this task. The only production-code
change it carries is a serializer-output correction of a plan artifact
(found by the singlebox drill itself, §3.4): the printed default config no
longer emits an explicit `api.auth` null that the plan's own validation
rejects. The delivery artifacts are this document and the runbook added to
`migrations/README.md` (rollout/rollback sections under "Versioned login
sessions"):

- the targeted regression, re-run in full on the final tree (§3.1);
- the rollout drill: drain old writers → schema upgrade (sessions only) →
  deploy fixed writers → enable `[api.auth]` → new login (§3.2);
- the rollback drill: gateway-only closes the OAuth lifecycle entry; the
  writer-binary boundary must never be re-crossed (§3.3);
- the unified singlebox coverage attempt under its real thresholds (§3.4);
- the line-count and diff hygiene sweep against the pre-plan base
  `64a9d07d826c663d69554885c83ab4d97f356505` (§3.5).

The drills are **walkthroughs recorded against repo evidence, not live
production exercises** — no production deployment of BCS exists. Every claim
below cites either a command executed this session (§3) or a test that pins
the behavior in the tree; nothing is claimed as "verified in production".

## 3. Validation

All Cargo commands ran from `apps/bcs` on branch
`docs/bcs-v1-api-auth-plugin-chain-refresh` at the plan's final implementation
commit `77c1e7281` (plus this task's post-drill repairs, committed together
with this document). Disk was watched continuously against the 10G stop rule;
the environment's 78G `/home` could not hold the default full-`--tests`
debug tree (observed >57G growth before the guard fired; nadir 697M free
during the manual halt of the workspace step), which is why the aborted
steps were re-run with the repo's own coverage-build disk recipe
(`CARGO_INCREMENTAL=0`, `CARGO_PROFILE_DEV_DEBUG=line-tables-only`) that
singlebox/ci also uses.

### 3.1 Targeted full regression (brief step 1, verbatim commands)

All commands ran from `apps/bcs`, sequentially, in the brief's order, with a
disk watcher that aborts cargo the moment `/home` drops below 10G. The
sequence hit that stop line on this environment's 78G `/home` (the full
`--tests` debug tree grew past 57G against 60G starting free), so the
aborted steps were re-run once with the exact disk-tuned recipe the repo's
own singlebox coverage build uses (`CARGO_INCREMENTAL=0`,
`CARGO_PROFILE_DEV_DEBUG=line-tables-only`) — same commands, same tree, same
watcher. What ran and what did not:

| # | Command (verbatim) | First run | Result |
| --- | --- | --- | --- |
| 1 | `cargo test -p bcs-config-api -p bcs-service-api -p bcs-auth-api -p bcs-jwt` | completed | **GREEN** — 58 test targets, **402 passed / 0 failed / 0 ignored** |
| 2 | `cargo test -p bcs-user-identity -p bcs-auth-oauth -p bcs-app-auth` | completed | **GREEN** — 20 test targets, **92 passed / 0 failed / 1 ignored** (the `#[ignore]`d live-MySQL gate — fails loudly without a URL, by design; §3.6) |
| 3 | `cargo test -p bcs-auth-alipay -p bcs-auth-github -p bcs-auth-google -p bcs-auth-wechat` | completed | **GREEN** — 13 test targets, **28 passed / 0 failed / 0 ignored** |
| 4 | `cargo test -p bcs-api-http -p bcs-http -p bcs` | **aborted** during compile by the 10G guard (rc=137 at 3G free; 0 test targets had executed) | re-run: **GREEN** — 150 test targets, **1791 passed / 0 failed / 24 ignored** (pre-existing `#[ignore]`d live-environment gates in this group) |
| 5 | `cargo test -p bcs-cli --test cli_command_contracts --test cli_decomposition_contract` | completed | **GREEN** — 2 test binaries, **88 passed / 0 failed / 0 ignored** |
| 6 | `cargo test --workspace` | **aborted** during compile (disk guard + manual kill at 697M free; 0 test targets had executed) | re-run: **NOT COMPLETED — aborted again at 9G free** (0 test targets had executed; compile-only). Environment finding, recorded for the repo: this 27-crate workspace's full `--tests` build needs a peak >50G of test executables ALONE — with `/home` at 78G total the command cannot finish in this environment even under the tuned flags, after two documented attempts. All crates this plan touched are covered GREEN through commands 1–5 and the command-4 re-run; other workspace members (bcs-admin, bcs-ws, bcs-client, ding-logger, deepseek-harness-channel-bcn, untouched services/plugins) were NOT test-executed this session — recorded, not approximated. |
| 7 | `bash scripts/ci/arch-check.sh` | never started | re-run: **COMPLETED** — `Total: 15 Pass: 7 Skip: 3 Fail: 5`, `Score: 51/100`, exit 5 |

Executed subtotal (first run): 610 passed / 0 failed / 1 ignored across
commands 1, 2, 3 and 5; the command-4 re-run added 1791 passed / 0 failed /
24 ignored (pre-existing `#[ignore]`d live-environment gates). None of the
aborted commands test-executed anything before an abort (compile-only), so
no result was silently truncated. Source state per column: the first-run
rows executed at exactly `77c1e7281`; the three re-run rows (4, 6, 7)
executed with this task's working-tree changes already applied (the §3.4
serializer fix + regression test, plus the EOF hygiene fix) — i.e. the
delivered tree.

R25.3 — the honest non-claim Task 14 left open ("full-workspace
conformance-test discovery") — **is still not settled**, and for the same
recorded reason: `cargo test --workspace -- --list` requires exactly the
full test build this environment cannot hold (disk aborts in both attempts
above; no padding or read-only reduction was invented). It remains a
recorded non-claim, neither passing nor represented by targeted runs.

`bash scripts/ci/arch-check.sh` completed, with — measured against the Task
14 baseline (`Total: 15 Pass: 7 Skip: 3 Fail: 5`, 51/100, rc=5) —
**zero delta**:

- `Total: 15 Pass: 7 Skip: 3 Fail: 5`, `Score: 51/100`, exit 5 — identical
  aggregate and weight;
- `Failed:` `DEP-1~8 LINT-1 LINT-4 TEST-1 R25` — identical failed set
  (DEP-6 `bcs-protocol → bcs-domain` and DEP-1 `bcs-service-api →
  bcs-config-api`/`bcs-storage-api` unchanged, same files the plan never
  touched);
- LINT-1's finding list still contains exactly this plan's ONE plan-introduced
  finding, `crates/service-api/bcs-service-api/src/port/oauth.rs:18`
  (`use crate::application::v1::error::ApplicationError`, Task 9), unchanged,
  alongside the pre-existing finding classes Task 14 recorded (adapters
  importing `port`/`core`, services importing `application`, service-api
  self-imports);
- R25.2's harness/boundary-impl findings remain the recorded pre-existing
  set (this plan's conformance drivers themselves pass);
- CFG-1 PASSED with `cargo test -p bcs --lib config` at **159 passed**
  (Task 14 baseline 158; the +1 is this task's new print-config round-trip
  regression test — the count delta is exactly accounted for);
- caveats on the environment: the gate ran with ≤9G free while its spawnable
  cargo compilations were being disk-guarded, so the recorded outcome relies
  on its static rules plus the cached/partial artifacts; R25.3 inside R25
  still fails for the disk reason above (identically to the baseline).

### 3.2 Rollout drill (walkthrough)

No production environment exists; this is the recorded runbook, each step
carrying the repo evidence that makes it safe. The full operational text is in
`migrations/README.md` ("Rollout runbook (V1 API auth chain, 2026-11)").

**Step R1 — block/drain all old callback/refresh/logout writer instances.**
Mixed old/new writer binaries against one shared session store are NOT a
supported upgrade path — not even as a canary: a pre-2026-11 binary writing
unconditionally can resurrect a session the new writers already revoked
(pinned in `api-contracts/v1/authentication/contract.md` §4, and the
writer-side invariant itself is enforced by the conformance harnesses in
`crates/test-support/bcs-test-support/src/contract/repo/auth_session.rs`:
install/rotate return `Applied`/`Conflict`, never unconditional rewrites —
driven live by `bcs-user-identity` `conformance_auth_session_memory` /
`conformance_auth_session_sqlite` / `conformance_auth_session_identity`).

**Step R2 — schema upgrade, sessions only.** Apply the chain through
`040_auth_session_version.sql` (SQLite/MySQL; `bcs-admin db migrate --apply`),
which adds `session_id`/`session_revision`/`session_expires_at` and clears
`token`/`token_expire_at` exactly once, guarded by the recorded version-40 row.
Identity rows and user assets are never deleted (spec §9; migration README).
Strict SQL mode is a hard precondition for MySQL (README §040).

**Step R3 — deploy the fully-fixed writers.** After the drain, every instance
runs binaries whose two entries (legacy `/auth/*` and V1
`/openapi/v1/auth/*`) share the one strict engine. That both entries are
constrained by it is proven end-to-end by
`crates/bootstrap/bcs/tests/api_auth_races.rs`: the cross-entry races
(`legacy_refresh_pausing_after_reading_a_loses_the_cas_to_a_v1_logout`,
`v1_refresh_pausing_after_reading_a_loses_the_cas_to_a_legacy_logout`) end
with exactly one committed rotation over the one shared session space — a
legacy entry that could bypass atomicity could not produce those outcomes.

**Step R4 — enable the new configuration.** Add `[api.auth]` (chain +
per-source tables, secrets as logical `[secret]`-backend references) and
restart; config switching is restart-only (contract.md §5). Structural
validation is strict and total: empty chain, unknown/duplicate source,
unknown field, missing required fields/keys, malformed public fields and
timeout-overflow all fail startup (`crates/service-api/bcs-config-api/tests/api_auth.rs`,
bootstrap `config_loader/tests.rs`, `tests/api_auth_config.rs`); a
present-but-not-chained provider table is inert and its secrets are never
read (`tests/api_auth_config.rs`).

**Step R5 — new login.** Pre-upgrade sid-less sessions are rejected by
design; re-login is the documented recovery (contract.md §4). Post-upgrade
behavior verified by repo evidence:

- legacy sid-less JWTs are **not recoverable**:
  `crates/plugins/bcs-auth-oauth/tests/strict_session.rs::legacy_sid_less_jwt_is_rejected_as_invalid`,
  with the delegated legacy-exit mapping pinned by the same file's
  `delegation_*` tests (`Invalid | Forbidden → Ok(None)`, dependency
  failures propagate, never a silent accept);
- new browser login succeeds through the real chain: the R25 drivers
  `crates/bootstrap/bcs/tests/conformance_api_auth_ports.rs` run the
  port-contract harnesses (login URL discovery → state/callback/browser
  binding → engine install) over the REAL bootstrap bridges
  (`ConfiguredOAuthProviderBridge`/`StrictSessionPortBridge`/
  `PendingLoginStoreBridge`), and
  `bcs-app-auth/tests/conformance_auth_service.rs` pins the service
  boundary (exactly one SetLoginChallenge, no session write in discovery,
  out-of-chain provider fails closed before state consume);
- refresh/logout atomicity holds under contention:
  `crates/bootstrap/bcs/tests/api_auth_races.rs` (7 tests, all
  barrier/pause-gate driven, no sleeps) — a committed logout kills both A
  and candidate B; exactly one of two concurrent refreshes commits and the
  loser carries no Set-Cookie; a post-response logout makes the late
  rotated cookie server-side dead; two independent store instances over one
  file-backed database still serialize the CAS.
- a full chain [gateway → alipay → github → google → wechat] behaves:
  `api_auth_compatibility.rs::n5_…` spends exactly one strict SELECT per
  request; DB faults surface as 503 not quiet denials
  (`api_auth_http.rs`, 4 tests injected at the DbPlugin boundary).

### 3.3 Rollback drill (walkthrough)

**Step B1 — cut the chain to gateway-only; the OAuth lifecycle entry
closes.** Removing `[api.auth]` returns the compatibility assembly: business
authentication stays Gateway-protected while the login family fails closed —
`auth_not_configured` (the login family answering
`auth_not_configured` while `/auth/user` keeps resolving Humans through the
remaining sources is pinned by
`bcs-api-http/tests/oauth_browser_binding.rs` (login-family fail-closed
block) and `crates/bootstrap/bcs/tests/integration_oauth_routes.rs:648`).
No OAuth pending state is left consumable: state consume is one atomic
install-path, and out-of-chain sources fail before state consume
(`bcs-app-auth/tests/conformance_auth_service.rs`,
`conformance_api_auth_ports.rs`).

**Step B2 — group-session signing keeps working.** It never depended on the
chain: an OAuth-only chain reads no gateway signing secret, and the
`friend_auth_sync` resigner keeps its own legacy secret contract
(compatibility tests
`api_auth_compatibility.rs::n4_oauth_only_chain_reads_no_gateway_secret_and_the_resigner_keeps_its_contract`,
fixture arm and production-boot arm).

**Step B3 — binary rollback is bounded by the writer boundary.** A rollback
target binary must still honor the sid/revision JWT contract and the 040
schema columns, and must never re-enable unconditional session writes;
neither happens implicitly (contract.md §4: "the schema (sid/revision
columns) and the session JWT contract persist"). If only a pre-2026-11
unconditional-writer binary remains, the safe rollback is to keep the OAuth
entry closed, revoke related sessions, and run gateway-only — never recurse
to old writers.

**Step B4 — record the re-login recovery for the forward restore.** Rolling
in either direction invalidates pre-upgrade sid-less sessions; after
restoring the fixed writers + `[api.auth]`, users re-login through the new
chain (identical to rollout step R5). Re-login paths are the same suites
cited in §3.2 (engine install → one SELECT + one install CAS per new login,
refresh one read + one conditional write, logout one conditional write —
spec §9.1, budgeted by `session_lifecycle.rs` sizing tests).

### 3.4 Unified singlebox coverage (brief step 4)

Executed from the repo ROOT; both commands really ran and both really failed —
this step is recorded as **NOT PASSED**, with the gate's own numbers:

```
bash singlebox/ci/singlebox_coverage.sh                 → rc=1
python3 singlebox/ci/verify_singlebox_coverage_artifacts.py \
  --reports-dir singlebox/.dependencies/coverage/singlebox/reports → rc=1
```

The full stack DID come up (BAAS, Backend, BCSFuse, instrumented BCS,
gateway, frontend, bots) and coverage WAS collected end to end; the gate
failed on real thresholds. Numbers (from
`singlebox/.dependencies/coverage/singlebox/reports/`):

| BCS e2e gate | Result | Threshold | Verdict |
| --- | --- | --- | --- |
| runtime line coverage | 50.86% | ≥ 41% | PASS |
| runtime method coverage | 45.14% | ≥ 36% | PASS |
| Router API endpoints | 173/173 (100%) | 100% | PASS |
| bcs-cli leaf commands | 66/66 (100%) | 100% | PASS |
| E2E changed-line coverage | **8.49% (260/3064 changed lines vs origin/dev)** | > 60% when ≥ 20 instrumentable lines | **FAIL** |

The changed-line requirement applies (3064 instrumentable changed lines ≫ 20)
and is NOT MET. The honest reading: this plan adds a large new
instrumentable surface (the whole V1 auth chain, its config, CSRF, cookie and
verifier code), and the story suite runs in the compatibility assembly with
gateway-token/loopback identities — it has no OAuth browser-login/stories for
the new surface, so those lines are not exercised by E2E. Per the brief this
miss may NOT be remediated by excluding the new auth paths or calling
domain code from tests; the missing story coverage for the new auth surface
is recorded as remaining E2E work.

The verify script's full failure set (`coverage artifact verification failed`):

- `summary.status expected 'passed', got 'failed'`
- `summary.systems.bcs.e2e_status expected 'passed', got 'failed'`
- `acceptance JUnit has failures/errors: failures=12, errors=0`
- module thresholds: `expert_chat core coverage 52.34% < 61.18%`,
  `expert_chat router API coverage 90.91% < 100.00%` (backend-side modules,
  untouched by this plan; the same gate was already red on this machine
  before the plan — the prior coverage artifacts dated 2026-10-28 show the
  identical failed shape).

BCS e2e story-level failures in this run (2):

1. `caller_execution_story.py` — "test BCS exited during startup":
   the story round-trips `bcs-caller-e2e --print-config`, overlays fields and
   reloads; `BcsConfig::default()` serialized `"api": {"auth": null}`, and
   the strict value-domain validation introduced by Task 6 (C3: absent and
   null are not equivalent) correctly rejected it. **Plan-introduced harness
   incompatibility, fixed in this task**: `ApiConfig::auth` now carries
   `skip_serializing_if = "Option::is_none"` (the printed default no longer
   contains a null `[api.auth]`), plus a permanent round-trip regression test
   (`config_loader/tests.rs::default_config_print_round_trips_without_an_explicit_null_api_auth`,
   red→green at the silently-broken base). A full singlebox re-run was not
   executed within this session (the changed-line outcome does not change:
   the auth E2E stories do not exist; the other gate failures pre-date the
   plan); the fix itself is directly proven on the real binary:

   `python3 scripts/e2e-test/caller_execution_story.py --binary
   target/debug/bcs-caller-e2e` → **rc=0**, "Caller HTTP/queue/provider E2E:
   32 HTTP assertions passed / Durable queue/cache-loss restart fails closed:
   PASS / Caller credential persistence and log redaction: PASS" — the exact
   story that died pre-fix with the startup panic above.
2. `story_cli_operator_chats_and_shares_files_in_terminal` —
   `bcs-cli chat` exited 1 (run submitted, assertion failed on delivery):
   the chat-delivery flake family already on record from prior sessions,
   unrelated to API auth (no auth assertion involved).

### 3.5 Hygiene sweep (brief step 5)

Against the pre-plan base `64a9d07d826c663d69554885c83ab4d97f356505`:
`git diff --name-only --diff-filter=ACM` lists 199 files, and
`git ls-files --others --exclude-standard` lists none (the workspace is
fully committed; `.superpowers/` scratch is git-ignored by design and was
excluded from the untracked analysis).

Line counts for every Rust/Python/TS/JS/SQL source file in that set: files
above 700 lines (all pose no gate action except the six over 1000):

| File | Lines at base | Lines now | Plan delta |
| --- | --- | --- | --- |
| `crates/adapters/http/bcs-http/tests/provider_routes_contract.rs` | 3531 | 3513 | −18 |
| `crates/test-support/bcs-test-support/src/contract/repo/mod.rs` | 2835 | 2837 | +2 |
| `crates/adapters/http/bcs-http/src/routes/sessions.rs` | 2264 | 2267 | +3 |
| `crates/adapters/http/bcs-http/tests/group_messages_contract.rs` | 2233 | 2224 | −9 |
| `crates/adapters/http/bcs-http/src/routes/session_files.rs` | 1801 | 1802 | +1 |
| `crates/adapters/http/bcs-http/tests/session_collection_contract.rs` | 1401 | 1392 | −9 |

All six >1000 files were **already oversized at the pre-plan base**; this
plan's net deltas on them are ≤3 added lines, and one of those lines is the
`auth_session` conformance registration in `contract/repo/mod.rs`. Every
other source file in the change set is ≤989 lines.

`git diff --check 64a9d07d8..HEAD` initially reported exactly one finding —
a new blank line at EOF in
`bcs-api-http/tests/friendship_routes/fixtures.rs:603` (introduced by this
plan's commit `707478790`) — fixed by this task (trailing blank line
removed); after the fix `git diff --check` against the base is clean.

### 3.6 Not executed (explicitly NOT RUN — not replaced by unit tests)

| Item | Command / reason |
| --- | --- |
| Live MySQL conformance gate | `cargo test -p bcs-user-identity --test conformance_auth_session_mysql -- --ignored --nocapture` — NOT RUN: no `BCS_TEST_MYSQL_URL` in this environment. Without the URL the gate fails loudly by design (verified in Task 13: without config it panics `BCS_TEST_MYSQL_URL must be set…`, never green by default). The MySQL-dialect behaviors it arbitrates (BIGINT overflow mapping, cross-connection CAS) therefore remain SQLite-pinned only. |
| Real third-party provider login smoke | NOT RUN: no real provider credentials/network in this environment. Per spec §10, the provider-login smoke is separate human evidence and cannot be substituted by mock passes; the local suites use fake providers over real chain code. |
| OceanBase | NOT RUN: no OceanBase deployment; per spec §9.1 the OceanBase directory holds no continuous MySQL-alias migration sequence and needs its own DDL/race verification when used. |
| OCB (downstream submodule/内部 SDK) integration | NOT RUN: this checkout has no downstream OCB workspace; the public factory/fake-assembly tests are in-tree (`api_auth_registry`, `conformance_api_auth_ports`), the internal SDK plan is out of scope here. |
| Production rollout/rollback execution | NOT RUN (does not exist): §3.2/§3.3 are recorded walkthroughs over repo evidence, not live deployment claims. |

## 4. Compatibility and risk

- **No mixed-writer grading.** The only safe order is drain → schema → fixed
  writers → config → re-login. Any shortcut that lets an old
  unconditional writer run against the upgraded store forfeits §8.5
  atomicity and revocation itself (contract.md §4).
- **Config absence ≠ old auth everywhere.** Absent `[api.auth]` keeps the
  compatibility assembly, but the upgraded legacy entry still gets the
  hardened lifecycles (browser binding, atomic CAS) — this is a deliberate
  safety tightening documented in the spec's compatibility-scope correction,
  status-shape changes on the legacy route are limited to 503/409s that
  already exist in the V1 contract.
- **Architecture gate finding carried from Task 9** (recorded in this
  validation per the plan ledger):
  `crates/service-api/bcs-service-api/src/port/oauth.rs:18` is flagged by
  `check-import-rules.sh` (port importing `use crate::application::`), a
  plan-introduced finding inside a gate whose full finding set is otherwise
  a pre-existing baseline red. This session's arch check re-ran to the
  identical aggregate as the Task 14 baseline (51/100, same five failed
  rules — §3.1), i.e. the finding carried with **zero weighted delta**.
  Fixing it was left to review rounds because the neighboring findings are
  the same rule class and pre-date the plan.
- **Session-invalidating migration is one-way by design.** The version-40
  clear runs exactly once and re-running migrations never re-invalidates;
  the cost is that every upgrade forces one re-login window.
- **Risks not covered by this validation:** live MySQL dialect behavior,
  real third-party provider flows, multi-instance OAuth without shared
  pending-state (forbidden until delivered — see spec §11), and downstream
  OCB SDK integration (spec §12).

## 5. Spec acceptance index (spec §10 → concrete suites)

| ID | Concrete suites on the final tree (all executed in §3.1 numbers unless marked NOT RUN) |
| --- | --- |
| C1 | bootstrap `tests/api_auth_config.rs` (compatibility assembly when `[api.auth]` absent; hardened-only legacy entry), `tests/integration_oauth_routes.rs`, `tests/integration_auth_user.rs` |
| C2 | `tests/api_auth_compatibility.rs` N4 production arm (OAuth-only `[api.auth]` boots without any gateway material), config-loader tests |
| C3 | `bcs-config-api/tests/api_auth.rs`, bootstrap `config_loader/tests.rs`, `tests/api_auth_config.rs` (empty chain / unknown, duplicate / unknown fields / missing keys / bad formats / timeout overflow) |
| C4 | `tests/api_auth_config.rs` (present-but-not-chained table: no enable, no secret read, no login exposure) |
| A1 | `bcs-api-http/tests/api_auth_boundary.rs` (17 tests: valid/dup-header/bad signature/issuer/audience/tenant), `gateway_principal/tests.rs` |
| A2 | `conformance_api_auth_ports.rs` (real provider builds incl. alipay with generated RSA keys), `bcs-auth-{alipay,github,google,wechat}` suites, `login_flow.rs`/`session_flow.rs` |
| A3 | `api_auth_compatibility.rs` N5 (chain `[alipay→github→…]` served by the valid github cookie), provider-not-enabled covered by `bcs-app-auth` service conformance |
| A4 | strict_session.rs / `session_lifecycle.rs` verify taxonomy, `api_auth_http.rs` (zero-rows → 401), `oauth_browser_binding.rs` cookie variants |
| A5 | bootstrap `tests/api_auth_http.rs` (faults at DbPlugin boundary → 503/50300 `auth_dependency_unavailable`; malformed row → 500; never folded into None), `auth_session_db_errors.rs` |
| A6 | `api_auth_compatibility.rs::composite_runs_verifiers_in_registration_order_and_first_success_wins` + `composite_stops_at_the_first_non_missing_outcome`; no-fallback N1 arm |
| A7 | `api_auth_compatibility.rs` N2/N3 arms — no cross-source identity merge, Bot never becomes owner Human |
| R1 | `api_auth_boundary.rs` (main router), `group_session_connection.rs` + `tests/group_session_connection_routes.rs` (connection-token route), `api_auth_compatibility.rs` N2 native-Human arm |
| R2 | `api_auth_compatibility.rs` N3 (`n3_public_groups_…`, invite gate after auth), `n3_authority_routes_…` |
| O1 | `oauth_browser_binding.rs`, `bcs-app-auth` flow suites, `integration_oauth_routes.rs` (all five entries consume the new assembly) |
| O2 | `bootstrap/tests/auth_log_redaction.rs`, `bcs-app-auth/tests/log_redaction.rs`, `bcs-auth-api/tests/login_state_redaction.rs`, `bcs-service-api/tests/auth_redaction.rs`; state replay/nonce in `bcs-auth-oauth/tests/conformance_pending_oauth_login.rs` + `bcs-http/src/oauth/tests.rs` |
| O3 | `oauth_browser_binding.rs` (cookie overwrite / single-session invalidation / re-login under changed keys), `verify_session_expiry_contract.rs` |
| O4 | `oauth_browser_binding.rs` (browser A state rejected in B, zero provider exchanges, challenge-cookie cleanup) |
| O5 | `oauth_browser_binding.rs` / `oauth_browser_binding/noops.rs` + `bcs-http/src/oauth/tests.rs` (nonce rotation, one redemption, batch cleanup, TTL, multi Set-Cookie) |
| O6 | `api_auth_races.rs` (logout→refresh and refresh→logout, barrier-driven; V1/V1 + both cross-entry legacy/V1 combos) |
| O7 | `api_auth_races.rs` (refresh vs re-login C, late logout, dual concurrent refresh; distinct same-second credentials pinned in `session_lifecycle.rs`) |
| O8 | `api_auth_http.rs` + `session_db_errors.rs` + `conformance_auth_session_{memory,sqlite,identity}`; the real-MySQL leg is NOT RUN (§3.6) |
| O9 | writer invariant in `bcs-test-support/src/contract/repo/auth_session.rs` (Applied/Conflict, never unconditional writes), the drills in §3.2/§3.3, `README.md` runbook; sid-less sessions force re-login (§3.2 step R5) |
| S1 | `api_auth_boundary.rs` `s1_…` (missing/illegal Origin on cookie-kind writes → final 403), `cookie_provenance_…` |
| S2 | `api_auth_boundary.rs` `s2_…` (cookie-first success + valid gateway header without Origin → 403, no gateway fallback), `real_router_gateway_first_success_ignores_cookie_csrf` / `real_router_cookie_first_success_requires_trusted_origin` |
| N1 | `api_auth_compatibility.rs` n1 arms (Bot deleted/disabled/actor-mismatch 401, registry fault 502 `bot_identity_unavailable`, session-store fault 503) |
| N2 | `api_auth_compatibility.rs` n2 arms (native Human substitution 401, expected-user 403 `user_context_changed`, no-store; Draft/Bot-Self own protocols untouched) |
| N3 | `api_auth_compatibility.rs` n3 arms (authority application admission preserved, public-groups invite gate) |
| N4 | `api_auth_compatibility.rs` n4 (OAuth-only: zero gateway-secret reads; production boot with missing gateway secret fails only the resigner contract) |
| N5 | `api_auth_compatibility.rs` n5 (four-provider chain, exactly one strict SELECT per request/verdict, zero transactions); the access-budget semantics of spec §9.1 pinned by `session_lifecycle.rs` |
| Downstream OCB factory/config extensibility | in-tree factory tests (`api_auth_registry.rs`, `conformance_api_auth_ports.rs`, `api_auth_wiring/tests.rs`); the internal OCB SDK leg is deliberately NOT RUN (§3.6) |

## 6. Delivery state

- Implementation Tasks 1–14 are complete at `77c1e7281`; this task adds no
  runtime features. Its commit contains: this validation document, the
  rollout/rollback runbook in `migrations/README.md`, the one-line EOF hygiene
  fix in `bcs-api-http/tests/friendship_routes/fixtures.rs`, and the
  singlebox-drill finding's repair — `ApiConfig::auth` gains
  `skip_serializing_if = "Option::is_none"` plus the permanent print-config
  round-trip regression test (§3.4) — a serializer-output correction of a
  Task 6/12 artifact (the printed default config carried an explicit
  `api.auth` null that the plan's own C3 validation rejects), proven
  failing-before/passing-after on the real e2e story binary.
- Not claimed: live MySQL, real provider smoke, OceanBase, OCB integration,
  production deployment, and singlebox gate passage (see §3.6 and §3.4).
