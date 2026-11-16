# Live collaboration repair acceptance

Run through `singlebox/ci/singlebox_coverage.sh`; these stories reuse its one
shared product stack and call the real owner-scoped Backend HTTP routes.

Ordinary public-local singlebox remains credentialless. For coverage only, the
existing guarded acceptance bootstrap installs `CollaborationRepairFixtureModule`
after the ownership fixture module. The runner registers an ephemeral provider
through BCS HTTP, stores its returned credentials in a private runtime file
identified by `SINGLEBOX_ACCEPTANCE_REPAIR_PROVIDER_FILE`, adds that provider ID
to the disposable BCS allowlist, and restarts BCS before acceptance. No provider
credential is checked in, printed, or added to the shipped default configuration.

The fixture lazily reads that file because Backend starts before provisioning.
It overrides only `CollaborationRepairPlugins`, using the production
`HttpCollaborationRepairPlugins`, a dedicated `BcnService` with real injected
HTTP transport, and the existing production principal minter. The globally
injected daily-sync configuration remains unchanged. Facts readers, projectors,
Core service, metadata adapter, ownership/managers HTTP plugins, and per-request
cleanup are production code; no test directly calls domain logic to inflate
coverage and no delivery response is simulated.

Stories prove:

- avatar registration, edits, explicit clears, and Group/Session readback;
- real repair restores saved name/avatar and confirms owner and
  manager roster, returns the three ordered outcomes, creates no Bot, and
  enqueues no work;
- empty/blank summaries return success after acknowledgement while retaining
  BCS's stored summary;
- a missing BCS target returns 404 after the real existence probe and is never
  created;
- an unreadable saved avatar returns 409 without clearing any remote profile
  or changing authority state;
- a non-owner receives the masked 404 before repair execution.

The normal credentialless 503 capability boundary remains covered by the
composition and HTTP adapter unit suites; it is not mislabeled as a successful
live repair story.
