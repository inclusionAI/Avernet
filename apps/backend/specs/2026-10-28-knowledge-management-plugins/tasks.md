# Tasks

- [x] Freeze migration inputs and inspect public architecture/contract rules.
- [x] Migrate management API, service, repository and ordinary catalog port.
- [x] Migrate resource content Service API, HTTP adapters and named operations.
- [x] Keep deployment sessions behind a default-closed KnowledgeSessionPlugin.
- [x] Reuse sync HTTP transport and provide lifecycle-managed async pooling.
- [x] Move generic tests and add ownership, compatibility and cleanup guards.
- [x] Run final public contracts, architecture, HTTP flow and adjacent suites.
- [x] Review final public diff for accidental private material and behavior drift.
- [x] Push through normal hooks and open a development-target pull request.

## Batch follow-up
- [x] Replace per-ID detail calls with one ordered, fail-closed batch.
- [x] Restore sync-context error code and assert its HTTP body.
- [x] Run public consumer/gates and downstream real-provider pairing.
- Delivery order: update this existing public PR first, verify the exact mirror object, then update the existing downstream PR; no merge or deployment.
