use super::*;
use bcs_service_api::port::repo::distributed::control::*;
pub struct InvalidRunner {
    pub calls: AtomicUsize,
    pub corrupt: bool,
}

#[async_trait::async_trait]
impl DistRunnerCoreService for InvalidRunner {
    fn validate_session(
        &self, _: &SessionSpec, _: &serde_json::Value,
    ) -> Result<Arc<dyn std::any::Any + Send + Sync>, RunnerError> {
        Ok(Arc::new(()))
    }
    async fn init(&self, _: &EvaluationContext<'_>) -> Result<RunnerState, RunnerError> {
        if self.corrupt {
            self.calls.fetch_add(1, Ordering::SeqCst);
            return Err(RunnerError::UnsupportedFormat { format: 999 });
        }
        Ok(RunnerState {
            format_version: 1,
            value: serde_json::Value::Null,
        })
    }
    async fn reduce(
        &self,
        _: &EvaluationContext<'_>,
        state: &RunnerState,
        _: &[RunnerInput],
    ) -> Result<Decision, RunnerError> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        Ok(Decision {
            next_state: state.clone(),
            next_deadlines: vec![],
            actions: vec![ActionIntent::FinishTask {
                tid: Tid("wrong-task".into()),
                status: TerminalStatus::Completed,
                result: Payload::Inline(serde_json::Value::Null),
            }],
        })
    }
}

pub struct DeadlineRunner(pub AtomicUsize);

#[async_trait::async_trait]
impl DistRunnerCoreService for DeadlineRunner {
    fn validate_session(
        &self, _: &SessionSpec, _: &serde_json::Value,
    ) -> Result<Arc<dyn std::any::Any + Send + Sync>, RunnerError> {
        Ok(Arc::new(()))
    }
    async fn init(&self, _: &EvaluationContext<'_>) -> Result<RunnerState, RunnerError> {
        Ok(RunnerState {
            format_version: 1,
            value: serde_json::Value::Null,
        })
    }
    async fn reduce(
        &self,
        _: &EvaluationContext<'_>,
        state: &RunnerState,
        inputs: &[RunnerInput],
    ) -> Result<Decision, RunnerError> {
        self.0.fetch_add(1, Ordering::SeqCst);
        let tid = match &inputs[0] {
            RunnerInput::Start { tid, .. } => tid.clone(),
            RunnerInput::Timeout(Deadline {
                kind: DeadlineKind::Task { tid },
                ..
            }) => tid.clone(),
            _ => panic!("unexpected"),
        };
        Ok(Decision {
            next_state: state.clone(),
            actions: vec![],
            next_deadlines: vec![Deadline {
                id: DeadlineId("once".into()),
                at: TimestampMillis(1000),
                kind: DeadlineKind::Task { tid },
            }],
        })
    }
}

pub struct FutureDeadlineRunner(pub AtomicUsize);

#[async_trait::async_trait]
impl DistRunnerCoreService for FutureDeadlineRunner {
    fn validate_session(
        &self, _: &SessionSpec, _: &serde_json::Value,
    ) -> Result<Arc<dyn std::any::Any + Send + Sync>, RunnerError> {
        Ok(Arc::new(()))
    }
    async fn init(&self, _: &EvaluationContext<'_>) -> Result<RunnerState, RunnerError> {
        Ok(RunnerState {
            format_version: 1,
            value: serde_json::Value::Null,
        })
    }
    async fn reduce(
        &self,
        _: &EvaluationContext<'_>,
        state: &RunnerState,
        inputs: &[RunnerInput],
    ) -> Result<Decision, RunnerError> {
        self.0.fetch_add(1, Ordering::SeqCst);
        let (tid, timeout) = match &inputs[0] {
            RunnerInput::Start { tid, .. } => (tid.clone(), false),
            RunnerInput::Timeout(Deadline {
                kind: DeadlineKind::Task { tid },
                ..
            }) => (tid.clone(), true),
            _ => panic!("unexpected"),
        };
        Ok(Decision {
            next_state: state.clone(),
            actions: if timeout {
                vec![ActionIntent::FinishTask {
                    tid: tid.clone(),
                    status: TerminalStatus::Completed,
                    result: Payload::Inline(serde_json::Value::Null),
                }]
            } else {
                vec![]
            },
            next_deadlines: if timeout {
                vec![]
            } else {
                vec![Deadline {
                    id: DeadlineId("future".into()),
                    at: TimestampMillis(2000),
                    kind: DeadlineKind::Task { tid },
                }]
            },
        })
    }
}

pub struct RefRunner {
    pub calls: AtomicUsize,
    pub result: BlobRef,
}

#[async_trait::async_trait]
impl DistRunnerCoreService for RefRunner {
    fn validate_session(
        &self, _: &SessionSpec, _: &serde_json::Value,
    ) -> Result<Arc<dyn std::any::Any + Send + Sync>, RunnerError> {
        Ok(Arc::new(()))
    }
    async fn init(&self, _: &EvaluationContext<'_>) -> Result<RunnerState, RunnerError> {
        Ok(RunnerState {
            format_version: 1,
            value: serde_json::Value::Null,
        })
    }
    async fn reduce(
        &self,
        _: &EvaluationContext<'_>,
        state: &RunnerState,
        inputs: &[RunnerInput],
    ) -> Result<Decision, RunnerError> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        let RunnerInput::Start { tid, .. } = &inputs[0] else {
            panic!()
        };
        Ok(Decision {
            next_state: state.clone(),
            next_deadlines: vec![],
            actions: vec![ActionIntent::FinishTask {
                tid: tid.clone(),
                status: TerminalStatus::Completed,
                result: Payload::Ref(self.result.clone()),
            }],
        })
    }
}

pub async fn quarantine(f: &Fixture, id: &str) {
    let c = f
        .coordination
        .load_control(&spec().sid)
        .await
        .unwrap()
        .unwrap();
    let guard = ControlGuard {
        owner: c.owner.clone(),
        expected_head: c.head,
        expected_revision: c.control.revision,
    };
    let attempt = AttemptIdentity {
        id: RequestId(format!("failure-{id}")),
        head: c.head,
        state_digest: c.checkpoint_digest,
        input_digest: Digest("i".into()),
        context_digest: Digest("c".into()),
        frozen_now: TimestampMillis(1000),
    };
    assert!(matches!(
        f.coordination
            .isolate(
                guard,
                attempt,
                AttemptOutcome::Deterministic {
                    code: "diagnostic".into(),
                    isolation_id: RequestId(id.into()),
                    safe_to_finalize: true
                }
            )
            .await
            .unwrap(),
        ControlOutcome::Applied(_)
    ));
}
