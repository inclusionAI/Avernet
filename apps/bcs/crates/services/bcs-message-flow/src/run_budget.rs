//! Pure execution/wait/total budgeting. Callers persist before publishing I/O.
use bcs_service_api::{ServiceError,ServiceResult};
use bcs_service_api::core::run_budget::*;

#[derive(Default)]
pub struct RunBudgetCore;
fn invalid(reason:&str)->ServiceError {ServiceError::InvalidOperation {message:reason.into(),request_id:None}}
fn future(now:u64,span:u64)->ServiceResult<u64> {now.checked_add(span).ok_or_else(||invalid("Run budget clock overflow"))}
fn active(state:&RunBudgetState,now:u64)->ServiceResult<()> {
    if state.remaining_execution_ms==0 || state.input_wait_limit_ms==0 {return Err(invalid("Run execution budget exhausted"));}
    if now>=state.total_deadline_ms {return Err(invalid("Run total budget expired"));}
    Ok(())
}
impl RunBudgetCoreService for RunBudgetCore {
    fn start(&self,now:u64,execution_deadline:u64,limits:RunBudgetLimits)->ServiceResult<RunBudgetChange> {
        if execution_deadline<=now || limits.input_wait_ms==0 || limits.total_run_ms==0 {return Err(invalid("Run budgets must be positive"));}
        let total=future(now,limits.total_run_ms)?;
        Ok(RunBudgetChange {state:RunBudgetState {remaining_execution_ms:execution_deadline-now,
            total_deadline_ms:total,input_wait_limit_ms:limits.input_wait_ms,phase:RunBudgetPhase::Executing {started_at_ms:now}},
            deadline_ms:execution_deadline.min(total)})
    }
    fn pause(&self,state:&RunBudgetState,now:u64,input_expires:u64)->ServiceResult<RunBudgetChange> {
        active(state,now)?;
        let mut next=state.clone();
        let deadline=match state.phase {
            RunBudgetPhase::Executing {started_at_ms} => {
                let elapsed=now.checked_sub(started_at_ms).ok_or_else(||invalid("Run budget clock moved backwards"))?;
                if elapsed>=state.remaining_execution_ms {return Err(invalid("Run execution budget exhausted"));}
                next.remaining_execution_ms-=elapsed;
                future(now,state.input_wait_limit_ms)?.min(state.total_deadline_ms).min(input_expires)
            }
            RunBudgetPhase::Waiting {started_at_ms,deadline_ms} => {
                if now<started_at_ms {return Err(invalid("Run budget clock moved backwards"));}
                deadline_ms.min(input_expires)
            }
        };
        if deadline<=now {return Err(invalid("Run input wait expired"));}
        let since=match state.phase {RunBudgetPhase::Waiting {started_at_ms,..}=>started_at_ms,_=>now};
        next.phase=RunBudgetPhase::Waiting {started_at_ms:since,deadline_ms:deadline};
        Ok(RunBudgetChange {state:next,deadline_ms:deadline})
    }
    fn resume(&self,state:&RunBudgetState,now:u64)->ServiceResult<RunBudgetChange> {
        active(state,now)?;
        let RunBudgetPhase::Waiting {started_at_ms,deadline_ms}=state.phase else {return Err(invalid("Run is not waiting for input"));};
        if now<started_at_ms {return Err(invalid("Run budget clock moved backwards"));}
        if now>=deadline_ms {return Err(invalid("Run input wait expired"));}
        let deadline=future(now,state.remaining_execution_ms)?.min(state.total_deadline_ms);
        let mut next=state.clone();next.phase=RunBudgetPhase::Executing {started_at_ms:now};
        Ok(RunBudgetChange {state:next,deadline_ms:deadline})
    }
}
