use super::*;

#[derive(Default)]
struct FailingUserNotice(std::sync::atomic::AtomicUsize);

#[async_trait::async_trait]
impl bcs_service_api::SystemMessageService for FailingUserNotice {
    async fn notify(&self, _: &str, event: bcs_domain::SystemMessageEvent, _: &str,
        _: &[bcs_domain::Participant]) -> bcs_service_api::ServiceResult<usize> {
        assert!(matches!(event, bcs_domain::SystemMessageEvent::UserNotification { .. }));
        self.0.fetch_add(1, Ordering::SeqCst);
        Err(bcs_service_api::ServiceError::InternalError("user notice persistence failed".into()))
    }
}

#[tokio::test]
async fn user_notice_failure_preserves_dispatch_and_worker_result() {
    for managed in [true, false] {
        let mut f = Fixture::new().await;
        let notice = Arc::new(FailingUserNotice::default());
        f.flow = Fixture::configured_flow(&f.support, &f.service, &f.repo, &f.live,
            |flow| flow.with_system_message(notice.clone())).await;
        if !managed {
            let mut policy = f.live.snapshot.read().await.policy.clone(); policy.flow_enabled.task = false;
            f.flow.replace_delivery_policy(admin(), 1, policy).await.unwrap();
        }
        let task = f.flow.handle_task_dispatch(dispatch_command()).await.unwrap();
        assert_eq!(task.status, if managed { "queued" } else { "dispatched" });
        assert_eq!(notice.0.load(Ordering::SeqCst), 1);
        let entry = f.flow.task_store.get(&task.task_id).await.unwrap();
        assert_eq!(entry.assignment_intent_id.as_deref(), Some("bcs_intent_assignment"));
        assert!(!matches!(entry.status, bcs_message_flow::task_store::TaskLedgerStatus::Failed));
        let run_id = if managed {
            let rows = f.rows().await;
            assert_eq!(rows.len(), 1, "a user notice failure creates no extra delivery");
            assert_eq!(rows[0].semantic_projection_json["task"]["task_id"], task.task_id);
            f.start(&rows[0]).await.run_id.unwrap()
        } else { task.task_id.clone() };
        assert_eq!(f.support.bot_delivery.kinds().await, vec![bcs_service_api::BotDeliveryKind::TaskDispatch]);
        f.flow.handle_bot_event(BotEventCommand { bot_id:"bot-observer".into(), run_id,
            group_id:"group-1".into(), bcs_session_id:Some(SESSION.into()),
            state:ChatEventState::Error, event_type:"chat.event".into(),
            event_payload:json!({"errorMessage":"Worker execution failed"}) }).await.unwrap();
        assert_eq!(notice.0.load(Ordering::SeqCst), 2);
        assert_eq!(f.flow.task_store.get(&task.task_id).await.unwrap().status,
            bcs_message_flow::task_store::TaskLedgerStatus::Failed);
        if managed {
            let rows = f.rows().await;
            assert_eq!(rows.len(), 2);
            let result = rows.iter().find(|r| r.semantic_projection_json["task"]["leg"] == "result").unwrap();
            f.start(result).await;
        }
        assert_eq!(f.support.bot_delivery.kinds().await,
            vec![bcs_service_api::BotDeliveryKind::TaskDispatch, bcs_service_api::BotDeliveryKind::TaskResult]);
    }
}

#[tokio::test]
async fn non_callback_terminal_is_atomic_idempotent_and_can_wake_manager() {
    for event in [Event::CancelRequested, Event::PreparationFailed] {
        let f = Fixture::new().await;
        let (task_id, row) = f.dispatch().await;
        let settled = f.transition(&row, event).await;
        f.transition(&settled, event).await;
        let rows = f.rows().await;
        let results: Vec<_> = rows.iter().filter(|r| r.semantic_projection_json["task"]["leg"] == "result").collect();
        assert_eq!(results.len(), 1);
        let source = f.repo.get_message_by_id(SESSION, &results[0].source_message_id).await.unwrap().unwrap();
        assert_eq!(source.message_id, format!("task-result:{task_id}"));
        let text = source.content["task_result_text"].as_str().unwrap();
        assert!(text.contains("TASK_BODY") && text.contains("bcs_intent_assignment"));
        if event == Event::CancelRequested { assert!(text.contains("开始执行前已取消")); }
        f.start(results[0]).await;
        let wire = serde_json::to_value(&f.support.bot_delivery.frames().await[0]).unwrap();
        assert_eq!(wire["method"], "chat.send");
        assert!(wire.to_string().contains(text.lines().next().unwrap()));
        // The existing assignment tool remains usable after the result Send.
        assert_eq!(f.flow.handle_task_dispatch(dispatch_command()).await.unwrap().status, "queued");
    }
}

#[tokio::test]
async fn unknown_and_unconfirmed_abort_do_not_notify_until_confirmed() {
    let f = Fixture::new().await;
    let (task_id, row) = f.dispatch().await;
    let row = f.start(&row).await;
    let row = f.transition(&row, Event::TransportUnknown).await;
    assert_eq!(f.rows().await.len(), 1);
    let row = f.transition(&row, Event::CancelRequested).await;
    let row = f.transition(&row, Event::AbortUnconfirmed).await;
    assert_eq!(row.state.status, Status::CancelUnknown);
    assert_eq!(f.rows().await.len(), 1);
    let row = f.transition(&row, Event::Aborted).await;
    let mut late = final_event(&row); late.state = ChatEventState::Aborted;
    f.flow.handle_bot_event(late.clone()).await.unwrap();
    f.flow.handle_bot_event(late).await.unwrap();
    assert_eq!(f.rows().await.len(), 2);
    assert_eq!(f.flow.task_store.get(&task_id).await.unwrap().status,
        bcs_message_flow::task_store::TaskLedgerStatus::Cancelled);
    let summary = f.flow.task_store.ledger_summary("group-1", Some(SESSION)).await;
    assert!(summary.failed.is_empty()); assert_eq!(summary.cancelled.len(), 1);
}

#[tokio::test]
async fn restarted_same_worker_tasks_keep_separate_assignment_references() {
    let mut f = Fixture::new().await;
    for id in ["bcs_intent_first", "bcs_intent_second"] {
        let mut cmd = dispatch_command(); cmd.payload["assignment_intent_id"] = json!(id);
        f.flow.handle_task_dispatch(cmd).await.unwrap();
    }
    f.flow = Fixture::make_flow(&f.support, &f.service, &f.repo, &f.live).await;
    for row in f.rows().await {
        let settled = f.transition(&row, Event::CancelRequested).await;
        assert_eq!(settled.state.status, Status::Cancelled);
        let task_id = row.semantic_projection_json["task"]["task_id"].as_str().unwrap();
        let result = f.repo.get_message_by_id(SESSION, &format!("task-result:{task_id}")).await.unwrap().unwrap();
        assert!(result.content["task_result_text"].as_str().unwrap()
            .contains(row.semantic_projection_json["task"]["assignment_intent_id"].as_str().unwrap()));
    }
    assert_eq!(f.rows().await.iter().filter(|r| r.semantic_projection_json["task"]["leg"] == "result").count(), 2);
}

#[tokio::test]
async fn legacy_aborted_callback_uses_short_result_and_cancelled_ledger() {
    let f = Fixture::new().await;
    let mut policy = f.live.snapshot.read().await.policy.clone(); policy.flow_enabled.task = false;
    f.flow.replace_delivery_policy(admin(), 1, policy).await.unwrap();
    let task = f.flow.handle_task_dispatch(dispatch_command()).await.unwrap();
    let event = BotEventCommand { bot_id:"bot-observer".into(), run_id:task.task_id.clone(), group_id:"group-1".into(),
        bcs_session_id:Some(SESSION.into()), state:ChatEventState::Aborted, event_type:"chat.event".into(),
        event_payload:json!({"reason":"用户中断了本次执行。"}) };
    f.flow.handle_bot_event(event.clone()).await.unwrap();
    f.flow.handle_bot_event(event).await.unwrap();
    let frames = f.support.bot_delivery.frames().await;
    assert_eq!(frames.len(), 2);
    let text = serde_json::to_value(&frames[1]).unwrap().to_string();
    assert!(text.contains("[任务中断]") && text.contains("用户中断") && text.contains("bcs_intent_assignment"));
    assert_eq!(f.flow.task_store.get(&task.task_id).await.unwrap().status,
        bcs_message_flow::task_store::TaskLedgerStatus::Cancelled);
}

#[tokio::test]
async fn closed_group_does_not_send_abnormal_result() {
    let f = Fixture::new().await;
    let (_, row) = f.dispatch().await;
    f.transition(&row, Event::CancelRequested).await;
    let mut group = f.support.group.get("group-1").await.unwrap();
    group.status = bcs_domain::GroupStatus::Closed;
    f.support.group.upsert(group).await.unwrap();
    let result = f.rows().await.into_iter().find(|r| r.semantic_projection_json["task"]["leg"] == "result").unwrap();
    let preparation = QueuedGroupPreparation { flow:Arc::downgrade(&f.flow), deliveries:f.service.clone() };
    assert!(preparation.prepare(&result).await.is_err());
    f.transition(&result, Event::PreparationFailed).await;
    assert_eq!(f.rows().await.len(), 2, "failed Manager notification must not generate another TaskResult");
    assert!(f.support.bot_delivery.frames().await.is_empty());
}

#[tokio::test]
async fn completed_session_blocks_abnormal_result_send() {
    let mut f = Fixture::new().await;
    let (_, row) = f.dispatch().await;
    let group = f.support.group.get("group-1").await.unwrap();
    let mut session = sessions::test_session(SESSION, "group-1", group.participants);
    session.status = bcs_service_api::SessionStatus::Completed;
    f.flow = Fixture::configured_flow(&f.support, &f.service, &f.repo, &f.live,
        |flow| flow.with_session_management(Arc::new(sessions::StaticSessionManagement::new(session)))).await;
    f.transition(&row, Event::CancelRequested).await;
    let result = f.rows().await.into_iter().find(|r| r.semantic_projection_json["task"]["leg"] == "result").unwrap();
    let preparation = QueuedGroupPreparation { flow:Arc::downgrade(&f.flow), deliveries:f.service.clone() };
    assert!(preparation.prepare(&result).await.is_err());
    assert!(f.support.bot_delivery.frames().await.is_empty());
}

#[tokio::test]
async fn explicit_user_abort_without_worker_callback_notifies_in_managed_and_legacy_modes() {
    for managed in [true, false] {
        let f = Fixture::new().await;
        let run = if managed {
            let (_, row) = f.dispatch().await;
            f.start(&row).await.run_id.unwrap()
        } else {
            let mut policy = f.live.snapshot.read().await.policy.clone(); policy.flow_enabled.task = false;
            f.flow.replace_delivery_policy(admin(), 1, policy).await.unwrap();
            f.flow.handle_task_dispatch(dispatch_command()).await.unwrap().task_id
        };
        let outcome = f.flow.handle_chat_abort(bcs_service_api::ChatAbortCommand {
            caller:admin(), group_id:"group-1".into(), session_id:SESSION.into(),
            bot_id:"bot-observer".into(), run_id:Some(run),
        }).await.unwrap();
        assert!(outcome.aborted);
        if managed {
            let rows = f.rows().await;
            let result = rows.iter().find(|r| r.semantic_projection_json["task"]["leg"] == "result").unwrap();
            let source = f.repo.get_message_by_id(SESSION, &result.source_message_id).await.unwrap().unwrap();
            assert!(source.content["task_result_text"].as_str().unwrap().contains("用户中断"));
            f.start(result).await;
        }
        let frames = f.support.bot_delivery.frames().await;
        assert_eq!(frames.len(), 2, "one assignment and one result");
        assert!(serde_json::to_string(&frames[1]).unwrap().contains("用户中断"));
    }
}

#[tokio::test]
async fn notification_transport_failure_does_not_reopen_legacy_worker_task() {
    let f = Fixture::new().await;
    let mut policy = f.live.snapshot.read().await.policy.clone(); policy.flow_enabled.task = false;
    f.flow.replace_delivery_policy(admin(), 1, policy).await.unwrap();
    let task = f.flow.handle_task_dispatch(dispatch_command()).await.unwrap();
    f.support.bot_delivery.fail_for("bot-driver").await;
    let event = BotEventCommand { bot_id:"bot-observer".into(), run_id:task.task_id.clone(), group_id:"group-1".into(),
        bcs_session_id:Some(SESSION.into()), state:ChatEventState::Error, event_type:"chat.event".into(),
        event_payload:json!({"errorMessage":"读取日志时权限不足。"}) };
    assert!(f.flow.handle_bot_event(event.clone()).await.is_err());
    assert_eq!(f.flow.task_store.get(&task.task_id).await.unwrap().status,
        bcs_message_flow::task_store::TaskLedgerStatus::Failed);
    f.flow.handle_bot_event(event).await.unwrap();
    assert_eq!(f.support.bot_delivery.frames().await.len(), 2, "an uncertain Manager Send is not blindly replayed");
}

#[tokio::test]
async fn callback_and_control_race_commit_one_result() {
    let f = Fixture::new().await;
    let (_, row) = f.dispatch().await;
    let row = f.start(&row).await;
    let command = DeliveryTransitionCommand { delivery_id:row.delivery_id.clone(), expected_state_version:row.state.state_version,
        event:Event::Aborted, now_ms:chrono::Utc::now().timestamp_millis(), request_id:None, actor_id:None,
        reply:None, transport_context_json:None, deadline_at_ms:None };
    let (_, callback) = tokio::join!(f.service.transition(command), f.flow.handle_bot_event(final_event(&row)));
    callback.unwrap();
    let rows = f.rows().await;
    assert_eq!(rows.iter().filter(|r| r.semantic_projection_json["task"]["leg"] == "result").count(), 1);
    assert!(matches!(rows.iter().find(|r| r.delivery_id == row.delivery_id).unwrap().state.status, Status::Completed | Status::Cancelled));
}
