# ClawWeb rolling-release safety

## Contract and scope

The public runner and internal Host share the frontend/static-delivery and shutdown
contracts in `@avernet/clawweb/server/*`. Internal packaging lives in OCB, not in
this repository. Merge the public change before building the corresponding OCB
Host against it. A successful HTTP health response is not proof of browser boot.

- `/assets/*` is a file namespace, not a SPA route. Missing assets return 404
  with `Cache-Control: no-store`, never successful index HTML.
- Existing build assets are immutable: never replace content at an existing URL.
- HTML (`/`, `/index.html`, SPA routes) must revalidate before reuse.
- Startup failure UI must exist outside the entry module and outside React's
  root. React error boundaries cannot catch an entry that never loaded.
- A bootstrap returns one lifecycle; the CLI owns signals and process exit.
  Its `stop()` drains HTTP before closing dependencies. Failed shutdown exits
  unsuccessfully; simultaneous signals do not invoke stop twice.

These are delivery/bootstrap changes, not domain, database schema, Plugin API,
Service API authentication, or workflow execution policy changes. Existing
bootstrap implementations returning void remain compatible, but cannot promise
asynchronous dependency cleanup.

## Cross-version availability is a separate invariant

Correct 404 responses and HTML revalidation do **not** restore missing modules.
Before B HTML is visible, every path that can receive B's asset requests must
already have B's full assets. Retain A's complete dependency set through the
supported old-page and rollback windows. Merely bundling A's files into B leaves
B HTML -> A instance broken. Old tabs can request lazy modules after rollout.

The OCB implementation supports an explicitly provisioned shared filesystem
asset store. It is disabled unless configured; it does not provision cloud
storage or change identity policy. Follow its two-stage adoption runbook before
claiming cross-instance retention is enabled. Do not use unrelated business-data
FUSE mounts as an implicit static-publishing service.

## Validation

Test static delivery with real HTTP responses, including MIME, HTML cache headers,
missing resource behavior and unknown API paths. Test shutdown with an in-flight
request, a shutdown deadline and rejected cleanup. Test startup failure before
entry execution as well as lazy import failure after mount.

Release acceptance additionally requires A HTML -> B asset service and B HTML -> A
asset service, old tabs that first visit a lazy page after rollout, and rollback.
Those deployment tests require the actual load-balancer/storage topology; unit
and composition tests do not certify that topology.
