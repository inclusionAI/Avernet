//! Internal execution/wait budgeting use case; independent of wire protocols.
use async_trait::async_trait;
use crate::{ServiceResult,types::run_budget::*};

#[async_trait]
pub trait RunBudgetService:Send+Sync {
    fn initialize(&self,now_ms:u64,execution_deadline_ms:u64,limits:RunBudgetLimits)->ServiceResult<RunBudgetChange>;
    /// Apply pure budget policy, then persist managed deadline and guarded Run
    /// context/cache deadline in that order. Exact canonical scope and expected
    /// deadline are mandatory. Partial failure propagates; no rollback or card
    /// publication is implied. Caller must persist its protocol correlation
    /// before exposing a wait or sending a continuation. No slot is released.
    async fn update_deadline(&self,target:RunBudgetTarget,update:RunBudgetUpdate,now_ms:u64)->ServiceResult<RunBudgetChange>;
}
