//! Consumer scenarios for the additive BCN APIs. Use isolated fixtures and real implementations.
//! These are exercised by the implementation crates when each business port is introduced.
use std::sync::Arc;
use bcn_runtime_model::*;
use bcn_runtime_api::{application::GatewayService, port::repo::{InboxPort, OwnershipPort}};
use bcn_runtime_storage_api::NodeLivenessPort;

/// Fixture must allow `caller` and deny `denied_caller` access to this target/task.
pub async fn gateway_service_contract_tests(
    gateway: Arc<dyn GatewayService>, caller: CallerContext, denied_caller: CallerContext, request: SubmitRequest,
) {
    let task = gateway.submit(caller.clone(), request.clone()).await.expect("admit");
    assert_eq!(task.task_id.scope, caller.scope);
    assert_eq!(task.target, request.target);
    let retried = gateway.submit(caller.clone(), request.clone()).await.expect("idempotent retry");
    assert_eq!(retried.task_id, task.task_id);
    let found = gateway.get(caller.clone(), task.task_id.clone()).await.expect("immediate visibility");
    assert_eq!(found.task_id, task.task_id);
    assert!(matches!(gateway.get(denied_caller, task.task_id.clone()).await, Err(RuntimeError::AccessDenied)));
    let mut conflict = request;
    conflict.message.parts.push(MessagePart::Text { text: "different request content".into() });
    assert!(matches!(gateway.submit(caller, conflict).await, Err(RuntimeError::Conflict(_))));
}

/// Use a fresh shard and two process incarnations in its scope.
pub async fn ownership_port_contract_tests(
    ownership: Arc<dyn OwnershipPort>, shard: ShardId, first: NodeIdentity, second: NodeIdentity,
) {
    let fence = ownership.claim_shard(shard.clone(), first.clone(), None).await.expect("initial claim");
    assert_eq!(fence.owner, first);
    assert!(ownership.claim_shard(shard.clone(), second.clone(), None).await.is_err());
    let next = ownership.transfer_shard(fence.clone(), second.clone()).await.expect("transfer");
    assert_eq!(next.owner, second);
    assert!(next.epoch > fence.epoch);
    assert!(ownership.transfer_shard(fence, first).await.is_err(), "stale ownership must be fenced");
    assert_eq!(ownership.read_owner(&shard).await.unwrap(), Some(next));
}

/// Fixture starts with an empty inbox for a valid admitted session.
pub async fn inbox_port_contract_tests(inbox: Arc<dyn InboxPort>, session: SessionId, input: SessionInput) {
    let position = inbox.append_and_wake(&session, input.clone()).await.expect("append");
    assert_eq!(inbox.append_and_wake(&session, input.clone()).await.unwrap(), position);
    let entries = inbox.read_batch(&session, 0, 1).await.expect("read");
    assert_eq!(entries.len(), 1);
    assert_eq!(entries[0].position, position);
    assert_eq!(entries[0].input, input);
    assert!(inbox.read_batch(&session, position, 1).await.unwrap().is_empty());
    assert!(inbox.read_batch(&session, 0, 0).await.is_err(), "zero limit must fail closed");
}

/// No wall-clock sleeps: use a live TTL and distinguish an unregistered incarnation.
pub async fn node_liveness_port_contract_tests(liveness: Arc<dyn NodeLivenessPort>, node: NodeIdentity) {
    liveness.register(node.clone(), 30_000).await.expect("register");
    liveness.renew(&node, 30_000).await.expect("renew");
    let alive = liveness.inspect_batch(std::slice::from_ref(&node)).await.expect("inspect");
    assert_eq!(alive.len(), 1);
    assert_eq!(alive[0].node, node);
    assert_eq!(alive[0].liveness, Liveness::Alive);
    liveness.mark_draining(&node).await.expect("drain");
    assert!(liveness.inspect_batch(std::slice::from_ref(&node)).await.unwrap()[0].draining);
    let other = NodeIdentity { incarnation: format!("{}-unregistered", node.incarnation), ..node };
    let unknown = liveness.inspect_batch(std::slice::from_ref(&other)).await.unwrap();
    assert_eq!(unknown.len(), 1);
    assert_eq!(unknown[0].node, other);
    assert_ne!(unknown[0].liveness, Liveness::Alive, "node name must not inherit another process's lease");
}

/// One published Judge command in an otherwise fresh scope; advance_clock sets
/// its backend time. Assert expiry, ABA fencing and immutable completion retry.
pub async fn command_effect_contract_tests(
    bus:Arc<dyn bcn_runtime_api::port::repo::CommandBus>,inbox:Arc<dyn InboxPort>,
    session:SessionId,first:NodeIdentity,second:NodeIdentity,input:SessionInput,
    advance_clock:impl Fn(u64),
) {
    let a=bus.receive_effects(&session.scope,&first,None,10).await.unwrap().items.remove(0);
    assert!(bus.renew_effect(&a.fence).await.unwrap()>0);
    assert!(bus.receive_effects(&session.scope,&second,None,10).await.unwrap().items.is_empty());
    advance_clock(5000);
    assert_eq!(bus.renew_effect(&a.fence).await,Err(RuntimeError::OwnershipLost));
    assert_eq!(bus.complete_effect(&a.fence,input.clone()).await,Err(RuntimeError::OwnershipLost));
    let b=bus.receive_effects(&session.scope,&second,None,10).await.unwrap().items.remove(0);
    advance_clock(9000);
    let new_a=bus.receive_effects(&session.scope,&first,None,10).await.unwrap().items.remove(0);
    assert!(new_a.fence.epoch>b.fence.epoch && b.fence.epoch>a.fence.epoch);
    assert_eq!(bus.complete_effect(&a.fence,input.clone()).await,Err(RuntimeError::OwnershipLost));
    assert_eq!(bus.complete_effect(&b.fence,input.clone()).await,Err(RuntimeError::OwnershipLost));
    advance_clock(13000);
    let renewed_a=bus.receive_effects(&session.scope,&first,None,10).await.unwrap().items.remove(0);
    assert!(renewed_a.fence.epoch>new_a.fence.epoch,"expired same-owner reacquire needs a new generation");
    assert_eq!(bus.complete_effect(&new_a.fence,input.clone()).await,Err(RuntimeError::OwnershipLost));
    let pos=bus.complete_effect(&renewed_a.fence,input.clone()).await.unwrap();
    assert_eq!(bus.complete_effect(&renewed_a.fence,input.clone()).await.unwrap(),pos);
    let mut changed=input.clone();changed.at_ms+=1;
    assert!(matches!(bus.complete_effect(&renewed_a.fence,changed).await,Err(RuntimeError::Conflict(_))));
    assert_eq!(inbox.find_input(&session,&input.event_id).await.unwrap(),Some(input));
    assert!(bus.receive_effects(&session.scope,&second,None,10).await.unwrap().items.is_empty());
}

/// Invoke a real observer with scoped durable quarantine evidence. The fixture
/// assertion inspects its actual diagnostic sink/counter, not a mocked call.
pub fn runtime_observer_port_contract_tests(
    observer: &dyn bcn_runtime_api::port::RuntimeObserverPort,
    session: &SessionId, recovery: &SessionRecovery,
    assert_diagnostic: impl FnOnce(&SessionId, &SessionRecovery),
) {
    assert!(recovery.quarantined);
    assert_eq!(session_shard(session), recovery.fence.shard);
    observer.session_quarantined(session, recovery);
    assert_diagnostic(session, recovery);
}
