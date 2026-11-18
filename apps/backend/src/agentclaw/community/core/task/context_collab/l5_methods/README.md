# L5 Method contracts and operations

Owner: Backend `core/task/context_collab/l5_methods`. L5 composes L4 primitives
into Methods and owns their profiles, bindings, composition, publication and
admission constraints. [L6](../l6_products/README.md) selects these contracts to
compose products; L5 does not import L6 or concrete product definitions.
This is the local implementation boundary, not a new ACCS wire operation.

## Context Boundary

```yaml
purpose: Method contracts and operations over L4 primitives, independent of product composition.
provides:
  - MethodProfile
  - MethodBinding
  - MethodPolicy
  - OpenMethodPolicy
  - AdmissionRole
  - OpenAdmissionPolicy
  - OpenPublicationProfile
  - BotSelfApplicationAdmission
  - OpenMethodOpsServiceProtocol
consumes:
  - MethodStoreProtocol
  - CollaborationReader
  - TopLevelBindingRecovery
internal_dependencies:
  - agentclaw.community.core.task.context_collab.errors
  - agentclaw.community.core.task.context_collab.l2_agent_network
  - agentclaw.community.core.task.context_collab.l4_context_primitives
  - agentclaw.community.core.task.context_collab.l5_methods
  - agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context
  - agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization
  - agentclaw.community.core.task.context_collab.g4_contracts_consistency.method_state_machines
  - agentclaw.community.core.task.context_collab.g4_contracts_consistency.state_machine
  - agentclaw.community.core.task.context_collab.persistence.method_store
  - agentclaw.community.utils
```

### Change impact

Method contracts affect generic Method services, L6 composition/validation,
product definitions, product services and DI authorization/admission adapters.
Preserve pinned versions and runtime authority; changing Python ownership alone
does not migrate ProductInstances, alter stored facts or authorize participation.

## Open policy ownership

- [method_policies.py](method_policies.py) defines `MethodPolicy` and
  `OpenMethodPolicy`. L6 may select a policy in `composition.policies` only for a
  required Method; it does not define another Method policy type.
- [publication.py](publication.py) defines `OpenAdmissionPolicy`,
  `OpenPublicationProfile` and `BotSelfApplicationAdmission`. Shared constraints
  require named, required role mappings, positive capacity and a typed
  participation outcome. Publication permits delivery only after `ADMITTED` and
  preserves `propagate_without_rollback` failure behavior.
- `OpenAdmissionPolicy` also declares who executes the admission decision and
  which bounded rule selector describes the judgement:
  `decision_actor`, `decision_agent_ref` and `decision_rule`. The
  `accountable_role` / `product_policy` defaults preserve existing owner
  behavior. `configured_agent` selects the exact `decision_agent_ref`; it is
  the admission decision identity, not an initial task executor or role
  binding. `capability_evidence` is the currently supported configured rule.
  These declarations do not bypass L5 G1/G4 execution: product composition must
  authorize and orchestrate the configured Agent's explicit `screen`/`admit`
  calls.
- `AdmissionRole` is L5's read-only structural view of role name, requiredness
  and participant source. L6 `ScenarioRole` satisfies it without an upward
  import, including in type-checking-only annotations.
- `BotSelfApplicationAdmission` retains designated Bot self-application,
  `COLLET_PARTICIPANT`, capacity 1, an executor applicant and accountable owner.
  These values do not themselves submit, screen, admit, select or start Work.
  Product services and DI still orchestrate those explicit operations.
- The commission-specific `DesignatedOpenAdmission` compatibility strategy stays
  in [the product](../products/task_commission/publication.py). Other products
  can select L5 policies without importing commission templates or services.

The former `l6_products.method_policies`, `l6_products.publication` and commission
`BotSelfApplicationAdmission` imports remain identity reexports. Internal
consumers import the canonical L5 definitions directly. Existing constructor
fields, validation, strategy IDs, installed versions, wire shapes and persistence
remain unchanged.

## Verification

- [Policy regressions](../../../../../../../tests/community/core/task/context_collab/test_l5_open_method_policies.py)
  cover standalone structural role validation, immutable policy values,
  self-application bounds, dispatch gating and legacy import identity.
- [L5 architecture gate](../../../../../../../tests/community/architecture/test_context_collab_l5_dependencies.py)
  rejects L6/product dependencies, including relative and type-checking imports.
- [L6 profile creation tests](../../../../../../../tests/community/core/task/context_collab/test_l6_product_profile_operations.py)
  cover policy selection, role mappings and shared creation/catalog validation.
