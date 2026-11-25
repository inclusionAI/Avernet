//! Execution and human-input budget policy, independent of delivery protocols.
use crate::ServiceResult;
pub use crate::types::run_budget::*;

pub trait RunBudgetCoreService:Send+Sync {
    fn start(&self,now_ms:u64,execution_deadline_ms:u64,limits:RunBudgetLimits)->ServiceResult<RunBudgetChange>;
    /// Repeated pause observations cannot restart or extend the same wait.
    fn pause(&self,state:&RunBudgetState,now_ms:u64,input_expires_at_ms:u64)->ServiceResult<RunBudgetChange>;
    /// Execution resumes with the remaining budget; human wait consumes only
    /// the independent wait/total limits. Expired budgets cannot be resumed.
    fn resume(&self,state:&RunBudgetState,now_ms:u64)->ServiceResult<RunBudgetChange>;
}
