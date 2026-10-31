//! Persistable protocol-independent execution and human-wait budget state.
use serde::{Deserialize,Serialize};

#[derive(Clone,Copy,Debug,PartialEq,Eq,Serialize,Deserialize)]
pub struct RunBudgetLimits { pub input_wait_ms:u64, pub total_run_ms:u64 }

#[derive(Clone,Debug,PartialEq,Eq,Serialize,Deserialize)]
#[serde(tag="phase",rename_all="snake_case")]
pub enum RunBudgetPhase {
    Executing { started_at_ms:u64 },
    Waiting { started_at_ms:u64, deadline_ms:u64 },
}

#[derive(Clone,Debug,PartialEq,Eq,Serialize,Deserialize)]
pub struct RunBudgetState {
    pub remaining_execution_ms:u64,
    pub total_deadline_ms:u64,
    pub input_wait_limit_ms:u64,
    pub phase:RunBudgetPhase,
}

#[derive(Clone,Debug,PartialEq,Eq)]
pub struct RunBudgetChange { pub state:RunBudgetState, pub deadline_ms:u64 }

#[derive(Clone,Debug)]
pub struct RunBudgetTarget {
    pub scope:crate::BotRunScope,
    pub run_id:String,
    pub expected_deadline_ms:u64,
}

#[derive(Clone,Debug)]
pub enum RunBudgetUpdate {
    /// The unchanged result of initialize(), applied after transport binding.
    InitialLimit(RunBudgetChange),
    Pause {state:RunBudgetState,input_expires_at_ms:u64},
    Resume {state:RunBudgetState},
}
