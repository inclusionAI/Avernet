# Provider slug validation — 2026-10-09

Branch: codex/provider-slug, based on 6c78364a1. Results below were recorded during
implementation. The subsequent commit/push/PR request explicitly skips new
verification and local Git hooks; no merge is requested.
Run Cargo commands from apps/bcs with
`CARGO_TARGET_DIR=/private/tmp/avernet-provider-slug-target`.
All dependencies were available offline; no private registry was used.

## Behavior and regression

| Command | Result |
| --- | --- |
| `cargo test --offline --no-fail-fast -p bcs-bot -p bcs-bot-store -p bcs-service-api -p bcs-protocol -p bcs-http -p bcs-app-bot -p bcs-admin` | Exit 0; 1,255 passed, 9 ignored, 0 failed |
| `cargo test --offline -p bcs --lib` | Exit 0; 309 passed, 5 ignored, 0 failed; includes 37 migration tests |
| `cargo test --offline -p bcs --test provider_slug_migration` | Exit 0; 1 passed |
| `cargo test --offline -p bcs-http --test provider_slug_contract` | Exit 0; 8 passed after review fix; also included in module regression |
| `git diff --check` | Exit 0 |

The expanded module/bootstrap commands initially encountered PermissionDenied
when existing tests bound localhost sockets. They were rerun outside the sandbox;
all tests above completed successfully. Existing ignored tests remain ignored.
Full logs are local scratch under /private/tmp/avernet-provider-slug-*.log.

New tests cover exact public response keys and all auth modes, protocol defaults,
slug validation, duplicates, rename, null/omission, disabled discovery, owner/admin
authentication, Memory/SQLite parity, SQL environment/index use, one-read lookup,
read/write failures and historical-row upgrade/repeatability.

## Independent review

A read-only reviewer found one P2 issue: invalid admin_callback_url validation
followed the slug write. Both focused regression tests were run and failed first;
they now pass, along with the full module suite. Core validates the callback and
merges its config before the single Provider insert/metadata update. A failed
registration does not reserve the slug on invalid callback, and a failed PATCH
preserves the complete original response. No other actionable findings.

## Architecture checks

- `bash scripts/ci/check-store-boundaries.sh`: exit 0.
- `bash scripts/ci/check-forbidden-symbols.sh`: exit 0.
- `bash scripts/ci/check-protocol-compat.sh`: exit 0.
- `bash scripts/ci/check-import-rules.sh`: exit 1. Its sorted output exactly
  matches the unchanged original dev checkout; no new import violations.
- `CARGO_NET_OFFLINE=true bash scripts/ci/check-conformance-entries.sh`: exit 1.
  Static failures decrease from 160 to 153 compared with the original checkout,
  with no new entries. Canonical Provider repository/core/application harnesses
  resolve 7 missing entries. The script also reported Cargo discovery failure;
  the extra `cargo test --offline --workspace --no-fail-fast -- --list` run and
  original-checkout comparison reached test enumeration but were stopped after
  prolonged execution (the explicit listing exited 143). Complete workspace
  discovery is therefore unverified. New Provider repository/core/application
  conformance suites actually ran and passed in the module regression above.

Full architecture CI is not claimed green. Gates and allowlists were unchanged.

## MySQL and deployment limits

Real MySQL validation could not run: Docker is installed but its daemon is not
running, and no disposable MySQL server/test URL is available. The existing
ignored full-chain test now checks migration 032, the new column/index, and the
same Provider repository harness used by Memory/SQLite. Its shared SQLite chain
path ran in bcs-admin regression. To validate on an **empty disposable database**:

```sh
cargo test --offline -p bcs-admin full_mysql_migration_chain_applies_and_preserves_history -- --ignored
```

Set BCS_TEST_MYSQL_URL through the test environment; do not commit credentials.
Full Singlebox E2E/coverage was not run; the required shared product stack and
MySQL service were not available in this session. These remain integration gates.

Apply MySQL 032 before deploying the new binary. SQLite startup applies 033.
Historical migration SQL/bodies/checksums were not modified; old rows retain null
slug. An older binary can coexist with the additive nullable column/index.

## Source file sizes

Every added/modified Rust source was counted. All new source files are below
1,000 lines. Existing oversized files remain intact per the user's explicit
instruction to defer splitting; this does not expand CI's allowlist.

| Existing source | Before → after |
| --- | --- |
| HTTP routes/providers.rs | 1,119 → 1,149 |
| HTTP tests/provider_routes_contract.rs | 3,363 → 3,370 |
| bcs-app-bot tests/v1_bot_service.rs | 1,554 → 1,556 |
| Bootstrap src/server.rs | 6,957 → 6,959 |
| Store src/provider.rs | 1,805 → 1,805 |
| Bot src/core/provider_core.rs | 1,330 → 1,398 |

The remaining changed sources are below 1,000 lines. Fixture-only additions in
the existing oversized tests/server add slug defaults; no unrelated refactor.

## PR #2564 CI repair

The first remote run found two feature defects: the live E2E story never called
`GET /providers/by-slug/{slug}`, and MySQL `ascii_bin` text was decoded as bytes
because the adapter treated `BINARY_FLAG` as proof of binary data. MySQL marks
binary-collation text with that flag too. The adapter now uses character-set ID
63 for character columns and preserves the existing non-character decoding.
Regression coverage includes binary-collation VARCHAR/TEXT, real binary payloads,
and text-protocol temporal values. The real MySQL contract exercises text and
prepared protocols, including unbound and bound queries.

Provider row parsing now propagates an unexpected slug conversion error instead
of silently returning a missing slug. The existing live operator story checks
public fields, missing/invalid slugs, conflicts, rename, and disable/re-enable.

A separate dev notification test assumed both failed targets always reached the
same notification tick. A controlled reproduction returned the valid first
notice, `Bot Driver 已离线`, before Observer failed. The test now controls the
single-batch subscriber start and separately verifies two offline notices across
ticks; production notification behavior is unchanged.

Focused validation completed before this follow-up commit: MySQL adapter 11
passed; Provider repository slug contracts 6 passed; notification integration
suite 4 passed. The byte-decoding, malformed-slug, and temporal regressions were
observed failing before their fixes. Broader affected-module and live-story
checks continue while the requested `--no-verify` push starts remote CI.

Real MySQL remains unavailable locally: the Docker CLI is installed, but no
running daemon or Docker app is available. CI runs the actual MySQL migration
chain and the extended driver conformance test. No migrations, CI thresholds,
hooks, or allowlists were weakened. Provider splitting remains deferred per the
user's instruction; the existing Provider store file remains above 1,000 lines.
