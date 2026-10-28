# Tasks

- [x] Freeze migration inputs and inspect public architecture/contract rules.
- [x] Migrate management API, service, repository and ordinary catalog port.
- [x] Migrate resource content Service API, HTTP adapters and named operations.
- [x] Initial migration used a default-closed session Plugin; superseded by the approved ordinary access-policy follow-up below.
- [x] Reuse sync HTTP transport and provide lifecycle-managed async pooling.
- [x] Move generic tests and add ownership, compatibility and cleanup guards.
- [x] Run final public contracts, architecture, HTTP flow and adjacent suites.
- [x] Review final public diff for accidental private material and behavior drift.
- [x] Open the original public PR; the user subsequently selected `REL20261029` as its target.

## Batch follow-up
- [x] Replace per-ID detail calls with one ordered, fail-closed batch.
- [x] Restore sync-context error code and assert its HTTP body.
- [x] Run public consumer/gates and downstream real-provider pairing.
- Delivery order: update this existing public PR first, verify the exact mirror object, then update the existing downstream PR; no merge or deployment.

## Reviewer follow-up
- [x] Record red tests for plugin absence, regular unique routing and provider-aware endpoint acceptance.
- [x] Replace session Plugin with an ordinary request access policy; preserve default denial and private credential rules.
- [x] Register management/content routers normally; retain profile-specific Bot alias policy.
- [x] Seed bottom boundaries only and prove provider mutations break acceptance.
- [x] Execute focused, boot/architecture, prior fixed ECB pairing and complete Backend gates; the latest full run has one unchanged artifact timeout failure, recorded in verification.md and not counted as green.
- [ ] Review the fixed diff, update original public PR, verify mirror, and update original corp PR preserving rollout changes.

## Release integration follow-up
- [x] Rebase on `REL20261029@0317937df`; retain risk APIs/flows and knowledge APIs/flows in both conflicting declarations.
- [x] Reuse phase-one normal resource routing and prove provider-aware HTTP composition and the unchanged source-size guard.
- [x] Record release-based complete Backend results and independent conflict review.
- [ ] Update the original public PR through normal hooks; downstream mirror/pointer integration remains separate.

## Bounded-query follow-up
- [x] Replace history-prefix state scans with exact, ordered composite-identity reads.
- [x] Forward provider pagination and load only current-page metadata.
- [x] Preserve the latest access-policy/resource-route simplification.
- [x] Focused knowledge, configured endpoint and composition regression: 363 passed, zero skips.
- [ ] Record the current complete Backend CI result and publish the bounded-query commit.
