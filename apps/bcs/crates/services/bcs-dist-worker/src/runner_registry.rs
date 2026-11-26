//! Bind the installed mode plugin to its immutable, versioned session config.
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::core::dist_runner::*;
use std::{any::Any, sync::Arc};

pub struct RunnerRegistry<R>(R);

impl<R: CollaborationModeRunner> RunnerRegistry<R> {
    pub fn new(runner: R) -> Self {
        Self(runner)
    }

    fn config<'a>(&self, context: &'a EvaluationContext<'_>) -> Result<&'a R::Config, RunnerError> {
        context
            .config
            .downcast_ref()
            .ok_or_else(|| RunnerError::InvalidWorkflow {
                code: "session_config_type".into(),
            })
    }

    fn encode(state: R::State) -> Result<RunnerState, RunnerError> {
        let value = serde_json::to_value(state).map_err(|_| RunnerError::CorruptState {
            code: "runner_state_encoding".into(),
        })?;
        Ok(RunnerState {
            format_version: R::STATE_FORMAT_VERSION,
            value,
        })
    }
}

#[async_trait]
impl<R: CollaborationModeRunner> DistRunnerCoreService for RunnerRegistry<R> {
    fn shares_session_downstream_context(&self, spec: &SessionSpec) -> bool {
        spec.mode_envelope.mode == self.0.mode()
            && spec.mode_envelope.mode_version == self.0.mode_version()
            && self.0.shares_session_downstream_context()
    }

    fn validate_session(
        &self,
        spec: &SessionSpec,
        raw: &serde_json::Value,
    ) -> Result<Arc<dyn Any + Send + Sync>, RunnerError> {
        if spec.mode_envelope.mode != self.0.mode()
            || spec.mode_envelope.mode_version != self.0.mode_version()
        {
            return Err(RunnerError::InvalidWorkflow {
                code: "unsupported_session_mode".into(),
            });
        }
        if bcs_dist_encoding::digest_json(raw) != spec.mode_envelope.digest {
            return Err(RunnerError::InvalidWorkflow {
                code: "mode_config_digest".into(),
            });
        }
        let config =
            self.0
                .parse_and_validate_config(raw, &spec.authorized_agents, &spec.limits)?;
        Ok(Arc::new(config))
    }

    async fn init(&self, ctx: &EvaluationContext<'_>) -> Result<RunnerState, RunnerError> {
        let state = self
            .0
            .init_task(
                ctx.spec,
                self.config(ctx)?,
                ctx.tid,
                ctx.downstream_generation,
                ctx.now,
            )
            .await?;
        Self::encode(state)
    }

    async fn reduce(
        &self,
        ctx: &EvaluationContext<'_>,
        state: &RunnerState,
        inputs: &[RunnerInput],
    ) -> Result<Decision, RunnerError> {
        if state.format_version != R::STATE_FORMAT_VERSION {
            return Err(RunnerError::UnsupportedFormat {
                format: state.format_version,
            });
        }
        let state =
            serde_json::from_value(state.value.clone()).map_err(|_| RunnerError::CorruptState {
                code: "runner_state_decoding".into(),
            })?;
        let decision = self
            .0
            .reduce_task(
                ctx.spec,
                self.config(ctx)?,
                ctx.tid,
                ctx.downstream_generation,
                &state,
                inputs,
                ctx.now,
            )
            .await?;
        Ok(Decision {
            next_state: Self::encode(decision.next_state)?,
            actions: decision.actions,
            next_deadlines: decision.next_deadlines,
        })
    }
}
