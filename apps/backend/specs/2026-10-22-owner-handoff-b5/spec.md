# B5 — Backend addressing and AgentPass handoff preparation

## Scope and safety boundary

Build on deployed B0–B4. Keep service/personal/default behavior, public HTTP
parameter names, response shapes, Frontend and SecBaaS unchanged. No transfer
endpoint, automatic Owner update, new digital-employee UI, DDL or data rewrite.
Only non-default service Bots are eligible for a future handoff.

The accepted execution rule is: no ACTIVE independent binding means execution
follows the current Owner; an ACTIVE independent binding (including STAFF)
remains independent. An Owner handoff must explicitly reissue the former case
for the new Owner; ordinary apply's empty executionWorkno fallback is retained,
but cannot itself replace an already valid old credential.

## Implemented boundaries

| Area | Change | Preserved identity |
| --- | --- | --- |
| App Grant persistence | Canonicalize complete owner/entity locators to stable entity before grants, probes, listings and revocations; retain physical column names | user_id remains the actual delegator; log facts are not reassigned |
| App Grant app listing | Exact entity-addressed batch Owner projection; filter_live_bots retains current-Owner semantics | Live collaborator/Owner permissions are still checked separately |
| App Grant management and views | Resolve either locator once before permission checks; carry the immutable context to resource operations; derive current Owner separately for owner-only lists and HTTP display | The core owner_id locator and actual delegator never change meaning |
| Collaboration collection | Page the existing relation bot_pk targets, applying app entity scope before pagination and excluding the current Owner | Historical collaborator owner snapshots remain audit facts |
| App Grant deletion | Final sweep can resolve a soft-deleted Bot's unique stable entity; conflicting historical entities fail closed | Does not guess across Bots or rewrite historical grants |
| OpenAPI app admission | Preserve request locator after the repository validates the grant, so downstream locator consistency checks accept current Owner as well as entity | Grant existence never establishes live user authority |
| OpenAPI member-management lookup | Resolve Bot before evaluating capability and lock policy | Current Bot metadata drives permissions |
| Legacy resource files/search/ZIP | Common _resolve_params uses entity for storage and current Owner for engine metadata lookup | Current caller and audit actor unchanged |
| Legacy preview | Resolve and authorize Bot before reading stable-addressed files; omitted-locator collaborator lookup requires a unique Bot | default keeps existing own-user fallback; ambiguity is 409 |
| Execution identity | Read-only plan_owner_handoff by bot_pk; reject personal/default, empty Owner/entity and concurrent pending identity change | ACTIVE independent executor stays; otherwise plan new Owner as executor |
| Activation | Require ISSUED status and expected credential_id when available before runtime injection | No PENDING/old credential activation |
| OCB production Passport | Resolve persisted Bot before apply/update/query/destroy/status/subresource RPC, sending stable entityId and current ownerWorkno; keep explicit reissue targets | ACTIVE independent executor retained; no binding leaves executionWorkno absent |
| OCB Caller exchange | Add stable Bot entity in production issuer composition | Real Caller token and delegating identity unchanged |
| tcauthmng reissue | Set confirmationMode=SKIP_CONFIRM_IF_ALLOWED on AAC reissue | Actual response status still governs; PENDING is not completion |

App Grant canonicalization is resource addressing, not authorization. Creation
and detached records preserve their explicitly supplied old address when no Bot
exists. Database failures and ambiguity are not fallback cases. Complete grant
pairs are never expanded using another person's Owner ID. Entity liveness is
exact, with existing tenant/env ORM restrictions.

Live grant records carry an internal, non-persisted current_owner_id projection.
HTTP authorization responses continue to display the current personnel Owner;
they do not relabel the entity as a person. No current_owner_id database column
or new HTTP response field is introduced.

The new BotRepository batch methods and execution plan are internal contracts;
no HTTP DTO or PassportPlugin method signatures change. Existing OCB Passport
DTO import paths are re-exported after moving Hessian DTOs to passport_dtos.py;
Java class names and legacy constructor shapes are retained. Production DI now
provides BotRepository and the existing execution binding repository explicitly.
Narrow DI tests must bind these dependencies rather than instantiate Protocols.

## Execution handoff lifecycle for the next transaction stage

1. Resolve Bot by stable PK, authorize the real actor, and acquire the future
   transfer operation's concurrency guard; revalidate the plan under that guard.
2. For an unbound default executor, durably track the transfer operation, then
   reissue with unchanged entityId, ownerWorkno=newOwner and
   executionWorkno=newOwner. Do not create an ACTIVE STAFF independent binding
   merely to represent this default policy: it would pin future handoffs.
3. Preserve existing resource manifest/access/admin configuration during reissue;
   explicitly handle admin ownership projection separately. Independent execution
   bindings do not automatically change with Owner.
4. PENDING authorization, remote timeouts, partial writes and token-injection
   failures require reconciliation; none means the handoff has completed.
5. Validate credential generation, update runtime tokens and caches, handle old
   credentials, and commit/reconcile responsibility according to a durable state
   machine. A database transaction cannot roll back an external AAC/ALC write.

This stage does not implement that transaction, retries, old-token revocation or
assignee/notification transfer. Keep the public handoff entry CLOSED.

## Remaining audit and release gates

B5 is preparation, not proof that every historical API is transfer-ready.
Historical Legacy Skill/SkillSet request parsers/direct metadata writes and
mixed Render Screen/Session Resource paths still require explicit review in the
API/mixed-field stages. In particular, do not reinterpret every parameter named
user_id as a resource key; actor, uploader, grant user and audit person stay real
people. Factories' existing explicit entity contract is not globally widened.

The original design's P stages and implementation B stages are not one-to-one.
Before declaring B5.1's all-entry audit complete, enumerate and resolve these
remaining Legacy paths; the changes above do not certify them. App Grant and
Passport preparation can be reviewed/deployed independently while transfer stays
closed. New/old Frontend and SecBaaS remain deferred as agreed, but their eventual
owner!=entity acceptance is required before opening production handoff.

OCB production Passport addressing adds a read dependency on the existing
ac_bot_execution_identity_binding table. Verify that table and its existing
schema are deployed; do not enable production DI against a missing table.
Explicit cross-env maintenance must not be resolved against the current-env Bot:
explicit entity requests are preserved, and owner-only maintenance uses its
explicit target environment for both Bot lookup and the ACTIVE execution binding.
The existing binding read contract has an optional env override for this
maintenance path; ordinary callers keep the current-env default. Detached/creation fallbacks do not confer access.

## Rollout and rollback

- First run preflight.sql read-only and classify exceptions; do not backfill or
  delete automatically. Owner==entity remains the initial compatibility baseline.
- Merge the Avernet change, then explicitly bump OCB's Avernet submodule to that
  merged commit. Do not ship a pointer to uncommitted local code. Review OCB's
  existing submodule changes separately; this task does not reset or choose them.
- Release the compatible tcauthmng branch and OCB Backend changes with no handoff
  entry. Verify normal apply/refresh/update/query, both user and app callers,
  Caller-mode exchange, resource files, deletion sweeps and App Grant CRUD.
- Then run a controlled pre-release A→B exercise through a durable transfer
  implementation, not arbitrary production SQL. Verify actual execution identity,
  all runtime token copies, authorization state and independent-binding behavior.
- With no transfers yet and normal owner==entity data, revert code versions if
  needed; no DDL rollback is required. Once ownership has changed, code-only
  rollback requires separate compatibility analysis.

## Verification

Results and environment limitations are recorded in validation.md. A passing
Community suite cannot certify OCB's private SDKs or ACM policy; AAC skip mode
is a request, not a guarantee that a user can never need confirmation.
