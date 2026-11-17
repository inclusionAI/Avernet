# AIStudio training nodes in RunDetail

A training task is a node in the existing workflow, not a separate dashboard.
When a run contains an `aistudio-train` node, its row exposes training operations
and expands the AIStudio watch card directly below that row. Other executor
types keep their existing details. Read-only child-run previews retain
observation details but do not expose manual-query controls.

The card reads the recorded task ID, safe task URL, external status, watch
lifecycle and query plan from ClawWeb. Browser refreshes of this observation
snapshot do not themselves query AIStudio. While AIS is running the workflow
node remains waiting; downstream nodes are not presented as completed.

## Manual query semantics

The manual action persists a request for the owning ClawMind runtime. HTTP 202
means accepted, not queried or completed. The card stays pending and refreshes
until the engine has updated its observation. Repeated requests have a server-
enforced 60-second cooldown and never reset the automatic query schedule.
Failures, unknown external statuses, access denial and unavailable configuration
are displayed without inventing a terminal training outcome.

A definite terminal result must be applied by the owning workflow runtime.
Resolved watches stop querying and RunDetail refreshes its run/node snapshot.

## Host integration

Apply Web migrations 155/156 together with Engine migrations 41/42. Mount both
user-facing and authenticated internal storage/audit routes. Manual queries are
audited through an exact-ID, atomic marker-and-event transaction before their
mailbox is consumed; failed audit writes leave the request pending for retry. The public route factory requires a verified principal and exact
Bot/Owner access policy. Inject these through host composition; do not reuse an
unverified user header or decoded development cookie as authentication. A public
runner without those providers deliberately reports the feature unavailable.

The browser calls only same-origin ClawWeb endpoints. It never executes a6o,
chooses an engine URL, or carries AIStudio credentials. Submission configurations
and raw CLI error streams are not returned to the browser. The recorded task URL
is optional; the frontend does not build it from the record ID.

See `docs/specs/2026-11-16-aistudio-train/spec.md` for API, rollout and validation
requirements. A production rollout still requires actual host identity wiring,
managed-schema deployment and a test run against the installed a6o runtime.
