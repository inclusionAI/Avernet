# Worker peer HTTP client

Implements DistWorkerPeerPort using member-record addresses. The composition root
supplies headers for this exact instance and a bounded request timeout. The
receiver authenticates them or accepts them only from a mutually trusted Worker
network. The client connects directly to member addresses, bypassing proxies.

The client rejects mismatched source/target instances and credential-bearing or
ambiguous addresses, disables redirects, caps response bodies, and does not retry
mutations automatically. Network/protocol/error responses remain uncertain; the
runtime reconciles current durable ownership. This is not a historical receipt
service. Credential headers are never returned in RuntimeError.
