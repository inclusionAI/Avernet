use super::*;

#[path = "loop_observability_recovery.rs"]
mod loop_observability_tests;
use std::sync::atomic::AtomicUsize;
use bcs_domain::{StateMachineDeliveryCorrelation, StateMachineNodeRun};
use bcs_service_api::{MarkHumanNodeRunningCommand, FailStateMachineNodeAttempt, StateMachineNodeAttemptFailure};

#[path = "failure_recovery.rs"]
mod failure_recovery_tests;

#[path = "judge_recovery.rs"]
mod judge_recovery_tests;

#[path = "opening_recovery.rs"]
mod opening_recovery_tests;

#[path = "dispatch_recovery.rs"]
mod dispatch_recovery_tests;

#[path = "recovery_sqlite.rs"]
mod sqlite_restart_tests;

#[path = "publication_recovery.rs"]
mod publication_recovery_tests;

#[path = "terminal_im_recovery.rs"]
mod terminal_im_tests;

struct FaultyRuns {
    fail_im_save: AtomicBool,
    fail_im_claim: AtomicBool,
    fail_im_marker: AtomicBool,
    fail_im_ack: AtomicBool,
    fail_publication_save: AtomicBool,
    fail_publication_claim: AtomicBool,
    fail_publication_marker: AtomicBool,
    fail_publication_finish: AtomicBool,
    inner: Arc<dyn StateMachineRunRepoPort>,
    fail_dispatch: AtomicBool,
    fail_dispatch_save: AtomicBool,
    fail_dispatch_claim: AtomicBool,
    fail_send_marker: AtomicBool,
    pause_after_send_marker: AtomicBool,
    fail_dispatch_finish: AtomicBool,
    fail_finalize: AtomicBool,
    fail_retry: AtomicBool,
    fail_run_failure: AtomicBool,
    fail_failure_write: AtomicBool,
    fail_judge_claim: AtomicBool,
    fail_judge_finish: AtomicBool,
    fail_opening_save: AtomicBool,
    fail_opening_mark: AtomicBool,
    hide_opening: AtomicBool,
    pause_start: AtomicBool,
    pause_opening_barrier: AtomicBool,
    startup_paused: Notify,
    fail_skip_after: AtomicUsize,
    cancel_before_dispatch: AtomicBool,
    cancel_after_activation: AtomicBool,
}

impl FaultyRuns {
    fn new(inner: Arc<dyn StateMachineRunRepoPort>) -> Self {
        Self { inner, fail_dispatch: AtomicBool::new(false),
            fail_im_save: AtomicBool::new(false), fail_im_claim: AtomicBool::new(false), fail_im_marker: AtomicBool::new(false), fail_im_ack: AtomicBool::new(false),
            fail_publication_save: AtomicBool::new(false), fail_publication_claim: AtomicBool::new(false),
            fail_publication_marker: AtomicBool::new(false), fail_publication_finish: AtomicBool::new(false),
            fail_dispatch_save: AtomicBool::new(false), fail_dispatch_claim: AtomicBool::new(false),
            fail_send_marker: AtomicBool::new(false), pause_after_send_marker: AtomicBool::new(false), fail_dispatch_finish: AtomicBool::new(false), fail_finalize: AtomicBool::new(false),
            fail_retry: AtomicBool::new(false), fail_run_failure: AtomicBool::new(false), fail_failure_write: AtomicBool::new(false),
            fail_judge_claim: AtomicBool::new(false), fail_judge_finish: AtomicBool::new(false),
            fail_opening_save: AtomicBool::new(false), fail_opening_mark: AtomicBool::new(false),
            hide_opening: AtomicBool::new(false), pause_start: AtomicBool::new(false),
            pause_opening_barrier: AtomicBool::new(false), startup_paused: Notify::new(),
            fail_skip_after: AtomicUsize::new(0), cancel_before_dispatch: AtomicBool::new(false),
            cancel_after_activation: AtomicBool::new(false) }
    }

    async fn before_dispatch(&self, run_id: &str) -> ServiceResult<()> {
        if self.cancel_before_dispatch.swap(false, Ordering::SeqCst) {
            self.inner.update_run_status(run_id, StateMachineRunStatus::Aborted, None, None, 100, Some(100)).await?;
        }
        if self.fail_dispatch.swap(false, Ordering::SeqCst) { return Err(injected()); }
        Ok(())
    }
}

fn injected() -> ServiceError { ServiceError::InternalError("injected progression write failure".into()) }

#[async_trait]
impl StateMachineRunRepoPort for FaultyRuns {
    async fn fail_missing_startup(&self, command: bcs_service_api::FailStateMachineStartup) -> ServiceResult<bool> { self.inner.fail_missing_startup(command).await }
    async fn get_startup_failure(&self, run: &str) -> ServiceResult<Option<bcs_service_api::StateMachineStartupFailure>> { self.inner.get_startup_failure(run).await }
    async fn fail_missing_dispatch(&self, run: &str, node: &str, attempt: i32, now: u64) -> ServiceResult<bool> { self.inner.fail_missing_dispatch(run, node, attempt, now).await }

    async fn save_terminal_im(&self, payload: bcs_service_api::StateMachineTerminalImPayload) -> ServiceResult<bool> {
        if self.fail_im_save.swap(false, Ordering::SeqCst) { return Err(injected()); }
        self.inner.save_terminal_im(payload).await
    }
    async fn get_terminal_im(&self, run: &str) -> ServiceResult<Option<bcs_service_api::StateMachineTerminalImCheckpoint>> { self.inner.get_terminal_im(run).await }
    async fn list_terminal_im_pending(&self, after: Option<&str>, limit: usize) -> ServiceResult<Vec<String>> { self.inner.list_terminal_im_pending(after, limit).await }
    async fn claim_terminal_im(&self, run: &str, owner: String, now: u64, until: u64) -> ServiceResult<Option<bcs_service_api::StateMachineTerminalImClaim>> {
        if self.fail_im_claim.swap(false, Ordering::SeqCst) { return Err(injected()); }
        self.inner.claim_terminal_im(run, owner, now, until).await
    }
    async fn update_terminal_im_progress(&self, claim: &bcs_service_api::StateMachineTerminalImClaim, expected: bcs_service_api::StateMachineTerminalImProgress, next: bcs_service_api::StateMachineTerminalImProgress, now: u64) -> ServiceResult<bool> {
        use bcs_service_api::StateMachineTerminalImDelivery as D;
        if next.deliveries.iter().zip(&expected.deliveries).any(|(n, e)| matches!(n, D::Sending) && n != e)
            && self.fail_im_marker.swap(false, Ordering::SeqCst) { return Err(injected()); }
        if next.deliveries.iter().zip(&expected.deliveries).any(|(n, e)| matches!(n, D::Delivered { .. }) && n != e)
            && self.fail_im_ack.swap(false, Ordering::SeqCst) { return Err(injected()); }
        self.inner.update_terminal_im_progress(claim, expected, next, now).await
    }
    async fn release_terminal_im(&self, claim: &bcs_service_api::StateMachineTerminalImClaim) -> ServiceResult<bool> { self.inner.release_terminal_im(claim).await }
    async fn supersede_terminal_im(&self, run: &str) -> ServiceResult<bool> { self.inner.supersede_terminal_im(run).await }

    async fn save_chat_result(&self, payload: bcs_service_api::StateMachineChatResultPayload) -> ServiceResult<bool> {
        if self.fail_publication_save.swap(false, Ordering::SeqCst) { return Err(injected()); }
        self.inner.save_chat_result(payload).await
    }
    async fn get_chat_result(&self, run: &str) -> ServiceResult<Option<bcs_service_api::StateMachineChatResultCheckpoint>> { self.inner.get_chat_result(run).await }
    async fn claim_chat_result(&self, run: &str, owner: String, now: u64, until: u64) -> ServiceResult<Option<bcs_service_api::StateMachineChatResultClaim>> {
        if self.fail_publication_claim.swap(false, Ordering::SeqCst) { return Err(injected()); }
        self.inner.claim_chat_result(run, owner, now, until).await
    }
    async fn begin_chat_result_send(&self, claim: &bcs_service_api::StateMachineChatResultClaim, now: u64) -> ServiceResult<bool> {
        if self.fail_publication_marker.swap(false, Ordering::SeqCst) { return Err(injected()); }
        self.inner.begin_chat_result_send(claim, now).await
    }
    async fn finish_chat_result(&self, claim: &bcs_service_api::StateMachineChatResultClaim, result: bcs_service_api::StateMachineChatResultOutcome, now: u64) -> ServiceResult<bool> {
        if self.fail_publication_finish.swap(false, Ordering::SeqCst) { return Err(injected()); }
        self.inner.finish_chat_result(claim, result, now).await
    }
    async fn expire_chat_result(&self, run: &str, now: u64) -> ServiceResult<bool> { self.inner.expire_chat_result(run, now).await }
    async fn release_chat_result(&self, claim: &bcs_service_api::StateMachineChatResultClaim) -> ServiceResult<bool> { self.inner.release_chat_result(claim).await }

    async fn save_node_dispatch(&self, payload: bcs_service_api::StateMachineDispatchPayload) -> ServiceResult<bool> {
        if self.fail_dispatch_save.swap(false, Ordering::SeqCst) { return Err(injected()); }
        self.inner.save_node_dispatch(payload).await
    }
    async fn get_node_dispatch(&self, run: &str, node: &str, attempt: i32) -> ServiceResult<Option<bcs_service_api::StateMachineDispatchCheckpoint>> { self.inner.get_node_dispatch(run, node, attempt).await }
    async fn claim_node_dispatch(&self, run: &str, node: &str, attempt: i32, owner: String, now: u64, until: u64) -> ServiceResult<Option<bcs_service_api::StateMachineDispatchClaim>> {
        if self.fail_dispatch_claim.swap(false, Ordering::SeqCst) { return Err(injected()); }
        self.inner.claim_node_dispatch(run, node, attempt, owner, now, until).await
    }
    async fn begin_node_dispatch_send(&self, claim: &bcs_service_api::StateMachineDispatchClaim, now: u64) -> ServiceResult<bool> {
        if self.fail_send_marker.swap(false, Ordering::SeqCst) { return Err(injected()); }
        let started = self.inner.begin_node_dispatch_send(claim, now).await?;
        if self.pause_after_send_marker.swap(false, Ordering::SeqCst) { self.startup_paused.notify_one(); std::future::pending::<()>().await; }
        Ok(started)
    }
    async fn finish_node_dispatch(&self, claim: &bcs_service_api::StateMachineDispatchClaim, result: bcs_service_api::StateMachineDispatchResult, now: u64) -> ServiceResult<bool> {
        if self.fail_dispatch_finish.swap(false, Ordering::SeqCst) { return Err(injected()); }
        self.inner.finish_node_dispatch(claim, result, now).await
    }
    async fn expire_node_dispatch(&self, command: FailStateMachineNodeAttempt) -> ServiceResult<bool> { self.inner.expire_node_dispatch(command).await }
    async fn release_node_dispatch(&self, claim: &bcs_service_api::StateMachineDispatchClaim) -> ServiceResult<bool> { self.inner.release_node_dispatch(claim).await }
    async fn supersede_inactive_node_dispatches(&self, run: &str) -> ServiceResult<()> { self.inner.supersede_inactive_node_dispatches(run).await }

    async fn save_run_opening(&self, payload: bcs_service_api::StateMachineOpeningPayload) -> ServiceResult<bool> {
        if self.fail_opening_save.swap(false, Ordering::SeqCst) { return Err(injected()); }
        self.inner.save_run_opening(payload).await
    }
    async fn get_run_opening(&self, run: &str) -> ServiceResult<Option<bcs_service_api::StateMachineOpeningCheckpoint>> {
        if self.hide_opening.load(Ordering::SeqCst) { return Ok(None); }
        self.inner.get_run_opening(run).await
    }
    async fn mark_run_opening_delivered(&self, run: &str, at: u64) -> ServiceResult<bool> {
        if self.pause_opening_barrier.swap(false, Ordering::SeqCst) {
            self.startup_paused.notify_one();
            std::future::pending::<()>().await;
        }
        if self.fail_opening_mark.swap(false, Ordering::SeqCst) { return Err(injected()); }
        self.inner.mark_run_opening_delivered(run, at).await
    }
    async fn list_pending_runs(&self, cursor: Option<&str>, limit: usize) -> ServiceResult<Vec<StateMachineRun>> {
        self.inner.list_pending_runs(cursor, limit).await
    }

    async fn begin_node_judging(&self, run: &str, node: &str, attempt: i32, artifact: String, responder: Option<String>) -> ServiceResult<bool> {
        self.inner.begin_node_judging(run, node, attempt, artifact, responder).await
    }
    async fn claim_node_judging(&self, run: &str, node: &str, attempt: i32, owner: String, now: u64, until: u64) -> ServiceResult<Option<bcs_service_api::StateMachineJudgeClaim>> {
        if self.fail_judge_claim.swap(false, Ordering::SeqCst) { return Err(injected()); }
        self.inner.claim_node_judging(run, node, attempt, owner, now, until).await
    }
    async fn release_node_judging(&self, claim: &bcs_service_api::StateMachineJudgeClaim) -> ServiceResult<bool> {
        self.inner.release_node_judging(claim).await
    }
    async fn commit_eventful_transition(&self, transition: bcs_service_api::port::repo::StateMachineEventfulTransition) -> ServiceResult<bool> {
        if matches!(&transition, bcs_service_api::port::repo::StateMachineEventfulTransition::FinishJudge(_))
            && self.fail_judge_finish.swap(false, Ordering::SeqCst) { return Err(injected()); }
        self.inner.commit_eventful_transition(transition).await
    }

    async fn create_run(&self, run: StateMachineRun, nodes: Vec<StateMachineNodeRun>) -> ServiceResult<()> {
        self.inner.create_run(run, nodes).await
    }
    async fn get_run(&self, id: &str) -> ServiceResult<Option<StateMachineRun>> { self.inner.get_run(id).await }
    async fn get_run_by_session_id(&self, id: &str) -> ServiceResult<Option<StateMachineRun>> { self.inner.get_run_by_session_id(id).await }
    async fn list_running_runs(&self, cursor: Option<&str>, limit: usize) -> ServiceResult<Vec<StateMachineRun>> {
        self.inner.list_running_runs(cursor, limit).await
    }
    async fn list_node_runs(&self, id: &str) -> ServiceResult<Vec<StateMachineNodeRun>> { self.inner.list_node_runs(id).await }
    async fn get_node_run(&self, run: &str, node: &str) -> ServiceResult<Option<StateMachineNodeRun>> { self.inner.get_node_run(run, node).await }
    async fn mark_node_running(&self, run: &str, node: &str, attempt: i32, delivery: String, at: u64) -> ServiceResult<()> {
        self.inner.mark_node_running(run, node, attempt, delivery, at).await
    }
    async fn mark_node_running_if_run_active(&self, run: &str, node: &str, attempt: i32, delivery: String, at: u64) -> ServiceResult<bool> {
        self.before_dispatch(run).await?;
        let marked = self.inner.mark_node_running_if_run_active(run, node, attempt, delivery, at).await?;
        if self.cancel_after_activation.swap(false, Ordering::SeqCst) {
            self.inner.update_run_status(run, StateMachineRunStatus::Aborted, None, None, at, Some(at)).await?;
        }
        Ok(marked)
    }
    async fn complete_node_attempt(&self, run: &str, node: &str, attempt: i32, outcome: String, artifact: String, responder: Option<String>, at: u64) -> ServiceResult<bool> {
        self.inner.complete_node_attempt(run, node, attempt, outcome, artifact, responder, at).await
    }
    async fn record_node_artifact_if_running(&self, run: &str, node: &str, attempt: i32, artifact: String) -> ServiceResult<bool> {
        self.inner.record_node_artifact_if_running(run, node, attempt, artifact).await
    }
    async fn record_human_response_if_running(&self, run: &str, node: &str, attempt: i32, artifact: String, responder: String) -> ServiceResult<bool> {
        self.inner.record_human_response_if_running(run, node, attempt, artifact, responder).await
    }
    async fn mark_human_node_running_if_run_active(&self, command: MarkHumanNodeRunningCommand) -> ServiceResult<bool> {
        self.before_dispatch(&command.run_id).await?;
        self.inner.mark_human_node_running_if_run_active(command).await
    }
    async fn fail_node_attempt(&self, run: &str, node: &str, attempt: i32, error: String, at: u64) -> ServiceResult<bool> {
        self.inner.fail_node_attempt(run, node, attempt, error, at).await
    }
    async fn fail_node_attempt_with_action(&self, command: FailStateMachineNodeAttempt) -> ServiceResult<bool> {
        if self.fail_failure_write.swap(false, Ordering::SeqCst) { return Err(injected()); }
        self.inner.fail_node_attempt_with_action(command).await
    }
    async fn get_node_attempt_failure(&self, run: &str, node: &str, attempt: i32) -> ServiceResult<Option<StateMachineNodeAttemptFailure>> {
        self.inner.get_node_attempt_failure(run, node, attempt).await
    }
    async fn schedule_node_retry(&self, run: &str, node: &str, failed: i32, next: i32) -> ServiceResult<bool> {
        if self.fail_retry.swap(false, Ordering::SeqCst) { return Err(injected()); }
        self.inner.schedule_node_retry(run, node, failed, next).await
    }
    async fn skip_node(&self, run: &str, node: &str, at: u64) -> ServiceResult<bool> {
        if self.fail_skip_after.fetch_update(Ordering::SeqCst, Ordering::SeqCst, |left| left.checked_sub(1)) == Ok(1) {
            return Err(injected());
        }
        self.inner.skip_node(run, node, at).await
    }
    async fn update_run_status(&self, run: &str, status: StateMachineRunStatus, output: Option<String>, error: Option<String>, at: u64, completed: Option<u64>) -> ServiceResult<bool> {
        if status == StateMachineRunStatus::Running && self.pause_start.swap(false, Ordering::SeqCst) {
            self.startup_paused.notify_one();
            std::future::pending::<()>().await;
        }
        if status == StateMachineRunStatus::Completed && self.fail_finalize.swap(false, Ordering::SeqCst) { return Err(injected()); }
        if status == StateMachineRunStatus::Failed && self.fail_run_failure.swap(false, Ordering::SeqCst) { return Err(injected()); }
        self.inner.update_run_status(run, status, output, error, at, completed).await
    }
    async fn upsert_delivery_correlation(&self, correlation: StateMachineDeliveryCorrelation) -> ServiceResult<()> { self.inner.upsert_delivery_correlation(correlation).await }
    async fn register_delivery_alias(&self, delivery: &str, alias: String) -> ServiceResult<()> { self.inner.register_delivery_alias(delivery, alias).await }
    async fn lookup_delivery_correlation(&self, id: &str) -> ServiceResult<Option<StateMachineDeliveryCorrelation>> { self.inner.lookup_delivery_correlation(id).await }
}

async fn install_runtime(h: &mut Harness, runs: Arc<dyn StateMachineRunRepoPort>, outcomes: &[&str]) {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.unwrap();
    let judge = Arc::new(SequencedJudge::new(outcomes.iter().map(|outcome| JudgeDecision {
        outcome: (*outcome).into(), reason: "recover test".into(), confidence: 1.0,
        checked_criteria: Vec::new(), retry_instruction: String::new(), raw_response: None,
    }).collect()));
    h.runtime = test_runtime!(h.definitions.clone(), h.store.clone(), runs, h.store.clone(), group,
        h.sessions.clone(), h.delivery.clone(), judge)
        .with_session_channel_outbound(h.channel.clone()).with_loop_execution();
}

async fn harness(outcomes: &[&str]) -> (Harness, Arc<FaultyRuns>) {
    let mut h = Harness::new(&[]).await;
    let runs = Arc::new(FaultyRuns::new(h.store.clone()));
    install_runtime(&mut h, runs.clone(), outcomes).await;
    (h, runs)
}

async fn terminal(h: &Harness, index: usize, run: &StateMachineRun, text: &str) -> Result<bcs_service_api::HandleBotTerminalEventOutcome, CollaborationRuntimeError> {
    let id = h.delivery.commands.lock().await[index].run_id.clone();
    h.runtime.handle_bot_terminal_event(bcs_service_api::HandleBotTerminalEventCommand {
        bot_id: "driver-bot".into(), run_id: id.clone(), event_type: "chat.event".into(),
        event_payload: json!({"run_id": id, "state": "final", "message": {"content": [{"type": "text", "text": text}]}}),
        state: ChatEventState::Final, bcs_session_id: Some(run.session_id.clone()),
    }).await
}

async fn recover(h: &Harness) {
    let page = h.runtime.recover_state_machine_progression(None, 32).await.unwrap();
    assert!(page.failures.is_empty(), "{:?}", page.failures);
}

#[tokio::test]
async fn completed_loop_result_recovers_after_dispatch_write_failure_without_rerunning_judge() {
    let (mut h, runs) = harness(&["again"]).await;
    let started = h.start(loop_yaml(3, true, false, 1), false).await;
    let run = &started.view.run;
    runs.fail_dispatch.store(true, Ordering::SeqCst);
    assert!(terminal(&h, 0, run, "immutable result").await.is_err());
    assert_eq!(h.delivery.commands.lock().await.len(), 1);
    let plan = h.plan(&run.run_id).await;
    let first = h.store.get_node_run(&run.run_id, &iteration_id(&plan, 1)).await.unwrap().unwrap();
    assert_eq!(first.status, StateMachineNodeStatus::Completed);
    // A new runtime has no original Judge decisions or compilation limits.
    h.definitions.hide_definitions.store(true, Ordering::SeqCst);
    install_runtime(&mut h, runs, &[]).await;
    h.runtime = h.runtime.with_fixed_loop_limits(bcs_config_api::FixedLoopLimits { max_fixed_loop_iterations: 1, ..Default::default() });
    recover(&h).await;
    assert_eq!(h.delivery.commands.lock().await.len(), 2);
    assert_eq!(h.prompt(1).await.matches("immutable result").count(), 1);
    assert!(h.prompt(1).await.contains("iteration: 2"));
    recover(&h).await;
    assert_eq!(h.delivery.commands.lock().await.len(), 2);
    let after = h.store.get_node_run(&run.run_id, &first.node_id).await.unwrap().unwrap();
    assert_eq!(serde_json::to_value(first).unwrap(), serde_json::to_value(after).unwrap());
}

#[tokio::test]
async fn partial_future_skip_is_repaired_through_already_skipped_nodes() {
    let (h, runs) = harness(&["done"]).await;
    let started = h.start(loop_yaml(4, true, false, 1), false).await;
    let run = &started.view.run;
    runs.fail_skip_after.store(2, Ordering::SeqCst);
    assert!(terminal(&h, 0, run, "break-result").await.is_err());
    let before = h.store.list_node_runs(&run.run_id).await.unwrap();
    assert_eq!(before.iter().filter(|node| node.status == StateMachineNodeStatus::Skipped).count(), 1);
    recover(&h).await;
    let after = h.store.list_node_runs(&run.run_id).await.unwrap();
    assert_eq!(after.iter().filter(|node| node.status == StateMachineNodeStatus::Skipped).count(), 4);
    assert_eq!(h.delivery.commands.lock().await.len(), 2);
    assert!(h.prompt(1).await.contains("break-result"));
    assert_eq!(h.finish(1, run, "published").await.view.unwrap().run.status, StateMachineRunStatus::Completed);
}

#[tokio::test]
async fn exhausted_route_and_service_finalization_recover_without_changing_real_outcome() {
    let (h, runs) = harness(&["again"]).await;
    let started = h.start(loop_yaml(1, true, false, 1), false).await;
    let run = &started.view.run;
    runs.fail_dispatch.store(true, Ordering::SeqCst);
    assert!(terminal(&h, 0, run, "last-result").await.is_err());
    recover(&h).await;
    assert!(h.prompt(1).await.contains("Resolve exhausted loop."));
    h.finish(1, run, "fallback").await;
    runs.fail_finalize.store(true, Ordering::SeqCst);
    assert!(terminal(&h, 2, run, "published").await.is_err());
    assert_eq!(h.store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Running);
    recover(&h).await;
    assert_eq!(h.store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Completed);
    assert_eq!(h.delivery.commands.lock().await.len(), 3);
}

#[tokio::test]
async fn concurrent_recovery_and_cancel_before_target_cas_do_not_double_dispatch() {
    for cancel in [false, true] {
        let (h, runs) = harness(&[]).await;
        let started = h.start(loop_yaml(2, false, false, 1), false).await;
        let run = &started.view.run;
        runs.fail_dispatch.store(true, Ordering::SeqCst);
        assert!(terminal(&h, 0, run, "first").await.is_err());
        runs.cancel_before_dispatch.store(cancel, Ordering::SeqCst);
        let (a, b) = tokio::join!(h.runtime.recover_state_machine_progression(None, 32), h.runtime.recover_state_machine_progression(None, 32));
        assert!(a.unwrap().failures.is_empty());
        assert!(b.unwrap().failures.is_empty());
        assert_eq!(h.delivery.commands.lock().await.len(), if cancel { 1 } else { 2 });
    }
}

#[tokio::test]
async fn recovery_returns_write_and_snapshot_failures_and_never_resends_a_running_attempt() {
    let (h, runs) = harness(&[]).await;
    let started = h.start(loop_yaml(2, false, false, 1), false).await;
    recover(&h).await;
    assert_eq!(h.delivery.commands.lock().await.len(), 1);
    runs.fail_dispatch.store(true, Ordering::SeqCst);
    assert!(terminal(&h, 0, &started.view.run, "first").await.is_err());
    runs.fail_dispatch.store(true, Ordering::SeqCst);
    let page = h.runtime.recover_state_machine_progression(None, 32).await.unwrap();
    assert_eq!(page.failures.len(), 1);
    assert!(page.failures[0].error.contains("injected"));
    assert_eq!(page.reconciled, 0);
    h.definitions.corrupt_read.store(true, Ordering::SeqCst);
    let page = h.runtime.recover_state_machine_progression(None, 32).await.unwrap();
    assert_eq!(page.failures.len(), 1);
    assert!(page.failures[0].error.contains("snapshot"));
    assert_eq!(h.delivery.commands.lock().await.len(), 1);
}

#[tokio::test]
async fn ordinary_dag_recovers_partial_skip_and_parallel_join_with_loop_disabled() {
    let (mut h, runs) = harness(&["approved"]).await;
    h.runtime = h.runtime.with_loop_execution_enabled(false);
    let started = h.start(judge_branch_yaml(), false).await;
    runs.fail_skip_after.store(2, Ordering::SeqCst);
    assert!(terminal(&h, 0, &started.view.run, "approved-result").await.is_err());
    recover(&h).await;
    assert!(h.prompt(1).await.contains("Publish final answer."));
    assert_eq!(h.finish(1, &started.view.run, "published").await.view.unwrap().run.status, StateMachineRunStatus::Completed);

    let (mut h, runs) = harness(&[]).await;
    h.runtime = h.runtime.with_loop_execution_enabled(false);
    let started = h.start(join_yaml(), false).await;
    runs.fail_dispatch.store(true, Ordering::SeqCst);
    assert!(terminal(&h, 0, &started.view.run, "start").await.is_err());
    recover(&h).await;
    assert_eq!(h.delivery.commands.lock().await.len(), 3);
    h.finish(1, &started.view.run, "branch-b").await;
    recover(&h).await;
    assert_eq!(h.delivery.commands.lock().await.len(), 3, "join still waits for C");
    runs.fail_dispatch.store(true, Ordering::SeqCst);
    assert!(terminal(&h, 2, &started.view.run, "branch-c").await.is_err());
    recover(&h).await;
    assert_eq!(h.delivery.commands.lock().await.len(), 4);
    assert!(h.prompt(3).await.contains("branch-b") && h.prompt(3).await.contains("branch-c"));
}

#[tokio::test]
async fn human_next_iteration_recovers_with_context_and_is_not_notified_twice() {
    let (h, runs) = harness(&[]).await;
    let started = h.start(loop_yaml(2, false, true, 1), true).await;
    let plan = h.plan(&started.view.run.run_id).await;
    runs.fail_dispatch.store(true, Ordering::SeqCst);
    assert!(h.runtime.respond_human_node(RespondHumanNodeCommand {
        run_id: started.view.run.run_id.clone(), node_id: iteration_id(&plan, 1),
        caller_actor_id: "human_1001".into(), content: "persisted-human-result".into(), source: HumanResponseSource::Http,
    }).await.is_err());
    recover(&h).await;
    recover(&h).await;
    let events = h.channel.events.lock().await;
    assert_eq!(events.len(), 2);
    assert_eq!(events[1].loop_context.as_ref().unwrap().previous_result.as_ref().unwrap().output, "persisted-human-result");
}

#[tokio::test]
async fn a_corrupt_run_does_not_starve_the_next_cursor_page() {
    let (h, runs) = harness(&[]).await;
    let started = h.start(loop_yaml(2, false, false, 1), false).await;
    runs.fail_dispatch.store(true, Ordering::SeqCst);
    assert!(terminal(&h, 0, &started.view.run, "first").await.is_err());
    let mut poison = started.view.run.clone();
    poison.run_id = "a-missing-snapshot".into();
    // Merge the Pending and Running pages under one shared exclusive cursor.
    poison.status = StateMachineRunStatus::Pending;
    h.store.create_run(poison, Vec::new()).await.unwrap();
    let first = h.runtime.recover_state_machine_progression(None, 1).await.unwrap();
    assert_eq!(first.scanned, 1);
    assert_eq!(first.failures.len(), 1);
    assert_eq!(first.next_run_id.as_deref(), Some("a-missing-snapshot"));
    let second = h.runtime.recover_state_machine_progression(first.next_run_id, 1).await.unwrap();
    assert_eq!(second.scanned, 1);
    assert!(second.failures.is_empty());
    assert_eq!(h.delivery.commands.lock().await.len(), 2);
    let end = h.runtime.recover_state_machine_progression(second.next_run_id, 1).await.unwrap();
    assert_eq!(end.scanned, 0);
    assert!(end.next_run_id.is_none());
}

#[tokio::test]
async fn chat_finalization_with_a_saved_acknowledgement_is_not_republished() {
    let (mut h, runs) = harness(&[]).await;
    let publisher = Arc::new(RecordingResultPublisher::default());
    h.runtime = h.runtime.with_result_publisher(publisher.clone());
    let session = h.sessions.create_or_reactivate(CreateOrReactivateCommand {
        group_id: "group-1".into(), session_id: None,
        params: NewSessionParams { session_kind: SessionKind::Chat, participants: test_group().participants, ..Default::default() },
    }).await.unwrap().session;
    let mut start = command(single_node_yaml(), false);
    start.session_id = Some(session.id);
    start.caller_id = Some("driver-bot".into());
    let started = h.runtime.start_state_machine_run(start).await.unwrap();
    runs.fail_finalize.store(true, Ordering::SeqCst);
    assert!(terminal(&h, 0, &started.view.run, "published-before-crash").await.is_err());
    let page = h.runtime.recover_state_machine_progression(None, 32).await.unwrap();
    assert!(page.failures.is_empty(), "{:?}", page.failures);
    assert_eq!(h.store.get_run(&started.view.run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Completed);
    assert_eq!(publisher.commands.lock().await.len(), 1);
}

#[tokio::test]
async fn cancel_after_activation_is_rechecked_before_external_send() {
    let (h, runs) = harness(&[]).await;
    let started = h.start(loop_yaml(2, false, false, 1), false).await;
    runs.fail_dispatch.store(true, Ordering::SeqCst);
    assert!(terminal(&h, 0, &started.view.run, "first").await.is_err());
    runs.cancel_after_activation.store(true, Ordering::SeqCst);
    recover(&h).await;
    assert_eq!(h.delivery.commands.lock().await.len(), 1);
    assert_eq!(h.store.get_run(&started.view.run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Aborted);
}

#[tokio::test]
async fn incomplete_startup_is_reported_without_guessing_an_opening_or_dispatch() {
    let (h, runs) = harness(&[]).await;
    runs.fail_dispatch.store(true, Ordering::SeqCst);
    assert!(h.runtime.start_state_machine_run(command(loop_yaml(2, false, false, 1), false)).await.is_err());
    runs.hide_opening.store(true, Ordering::SeqCst);
    let page = h.runtime.recover_state_machine_progression(None, 32).await.unwrap();
    assert_eq!(page.failures.len(), 1);
    assert!(page.failures[0].error.contains("startup recovery requires"));
    assert!(h.delivery.commands.lock().await.is_empty());
}

#[tokio::test]
async fn committed_retry_recovers_the_same_iteration_and_attempt_with_unchanged_context() {
    for iteration in [1, 2] {
        let (h, runs) = harness(&[]).await;
        let started = h.start(loop_yaml(3, false, false, 2), false).await;
        let run = &started.view.run;
        if iteration == 2 { h.finish(0, run, "previous-result").await; }
        let index = (iteration - 1) as usize;
        let original = h.prompt(index).await;
        let id = h.delivery.commands.lock().await[index].run_id.clone();
        runs.fail_dispatch.store(true, Ordering::SeqCst);
        assert!(h.runtime.handle_bot_terminal_event(bcs_service_api::HandleBotTerminalEventCommand {
            bot_id: "driver-bot".into(), run_id: id.clone(), event_type: "chat.event".into(),
            event_payload: json!({"run_id": id, "state": "error", "error": "retryable"}),
            state: ChatEventState::Error, bcs_session_id: Some(run.session_id.clone()),
        }).await.is_err());
        let plan = h.plan(&run.run_id).await;
        let node = h.store.get_node_run(&run.run_id, &iteration_id(&plan, iteration)).await.unwrap().unwrap();
        assert_eq!(node.status, StateMachineNodeStatus::RetryScheduled);
        assert_eq!(node.attempt, 1);
        recover(&h).await;
        assert_eq!(h.prompt(index + 1).await, original);
        recover(&h).await;
        assert_eq!(h.delivery.commands.lock().await.len(), index + 2);
    }
}

#[tokio::test]
async fn scanner_recovers_missed_human_notification_from_snapshot_and_preserves_deadline() {
    let (h, _) = harness(&[]).await;
    h.channel.fail_human_publish.store(true, Ordering::SeqCst);
    let started = h.start(loop_yaml(2, false, true, 1), true).await;
    assert!(h.channel.events.lock().await.is_empty());
    let run = &started.view.run;
    let node = h.store.list_node_runs(&run.run_id).await.unwrap().into_iter().find(|n| n.status == StateMachineNodeStatus::Running).unwrap();
    h.definitions.hide_definitions.store(true, Ordering::SeqCst);
    h.channel.fail_human_publish.store(true, Ordering::SeqCst);
    let failed = h.runtime.recover_state_machine_progression(None, 32).await.unwrap();
    assert_eq!(failed.failures.len(), 1);
    recover(&h).await; recover(&h).await;
    let events = h.channel.events.lock().await;
    assert_eq!(events.len(), 1); assert!(h.channel.human_publish_calls.load(Ordering::SeqCst) >= 4);
    assert_eq!(events[0].timeout_deadline_ms, node.timeout_deadline_ms);
    assert_eq!(events[0].node_id, node.node_id);
    assert_eq!(events[0].loop_context.as_ref().unwrap().iteration, 1);
    drop(events);
    assert!(h.runtime.human_input_notification_is_current(&run.run_id, &run.session_id, &node.node_id, node.timeout_deadline_ms.unwrap()).await.unwrap());
    assert!(!h.runtime.human_input_notification_is_current(&run.run_id, "different-session", &node.node_id, node.timeout_deadline_ms.unwrap()).await.unwrap());
    assert!(!h.runtime.human_input_notification_is_current(&run.run_id, &run.session_id, &node.node_id, node.timeout_deadline_ms.unwrap() + 1).await.unwrap());
    assert!(!h.runtime.human_input_notification_is_current(&run.run_id, &run.session_id, &node.node_id, 1).await.unwrap());
    let calls = h.channel.human_publish_calls.load(Ordering::SeqCst);
    h.runtime.cancel_state_machine_run(bcs_service_api::CancelStateMachineRunCommand { run_id: run.run_id.clone(), reason: Some("cancel recovery".into()) }).await.unwrap();
    recover(&h).await;
    assert_eq!(h.channel.human_publish_calls.load(Ordering::SeqCst), calls);
    assert!(!h.runtime.human_input_notification_is_current(&run.run_id, &run.session_id, &node.node_id, node.timeout_deadline_ms.unwrap()).await.unwrap());
    assert!(h.delivery.commands.lock().await.is_empty());
}
