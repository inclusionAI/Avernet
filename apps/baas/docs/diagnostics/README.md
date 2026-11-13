# Diagnostic navigation

Start with [Publish Failures](publish-failures.md) when applicable. Start with the symptom and preserve the target revision, environment and evidence. These routes identify investigation starting points, not proven root causes.

| Symptom | First evidence to distinguish | Existing guidance |
| --- | --- | --- |
| Unexpected provider or plugin | Compare the selected plugin configuration with the registered SPI implementation and bootstrap wiring. | [Reference](../adr/0003-spi-plugin-backend-selection.md) |
| Bot, device or publish state differs | Compare the observed state and transition with the applicable contract and tests; do not infer a historical transition from the current state. | [Reference](../adr/0004-lifecycle-state-machines.md) |
| BCN interaction or SSE fails | Identify the request, interaction and transport stage before inspecting the associated source and tests. | [Reference](../adr/0012-sse-bcn-downlink-interactions.md) |

Keep credentials, private runtime data and raw sessions out of repository knowledge. Add a topic page only when a supported, reusable diagnosis is missing.

## Record and exclude

Record symptoms, competing causes, distinguishing evidence, next actions and limits. Do not store secrets, raw private incident records or unverified conclusions.

For the wider inventory of existing app documentation and domain context, see the [existing knowledge catalog](../agents/existing-knowledge.md). Check each entry's status and target version.
