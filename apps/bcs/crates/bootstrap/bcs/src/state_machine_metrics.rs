use std::sync::Arc;
use bcs_domain::StateMachineLoopRouteKind;
use bcs_service_api::{StateMachineLoopCompileRejection, StateMachineLoopInstrumentationHook,
    StateMachineLoopMetric, StateMachineLoopOutcome};

pub struct MetricsStateMachineLoopHook {
    env: Arc<str>,
}

impl MetricsStateMachineLoopHook {
    pub fn new(env: Arc<str>) -> Self { Self { env } }
}

impl StateMachineLoopInstrumentationHook for MetricsStateMachineLoopHook {
    fn record(&self, metric: StateMachineLoopMetric) {
        match metric {
            StateMachineLoopMetric::IterationStarted => {
                metrics::counter!("state_machine_loop_iterations_started_total", "env" => self.env.to_string()).increment(1);
            }
            StateMachineLoopMetric::IterationCompleted { route, outcome } => {
                metrics::counter!("state_machine_loop_iterations_completed_total", "env" => self.env.to_string()).increment(1);
                match route {
                    StateMachineLoopRouteKind::Continue => {}
                    StateMachineLoopRouteKind::Exhausted => {
                        metrics::counter!("state_machine_loop_exhausted_total", "env" => self.env.to_string()).increment(1);
                    }
                    StateMachineLoopRouteKind::Break => {
                        let outcome = match outcome {
                            StateMachineLoopOutcome::Complete => "complete",
                            StateMachineLoopOutcome::Done => "done",
                            StateMachineLoopOutcome::Approved => "approved",
                            StateMachineLoopOutcome::Rejected => "rejected",
                            StateMachineLoopOutcome::Other => "other",
                        };
                        metrics::counter!("state_machine_loop_break_total", "env" => self.env.to_string(), "outcome" => outcome).increment(1);
                    }
                }
            }
            StateMachineLoopMetric::CompileRejected { reason } => {
                let reason = match reason {
                    StateMachineLoopCompileRejection::InvalidDefinition => "invalid_definition",
                    StateMachineLoopCompileRejection::ResourceLimit => "resource_limit",
                };
                metrics::counter!("state_machine_loop_compile_rejected_total", "env" => self.env.to_string(), "reason" => reason).increment(1);
            }
        }
    }
}
