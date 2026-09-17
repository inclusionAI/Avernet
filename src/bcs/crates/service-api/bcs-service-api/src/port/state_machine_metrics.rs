//! Post-commit, low-cardinality observations for fixed Loop execution.

use bcs_domain::StateMachineLoopRouteKind;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StateMachineLoopOutcome {
    Complete,
    Done,
    Approved,
    Rejected,
    Other,
}

impl StateMachineLoopOutcome {
    /// Author-defined outcomes are never used directly as metric labels.
    pub fn from_outcome(outcome: &str) -> Self {
        match outcome {
            "complete" => Self::Complete,
            "done" => Self::Done,
            "approved" => Self::Approved,
            "rejected" => Self::Rejected,
            _ => Self::Other,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StateMachineLoopCompileRejection {
    InvalidDefinition,
    ResourceLimit,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StateMachineLoopMetric {
    /// Entry node's first Running CAS (attempt zero), including HumanInput.
    IterationStarted,
    /// Result node's Completed CAS, once regardless of selected edge fan-out.
    IterationCompleted { route: StateMachineLoopRouteKind, outcome: StateMachineLoopOutcome },
    /// One rejected invocation of the typed v2 compiler, including validation.
    CompileRejected { reason: StateMachineLoopCompileRejection },
}

/// Must return promptly and never block, panic or fail execution. Implementations
/// only update local observations; persistence and network IO do not belong here.
/// Signals follow successful local CAS writes. A crash between commit and signal
/// may lose an observation; replay does not recount committed transitions. These
/// process metrics are not a durable audit ledger or an exactly-once guarantee.
pub trait StateMachineLoopInstrumentationHook: Send + Sync {
    fn record(&self, metric: StateMachineLoopMetric);
}
