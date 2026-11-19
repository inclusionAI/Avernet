use bcs_dist_model::Instance;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RuntimeSnapshot {
    pub instance: Instance,
    pub ready: bool,
    pub draining: bool,
    pub active_work: u64,
    pub pending_effects: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DrainOutcome {
    Drained,
    DeadlineReached { remaining: u64 },
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("runtime operation failed: {code}")]
pub struct RuntimeError {
    pub code: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WorkerControlAction {
    Inspect,
    Resume,
}
