//! Consumer contract for policy + managed/cache deadline orchestration.
use bcs_service_api::{RunBudgetService,ManagedMessageDeliveryService,BotRunContextPort};
use bcs_service_api::types::run_budget::*;

/// Fixture supplies one live, transport-bound canonical Send in both stores.
pub async fn run_budget_service_contract_tests(
    service:&dyn RunBudgetService,deliveries:&dyn ManagedMessageDeliveryService,
    contexts:&dyn BotRunContextPort,target:RunBudgetTarget,now:u64,
) {
    let row=deliveries.snapshot(Some(&target.scope.session_id)).await.unwrap().remove(0);
    let started=service.initialize(now,target.expected_deadline_ms,RunBudgetLimits {input_wait_ms:10_000,total_run_ms:600_000}).unwrap();
    assert!(service.update_deadline(target.clone(),RunBudgetUpdate::Resume {state:started.state.clone()},now+10).await.is_err());
    let wrong=RunBudgetTarget {scope:bcs_service_api::BotRunScope {session_id:"foreign".into(),..target.scope.clone()},..target.clone()};
    assert!(service.update_deadline(wrong,RunBudgetUpdate::Pause {state:started.state.clone(),input_expires_at_ms:now+30_000},now+50).await.is_err());
    assert_eq!(contexts.get_context(&target.run_id).await.unwrap().deadline_ms,target.expected_deadline_ms);
    let paused=service.update_deadline(target.clone(),RunBudgetUpdate::Pause {state:started.state,input_expires_at_ms:now+30_000},now+50).await.unwrap();
    assert_eq!(paused.deadline_ms,now+10_050);
    assert_eq!(paused.state.remaining_execution_ms,target.expected_deadline_ms-now-50);
    assert_eq!(contexts.get_context(&target.run_id).await.unwrap().deadline_ms,paused.deadline_ms);
    let waiting=RunBudgetTarget {expected_deadline_ms:paused.deadline_ms,..target.clone()};
    let repeated=service.update_deadline(waiting.clone(),RunBudgetUpdate::Pause {state:paused.state.clone(),input_expires_at_ms:now+60_000},now+1000).await.unwrap();
    assert_eq!(repeated,paused,"another observation does not renew human wait");
    let resumed=service.update_deadline(waiting,RunBudgetUpdate::Resume {state:paused.state},now+2000).await.unwrap();
    assert_eq!(resumed.deadline_ms,target.expected_deadline_ms+1950,"human wait is not execution time");
    let updated=deliveries.snapshot(Some(&target.scope.session_id)).await.unwrap().remove(0);
    assert_eq!(updated.run_deadline_at_ms,Some(resumed.deadline_ms as i64));
    assert_eq!(contexts.get_context(&target.run_id).await.unwrap().deadline_ms,resumed.deadline_ms);
    assert_eq!(updated.attempt_no,row.attempt_no);assert_eq!(updated.request_id,row.request_id);
    assert_eq!(updated.state.status,row.state.status);assert_eq!(deliveries.active_count(&target.scope.bot_id).await.unwrap(),1);
}
