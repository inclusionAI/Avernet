# App architecture decisions

Record durable app-scoped decisions with status, context, rationale, consequences, evidence and supersession links. Use the [shared ADR template](../../../../docs/agents/templates/adr.md) and the [app knowledge standard](../agents/knowledge-standard.md). Do not copy existing decisions or store task plans and unverified incident hypotheses here.

[Repository decisions](../../../../docs/adr/README.md) remain authoritative for decisions with system-wide scope. Before adding an app decision, check the relevant existing app contracts and repository ADRs; link them rather than moving or duplicating them. An index alone does not assert that an app-specific decision has been accepted.

## Existing repository decisions relevant to Backend and its integrations

These decisions remain at repository scope. Check each decision’s status and scope; inclusion here does not make every decision Backend-owned.

- [0001-stable-skill-identity-across-versions](../../../../docs/adr/0001-stable-skill-identity-across-versions.md)
- [0002-market-and-space-skills-track-latest](../../../../docs/adr/0002-market-and-space-skills-track-latest.md)
- [0003-skill-space-ownership-is-relational](../../../../docs/adr/0003-skill-space-ownership-is-relational.md)
- [0004-block-withdrawal-when-bots-reference-skill](../../../../docs/adr/0004-block-withdrawal-when-bots-reference-skill.md)
- [0005-separate-internal-version-ordinal-from-external-version-key](../../../../docs/adr/0005-separate-internal-version-ordinal-from-external-version-key.md)
- [0006-preserve-observable-skill-identities](../../../../docs/adr/0006-preserve-observable-skill-identities.md)
- [0007-separate-space-governance-from-skill-grants](../../../../docs/adr/0007-separate-space-governance-from-skill-grants.md)
- [0008-skill-md-is-the-metadata-source-of-truth](../../../../docs/adr/0008-skill-md-is-the-metadata-source-of-truth.md)
- [0009-separate-draft-attempt-version-and-propagation-state](../../../../docs/adr/0009-separate-draft-attempt-version-and-propagation-state.md)
- [0010-teamclaw-is-authority-for-managed-skills](../../../../docs/adr/0010-teamclaw-is-authority-for-managed-skills.md)
- [0011-dedicated-skill-draft-edit-lease-with-fencing](../../../../docs/adr/0011-dedicated-skill-draft-edit-lease-with-fencing.md)
- [0014-engine-owned-package-deletion](../../../../docs/adr/0014-engine-owned-package-deletion.md)
- [0014-separate-bot-mcp-overrides-from-user-config](../../../../docs/adr/0014-separate-bot-mcp-overrides-from-user-config.md)
- [0015-event-driven-desktop-skill-recovery](../../../../docs/adr/0015-event-driven-desktop-skill-recovery.md)
- [0015-merge-user-and-bot-mcp-headers-per-key](../../../../docs/adr/0015-merge-user-and-bot-mcp-headers-per-key.md)
- [0015-scope-default-mcp-exclusion-to-default-source](../../../../docs/adr/0015-scope-default-mcp-exclusion-to-default-source.md)
- [0016-mcp-url-and-explicit-headers-are-independent](../../../../docs/adr/0016-mcp-url-and-explicit-headers-are-independent.md)
- [0016-separate-skills-pool-admission-from-observation](../../../../docs/adr/0016-separate-skills-pool-admission-from-observation.md)
- [0017-preserve-recorded-local-skill-locators](../../../../docs/adr/0017-preserve-recorded-local-skill-locators.md)
- [0018-manifest-capability-full-replace-direct-claims](../../../../docs/adr/0018-manifest-capability-full-replace-direct-claims.md)
- [0019-bot-genome-is-the-unit-of-evolution](../../../../docs/adr/0019-bot-genome-is-the-unit-of-evolution.md)
- [0020-promotion-is-platform-owned](../../../../docs/adr/0020-promotion-is-platform-owned.md)
- [0021-evolution-control-plane-in-apps-evolution](../../../../docs/adr/0021-evolution-control-plane-in-apps-evolution.md)
- [0022-apply-engine-desired-state-and-observed-drift](../../../../docs/adr/0022-apply-engine-desired-state-and-observed-drift.md)
- [0023-bot-mcp-config-lifecycle](../../../../docs/adr/0023-bot-mcp-config-lifecycle.md)

## Related app decisions

- [BaaS architecture decisions](../../../baas/docs/adr/README.md): BaaS lifecycle, providers, platform assembly and public/enterprise split. These remain BaaS-owned decisions.
- [BCS app decisions](../../../bcs/docs/adr/README.md) and [BCSFuse app decisions](../../../bcsfuse/docs/adr/README.md).
