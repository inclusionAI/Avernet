# bcs-edge-permission Context

## Provides

- Existing Connect, friend and Admission Service API implementations and the
  Bot authority Core implementation, with their legacy behavior unchanged.
- `AgentInvokePolicy`, implementing pure `AgentInvokePolicyCoreService` for
  exact-principal/pair/domain management policy and direct invocation decisions.
- `AgentInvokeApplicationService`, implementing the independent invocation and
  management application contracts. It validates ingress before reading facts,
  calls one complete fact snapshot, and submits at most one atomic command.
- `AgentInvokeQueryPolicy` and `AgentInvokeQueryApplicationService` implement
  independent governance reads without fabricating mutation commands.

## Consumes

- Service API application/core/port/repo declarations and domain types.
- `AgentInvokeFactsPort` for trusted, consistent identity/qualification/approval
  snapshots, never raw AgentCard labels or SDK credentials.
- `AgentInvokeCommandRepoPort` for atomic version/fence/quota/audit/receipt
  persistence; no application-level sequence of legacy writes is allowed.
- `AgentInvokeQueryRepoPort` for an authorized, consistent governance snapshot.

## Allowed dependencies

Service API/domain contracts and utility crates. Stores are test-only fixtures.

## Forbidden dependencies

Delivery/bootstrap, transport frameworks, host SDKs, SQL/DB plugins, raw runtime
configuration/environment reads and concrete production fact/store providers.

## Change impact and security boundary

New contracts are additive. The operation provider can be registered in the
central interceptor chain; production bootstrap keeps this capability disabled. A stored AgentInvoke candidate alone never authorizes a generic
profile/friend/public path. Invocation is direct, exact Bot identity,
originator==caller, current generation and no public/default fallback.
Management prechecks authorize the operator; commit-time qualification,
requester authority epochs, versions, limits and receipt replay are atomic-store
responsibilities. Revocation and history recovery cannot be blocked by a new
positive-grant qualification check. Unknown commit outcomes are never retried.

The fact provider must coordinate durable invalidation with persisted approvals;
this crate cannot infer that guarantee from a current boolean or agent_code.
No production provider or SQL writer is introduced here. One use case reads one
bounded pair snapshot; command orchestration performs one atomic store call,
not N+1 or per-output-frame work. Actual provider query budgets require their
concrete implementation; local mutex tests do not prove production costs.

## Conformance

`tests::agent_invoke_policy` records actual fact/command port hits, covers the
role matrix, exact identity/operation/domain checks, generation failures and
closed errors. `bcs-test-support::contract::agent_invoke` drives the real
application against the synthetic local lifecycle implementation. Registered
HTTP management delivery and explicit Direct A2A runtime integration exist;
trusted invocation ingress and production host assembly remain disabled. Concrete SQL
transaction implementations and their tests belong to the store crate.


## Governance query boundary

Policy validates the complete cursor context and bounded page shape before a
repo call; the application calls the repo once, propagates errors and rejects
a mismatched result context/resource. Human owner/manager can read controlled
Bot incident history. Ordinary Agent principals are forbidden. Governance is
exact-pair Query delegation only (both directions for degrees), not whole-Bot
graph access. Audit is pair-only. Current authority, not Agent eligibility or
writer/runtime activation, permits history reads. StoredApproved counts and
optional display metadata cannot be used as runtime invocation authority.

Shared query policy/application/repo conformance runs against real SQLite
lifecycle data. Unit tests record actual port calls and cover cursor context,
roles, validation, errors and mismatched results. SQL costs, transaction
reauthorization and native MySQL acceptance are the store's responsibilities.


## Operation-aware interceptor provider

`AgentInvokeInterceptor` implements the existing MessageInterceptor extension,
with independent on_agent_invocation delegation to AgentInvocationService. Its
ordinary outbound callback passes without creating friend/group/tool authority.
None is unsupported, not a grant: explicit callers require a registered provider
and all returned permits must agree. Each callback invokes the application anew;
there is no credential requirement or positive cache. A2aChat calls it before
admission effects, after inline preparation, on queue pickup and after send-start
CAS in before_send. Provider collaboration remains a separate upper boundary.
The shared operation-provider harness is invoked from the discoverable Direct
A2A conformance entry, with real SQLite approval/revocation and actual scheduler/
transport integration regressions. This is not HTTP/Singlebox or host proof.
