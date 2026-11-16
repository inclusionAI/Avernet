use crate::{graph, initialize, reduce, validation};
use bcn_runtime_api::core::SessionRunner;
use bcn_runtime_model::*;

#[derive(Debug, Default)]
pub struct StructuredGroupRunner;
impl SessionRunner for StructuredGroupRunner {
    fn kind(&self) -> RunnerKind {
        RunnerKind::StructuredGroup
    }
    fn validate(&self, spec: &SessionSpec) -> RuntimeResult<()> {
        validation::validate_definition(spec)
    }
    fn initialize(&self, spec: &SessionSpec) -> RuntimeResult<RunnerState> {
        self.validate(spec)?;
        Ok(initialize(spec, graph::normalize(spec)))
    }
    fn reduce(&self, state: &RunnerState, input: &SessionInput) -> RuntimeResult<Transition> {
        reduce(self.kind(), state, input)
    }
}
