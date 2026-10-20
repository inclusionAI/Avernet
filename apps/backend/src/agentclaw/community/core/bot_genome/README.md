# Bot Genome Registry

The registry owns a bot's **Genome Revisions**: immutable, content-addressed
versions of its definition (persona files, skills, resources, MCP servers,
startup script, curated memory once RSI-05 lands) plus the owner's non-evolvable
**policy**. It records them, names them with **refs** that move only by
compare-and-swap, applies **patches** to them, diffs them, compiles them into
the complete desired-state document delivery consumes, and records
**promotions**. Design: `apps/backend/docs/bot-evolution/01-genome.md`;
format: `apps/backend/api-contracts/genome/v1/` (RSI-02); this
implementation: `apps/backend/specs/2026-10-19-bot-genome-registry/` (RSI-03).

## What a revision is, and where it comes from

A revision is the contract's JSON: `spec` (every category present, file
content by digest in the manifest content store) and `policy`, hashed
together into the id (`canonical.py`). Two ways to record one:

- **Capture** (`capture/`): read what the bot has now through the services
  the Manifest materialisers and the compose collector already read through,
  normalise it as README §7 says, and store every byte. The parent is the
  bot's `active` revision, or none for the first.
- **Patch** (`patch/`): apply a typed, itemized Genome Patch to its own
  `base`. Policy and `allowed_genes` are enforced structurally
  (`patch/policy.py`), each op maps to a risk tier (`patch/risk.py`), and the
  rewrite measure (`rewrite.py`) raises a tier when a file changed too much.

Nothing is recorded that apply would refuse: `spec_checks.py` runs the same
per-entry predicates the Manifest materialisers run before writing.

The policy is not bot state, so capture copies it from `active` and the
owner changes it with its own operation (`set_policy`).

## Delivery

A promotion (`promotion/`) moves `active` and `previous` in one
compare-and-swap, marks the revision `promoted`, records the promotion with
the compiled document and its digest, and hands the compiled revision to the
`RevisionApplier` seam. The only applier bound here (`PendingDeliveryApplier`)
refuses before any ref moves: direct delivery into the apply engine is
RSI-04. No Manifest document is written by anything in this package; the
`/config-manifest` endpoints keep working as before and are unrelated.

"Which revision does this bot run" joins the bot's last Manifest apply to the
promotion that started it; a mismatch, or a last apply nobody promoted, is
**drift**. Owners may promote on a drifted bot (that is how they reconcile);
automated actors may not.

## Storage

Five tables, `sql/2026_10_19_bot_genome.sql`: revisions (the hashed canonical
JSON, never updated except status and annotations), refs (one row per name,
CAS on the expected revision), an append-only ref event log, patches
(content-addressed; the digest is the candidate id) and promotions. Bytes
live in the manifest content store; produced bytes (captured files, files a
patch writes, uploads) go through its `store_produced` write path, and a
digest is readable for a bot only when a provenance row ties the two.

## Context Boundary

```yaml
purpose: Own a bot's Genome Revisions (immutable, content-addressed versions of its definition plus policy), the refs that name them, the patches that change them, diffs, the compiled desired-state document, and the promotion record; hand delivery to one seam.
provides:
  - GenomeRegistryProtocol  # the registry Service API (api/bot_genome_registry_service.py re-exports it)
  - GenomeRegistryService
  - GenomePromotionServiceProtocol  # the promotion Service API (api/bot_genome_promotion_service.py re-exports it)
  - GenomePromotionService
  - RevisionApplier  # the delivery seam RSI-04 binds its direct-delivery applier into
  - PendingDeliveryApplier
  - BotRef
  - Provenance
  - RunScope
  - GenomeRevision
  - RevisionMetadata
  - RecordedRevision
  - RevisionPage
  - GenomeRef
  - RefUpdate
  - RefMover
  - GenomeDiff
  - GeneChange
  - CompiledManifest
  - ContentReceipt
  - ApplyResult
  - Promotion
  - PromotionPage
  - PolicyRejected
  - GenomeError  # and its subclasses: RevisionNotFound, RefConflict, PatchRejected, CaptureRefused, LimitExceeded, MoverNotAllowed, ContentNotReachable, Drifted, NotPromotable, NotAppliable, PromotionUnsupported
  - Reason
  - canonicalize  # RFC 8785 subset, revision ids, digests (canonical.py)
  - revision_id
  - sha256_digest
  - tree_digest
  - rewrite_flagged  # the contract's rewrite measure (rewrite.py)
  - op_gene_path  # gene-path grammar helpers (gene_paths.py)
  - covers
  - overlaps
  - apply_patch  # patch application, policy and risk (patch/)
  - diff_specs
  - compile_spec
  - capture_spec
  - BotStateReader  # the narrow read port capture reads a bot through
  - PlatformBotStateReader
  - GenomeRevisionRepositoryProtocol  # declared in core.repository.protocols.bot.genome; ORM models here
  - GenomeRefRepositoryProtocol
  - GenomePatchRepositoryProtocol
  - GenomePromotionRepositoryProtocol
consumes:
  - "ManifestContentServiceProtocol (core.bot_config_manifest.content) — every byte a revision references lives in the manifest content store; produced bytes go through store_produced and reachability through is_reachable"
  - "canonical_tree_bytes / decode_tree_bytes (core.bot_config_manifest.apply.entry_delivery) — the acm-tree-v1 encoding a skill or directory resource is stored as"
  - "SkillPackageValidator (core.skill_center.skill_package) — a local skill's tree is what the validator keeps (README §4.2)"
  - "admission_refusal (core.resources.services.file_service) — every resource file a revision writes must pass the workspace admission rule"
  - "legal_identity_types (core.bot_config_manifest.schema.entries) — the persona types the bot's engine accepts"
  - "MCPAuthServiceProtocol (core.mcp) and MCPConfigServiceProtocol (core.mcp) — the MCP permission and bot-override checks apply runs, re-run at record time"
  - "placeholders.resolve (core.bot_config_manifest.schema.placeholders) — the ${BOT_*} substitution a captured script body gets, as apply would"
  - "IdentityFilePort (core.ports), BotCapabilityStateReaderProtocol, DirectActivationServiceProtocol, SkillServiceFactory local package storage (core.skill_center), ResourceService / ResourceFileService (core.resources, core.services), BotStartupScriptServiceProtocol (core.bot_startup_script) — what capture reads a bot through"
  - "BotConfigManifestApplyServiceProtocol.last_apply (core.bot_config_manifest) — the join applied_revision makes to find which promotion started the bot's last apply"
  - "the limits of core.bot_config_manifest.fetch.limits and schema.limits — mirrored, never redefined"
  - "the genome repository Protocols (core.repository) — persistence for the five ac_genome_* tables"
consumed_by:
  - "adapters/http/openapi_v1/bots/genome — the public /openapi/v1/bots/{bot_id}/genome surface"
  - "di/modules/bot_genome_module — binds the repositories, both services and the applier seam"
  - "RSI-04 (direct delivery) — binds a real RevisionApplier; RSI-08 (evolution runs) records candidates through GenomeRegistryProtocol"
internal_dependencies:
  - agentclaw.community.core.base
  - agentclaw.community.core.bot_config_manifest.apply.entry_delivery  # acm-tree-v1 encode/decode
  - agentclaw.community.core.bot_config_manifest.bot_config_manifest_apply_service_protocol  # last_apply, for applied_revision
  - agentclaw.community.core.bot_config_manifest.capabilities  # ManifestCapabilities, for the Manifest's MCP entry rules
  - agentclaw.community.core.bot_config_manifest.content  # the content store Service API and scope
  - agentclaw.community.core.bot_config_manifest.fetch.limits  # the fetch-time caps the contract mirrors
  - agentclaw.community.core.bot_config_manifest.schema  # limits, legal_identity_types, placeholders, the MCP entry rules
  - agentclaw.community.core.bot_startup_script  # the script body capture reads
  - agentclaw.community.core.mcp.mcp_auth_service_protocol  # the MCP permission check
  - agentclaw.community.core.mcp.mcp_config_service_protocol  # bot-override validation and the override capture reads
  - agentclaw.community.core.ports.identity_file_port  # the identity files capture reads
  - agentclaw.community.core.repository
  - agentclaw.community.core.resources.services.file_service  # admission_refusal
  - agentclaw.community.core.resources.resource_service_protocol  # the per-bot resource service factory capture lists rows through
  - agentclaw.community.core.services.identity  # _remove_reference_section, VALID_IDENTITY_FILES (lazy import; the module reaches the device dispatcher)
  - agentclaw.community.core.services.resource_file_service  # resource file bytes capture reads (TYPE_CHECKING)
  - agentclaw.community.core.skill_center.capability_state_contract  # the active skill set
  - agentclaw.community.core.skill_center.direct_activation_service_protocol  # installed MCP codes
  - agentclaw.community.core.skill_center.skill_service_factory_protocol  # local skill package storage by locator
  - agentclaw.community.core.skill_center.skill_package  # SkillPackageValidator
  - agentclaw.community.log
  - agentclaw.community.plugin_api.database
  - agentclaw.community.utils.avernet_tenant
  - agentclaw.community.utils.avernet_tenant_guard
  - agentclaw.community.utils.env_utils
```
