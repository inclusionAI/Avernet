# B6–B7: stable resource addresses and durable service-Bot handoff

## Authorized scope

Develop on dev_haoqian_changeowner_B6, commit B6 compatibility separately from
B7 orchestration, and jointly verify with persisted owner!=entity records.
Keep Frontend/SecBaaS unchanged and the production transfer entry disabled.
Entity, Bot PK, tenant, environment, resource addresses and historical actors
never change during handoff. Personal/default Bots cannot transfer.

## B6

Legacy Skill/SkillSet inputs remain compatible owner/entity locators. Resolve a
persisted Bot before selecting resource paths; use entity for storage, current
Owner for metadata and authenticated actor for authorization. Preserve existing
shared/detached/default read behavior, response shapes and route ordering.
Factories' explicit entity remains a resource identity, never an actor. Audit
mixed resources separately; do not transfer uploaders or audit persons.
Split oversized affected modules by responsibility without relaxing CI gates.

## B7

Persist one idempotent transfer operation per Bot. Only the current Owner or
an existing authorized operations administrator may initiate. Reject conflicting
execution changes and unsafe concurrent operations. Persist remote-operation
intent before invoking it; uncertain remote results must be reconciled rather
than blindly reissued. PENDING authorization is not success.

No independent executor: explicitly reissue for the new Owner and preserve the
absence of an independent binding. Existing ACTIVE independent executor remains
unchanged. Do not use the independent-identity change API to represent default
Owner-following execution.

Success requires stable-address resource access, confirmed execution/runtime
cutover, old-generation credential disposition, responsibility/permission update
and durable audit. External writes cannot be rolled back by SQL transactions.
Retries must recover after a process restart; never store raw credentials in
operations, task payloads, logs, or public responses.

## Verification and rollout

Pin legacy contracts and route inventory; test both old owner/entity and new
Owner locators, explicit stable entity, default/personal, collaborator and App
paths. Jointly exercise A→B with durable DB records and injected faults at each
boundary. Local contract doubles do not constitute real AAC/runtime acceptance.
Deploy schema before enabled consumers. Keep production transfer closed until
Frontend, SecBaaS and real credential/runtime acceptance complete.

## B6 mixed-resource decisions

Render Screen's Bot-owner rows retain entity in the physical owner_id while
current Owner governs write permission and personnel response projection.
creator_id is immutable history; unrelated users' personal customizations are
not reassigned. Session Resource owner_id is the authenticated uploader and
part of its private-session authorization contract; preserve it and the stable
binding_id / target identity rather than grant the new Owner private-user
access. Do not rewrite historical work-order actors or notifications. Active
Bot approval work must be classified/blocked or explicitly handed over by B7.
