//! Application orchestration: domain policy, managed deadline, then Run cache.
use std::sync::Arc;
use async_trait::async_trait;
use bcs_service_api::{RunBudgetService,BotRunContextPort,ManagedMessageDeliveryService,ServiceError,ServiceResult};
use bcs_service_api::core::run_budget::RunBudgetCoreService;
use bcs_service_api::types::run_budget::*;

pub struct RunBudgetManagement {
    policy:Arc<dyn RunBudgetCoreService>,
    deliveries:Arc<dyn ManagedMessageDeliveryService>,
    contexts:Arc<dyn BotRunContextPort>,
}
impl RunBudgetManagement {
    pub fn new(policy:Arc<dyn RunBudgetCoreService>,deliveries:Arc<dyn ManagedMessageDeliveryService>,contexts:Arc<dyn BotRunContextPort>)->Self {
        Self {policy,deliveries,contexts}
    }
}
fn invalid(reason:&str)->ServiceError {ServiceError::InvalidOperation {message:reason.into(),request_id:None}}

#[async_trait]
impl RunBudgetService for RunBudgetManagement {
    fn initialize(&self,now_ms:u64,execution_deadline_ms:u64,limits:RunBudgetLimits)->ServiceResult<RunBudgetChange> {
        self.policy.start(now_ms,execution_deadline_ms,limits)
    }

    async fn update_deadline(&self,target:RunBudgetTarget,update:RunBudgetUpdate,now_ms:u64)->ServiceResult<RunBudgetChange> {
        if target.run_id.is_empty() || target.expected_deadline_ms<=now_ms {return Err(invalid("Run budget target expired or absent"));}
        let change=match update {
            RunBudgetUpdate::InitialLimit(change)=>{
                if !matches!(change.state.phase,RunBudgetPhase::Executing {started_at_ms} if started_at_ms<=now_ms)
                    || change.state.remaining_execution_ms==0 || change.state.input_wait_limit_ms==0
                    || change.deadline_ms>target.expected_deadline_ms || change.deadline_ms>change.state.total_deadline_ms
                    || change.deadline_ms<=now_ms {return Err(invalid("Invalid initial Run budget limit"));}
                // Ordinary initialization already accompanies correlation create.
                // No deadline changed, so no managed/cache writes are required.
                if change.deadline_ms==target.expected_deadline_ms {return Ok(change);}
                change
            }
            RunBudgetUpdate::Pause {state,input_expires_at_ms}=>self.policy.pause(&state,now_ms,input_expires_at_ms)?,
            RunBudgetUpdate::Resume {state}=>self.policy.resume(&state,now_ms)?,
        };
        self.deliveries.reschedule_run_deadline(&target.scope,&target.run_id,change.deadline_ms,now_ms).await
            .map_err(|_|invalid("Managed Run budget deadline persistence failed"))?;
        if !self.contexts.reschedule_deadline(&target.scope,&target.run_id,target.expected_deadline_ms,change.deadline_ms).await? {
            return Err(invalid("Run budget context changed or expired"));
        }
        Ok(change)
    }
}
