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
content by digest in the content store) and `policy`, hashed
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

A promotion (`promotion/`) compiles the revision into the apply engine's
typed desired state (`compile.py`) to check it, asks the delivery seam
whether it can take the bot (`RevisionApplier.preflight`), files the
promotion `pending`, and starts the delivery: `DirectApplier` starts an apply
run whose state reference is `StateRef("genome", promotion_id)`, under the
bot's apply lock (a busy bot is refused, `ApplyBusy`, with nothing moved).
The row turns `applying` with the apply id. In the worker, `GenomeCompiler`
maps the promotion to its revision and compiles it (no status check: the
compile is pure, so a re-run after a crash compiles the same state), and the
apply engine applies it.

The refs move when that apply ends, not before. The apply engine publishes
every apply's end to the subscriber registered for its state kind
(`core/apply_engine/completion.py`); the genome's is `GenomeCompletionHandler`
over `PromotionSettler` (`promotion/settle.py`), which in one transaction moves
`active` (and `previous`) by compare-and-swap against the promotion's
`from_revision`, marks the revision `promoted` and gives the row its final
status: `applied`, `applied_partial` (the refs move and `active` is labelled
`partial`), or `apply_failed` (nothing moves; also when `active` moved while
the apply ran). An automated actor's apply runs with the bot owner as its
principal; the row records the actor. A revision, like an apply, speaks only
for the bot's Direct skills and MCP servers: capture leaves out what the bot's
skill sets supply (the platform's default MCP servers included), and the apply
neither counts nor removes it. A Center skill is delivered by name and a
shared-repository skill by path (`SharedSkillPort`). Capture reads Direct Local
Skill packages through the Skill factory using the recorded locator without its
`local://` scheme; resolution and package reads retain the factory's Bot-scoped
path validation. An invalid or unreadable package refuses capture and drift
observation rather than silently omitting the skill. No Manifest document is
written by anything in this package; the `/config-manifest` endpoints keep
working as before and are unrelated. Promoting a service bot's verify or
online stage through the publish pipeline is inclusionAI/Avernet#3212.

"Which revision does this bot run" is observed, never recorded:
`GenomeRegistryProtocol.drift` captures the bot's state as `capture` would,
computes the revision id it would record under `active`'s policy and compares
it with `active`, both specs put in one representation first (a directory
resource is one tree in a revision but its member files, one row each, on the
applied bot). A promotion by an automated actor asks first and is
refused (`Drifted`) while the bot holds something else; an owner's is how the
owner reconciles.

## Storage

Five tables, `sql/2026_10_19_bot_genome.sql`: revisions (the hashed canonical
JSON, never updated except status and annotations), refs (one row per name,
CAS on the expected revision), an append-only ref event log, patches
(content-addressed; the digest is the candidate id) and promotions. Bytes
live in the content store (`core/content_store/`); produced bytes (captured files, files a
patch writes, uploads) go through its `store_produced` write path, and a
digest is readable for a bot only when a provenance row ties the two.

## Context Boundary

```yaml
purpose: Own a bot's Genome Revisions (immutable, content-addressed versions of its definition plus policy), the refs that name them, the patches that change them, diffs, the revision's compiled desired state and the drift observed against it, and the promotion record; hand delivery to one seam.
provides:
  - GenomeRegistryProtocol  # the registry Service API (api/bot_genome_registry_service.py re-exports it)
  - GenomeRegistryService
  - GenomePromotionServiceProtocol  # the promotion Service API (api/bot_genome_promotion_service.py re-exports it)
  - GenomePromotionService
  - RevisionApplier  # the delivery seam
  - DirectApplier  # its binding: a promotion's revision delivered as an apply run (RSI-04)
  - PromotionSettler  # a promotion's final status, and the refs it moves, once its apply ends
  - GenomeCompletionHandler  # the genome row of the apply run service's completion map
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
  - GenomeCompiler  # the DesiredStateCompiler for the "genome" kind (compile.py); compile_spec renders a spec
  - Drift
  - ContentReceipt
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
  - "ContentStoreProtocol (core.content_store) — every byte a revision references lives in the content store; produced bytes go through store_produced and reachability through is_reachable"
  - "canonical_tree_bytes / decode_tree_bytes (core.content_store) — the acm-tree-v1 encoding a skill or directory resource is stored as; MAX_AUDIT_ACTOR_CHARS sizes the actor columns"
  - "SkillPackageValidator (core.skill_center.skill_package) — a local skill's tree is what the validator keeps (README §4.2)"
  - "admission_refusal (core.resources.services.file_service) — every resource file a revision writes must pass the workspace admission rule"
  - "legal_identity_types (core.apply_engine.identity_types) — the persona types the bot's engine accepts"
  - "MCPAuthServiceProtocol (core.mcp) and MCPConfigServiceProtocol (core.mcp) — the MCP permission and bot-override checks apply runs, re-run at record time"
  - "placeholders.resolve (core.bot_config_manifest.schema.placeholders) — the ${BOT_*} substitution a captured script body gets, as apply would"
  - "IdentityFilePort (core.ports), BotCapabilityStateReaderProtocol, DirectActivationServiceProtocol, SkillServiceFactory local package storage (core.skill_center), ResourceService / ResourceFileService (core.resources, core.services), BotStartupScriptServiceProtocol (core.bot_startup_script) — what capture reads a bot through"
  - "the limits of core.bot_config_manifest.fetch.limits and schema.limits — mirrored, never redefined"
  - "the genome repository Protocols (core.repository) — persistence for the five ac_genome_* tables"
  - "ApplyRunServiceProtocol (core.apply_engine.run) — DirectApplier starts the apply run that delivers a promotion; the run service publishes its end to GenomeCompletionHandler"
consumed_by:
  - "adapters/http/openapi_v1/bots/genome — the public /openapi/v1/bots/{bot_id}/genome surface"
  - "di/modules/bot_genome_module — binds the repositories, both services and the applier seam"
  - "core.apply_engine — compiles a promotion's revision through GenomeCompiler and tells GenomeCompletionHandler when its apply ends"
  - "RSI-08 (evolution runs) — records candidates through GenomeRegistryProtocol"
internal_dependencies:
  - agentclaw.community.core.base
  - agentclaw.community.core.workspace.protected_entries  # the one list of workspace entries that are never resources (spec_checks refuses them)
  - agentclaw.community.core.apply_engine.compiler  # Compiled, StateRef — the genome is one of the run service's compilers, under GENOME_STATE_KIND
  - agentclaw.community.core.apply_engine.completion  # ApplyCompletionHandler, ApplyOutcome — the genome subscribes to its applies' ends to settle promotions
  - agentclaw.community.core.apply_engine.outcomes  # ApplyStatus, read off an apply's outcome when settling
  - agentclaw.community.core.apply_engine.run.service_protocol  # ApplyRunServiceProtocol — DirectApplier starts the apply run that delivers a promotion
  - agentclaw.community.core.apply_engine.constructs  # the construct vocabulary a creation half narrows a state by
  - agentclaw.community.core.apply_engine.context  # ApplyContext, the run the genome compiler compiles for (typing only)
  - agentclaw.community.core.apply_engine.desired_state  # the DesiredState a revision compiles to
  - agentclaw.community.core.apply_engine.identity_types  # legal_identity_types — the persona types the bot's engine accepts
  - agentclaw.community.core.apply_engine.capabilities  # Capabilities, for the Manifest's MCP entry rules
  - agentclaw.community.core.bot_config_manifest.fetch.limits  # the fetch-time caps the contract mirrors
  - agentclaw.community.core.bot_config_manifest.schema  # limits, legal_identity_types, placeholders, the MCP entry rules
  - agentclaw.community.core.bot_startup_script  # the script body capture reads
  - agentclaw.community.core.content_store  # the store Service API and scope, DIGEST_RE, acm-tree-v1 encode/decode, MAX_AUDIT_ACTOR_CHARS
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

### Read-only HTTP drift observation

`GET /openapi/v1/bots/{bot_id}/genome/drift` exposes `GenomeRegistryProtocol.drift`
through the existing MEMBER and addressed-Bot application-delegation gates.
The success envelope contains `data.drift`, either `{active, observed}` or null.
Null includes an absent active baseline; consumers requiring a parent must also
resolve active. Observation failures propagate; they are not "no drift".
This operation records no content/revision, moves no refs and starts no apply.
It is an additive Service API delivery operation for isolated Evolution providers.

For an added or removed local Skill package, `diff` includes the package and its
member paths, with bounded UTF-8 text hunks against an empty file; binary or
oversized member diffs are null. This is a read-only review projection; the
package operation retains its existing risk tier and policy checks.

## Frozen Run mutation authorization

`RunScope.mutation_scope` optionally carries the shared `FrozenMutationScope`
(`avernet_genome.mutation_scope`). This is Run authorization metadata, outside
Genome/patch content hashes. `None` preserves legacy `allowed_genes` behavior;
a present empty scope grants nothing. Scope is frozen by Evolution from strategy
capability, Binding, existing parent inventory/ownership and policy, not inferred
by Registry from a strategy name.

Registry independently checks the patch's base spec: only existing Persona and
local-origin Skill updates are permitted, with **every** `skill.update` member
checked against allowed targets and pins. Whole-Skill add/remove and other gene
operations refuse. The existing shape, evidence, policy and result-spec checks
still apply before content/revision writes. Scope does not grant publication,
activation, ref movement or Apply. The built-in callback verifies a signed scope
digest as well as current operator access; a caller cannot drop/expand scope.

See the [Run contract](../../../../../../evolution/src/avernet_evolution/core/evolution_run/README.md#frozen-mutation-scope),
[Registry rejection tests](../../../../../tests/community/core/bot_genome/test_mutation_scope.py)
and [three-scope cross-service tests](../../../../../tests/community/adapters/http/openapi_v1/test_evolution_mutation_scope.py).
The latter uses synthetic snapshots/model/file assertions and manual review,
not real Bot quality or a deployed Worker.

## Explicit human RSI promotion

[ADR 0028](../../../../../../../docs/adr/0028-human-promotion-authorization-is-not-evaluation.md)
adds `promote_manual` for an exact strategy-run candidate, not an evaluation pass.
Backend validates the immutable authorization and candidate provenance, current
ADMIN operator, live drift and frozen parent, then uses the existing DirectApplier.
The authorization ID claims the delivery once; replay returns that promotion and
never restarts Apply. `authorization` is immutable JSON, NULL for normal promotions.
Fresh schema creation already includes this nullable column. Existing databases
must back up and check column presence before applying
[`sql/2026_11_26_manual_promotion.sql`](sql/2026_11_26_manual_promotion.sql) once;
ORM `create_all` does not upgrade existing tables. The additive standard SQL is
intended for MySQL/PostgreSQL/SQLite; the regression test executes it on an
existing SQLite table and preserves old rows/NULL values. Actual MySQL and
PostgreSQL migration execution remains deployment validation, not test evidence.
Do not rerun the migration on a schema that already has the column.
The settler permits this authorized candidate to become promoted only after Apply
success/partial, retaining CAS and original verdict. `applied_partial` advances refs
but retains incomplete-delivery evidence; `apply_failed` never advances refs.
Tests: `tests/community/core/bot_genome/test_manual_promotion.py` and the signed
HTTP test `tests/community/adapters/http/openapi_v1/test_evolution_manual_promotion.py`.
