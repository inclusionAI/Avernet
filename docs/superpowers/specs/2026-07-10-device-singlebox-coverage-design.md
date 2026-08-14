# Device Singlebox Coverage Design

## Goal

Improve the Avernet Backend `devices` module with meaningful live singlebox
coverage. The source of truth is the artifact produced by
`scripts/ci/singlebox_coverage.sh`, not in-process E2E coverage or manually
declared evidence.

## Profile / Env Prerequisite

This work is stacked on the Profile/Env separation in PR #93 and the singlebox
BaaS provider assembly in PR #98. Singlebox is a `DeployProfile`, persisted
Device Env remains `dev`, and the persisted provider is `baas`. This PR must not
add an HTTP Schema alias or otherwise teach Device Core that `singlebox` is an
Env.

## Baseline

- Development base: PR #98 (`codex/singlebox-baas-device-provider`) until the
  stacked PR train merges into `dev`.
- The coverage script starts the real standalone product stack and emits
  `backend-coverage.json`, runtime Router hits, runtime Plugin hits, JUnit, and
  HTML reports.
- The current devices acceptance suite exercises three read routes and one
  repeated no-data baseline. It does not exercise the BaaS-backed device
  lifecycle.
- The measured devices-only live baseline is Core `1243/3240 = 38.36%` and
  Router `3/18 = 16.67%`.
- The device router currently exposes 18 Router APIs. The existing live suite
  reaches three unique Router APIs.
- Device-owned Plugin API coverage is not currently declared. Service or Core
  methods must not be relabeled as Plugin APIs merely to produce a percentage.

## User Stories

### 1. Device query contracts

Keep the existing fresh-state behavior:

- list devices for the current user;
- read a missing binding;
- read a missing device ID;
- preserve the committed no-data response baseline.

### 2. BaaS-backed device lifecycle

Create a real personal bot through the public Backend API so Backend allocates
its BaaS-provider device while the full Backend/BaaS stack is running. From the
returned bot and binding:

- read the binding by binding ID and device ID;
- observe it in the user's device list;
- obtain connection information;
- verify binding-level connection succeeds through BaaS;
- make current local-BaaS gaps explicit: bot-id routes have no successful
  publish record, instance inventory is empty, restart cannot target an
  instance, and reapply lacks the required `bot_type` input;
- release the binding and verify its state is `RELEASED`.

Assertions must validate returned identity, ownership, provider, lifecycle
state, and physical/runtime facts where available. HTTP 200 alone is not a
valid assertion. The test must not seed records or manufacture instance state
to turn the current local-BaaS capability gaps into synthetic success paths.

### 3. Ownership and failure behavior

Exercise user-relevant guards without mocking Core services:

- another user cannot read the owner's binding;
- another user cannot restart the owner's instance;
- connection, instance, release, and restart requests for missing bindings
  return their documented error contracts.

## Coverage Metrics

All metrics are derived after the real singlebox process exits and flushes its
runtime artifacts.

### Core Line

- Denominator: executable statements under
  `src/agentclaw/community/core/devices/**` in `backend-coverage.json`.
- Numerator: statements in that same path executed by the live singlebox
  process while running the devices acceptance target.
- First milestone: at least 43.36%, five percentage points above the measured
  38.36% devices-only baseline.

### Router API

- Denominator: the 18 FastAPI operations declared by
  `adapters/http/devices/router.py`.
- Numerator: distinct matching keys in the live `router_hits.jsonl` artifact.
- First milestone: at least 8/18 operations, or 44.44%.

Admin-only, callback, and multi-instance operations remain in the denominator.
They are honest future gaps rather than exclusions.

### Plugin API

The value is `not_applicable` until a device-owned protocol in
`agentclaw.community.plugin_api` is both:

1. semantically part of the device lifecycle user story; and
2. observed from executed implementation lines in `backend-coverage.json`.

No Core, Router, or service method may be counted as a Plugin API. Adding a
real device Plugin API denominator later requires a separate reviewed manifest
change.

## Reporting Design

Add a small module reporter used by `singlebox_coverage.sh` after it combines
Backend coverage:

- read `backend-coverage.json`;
- read and deduplicate runtime Router hit JSONL files;
- derive Plugin API evidence from coverage.py executed lines and checked-in AST
  symbols;
- read a checked-in module manifest containing device Core paths and Router
  operation keys;
- write module metrics into `summary.json` and render the same values in
  `summary.md` and `dashboard.html`.

The default CI target remains unchanged. A device run selects
`tests/community/acceptance/devices` through the coverage script's acceptance
target option/environment variable. Reports always record the exact target.

## Architecture Constraints

- Do not import coverage helpers from `core/`, ordinary Router handlers, or
  business services.
- Router hits continue to come from the FastAPI middleware.
- Plugin API manifest items identify a real Plugin Protocol method plus an
  implementation `path` and `Class.method` symbol under
  `agentclaw/community/plugins/`; the reporter derives hits offline from
  `backend-coverage.json`.
- Do not introduce `exclude_paths` or remove difficult APIs from denominators.
- Do not seed database rows as a substitute for the real BaaS-backed lifecycle
  when the product stack can create the state itself.
- Keep the existing generic flow/e2e/acceptance organization. Live-only
  lifecycle details belong under `tests/community/acceptance/devices/`.

## Failure Handling

- A failed lifecycle assertion fails pytest and therefore the coverage gate.
- Missing coverage, Router-hit, JUnit, summary, or dashboard artifacts fail the
  coverage script.
- Cleanup always stops the isolated standalone stack and preserves diagnostic
  logs produced under the selected coverage root.
- A runtime capability that is unavailable must fail with evidence; tests must
  not silently skip it after the first successful run.

## Verification

1. Run existing device in-process E2E tests as a fast regression check.
2. Run reporter unit tests with synthetic coverage and hit artifacts.
3. Run `scripts/test_singlebox_coverage_gate.sh`.
4. Run real `scripts/ci/singlebox_coverage.sh` with the devices acceptance
   target and an isolated coverage root.
5. Verify JUnit has executed tests with zero failures and zero skips.
6. Verify the generated device Core and Router metrics meet the milestones and
   the dashboard displays the same values as `summary.json`.
