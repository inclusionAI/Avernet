use super::*;

async fn install(h: &mut Harness, runs: Arc<FaultyRuns>, messages: Arc<MemoryMessageRepo>, name: &str) {
    let group = Arc::new(GroupStore::new());
    let mut value = test_group(); value.label = Some(name.into());
    value.group_strategy = GroupStrategy::StateMachine;
    value.opening_message = Some(OpeningMessage::Text(format!("{name} {{{{bcs.run_id}}}}")));
    group.upsert(value).await.unwrap();
    h.runtime = CollaborationRuntime::new(h.definitions.clone(), h.store.clone(), runs, h.store.clone(), group,
        h.sessions.clone(), h.delivery.clone(), noop_judge())
        .with_message_repo(messages).with_session_channel_outbound(h.channel.clone()).with_experimental_fixed_loop_execution();
}

async fn interrupt_start(h: &Harness, runs: &FaultyRuns, human: bool) {
    tokio::select! {
        result = h.runtime.start_state_machine_run(command(loop_yaml(3, false, human, 1), human)) => panic!("startup did not pause: {result:?}"),
        () = runs.startup_paused.notified() => {}
    }
}

#[tokio::test]
async fn opening_recovers_pending_and_running_startup_with_original_text_once() {
    for before_start in [true, false] {
        for human in [false, true] {
            let (mut h, runs) = harness(&[]).await;
            let messages = Arc::new(MemoryMessageRepo::new());
            install(&mut h, runs.clone(), messages.clone(), "original opening").await;
            if before_start { runs.pause_start.store(true, Ordering::SeqCst); }
            else { runs.pause_opening_barrier.store(true, Ordering::SeqCst); }
            interrupt_start(&h, &runs, human).await;
            let run = if before_start { h.store.list_pending_runs(None, 1).await.unwrap().remove(0) }
                else { h.store.list_running_runs(None, 1).await.unwrap().remove(0) };
            assert!(h.delivery.commands.lock().await.is_empty());
            let saved = h.store.get_run_opening(&run.run_id).await.unwrap().unwrap();
            assert_eq!(saved.delivered_at_ms, None);
            assert!(saved.payload.content.contains("original opening"));
            assert!(saved.payload.content.contains(&run.run_id));
            assert_eq!(messages.get_current_seq(&run.session_id).await.unwrap(), if before_start { 0 } else { 1 });
            install(&mut h, runs, messages.clone(), "changed Group opening").await;
            h.definitions.hide_definitions.store(true, Ordering::SeqCst);
            h.runtime = h.runtime.with_fixed_loop_limits(bcs_config_api::FixedLoopLimits { max_fixed_loop_iterations: 1, ..Default::default() });
            let (a, b) = tokio::join!(h.runtime.recover_state_machine_progression(None, 32), h.runtime.recover_state_machine_progression(None, 32));
            assert!(a.unwrap().failures.is_empty()); assert!(b.unwrap().failures.is_empty());
            recover(&h).await;
            assert_eq!(messages.get_current_seq(&run.session_id).await.unwrap(), 1);
            let history = messages.get_message_by_id(&run.session_id, &saved.payload.client_msg_id).await.unwrap().unwrap();
            assert_eq!(history.content["text"], saved.payload.content);
            assert_eq!(h.store.get_run_opening(&run.run_id).await.unwrap().unwrap().payload, saved.payload);
            assert!(h.store.get_run_opening(&run.run_id).await.unwrap().unwrap().delivered_at_ms.is_some());
            assert_eq!(h.store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Running);
            assert_eq!(h.delivery.commands.lock().await.len(), if human { 0 } else { 1 });
            assert_eq!(h.store.list_node_runs(&run.run_id).await.unwrap().iter().filter(|node| node.status == StateMachineNodeStatus::Running).count(), 1);
        }
    }
}

#[tokio::test]
async fn opening_history_collision_blocks_initial_dispatch() {
    let (mut h, runs) = harness(&[]).await;
    let messages = Arc::new(MemoryMessageRepo::new());
    install(&mut h, runs.clone(), messages.clone(), "original").await;
    runs.pause_start.store(true, Ordering::SeqCst);
    interrupt_start(&h, &runs, false).await;
    let run = h.store.list_pending_runs(None, 1).await.unwrap().remove(0);
    let id = format!("{}:000-panel", run.run_id);
    messages.append_message_with_id(id.clone(), NewMessage {
        group_id: run.group_id.clone(), session_id: run.session_id.clone(), sender_id: bcs_domain::BCS_STATE_MACHINE_MESSAGE_SENDER.into(),
        sender_type: bcs_domain::SenderType::Bot, message_type: STATE_MACHINE_PANEL_MESSAGE_TYPE.into(),
        content: json!({"text": "conflicting stored content"}), client_msg_id: Some(id), owner_bot_id: None,
        created_at: run.created_at, run_id: run.run_id.clone(), visibility_domain: MessageVisibilityDomain::StateMachine,
        audience: Some(MessageAudience::Public),
    }).await.unwrap();
    let page = h.runtime.recover_state_machine_progression(None, 32).await.unwrap();
    assert_eq!(page.failures.len(), 1);
    assert!(page.failures[0].error.contains("opening history does not match"));
    assert!(h.delivery.commands.lock().await.is_empty());
    assert_eq!(h.store.get_run_opening(&run.run_id).await.unwrap().unwrap().delivered_at_ms, None);
}

#[tokio::test]
async fn opening_recovery_does_not_start_cancelled_run() {
    let (mut h, runs) = harness(&[]).await;
    let messages = Arc::new(MemoryMessageRepo::new());
    install(&mut h, runs.clone(), messages.clone(), "original").await;
    runs.pause_start.store(true, Ordering::SeqCst);
    interrupt_start(&h, &runs, false).await;
    let run = h.store.list_pending_runs(None, 1).await.unwrap().remove(0);
    h.store.update_run_status(&run.run_id, StateMachineRunStatus::Aborted, None, None, 100, Some(100)).await.unwrap();
    recover(&h).await;
    assert!(h.delivery.commands.lock().await.is_empty());
    assert_eq!(messages.get_current_seq(&run.session_id).await.unwrap(), 0);
    assert_eq!(h.store.get_run_opening(&run.run_id).await.unwrap().unwrap().delivered_at_ms, None);
}

#[tokio::test]
async fn opening_history_barrier_recovers_initial_dispatch_failure() {
    let (h, runs) = harness(&[]).await;
    runs.fail_dispatch.store(true, Ordering::SeqCst);
    assert!(h.runtime.start_state_machine_run(command(loop_yaml(2, false, false, 1), false)).await.is_err());
    let run = h.store.list_running_runs(None, 1).await.unwrap().remove(0);
    assert!(h.store.get_run_opening(&run.run_id).await.unwrap().unwrap().delivered_at_ms.is_some());
    recover(&h).await;
    recover(&h).await;
    assert_eq!(h.delivery.commands.lock().await.len(), 1);
    assert_eq!(h.store.list_node_runs(&run.run_id).await.unwrap().iter().filter(|node| node.status == StateMachineNodeStatus::Running).count(), 1);
}

#[tokio::test]
async fn opening_checkpoint_write_failures_propagate_without_initial_dispatch() {
    for fail_save in [true, false] {
        let (h, runs) = harness(&[]).await;
        if fail_save { runs.fail_opening_save.store(true, Ordering::SeqCst); }
        else { runs.fail_opening_mark.store(true, Ordering::SeqCst); }
        let error = h.runtime.start_state_machine_run(command(loop_yaml(2, false, false, 1), false)).await.unwrap_err();
        assert!(error.to_string().contains("injected"));
        assert!(h.delivery.commands.lock().await.is_empty());
        assert!(h.store.list_running_runs(None, 10).await.unwrap().is_empty());
        assert!(h.store.list_pending_runs(None, 10).await.unwrap().is_empty());
    }
}
