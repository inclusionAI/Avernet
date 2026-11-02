use super::*;

// ---- review-V B3.2 §6.8 same-dispatch redrive harness -----------------

struct RecordingHistoryBackfill {
    term: Option<Arc<std::sync::atomic::AtomicU64>>,
    bodies: std::sync::Mutex<Vec<bcs_service_api::port::ObserveInjectCommand>>,
    mode: String,
    calls: std::sync::Mutex<Vec<bcs_service_api::port::HistoryCompleteCommand>>,
    /// None = always ready; Some(false) = non-retryable refusal.
    refusal: Option<bool>,
}
#[async_trait]
impl bcs_service_api::port::HistoryBackfillPort for RecordingHistoryBackfill {
    async fn inject_history_message(
        &self, _target: &BotDeliveryTarget, _credential: &RedactedToken,
        command: bcs_service_api::port::ObserveInjectCommand,
    ) -> Result<(), bcs_service_api::port::HistoryBackfillError> {
        self.bodies.lock().unwrap().push(command);
        if self.mode == "body-refused" { return Err(bcs_service_api::port::HistoryBackfillError::NotSent); }
        if self.mode == "demote-body" {
            self.term.as_ref().unwrap().fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        }
        Ok(())
    }
    async fn inject_history_complete(
        &self,
        _target: &BotDeliveryTarget,
        _credential: &RedactedToken,
        command: bcs_service_api::port::HistoryCompleteCommand,
    ) -> Result<(), bcs_service_api::port::HistoryBackfillError> {
        assert_eq!(self.bodies.lock().unwrap().len(), 1, "completion requires the historical body first");
        if let Some(false) = self.refusal {
            return Err(bcs_service_api::port::HistoryBackfillError::Unavailable { retryable: false });
        }
        self.calls.lock().unwrap().push(command);
        if let Some(term) = &self.term { term.fetch_add(1, std::sync::atomic::Ordering::SeqCst); }
        Ok(())
    }
}

/// Rejects the FIRST send with retryable HISTORY_NOT_READY and either
/// accepts the redrive or refuses it closed by configuration.
struct BarrierFlippingSender {
    state: std::sync::Mutex<Vec<bcs_service_api::port::SendCallerExecutionCommand>>,
    redrive: CallerDispatchAck,
}
#[async_trait]
impl CallerExecutionSendPort for BarrierFlippingSender {
    async fn send_caller_execution(
        &self,
        _target: &BotDeliveryTarget,
        _credential: &RedactedToken,
        command: bcs_service_api::port::SendCallerExecutionCommand,
    ) -> Result<CallerDispatchAck, CallerSendError> {
        let mut seen = self.state.lock().unwrap();
        seen.push(command);
        if seen.len() == 1 {
            return Ok(CallerDispatchAck::Rejected {
                error_code: "HISTORY_NOT_READY".into(),
                retryable: true,
            });
        }
        Ok(self.redrive.clone())
    }
}

async fn delegate_barrier_world(
    redrive: CallerDispatchAck,
    refusal: Option<bool>,
    term: Option<Arc<std::sync::atomic::AtomicU64>>,
) -> (
    Arc<ManagedMessageDelivery>,
    Arc<BarrierFlippingSender>,
    Arc<RecordingHistoryBackfill>,
) {
    delegate_history_world(redrive, refusal, term, "normal").await
}

async fn delegate_history_world(
    redrive: CallerDispatchAck, refusal: Option<bool>,
    term: Option<Arc<std::sync::atomic::AtomicU64>>, mode: &str,
) -> (Arc<ManagedMessageDelivery>, Arc<BarrierFlippingSender>, Arc<RecordingHistoryBackfill>) {
    let repo = Arc::new(MemoryMessageRepo::new());
    let managed = Arc::new(ManagedMessageDelivery::new(repo.clone()));
    let mut history = admit_caller(managed.as_ref(), "prior-message");
    history.message.session_id = "session-delegate-backfill".into();
    history.message.content = json!({"text": "历史正文，不是当前任务"});
    if mode == "attachment" { history.message.content["attachments"] = json!([{"type":"image","attachment_id":"a1","file_name":"test.png","url":"https://example.invalid/test.png"}]); }
    history.targets[0].kind = DeliveryType::Inject;
    history.targets[0].semantic_projection_json = json!({"version": 1});
    managed.admit(history).await.unwrap();
    let message_id = "delegate-backfill";
    let mut admission = admit_caller(managed.as_ref(), message_id);
    if let Value::Object(map) = &mut admission.targets[0].semantic_projection_json {
        map.insert("caller_delegation".into(), json!({
            "parent_run_id": "run-manager",
            "parent_attempt": 1,
            "parent_bot_id": "bot-manager",
            "invocation_id": "tool-call-1",
            "execution_purpose": "execution",
        }));
    } else {
        panic!("the admission projection must be an object");
    }
    managed.admit(admission).await.expect("admit delegated caller row");

    let lineage_index = Arc::new(bcs_message_flow::caller_lineage::RootLineageIndex::new(8));
    assert!(lineage_index.register(
        "run-manager",
        bcs_message_flow::caller_lineage::RootLineage {
            root_run_id: "root-run".into(),
            environment: "local".into(),
            delegation_depth: 0,
            attempt: 1,
            message_turn_id: format!("canonical-{message_id}"),
            group_id: "group".into(),
            session_id: format!("session-{message_id}"),
            history_cutoff_seq: 1,
            expires_at_ms: now_ms() + 300_000,
            identity_subject: "root-pseudonym".into(),
            identity_subject_type: bcs_service_api::application::caller_identity::CallerSubjectRefType::BcsEphemeral,
            delegate_subject: "root-pseudonym".into(),
            parent_execution: false,
        },
        now_ms(),
    ));
    let handoff = Arc::new(HumanCallerDeliveryHandoff::new(8));
    let gateway = Arc::new(RecordingGateway::default());
    let sender = Arc::new(BarrierFlippingSender { state: std::sync::Mutex::new(Vec::new()), redrive });
    let backfill = Arc::new(RecordingHistoryBackfill { mode: mode.into(), bodies: std::sync::Mutex::new(Vec::new()), calls: std::sync::Mutex::new(Vec::new()), refusal, term: term.clone() });
    let preparation = Arc::new(CallerContainerPreparation::new(gateway.clone() as Arc<dyn CallerContainerGatewayPort>));
    let pipeline = CallerDispatchPipeline::new(
            handoff.clone() as Arc<dyn HumanCallerDeliveryHandoffService>,
            preparation,
            Arc::new(StubIdentity),
            sender.clone() as Arc<dyn CallerExecutionSendPort>,
            3_600_000,
        )
        .with_managed_commit(managed.clone())
        .with_root_lineage(lineage_index)
        .with_history_backfill(backfill.clone() as Arc<dyn bcs_service_api::port::HistoryBackfillPort>)
        .with_delivery_term(term.unwrap_or_else(|| Arc::new(std::sync::atomic::AtomicU64::new(1))))
        .with_barrier_backoff_for_tests(vec![10]);
    let pipeline = Arc::new(if mode == "no-repo" { pipeline } else { pipeline.with_history_messages(repo) });
    let ordinary = Arc::new(OrdinaryIo::default());
    let runtime = DeliveryRuntime {
        policy: None,
        service: managed.clone(),
        preparation: ordinary.clone(),
        transport: ordinary.clone(),
        config: DeliveryRuntimeConfig {
            max_safe_retries: 0,
            pause_dispatch: false,
            bots: std::collections::BTreeMap::from([(
                "bot".into(),
                DeliveryRuntimePolicy { max_running: 2, min_send_interval_ms: 0 },
            )]),
            tick: Duration::from_millis(5),
            expiry_tick: Duration::from_millis(50),
            io_timeout: Duration::from_secs(1),
            run_timeout: Duration::from_secs(30),
            cancel_timeout: Duration::from_secs(1),
            startup_recovery_grace: Duration::from_secs(2),
            max_tasks: 2,
            max_abort_tasks: 1,
        },
        caller_dispatch: Some(pipeline),
    };
    let (stop, shutdown) = tokio::sync::watch::channel(false);
    tokio::spawn(runtime.run(shutdown));
    // The sender side must outlive the runtime task (the e2e asserts the
    // row's convergence, not a graceful stop): dropping it would close the
    // watch and select-exit the run loop immediately.
    std::mem::forget(stop);
    (managed, sender, backfill)
}

#[tokio::test]
async fn a_ready_backfill_redrives_the_same_dispatch_and_converges_submission() {
    // Spec §6.8 (review-V B3.2): after the backfill's explicit history_ready,
    // the SAME dispatch — same id, same digest, same attempt, same proof —
    // is re-sent, never a new attempt; the row's submission follows through
    // the managed transitions (a trusted Bot event still settles terminal).
    let (managed, sender, backfill) = delegate_barrier_world(
        CallerDispatchAck::Accepted {
            control_revision: 0,
            request_digest: "sha256:not-the-facts-one".into(),
        },
        None, None,
    )
    .await;
    let row = wait_row(&managed, "delegate-backfill", |_| {
        sender.state.lock().unwrap().len() >= 2
    }).await;
    let sends = sender.state.lock().unwrap();
    assert_eq!(sends.len(), 2, "the SAME dispatch is re-sent exactly once");
    let original = &sends[0];
    let redrive = &sends[1];
    assert_eq!(original.association.dispatch_id, redrive.association.dispatch_id);
    assert_eq!(original.association.attempt, redrive.association.attempt);
    assert_eq!(original.request_digest, redrive.request_digest);
    assert_eq!(
        original.execution_token.expose(),
        redrive.execution_token.expose(),
        "the redrive carries the ORIGINAL proof — no re-issue, no new attempt"
    );
    let bodies = backfill.bodies.lock().unwrap();
    assert_eq!(bodies.len(), 1);
    assert_eq!(bodies[0].message_id, "prior-message");
    assert_eq!(bodies[0].text, "历史正文，不是当前任务");
    assert_eq!(bodies[0].caller_subject.expose(), original.caller_subject_ref.expose());
    assert_eq!(bodies[0].session_id, original.association.session_id);
    let calls = backfill.calls.lock().unwrap();
    assert_eq!(calls.len(), 1, "one history_complete attestation");
    assert_eq!(calls[0].snapshot_id, redrive.history.as_ref().map(|h| h.history_snapshot_id.as_str()).unwrap_or_default());
    assert_eq!(calls[0].manifest_digest, redrive.history.as_ref().map(|h| h.manifest_digest.as_str()).unwrap_or_default());
    // The row stays Dispatching-but-submitted: the runtime's submission
    // bookkeeping lands while the trusted terminal evidence still governs.
    assert!(matches!(row.state.status, Status::Dispatching));
    // The converger recorded the submission attempt — the request identity
    // never rotated (CAS-verified state kept the same attempt facts).
    assert_eq!(row.attempt_no, 1, "no new attempt on the same-dispatch redrive");
}

#[tokio::test]
async fn a_nonretryable_backfill_refusal_converges_the_barrier_closed() {
    // Spec §1842: a permission/closed refusal cannot use the history
    // exception — the barrier converges as a transport rejection, never by
    // re-admitting a new attempt.
    let (managed, sender, backfill) = delegate_barrier_world(
        CallerDispatchAck::Accepted {
            control_revision: 0,
            request_digest: "sha256:not-the-facts-one".into(),
        },
        Some(false), None,
    )
    .await;
    let row = wait_row(&managed, "delegate-backfill", |row| {
        matches!(row.state.status, Status::Failed)
    }).await;
    assert_eq!(sender.state.lock().unwrap().len(), 1, "the redrive never re-sent");
    assert!(backfill.calls.lock().unwrap().is_empty());
    assert_eq!(row.state.status, Status::Failed);
}


#[path = "caller_barrier_term_case.rs"]
mod barrier_term_case;

#[tokio::test]
async fn incomplete_body_replay_never_attests_or_resends() {
    for mode in ["no-repo", "body-refused", "attachment"] {
        let (managed, sender, backfill) = delegate_history_world(CallerDispatchAck::Unknown, None, None, mode).await;
        let row = wait_row(&managed, "delegate-backfill", |row| row.state.status == Status::Failed).await;
        assert_eq!(row.state.status, Status::Failed, "{mode}");
        assert_eq!(sender.state.lock().unwrap().len(), 1, "{mode}");
        assert!(backfill.calls.lock().unwrap().is_empty(), "{mode}");
    }
}

#[tokio::test]
async fn demotion_during_body_replay_never_attests_or_resends() {
    let term = Arc::new(std::sync::atomic::AtomicU64::new(1));
    let (_, sender, backfill) = delegate_history_world(CallerDispatchAck::Unknown, None, Some(term.clone()), "demote-body").await;
    tokio::time::timeout(Duration::from_secs(2), async {
        while term.load(std::sync::atomic::Ordering::SeqCst) == 1 { tokio::task::yield_now().await; }
    }).await.unwrap();
    tokio::time::sleep(Duration::from_millis(30)).await;
    assert_eq!(sender.state.lock().unwrap().len(), 1);
    assert!(backfill.calls.lock().unwrap().is_empty());
}
