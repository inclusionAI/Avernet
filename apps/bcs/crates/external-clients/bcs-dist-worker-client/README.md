# Worker peer HTTP client

Implements DistWorkerPeerPort using member-record addresses. The composition root
supplies credentials for this exact instance and a bounded request timeout. No
internal authentication implementation is embedded in this public crate.

The client rejects mismatched source/target instances and credential-bearing or
ambiguous addresses, disables redirects, caps response bodies, and does not retry
mutations automatically. Network/protocol/error responses remain uncertain; the
runtime reconciles current durable ownership. This is not a historical receipt
service. Credential headers are never returned in RuntimeError.
