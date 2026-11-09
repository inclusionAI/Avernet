# Diagnostic navigation

Start with [Session Delete](session-delete.md) when applicable. Start with the symptom and preserve the target revision, environment and evidence. These routes identify investigation starting points, not proven root causes.

| Symptom | First evidence to distinguish | Existing guidance |
| --- | --- | --- |
| Session deletion fails or differs across clients | Compare the caller and request with the protocol compatibility and conformance sections. | [Reference](../session-delete-protocol.md) |
| Authentication or principal mismatch | Determine the authentication path and compare its inputs with the applicable contract; inspect the gateway principal contract for gateway calls. | [Reference](../../api-contracts/v1/authentication/contract.md) |
| Service or provider selection is unclear | Trace the adapter, service API and selected provider through the documented crate boundaries and tests. | [Reference](../../README.md) |

Keep credentials, private runtime data and raw sessions out of repository knowledge. Add a topic page only when a supported, reusable diagnosis is missing.

## Record and exclude

Record symptoms, competing causes, distinguishing evidence, next actions and limits. Do not store secrets, raw private incident records or unverified conclusions.

For the wider inventory of existing app documentation and domain context, see the [existing knowledge catalog](../agents/existing-knowledge.md). Check each entry's status and target version.
