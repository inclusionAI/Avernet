use super::*;
use bcs_service_api::TaskHandleStatus as S;

async fn assign(f: &Fixture, id: &str, timeout: Option<u64>) -> PersistedMessageDelivery {
    let mut cmd = dispatch_command();
    cmd.payload["assignment_intent_id"] = json!(format!("bcs_intent_{id}"));
    if let Some(timeout) = timeout { cmd.payload["timeout_s"] = json!(timeout); }
    let task = f.flow.handle_task_dispatch(cmd).await.unwrap();
    f.rows().await.into_iter().find(|r| r.semantic_projection_json["task"]["task_id"] == task.task_id).unwrap()
}
async fn state(f: &Fixture, value: &str) -> S {
    f.flow.task_status(vec![value.into()]).await.unwrap().remove(0).status
}

#[tokio::test]
async fn query_admission_acceptance_result_and_restart() {
    let f = Fixture::new().await;
    assert_eq!(state(&f, "T-11111111").await, S::NotFound);
    let row = assign(&f, "11111111000000000000000000000000", None).await;
    for reference in ["T-11111111", "11111111", "bcs_intent_11111111000000000000000000000000"] {
        assert_eq!(state(&f, reference).await, S::Queued);
    }
    let row = f.start(&row).await;
    assert_eq!(state(&f, "11111111").await, S::Queued, "StartSend is not acceptance");
    let row = f.transition(&row, Event::Accepted).await;
    assert_eq!(state(&f, "11111111").await, S::Running);
    f.flow.handle_bot_event(final_event(&row)).await.unwrap();
    assert_eq!(state(&f, "11111111").await, S::Completed);
    let restarted = Fixture::make_flow(&f.support, &f.service, &f.repo, &f.live).await;
    assert_eq!(restarted.task_status(vec!["11111111".into()]).await.unwrap()[0].status, S::Completed);
}

#[tokio::test]
async fn collision_never_cancels_and_longer_ref_disambiguates() {
    let f = Fixture::new().await;
    assign(&f, "abcdef12000000000000000000000000", None).await;
    assign(&f, "abcdef12ffffffffffffffffffffffff", None).await;
    assert_eq!(state(&f, "T-abcdef12").await, S::Ambiguous);
    assert_eq!(f.flow.cancel_task("T-abcdef12".into(), "stop".into()).await.unwrap().status, S::Ambiguous);
    assert!(f.rows().await.iter().all(|r| r.state.status == Status::Queued));
    assert_eq!(f.flow.cancel_task("abcdef120".into(), "requested stop".into()).await.unwrap().status, S::Cancelled);
    assert_eq!(f.flow.cancel_task("abcdef120".into(), "again".into()).await.unwrap().status, S::Cancelled);
    let rows = f.rows().await;
    assert_eq!(rows.iter().filter(|r| r.semantic_projection_json["task"]["leg"] == "result").count(), 1);
    assert_eq!(rows.iter().find(|r| r.state.status == Status::Cancelled).unwrap().cancel_reason.as_deref(), Some("requested stop"));
    assert_eq!(state(&f, "abcdef12f").await, S::Queued);
}

#[tokio::test]
async fn cancel_wait_timeout_keeps_intent_then_final_wins() {
    let f = Fixture::new().await;
    f.live.snapshot.write().await.policy.task_cancel_wait_ms = 100;
    let row = assign(&f, "12345678000000000000000000000000", None).await;
    let row = f.start(&row).await;
    assert_eq!(f.flow.cancel_task("12345678".into(), "stop".into()).await.unwrap().status, S::Unknown);
    assert_eq!(state(&f, "12345678").await, S::Unknown);
    f.flow.handle_bot_event(final_event(&row)).await.unwrap();
    assert_eq!(f.flow.cancel_task("12345678".into(), "stop again".into()).await.unwrap().status, S::AlreadyFinished);
}

#[tokio::test]
async fn absent_cancel_is_not_a_tombstone_and_timeout_is_clamped() {
    let f = Fixture::new().await;
    assert_eq!(f.flow.cancel_task("23456789".into(), "stop".into()).await.unwrap().status, S::NotFound);
    f.live.snapshot.write().await.policy.task_timeout_min_s = 60;
    f.live.snapshot.write().await.policy.task_timeout_max_s = 120;
    let row = assign(&f, "23456789000000000000000000000000", Some(1)).await;
    assert_eq!(row.semantic_projection_json["task"]["timeout_s"], 60);
    assert_eq!(row.run_deadline_at_ms, None, "queue admission must not start runtime deadline");
    assert_eq!(state(&f, "23456789").await, S::Queued);
}

#[tokio::test]
async fn definite_rejection_is_durable_queryable_and_wakes_manager_once() {
    let f = Fixture::new().await;
    let mut cmd = dispatch_command();
    cmd.target_bot_id = "missing".into();
    cmd.payload["assignment_intent_id"] = json!("bcs_intent_34567890000000000000000000000000");
    assert!(f.flow.handle_task_dispatch(cmd.clone()).await.is_err());
    assert!(f.flow.handle_task_dispatch(cmd).await.is_err());
    assert_eq!(state(&f, "34567890").await, S::Failed);
    let rows = f.rows().await;
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].target_bot_id, "bot-driver");
    assert_eq!(rows[0].flow_kind, DeliveryFlowKind::System);
    let source = f.repo.get_message_by_id(SESSION, &rows[0].source_message_id).await.unwrap().unwrap();
    assert!(source.content["text"].as_str().unwrap().starts_with("[任务派发失败] ref=T-34567890"));
    f.start(&rows[0]).await;
    assert_eq!(f.support.bot_delivery.frames().await.len(), 1);
}

#[tokio::test]
async fn runtime_honors_custom_timeout_from_send_start_and_stops_once() {
    use bcs_message_flow::delivery_runtime::{DeliveryRuntime, DeliveryRuntimeConfig};
    use std::time::Duration;
    let f = Fixture::new().await;
    let row = assign(&f, "45678901000000000000000000000000", Some(1)).await;
    let runtime = DeliveryRuntime { caller_dispatch:None, policy:Some(f.live.clone()), service:f.service.clone(),
        preparation:Arc::new(QueuedGroupPreparation { flow:Arc::downgrade(&f.flow), deliveries:f.service.clone() }),
        transport:f.support.bot_delivery.clone(), config:DeliveryRuntimeConfig {
            max_safe_retries:0, pause_dispatch:false, bots:Default::default(), tick:Duration::from_millis(2),
            expiry_tick:Duration::from_millis(20), io_timeout:Duration::from_secs(1), run_timeout:Duration::from_secs(1800),
            cancel_timeout:Duration::from_secs(1), startup_recovery_grace:Duration::ZERO, max_tasks:4, max_abort_tasks:1 } };
    let (stop, shutdown) = tokio::sync::watch::channel(false);
    let job = tokio::spawn(runtime.run(shutdown));
    f.wait_frames(1).await;
    let started = f.rows().await.into_iter().find(|r| r.delivery_id == row.delivery_id).unwrap();
    assert_eq!(started.run_deadline_at_ms.unwrap() - started.send_started_at_ms.unwrap(), 1000);
    tokio::time::sleep(Duration::from_millis(200)).await;
    assert_eq!(f.support.bot_delivery.frames().await.len(), 1, "no soft reminder");
    tokio::time::timeout(Duration::from_secs(4), async {
        while state(&f, "45678901").await != S::TimedOut { tokio::time::sleep(Duration::from_millis(10)).await; }
    }).await.unwrap();
    assert_eq!(f.support.bot_delivery.aborts().await.len(), 1);
    stop.send(true).unwrap(); job.await.unwrap().unwrap();
}

#[tokio::test]
async fn activity_is_soft_and_only_returned_while_running() {
    let f = Fixture::new().await;
    let row = assign(&f, "56789012000000000000000000000000", None).await;
    let row = f.start(&row).await;
    let row = f.transition(&row, Event::Accepted).await;
    let mut delta = final_event(&row); delta.state = ChatEventState::Delta;
    delta.event_payload = json!({"delta_text":"working"});
    f.flow.handle_bot_event(delta).await.unwrap();
    let view = f.flow.task_status(vec!["56789012".into()]).await.unwrap().remove(0);
    assert_eq!(view.status, S::Running); assert!(view.running_s.is_some()); assert!(view.idle_s.is_some());
    let restarted = Fixture::make_flow(&f.support, &f.service, &f.repo, &f.live).await;
    let view = restarted.task_status(vec!["56789012".into()]).await.unwrap().remove(0);
    assert_eq!(view.status, S::Running); assert!(view.idle_s.is_none());
}

#[tokio::test]
async fn rejection_works_with_task_only_policy_and_manager_queue_off() {
    let f = Fixture::new().await;
    let mut policy = f.live.snapshot.read().await.policy.clone();
    policy.flow_enabled.group = false; policy.flow_enabled.system = false;
    policy.bots.insert("bot-driver".into(), bcs_config_api::message_delivery::BotDeliveryOverride {
        mode:Some(BotDeliveryMode::Off), ..Default::default()
    });
    f.flow.replace_delivery_policy(admin(), 1, policy).await.unwrap();
    let mut cmd = dispatch_command(); cmd.target_bot_id = "missing".into();
    cmd.payload["assignment_intent_id"] = json!("bcs_intent_67890123000000000000000000000000");
    assert!(f.flow.handle_task_dispatch(cmd).await.is_err());
    assert_eq!(state(&f, "67890123").await, S::Failed);
    assert_eq!(f.rows().await.len(), 1);
}

#[tokio::test]
async fn legacy_worker_managed_manager_timeout_remains_timed_out() {
    let f = Fixture::new().await;
    let mut policy = f.live.snapshot.read().await.policy.clone();
    policy.bots.insert("bot-observer".into(), bcs_config_api::message_delivery::BotDeliveryOverride {
        mode:Some(BotDeliveryMode::Off), ..Default::default()
    });
    f.flow.replace_delivery_policy(admin(), 1, policy).await.unwrap();
    let mut cmd = dispatch_command();
    cmd.payload["assignment_intent_id"] = json!("bcs_intent_78901234000000000000000000000000");
    cmd.payload["timeout_s"] = json!(1);
    f.flow.handle_task_dispatch(cmd).await.unwrap();
    tokio::time::timeout(std::time::Duration::from_secs(4), async {
        while state(&f, "78901234").await != S::TimedOut { tokio::time::sleep(std::time::Duration::from_millis(10)).await; }
    }).await.unwrap();
    let rows = f.rows().await;
    let result = rows.iter().find(|row| row.semantic_projection_json["task"]["leg"] == "result").unwrap();
    let message = f.repo.get_message_by_id(SESSION, &result.source_message_id).await.unwrap().unwrap();
    assert_eq!(message.content["task_state"], "timed_out");
    assert_eq!(f.support.bot_delivery.aborts().await.len(), 1);
}

#[tokio::test]
async fn worker_capacity_rejection_reports_failure_without_a_second_task_identity() {
    let f = Fixture::new().await;
    f.live.snapshot.write().await.policy.defaults.max_queued = 1;
    assign(&f, "89012345000000000000000000000000", None).await;
    let mut cmd = dispatch_command();
    cmd.payload["assignment_intent_id"] = json!("bcs_intent_90123456000000000000000000000000");
    let error = f.flow.handle_task_dispatch(cmd).await.unwrap_err();
    assert!(error.to_string().contains("task_dispatch_rejected:"), "{error}");
    assert_eq!(state(&f, "90123456").await, S::Failed, "not unknown or ambiguous");
    assert_eq!(state(&f, "89012345").await, S::Queued);
    let rows = f.rows().await;
    let rejected = rows.iter().find(|r| r.state.status == Status::RejectedCapacity).unwrap();
    assert!(!rejected.state.may_have_been_sent);
    let notice = rows.iter().find(|r| r.target_bot_id == "bot-driver").unwrap();
    assert!(notice.semantic_projection_json.get("task_rejected_id").is_none());
    assert!(notice.semantic_projection_json.get("task_uncertain_id").is_none());
    let source = f.repo.get_message_by_id(SESSION, &notice.source_message_id).await.unwrap().unwrap();
    assert!(source.content["text"].as_str().unwrap().starts_with("[任务派发失败] ref=T-90123456"));
    f.start(notice).await;
    let frames = f.support.bot_delivery.frames().await;
    assert_eq!(frames.len(), 1, "only Manager is sent a notification");
}

#[tokio::test]
async fn uncertain_notice_fact_survives_abort_rejection_and_restart_recovery() {
    let f = Fixture::new().await;
    let row = assign(&f, "a0123456000000000000000000000000", None).await;
    let row = f.start(&row).await;
    let row = f.transition(&row, Event::TransportUnknown).await;
    let row = f.transition(&row, Event::CancelRequested).await;
    let row = f.transition(&row, Event::StartAbort).await;
    let row = f.service.transition(DeliveryTransitionCommand { caller_dispatch_commit:None, caller_cancel:None, caller_terminal:None, delivery_id:row.delivery_id.clone(),
        expected_state_version:row.state.state_version, event:Event::AbortUnconfirmed,
        now_ms:chrono::Utc::now().timestamp_millis(), request_id:None, actor_id:None, reply:None,
        transport_context_json:Some(json!({"task_cancel_rejected":true})), deadline_at_ms:None }).await.unwrap();
    assert_eq!(row.last_error_code.as_deref(), Some("task_cancel_rejected"));
    assert_eq!(row.transport_context_json.as_ref().unwrap()["task_uncertainty_notified"], true);
    let row = f.transition(&row, Event::ScopeAbortRequested).await;
    let row = f.transition(&row, Event::Recover).await;
    assert_eq!(row.state.status, Status::CancelUnknown);
    assert_eq!(f.rows().await.iter().filter(|r| r.source_message_id.starts_with("task-unknown:")).count(), 1);
}

#[tokio::test]
async fn worker_abort_before_synthetic_timeout_has_consistent_legacy_result() {
    let f = Fixture::new().await;
    let mut policy = f.live.snapshot.read().await.policy.clone();
    policy.bots.insert("bot-observer".into(), bcs_config_api::message_delivery::BotDeliveryOverride {
        mode:Some(BotDeliveryMode::Off), ..Default::default()
    });
    f.flow.replace_delivery_policy(admin(), 1, policy).await.unwrap();
    let mut cmd = dispatch_command();
    cmd.payload["assignment_intent_id"] = json!("bcs_intent_a1234567000000000000000000000000");
    let outcome = f.flow.handle_task_dispatch(cmd).await.unwrap();
    let mut entry = f.flow.task_store.get(&outcome.task_id).await.unwrap();
    entry.cancel_reason = Some("任务运行已达到设定的超时期限，已按超时规则停止。".into());
    entry.cancel_started = true;
    f.flow.task_store.register(entry).await;
    f.flow.handle_bot_event(BotEventCommand { bot_id:"bot-observer".into(), run_id:outcome.task_id,
        group_id:"group-1".into(), bcs_session_id:Some(SESSION.into()), event_type:"chat.event".into(),
        state:ChatEventState::Aborted, event_payload:json!({}) }).await.unwrap();
    assert_eq!(state(&f, "a1234567").await, S::TimedOut);
    let rows = f.rows().await;
    let result = rows.iter().find(|r| r.semantic_projection_json["task"]["leg"] == "result").unwrap();
    let source = f.repo.get_message_by_id(SESSION, &result.source_message_id).await.unwrap().unwrap();
    assert_eq!(source.content["task_state"], "timed_out");
}

#[tokio::test]
async fn confirmed_legacy_stop_prevents_late_final_from_replacing_cancelled_result() {
    let f = Fixture::new().await;
    let mut policy = f.live.snapshot.read().await.policy.clone();
    policy.bots.insert("bot-observer".into(), bcs_config_api::message_delivery::BotDeliveryOverride {
        mode:Some(BotDeliveryMode::Off), ..Default::default()
    });
    f.flow.replace_delivery_policy(admin(), 1, policy).await.unwrap();
    let mut cmd = dispatch_command();
    cmd.payload["assignment_intent_id"] = json!("bcs_intent_a2345678000000000000000000000000");
    let outcome = f.flow.handle_task_dispatch(cmd).await.unwrap();
    let mut entry = f.flow.task_store.get(&outcome.task_id).await.unwrap();
    entry.cancel_started = true; entry.stopped_confirmed = true; entry.cancel_reason = Some("stop".into());
    f.flow.task_store.register(entry).await;
    let mut event = BotEventCommand { bot_id:"bot-observer".into(), run_id:outcome.task_id,
        group_id:"group-1".into(), bcs_session_id:Some(SESSION.into()), event_type:"chat.event".into(),
        state:ChatEventState::Final, event_payload:json!({"message":{"content":[{"type":"text","text":"late final"}]}}) };
    f.flow.handle_bot_event(event.clone()).await.unwrap();
    assert!(f.rows().await.is_empty());
    event.state = ChatEventState::Aborted; event.event_payload = json!({});
    f.flow.handle_bot_event(event).await.unwrap();
    assert_eq!(state(&f, "a2345678").await, S::Cancelled);
    let rows = f.rows().await;
    assert_eq!(rows.len(), 1);
    let source = f.repo.get_message_by_id(SESSION, &rows[0].source_message_id).await.unwrap().unwrap();
    assert_eq!(source.content["task_state"], "cancelled");
}

#[derive(Default)]
struct BlockingCompletion {
    entered: tokio::sync::Notify,
    release: tokio::sync::Notify,
}
#[async_trait::async_trait]
impl bcs_service_api::port::EventRecorderPort for BlockingCompletion {
    async fn record(&self, event: bcs_service_api::port::NewEvent)
        -> Result<bcs_service_api::port::EventRecordResult, bcs_service_api::port::EventRecordError> {
        if event.event_type == "task.completed" {
            self.entered.notify_one(); self.release.notified().await;
        }
        Ok(bcs_service_api::port::EventRecordResult::Disabled)
    }
}

struct DelayedAbort {
    inner: Arc<support::RecordingBotDelivery>,
    entered: tokio::sync::Notify,
    release: tokio::sync::Notify,
}
#[async_trait::async_trait]
impl BotDeliveryPort for DelayedAbort {
    async fn is_available(&self, target: &bcs_service_api::BotDeliveryTarget) -> bool { self.inner.is_available(target).await }
    async fn connection_identity(&self, target: &bcs_service_api::BotDeliveryTarget) -> Option<String> { self.inner.connection_identity(target).await }
    async fn deliver(&self, cmd: bcs_service_api::BotDeliveryCommand) -> bcs_service_api::ServiceResult<bcs_service_api::BotDeliveryResult> { self.inner.deliver(cmd).await }
    async fn abort(&self, cmd: bcs_service_api::BotAbortDeliveryCommand) -> bcs_service_api::ServiceResult<bcs_service_api::BotAbortDeliveryResult> {
        self.entered.notify_one(); self.release.notified().await; self.inner.abort(cmd).await
    }
    async fn abort_on_connection(&self, cmd: bcs_service_api::BotAbortDeliveryCommand, _: &str) -> bcs_service_api::ServiceResult<bcs_service_api::BotAbortDeliveryResult> { self.abort(cmd).await }
}

#[tokio::test]
async fn legacy_final_holding_event_lock_wins_over_stop_confirmation() {
    let mut f = Fixture::new().await;
    let mut policy = f.live.snapshot.read().await.policy.clone();
    policy.defaults.mode = BotDeliveryMode::Off;
    policy.task_cancel_wait_ms = 100;
    f.flow.replace_delivery_policy(admin(), 1, policy).await.unwrap();
    let recorder = Arc::new(BlockingCompletion::default());
    let transport = Arc::new(DelayedAbort { inner:f.support.bot_delivery.clone(), entered:Default::default(), release:Default::default() });
    f.flow = Fixture::configured_flow(&f.support, &f.service, &f.repo, &f.live,
        |mut flow| { flow.bot_delivery = transport.clone(); flow.with_event_recorder(recorder.clone()) }).await;
    let mut cmd = dispatch_command();
    cmd.payload["assignment_intent_id"] = json!("bcs_intent_a3456789000000000000000000000000");
    let task = f.flow.handle_task_dispatch(cmd).await.unwrap();
    let owner = f.flow.clone();
    let cancel_job = tokio::spawn(async move { owner.cancel_task("a3456789".into(), "stop".into()).await });
    tokio::time::timeout(std::time::Duration::from_secs(2), transport.entered.notified()).await.unwrap();
    let owner = f.flow.clone();
    let final_job = tokio::spawn(async move {
        owner.handle_bot_event(BotEventCommand { bot_id:"bot-observer".into(), run_id:task.task_id,
            group_id:"group-1".into(), bcs_session_id:Some(SESSION.into()), event_type:"chat.event".into(),
            state:ChatEventState::Final, event_payload:json!({"message":{"content":[{"type":"text","text":"finished"}]}}) }).await
    });
    tokio::time::timeout(std::time::Duration::from_secs(2), recorder.entered.notified()).await.unwrap();
    // The final has sent its result, but its ledger write is still blocked.
    transport.release.notify_one();
    assert_eq!(cancel_job.await.unwrap().unwrap().status, S::Unknown);
    assert_eq!(f.support.bot_delivery.aborts().await.len(), 1);
    recorder.release.notify_one();
    final_job.await.unwrap().unwrap();
    tokio::time::sleep(std::time::Duration::from_millis(30)).await;
    assert_eq!(state(&f, "a3456789").await, S::Completed);
    assert_eq!(f.flow.cancel_task("a3456789".into(), "again".into()).await.unwrap().status, S::AlreadyFinished);
    assert_eq!(f.support.bot_delivery.frames().await.len(), 2, "assignment and completed result only");
}

#[tokio::test]
async fn unknown_notice_works_with_task_only_policy_and_manager_queue_off() {
    let f = Fixture::new().await;
    let row = assign(&f, "a1234567000000000000000000000000", None).await;
    let row = f.start(&row).await;
    let mut policy = f.live.snapshot.read().await.policy.clone();
    policy.flow_enabled.group = false;
    policy.flow_enabled.system = false;
    policy.bots.insert("bot-driver".into(), bcs_config_api::message_delivery::BotDeliveryOverride {
        mode: Some(BotDeliveryMode::Off), ..Default::default()
    });
    f.flow.replace_delivery_policy(admin(), 1, policy).await.unwrap();
    let row = f.transition(&row, Event::TransportUnknown).await;
    assert_eq!(row.state.status, Status::Unknown);
    let task_id = row_task_id(&row).to_owned();
    let notices: Vec<_> = f.rows().await.into_iter()
        .filter(|notice| notice.source_message_id == format!("task-unknown:{task_id}"))
        .collect();
    assert_eq!(notices.len(), 1, "uncertainty notice must be admitted as a task control outcome");
    assert_eq!(notices[0].target_bot_id, "bot-driver");
    assert_eq!(notices[0].state.kind, DeliveryType::Send);
    assert_eq!(notices[0].semantic_projection_json["task_control_notice"], true);
    assert_eq!(notices[0].semantic_projection_json["task_uncertain_id"], "a1234567000000000000000000000000");
}

fn row_task_id(row: &PersistedMessageDelivery) -> &str {
    row.semantic_projection_json["task"]["task_id"].as_str().unwrap()
}

#[tokio::test]
async fn native_task_uuid_suppresses_uncertainty_before_and_after_late_final() {
    for sqlite in [false, true] {
        let f = if sqlite {
            let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
            migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
            use bcs_db_api::DbPlugin;
            db.execute(bcs_db_api::DbStatement::new("INSERT INTO bcs_group_sessions (session_id, group_id, env, participants) VALUES ('group-1:task', 'group-1', 'dev', '[]')")).await.unwrap();
            let repo = Arc::new(bcs_message_store::MySqlMessageStore::sqlite(db, "dev".into()));
            Fixture::with_repos(repo.clone(), repo).await
        } else { Fixture::new().await };
        let mut command = dispatch_command();
        command.payload.as_object_mut().unwrap().remove("assignment_intent_id");
        let outcome = f.flow.handle_task_dispatch(command).await.unwrap();
        let reference = outcome.task_id.replace('-', "")[..8].to_owned();
        let row = f.rows().await.into_iter().find(|row| row_task_id(row) == outcome.task_id).unwrap();
        let row = f.start(&row).await;
        let row = f.transition(&row, Event::TransportUnknown).await;
        assert_eq!(state(&f, &reference).await, S::Unknown, "the notice is not another task");
        f.flow.handle_bot_event(final_event(&row)).await.unwrap();
        assert_eq!(state(&f, &reference).await, S::Completed, "the final supersedes uncertainty");
        let restarted = Fixture::make_flow(&f.support, &f.service, &f.repo, &f.live).await;
        assert_eq!(restarted.task_status(vec![reference]).await.unwrap()[0].status, S::Completed);
    }
}
