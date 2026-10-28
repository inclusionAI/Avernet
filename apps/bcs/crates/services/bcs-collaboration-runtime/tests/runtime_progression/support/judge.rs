use super::super::{
    Arc,
    Duration,
    JudgeDecision,
    JudgeEvaluatorPort,
    JudgeRequest,
    Mutex,
    Notify,
    ServiceError,
    VecDeque,
    async_trait
};

#[derive(Default)]
pub(in super::super) struct RecordingJudge {
    outcome: String,
    error: Option<String>,
    delay_ms: Option<u64>,
    pub(in super::super) requests: Mutex<Vec<JudgeRequest>>,
}

impl RecordingJudge {
    pub(in super::super) fn with_outcome(outcome: &str) -> Self {
        Self {
            outcome: outcome.to_string(),
            error: None,
            delay_ms: None,
            requests: Mutex::new(Vec::new()),
        }
    }

    pub(in super::super) fn with_error(error: &str) -> Self {
        Self {
            outcome: String::new(),
            error: Some(error.to_string()),
            delay_ms: None,
            requests: Mutex::new(Vec::new()),
        }
    }

    pub(in super::super) fn with_delayed_outcome(outcome: &str, delay_ms: u64) -> Self {
        Self {
            outcome: outcome.to_string(),
            error: None,
            delay_ms: Some(delay_ms),
            requests: Mutex::new(Vec::new()),
        }
    }
}

#[async_trait]
impl JudgeEvaluatorPort for RecordingJudge {
    async fn judge(&self, request: JudgeRequest) -> Result<JudgeDecision, ServiceError> {
        self.requests.lock().await.push(request);
        if let Some(delay_ms) = self.delay_ms {
            tokio::time::sleep(Duration::from_millis(delay_ms)).await;
        }
        if let Some(error) = &self.error {
            return Err(ServiceError::InternalError(error.clone()));
        }
        Ok(JudgeDecision {
            outcome: self.outcome.clone(),
            reason: "mock decision".to_string(),
            confidence: 1.0,
            checked_criteria: Vec::new(),
            retry_instruction: String::new(),
            raw_response: None,
        })
    }
}

pub(in super::super) struct BlockingJudge {
    outcome: String,
    pub(in super::super) requests: Mutex<Vec<JudgeRequest>>,
    pub(in super::super) started: Notify,
    pub(in super::super) release: Notify,
}

impl BlockingJudge {
    pub(in super::super) fn new(outcome: &str) -> Self {
        Self {
            outcome: outcome.to_string(),
            requests: Mutex::new(Vec::new()),
            started: Notify::new(),
            release: Notify::new(),
        }
    }
}

#[async_trait]
impl JudgeEvaluatorPort for BlockingJudge {
    async fn judge(&self, request: JudgeRequest) -> Result<JudgeDecision, ServiceError> {
        self.requests.lock().await.push(request);
        self.started.notify_one();
        self.release.notified().await;
        Ok(JudgeDecision {
            outcome: self.outcome.clone(),
            reason: "mock decision".to_string(),
            confidence: 1.0,
            checked_criteria: Vec::new(),
            retry_instruction: String::new(),
            raw_response: None,
        })
    }
}

pub(in super::super) struct SequencedJudge {
    decisions: Mutex<VecDeque<JudgeDecision>>,
    pub(in super::super) requests: Mutex<Vec<JudgeRequest>>,
}

impl SequencedJudge {
    pub(in super::super) fn new(decisions: Vec<JudgeDecision>) -> Self {
        Self {
            decisions: Mutex::new(decisions.into()),
            requests: Mutex::new(Vec::new()),
        }
    }
}

#[async_trait]
impl JudgeEvaluatorPort for SequencedJudge {
    async fn judge(&self, request: JudgeRequest) -> Result<JudgeDecision, ServiceError> {
        self.requests.lock().await.push(request);
        self.decisions
            .lock()
            .await
            .pop_front()
            .ok_or_else(|| ServiceError::InternalError("missing test decision".to_string()))
    }
}

pub(in super::super) fn noop_judge() -> Arc<RecordingJudge> {
    Arc::new(RecordingJudge::with_outcome("complete"))
}
