# Temporary chat attachment downloads

`TemporaryUrlPullClient` is the Plugin API consumed by Engine resource
materialization for chat images and other temporary-URL attachments.
`HttpTemporaryUrlPullClient` implements it. A shared-file URL may return a
redirect to object storage instead of returning the file body directly.

The HTTP implementation follows up to five 301, 302, 303, 307 or 308 responses.
It resolves relative `Location` values against the original URL for that hop,
not the pinned IP URL. Missing Locations, excess redirects and HTTPS-to-HTTP
transitions fail preparation. Initial HTTP URLs remain supported.

Every hop rejects userinfo and unsupported schemes, resolves DNS again,
rejects any non-public result, and connects to a validated IP while preserving
Host and TLS SNI. Each hop uses a fresh client without environment proxies,
so cookies and ambient proxy routing cannot bypass these controls. TLS still
uses the configured CA bundle (`SSL_CERT_FILE` / `SSL_CERT_DIR`); certificate
and hostname verification remain enabled. No prior
hop's credentials are forwarded. One deadline covers DNS, all redirects and
body streaming. The existing configured/request-specific byte limits apply
to the final response, followed by the service's content validation.

This extends accepted download responses without changing request schemas,
configuration or error events. Existing preparation failures still propagate
through the service. Consumers are the chat image and file materialization
paths; BCS and OpenClaw need no protocol changes. Deploy the Engine change to
activate it; no data migration is required. Reverting the implementation
restores rejection of redirects.

Regression coverage is in
`community/plugins/tests/test_resource_materialization.py`; service and
WebSocket attachment tests cover preparation results and error propagation.
