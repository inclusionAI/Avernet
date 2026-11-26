//! The controllable downstream A2A ports (protocol-allowed fixture).
//! (Shared items come from `super::*`.)
use super::*;
// ---------------------------------------------------------------------------
// The controllable downstream A2A journal (protocol-allowed fixture):
// every remote start executes exactly once per stable request — the
// idempotent dedupe replay answers the same remote ref without a second
// execution — and completion happens only when the test flips it.
// ---------------------------------------------------------------------------

/// The fixture's scripted remote behavior. Default: straight idempotent
/// acceptance with completion on demand. `ResultsUnknownUntilHandoff`: the
/// FIRST start answers `OutcomeUnknown` and every later lookup stays
/// silent (`NotFound` by request, working by task) — the profile that
/// drives the Connector's real short-term-exhaustion handoff to the
/// verification lane, then the verification deadline quarantine.
#[derive(Clone, Copy, PartialEq)]
pub enum JournalPlan {
    Accept,
    SilentRemote,
}

pub struct DownstreamJournal {
    killed: AtomicBool,
    pub executions: AtomicUsize,
    pub starts_seen: AtomicUsize,
    pub dedupe_hits: AtomicUsize,
    plan: AtomicU8,
    first_answered: Mutex<bool>,
    started: Mutex<HashMap<RequestId, (RemoteTaskRef, String)>>,
    /// Node-keyed completions: `finish(node)` is the remote finishing that
    /// node's work; every later query of the node answers Final.
    pub completed: Mutex<HashMap<String, Value>>,
}

impl DownstreamJournal {
    pub fn new() -> Arc<Self> {
        Arc::new(Self {
            killed: AtomicBool::new(false),
            executions: AtomicUsize::new(0),
            starts_seen: AtomicUsize::new(0),
            dedupe_hits: AtomicUsize::new(0),
            plan: AtomicU8::new(0),
            first_answered: Mutex::new(false),
            started: Mutex::new(HashMap::new()),
            completed: Mutex::new(HashMap::new()),
        })
    }
    /// Scripts the remote behavior (see `JournalPlan`).
    pub fn set_plan(&self, plan: JournalPlan) {
        self.plan
            .store(plan as u8, std::sync::atomic::Ordering::SeqCst);
    }
    fn plan(&self) -> JournalPlan {
        match self.plan.load(std::sync::atomic::Ordering::SeqCst) {
            1 => JournalPlan::SilentRemote,
            _ => JournalPlan::Accept,
        }
    }
    /// The in-process connector-death gate: every agent-port call of the
    /// dead incarnation hangs forever — the process-gone face of the
    /// transport, so the predecessor's actor never answers anything again.
    pub fn kill(&self) {
        self.killed.store(true, Ordering::SeqCst);
    }
    /// Re-opens the transport for NEW calls (the successor incarnation):
    /// the dead incarnation's already-hung futures stay hung forever — a
    /// never-resolving poll is exactly the socket-gone face — so only
    /// calls made after the re-open can answer.
    pub fn reopen(&self) {
        self.killed.store(false, Ordering::SeqCst);
    }
    /// Completes the remote work of `node`: subsequent queries answer Final
    /// with the node's answer.
    pub fn finish(&self, node: &str) {
        // The A2A complete-result shape the connector's completed-result
        // rule accepts: an ordered artifact checklist (never a bare word).
        self.completed.lock().unwrap().insert(
            node.to_string(),
            json!({
                "state": "completed",
                "artifacts": [{
                    "artifactId": format!("{node}-answer"),
                    "parts": [{"text": format!("answer-of-{node}")}],
                }],
            }),
        );
    }
    fn started_of(&self, request: &RequestId) -> Option<(RemoteTaskRef, String)> {
        self.started.lock().unwrap().get(request).cloned()
    }
}

#[async_trait]
impl DistAgentClientPort for DownstreamJournal {
    async fn start(&self, _: &CallAuthority, request: StableSendRequest) -> SendOutcome {
        if self.killed.load(Ordering::SeqCst) {
            pending::<SendOutcome>().await;
        }
        self.starts_seen.fetch_add(1, Ordering::SeqCst);
        if self.plan() == JournalPlan::SilentRemote {
            let mut answered = self.first_answered.lock().unwrap();
            if !*answered {
                *answered = true;
                // The remote submitted-then-lost profile: the request may
                // have left, nothing is provable, and the stable lookup is
                // silent from now on — exactly the profile the Connector
                // hands to the verification lane.
                return SendOutcome::OutcomeUnknown { code: "companion_unknown_result".into() };
            }
            return SendOutcome::OutcomeUnknown { code: "companion_unknown_repeat".into() };
        }
        if let Some((remote, _node)) = self.started_of(&request.request_id) {
            // The persisted-dedupe replay of the same stable request: the
            // remote task already exists — no second execution.
            self.dedupe_hits.fetch_add(1, Ordering::SeqCst);
            return SendOutcome::Accepted { remote, events: None };
        }
        self.executions.fetch_add(1, Ordering::SeqCst);
        let remote = RemoteTaskRef {
            task_id: format!("remote-{}", request.request_id.0),
            context_id: None,
        };
        self.started
            .lock()
            .unwrap()
            .insert(request.request_id.clone(), (remote.clone(), request.node.0.clone()));
        SendOutcome::Accepted { remote, events: None }
    }
    async fn send_input(
        &self,
        _: &CallAuthority,
        _: &RemoteTaskRef,
        _: StableSendRequest,
    ) -> SendOutcome {
        if self.killed.load(Ordering::SeqCst) {
            pending::<SendOutcome>().await;
        }
        SendOutcome::OutcomeUnknown {
            code: "companion_no_input".into(),
        }
    }
    async fn query(&self, authority: &CallAuthority, query: RemoteQuery) -> QueryOutcome {
        if self.killed.load(Ordering::SeqCst) {
            pending::<QueryOutcome>().await;
        }
        let (remote, node) = match &query {
            RemoteQuery::ByTask(remote) => (remote.clone(), authority.node.0.clone()),
            RemoteQuery::ByRequest(request) => {
                if self.plan() == JournalPlan::SilentRemote {
                    // The silent remote keeps the by-request lookup absent:
                    // the verification lane's obligation is retained, never
                    // answered, until the stop-verification deadline bound
                    // holds it in `Quarantined`.
                    return QueryOutcome::NotFound;
                }
                let Some((remote, node)) = self.started_of(request) else {
                    return QueryOutcome::NotFound;
                };
                (remote, node)
            }
        };
        if self.plan() == JournalPlan::SilentRemote {
            let _ = node;
            return QueryOutcome::Found {
                remote: self
                    .started
                    .lock()
                    .unwrap()
                    .values()
                    .next()
                    .cloned()
                    .map(|(r, _)| r)
                    .unwrap_or(remote),
                status: TaskStatus::Working,
                snapshot: Payload::Inline(json!({"state": "working"})),
            };
        }
        let answer = self.completed.lock().unwrap().get(&node).cloned();
        match answer {
            Some(answer) => QueryOutcome::Found {
                remote,
                status: TaskStatus::Completed,
                snapshot: Payload::Inline(answer),
            },
            None => QueryOutcome::Found {
                remote,
                status: TaskStatus::Working,
                snapshot: Payload::Inline(json!({"state": "working"})),
            },
        }
    }
    async fn subscribe(
        &self,
        _: &CallAuthority,
        _: &RemoteTaskRef,
        _: Option<ReplayToken>,
    ) -> Result<RemoteEventStream, AgentReadError> {
        Err(AgentReadError {
            code: "companion_subscribe_off".into(),
        })
    }
    async fn cancel(
        &self,
        _: &CallAuthority,
        _: &RemoteTaskRef,
        _: RequestId,
    ) -> CancelOutcome {
        if self.killed.load(Ordering::SeqCst) {
            pending::<CancelOutcome>().await;
        }
        CancelOutcome::OutcomeUnknown {
            code: "companion_cancel_unknown".into(),
        }
    }
}
