# Flow navigation

Start with [Publish Lifecycle](publish-lifecycle.md). Use these existing contracts and guides to trace the relevant flow. Verify the target version, implementation and tests before applying a document to a running system.

- [Bot, device and publish states](../adr/0004-lifecycle-state-machines.md)
- [Gated publish pipeline](../adr/0005-gated-publish-pipeline.md)
- [Sandbox provider selection](../adr/0006-paas-provider-abstraction.md)
- [BCN interaction transport](../adr/0012-sse-bcn-downlink-interactions.md)

For cross-app flows start at the [repository flow index](../../../../docs/flows/README.md). Add app-local flow pages here only when existing documents do not cover the needed boundary.

## Record and exclude

Record trigger, participants, state ownership, failure branches and evidence. Keep app-local implementation here and link cross-app flows; do not copy incident transcripts or credentials.

For the wider inventory of existing app documentation and domain context, see the [existing knowledge catalog](../agents/existing-knowledge.md). Check each entry's status and target version.
