---
status: in-progress
date: 2026-11-20
owner: BCS distributed coordination
---

# Retained membership verification

Public baseline: `051f08bd6963b9dc1a5ca2e6ae7a59c01d0edbc4`, on GitHub dev
`a2433cbf2bafa4c38fbff78b226d61701adcd5a2`. The companion provider is independently
owned and validated; public CI does not execute an enterprise provider.

## Local evidence

- RED: timeout-before-termination renewal and 30-day collection failed against
  the earlier model (11 original tests passed, 2 new tests failed).
- RED: losing the terminal decision reply originally returned Terminated; the
  regression now requires OutcomeUnknown and a subsequent confirmed read.
- GREEN: 14 coordination primitive tests and three negative conformance tests.
- Regression: `cargo +1.94.0 test --manifest-path apps/bcs/Cargo.toml
  -p bcs-dist-store -p bcs-dist-worker -p bcs-test-support --offline` passed,
  including doc tests (the documented opt-in example remains ignored).
- No local Singlebox, live service or performance run was performed for this change.

## Publication

[Avernet PR #4245](https://github.com/inclusionAI/Avernet/pull/4245) carries the
public contract/model change. Required GitHub Unit Tests and Singlebox Coverage
will be checked on the new published head; earlier-head green runs are not new
verification evidence. Publication does not imply release, integration or deployment.

## CI follow-up

At public head `1f9f3fd772`, [Singlebox run 44573792889](https://github.com/inclusionAI/Avernet/actions/runs/44573792889)
failed in the retained Dispatch-obligation assertion. The companion's `due` probe
shares the supervisor's durable scan cursor: a first end-of-round can cover only
the remaining suffix, so it cannot prove absence. The probe now crosses that
boundary and checks a complete subsequent round before returning false. It keeps
the same 64-poll bound and production Store/Supervisor behavior. No business
assertion, coverage threshold or exclusion was relaxed. Verification of the fix
uses a remote Singlebox rerun; no local Singlebox was executed.

[Unit Tests run 44573792866](https://github.com/inclusionAI/Avernet/actions/runs/44573792866)
passed on `1f9f3fd772`, including the BCS coverage and MySQL conformance steps.
The corrected companion passed `cargo +1.94.0 check -p bcs --features
worker-runtime-e2e --bin bcs-worker-e2e --locked --offline` with the public manifest.
The follow-up head still requires its own remote results.
