# Worker control HTTP adapter

The three POST routes `/internal/bcn/worker/handoff`, `/source-done` and `/wake`
(the latter two share the same `/internal/bcn/worker` prefix) call only the
Worker application service. Request bodies are bounded to 16 KiB. Host
middleware binds both node and incarnation and installs `Extension<Instance>`;
missing identity returns 401. Source and target are checked by the
application/runtime.

The host may authenticate the exact instance or accept a declared instance on a
listener restricted to mutually trusted Workers. With the latter policy every
caller with listener access can assert any instance, so a shared or public
listener requires instance authentication. The router requires an explicit
mount and an instance binding. Snapshot and drain admin endpoints are private.

The external client lives in `bcs-dist-worker-client`, separate from the inbound
adapter. Loopback integration tests exercise its actual requests against this
router. Transport errors are not proof that the handoff was not committed.
