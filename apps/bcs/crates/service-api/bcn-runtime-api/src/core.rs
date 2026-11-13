use bcn_runtime_model::*;

/// Pure reducer. No I/O, clocks, randomness or mutation of supplied state.
/// Persist the entire Transition with input position and ownership fence before effects.
pub trait SessionRunner: Send + Sync {
    fn kind(&self) -> RunnerKind;
    fn validate(&self, spec: &SessionSpec) -> RuntimeResult<()>;
    fn initialize(&self, spec: &SessionSpec) -> RuntimeResult<RunnerState>;
    fn reduce(&self, state: &RunnerState, input: &SessionInput) -> RuntimeResult<Transition>;
}
