# Worker control HTTP adapter

The three POST routes `/internal/bcn/worker/handoff`, `/source-done` and `/wake`
(the latter two share the same `/internal/bcn/worker` prefix) call only the
Worker application service. Request bodies are bounded to 16 KiB. No caller body
or header is treated as identity. Trusted deployment authentication must bind
both node and incarnation and install `Extension<Instance>`; missing identity
returns 401. Source and target identities are checked by the application/runtime.

This router is not automatically mounted. Possession of an ordinary service key
or Worker role alone is insufficient to install an instance identity. The host
provides that binding; deployment must remain disabled without it. Snapshot and
drain admin endpoints are not exposed here.

The external client lives in `bcs-dist-worker-client`, separate from the inbound
adapter. Loopback integration tests exercise its actual requests against this
router. Transport errors are not proof that the handoff was not committed.
