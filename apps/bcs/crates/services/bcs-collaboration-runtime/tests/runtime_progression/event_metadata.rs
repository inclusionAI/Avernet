use super::*;
use bcs_service_api::HandleBotTerminalEventCommand;
use bcs_service_api::port::repo::EventRepoPort;

#[derive(Default)]
struct PersistingEventFactory { events: StdMutex<Vec<NewEvent>> }
impl EventRecordFactoryPort for PersistingEventFactory {
    fn prepare(&self, event: NewEvent) -> Result<Option<AppendEventRecord>, EventRecordError> {
        self.events.lock().unwrap().push(event.clone());
        Ok(Some(AppendEventRecord { recorded_at: event.occurred_at.clone(), event,
            retention_until_ms: u64::MAX, env: "test".into() }))
    }
}

pub(super) async fn persisted_output(
    messages: &dyn MessageRepoPort, run: &StateMachineRun, node_id: &str, attempt: i32,
) -> PersistedMessage {
    let page = messages.query_messages(MessageQuery {
        group_id: run.group_id.clone(), session_id: run.session_id.clone(), cursor: None, limit: 100,
        keyword: None, sender_id: None, message_type: Some(bcs_domain::STATE_MACHINE_OUTPUT_MESSAGE_TYPE.into()),
        owner_filter: MessageOwnerFilter::Any, time_range: None, visible_from_seq: None, human_view: None,
    }).await.unwrap();
    let mut matching = page.messages.into_iter().filter(|message| message.run_id == run.run_id
        && message.content["metadata"]["state_machine"]["node_id"] == node_id
        && message.content["metadata"]["state_machine"]["attempt"] == attempt);
    let message = matching.next().expect("persisted output");
    assert!(matching.next().is_none(), "one message per node attempt");
    message
}

#[tokio::test]
async fn events_and_persisted_history_share_execution_through_retry_and_next_iteration() {
    let mut h = Harness::new(&["again", "done"]).await;
    let factory = Arc::new(PersistingEventFactory::default());
    let messages = Arc::new(MemoryMessageRepo::new());
    h.runtime = h.runtime.with_event_record_factory(factory.clone()).with_message_repo(messages.clone());
    let started = h.start(loop_yaml(2, true, false, 2), false).await;
    let map = started.view.node_execution_metadata.as_ref().unwrap();
    h.fail(0, &started.view.run).await;
    h.finish(1, &started.view.run, "first iteration").await;
    h.finish(2, &started.view.run, "second iteration").await;
    let recorded = factory.events.lock().unwrap().clone();
    let events: Vec<_> = recorded.iter().filter(|event| event.event_type.starts_with("state_machine.node.")).collect();
    assert!(events.iter().any(|event| event.event_type.ends_with("retry_scheduled") && event.data["attempt"] == 0 && event.data["next_attempt"] == 1));
    for event in events {
        let saved_event = h.public_events.get_event(&event.event_id, "test").await.unwrap().unwrap();
        assert_eq!(saved_event.envelope.data, event.data);
        let id = event.data["node_id"].as_str().unwrap();
        if let Some(execution) = map.get(id) {
            assert_eq!(event.data["execution"], json!(execution));
        } else { assert!(!event.data.contains_key("execution")); }
    }
    let plan = h.plan(&started.view.run.run_id).await;
    for (iteration, attempt) in [(1, 1), (2, 0)] {
        let id = iteration_id(&plan, iteration);
        let stored = persisted_output(messages.as_ref(), &started.view.run, &id, attempt).await;
        assert!(stored.message_id.len() <= 64, "output ID must fit the MySQL primary key");
        assert_eq!(stored.content["metadata"]["state_machine"]["execution"], json!(map[&id]));
        assert_eq!(stored.content["metadata"]["state_machine"]["attempt"], attempt);
        assert_eq!(stored.audience, Some(MessageAudience::FullOnly));
    }
    let seq = messages.get_current_seq(&started.view.run.session_id).await.unwrap();
    // Duplicate terminal delivery neither overwrites history nor allocates a sequence.
    h.finish(2, &started.view.run, "late altered output").await;
    assert_eq!(messages.get_current_seq(&started.view.run.session_id).await.unwrap(), seq);
    h.definitions.hide_definitions.store(true, Ordering::SeqCst);
    let reader = api_projection_tests::read_only_runtime(&h).await.with_message_repo(messages.clone());
    let history = reader.get_state_machine_session_history(&started.view.run.session_id, 100, None).await.unwrap().unwrap();
    for message in history.messages.iter().filter(|message| message.metadata.as_ref().and_then(|m| m.pointer("/state_machine/event")).is_some_and(|event| event == "output")) {
        let stored = messages.get_message_by_id(&started.view.run.session_id, &message.id).await.unwrap().unwrap();
        assert_eq!(message.metadata.as_ref().unwrap(), &stored.content["metadata"]);
        assert_eq!(message.content, stored.content["text"].as_str().unwrap());
    }
    let access = StateMachineRunAccessCommand { run_id: started.view.run.run_id.clone(), authenticated_human: None };
    assert!(reader.get_state_machine_run_graph_with_access(access).await.unwrap().is_some());
}

#[tokio::test]
async fn nested_api_events_and_history_share_the_complete_loop_path() {
    let mut h = Harness::new(&["approved", "done"]).await;
    let factory = Arc::new(PersistingEventFactory::default());
    let messages = Arc::new(MemoryMessageRepo::new());
    h.runtime = h.runtime.with_event_record_factory(factory.clone()).with_message_repo(messages.clone());
    let started = h.start(nested_loop_yaml(), false).await;
    let run = &started.view.run;
    let metadata = started.view.node_execution_metadata.as_ref().unwrap();
    assert!(metadata.values().all(|execution| execution.loop_path.is_some()));
    let graph = h.runtime.get_state_machine_run_graph(&run.run_id).await.unwrap().unwrap();
    assert_eq!(graph.loops.keys().cloned().collect::<Vec<_>>(), vec!["outer", "outer/inner"]);

    h.finish(0, run, "prepared").await;
    h.finish(1, run, "drafted").await;
    let inner_id = metadata.iter().find(|(_, execution)| execution.definition_node_id == "draft"
        && execution.loop_path.as_ref().is_some_and(|path| path[0].iteration == 1 && path[1].iteration == 1))
        .map(|(id, _)| id.clone()).unwrap();
    let execution = &metadata[&inner_id];
    let path = execution.loop_path.as_ref().unwrap();
    assert_eq!(path.iter().map(|frame| frame.loop_id.as_str()).collect::<Vec<_>>(), vec!["outer", "inner"]);
    let stored = persisted_output(messages.as_ref(), run, &inner_id, 0).await;
    assert_eq!(stored.content["metadata"]["state_machine"]["execution"], json!(execution));
    let event = factory.events.lock().unwrap().iter().find(|event|
        event.event_type == "state_machine.node.completed" && event.data["node_id"] == inner_id).cloned().unwrap();
    assert_eq!(event.data["execution"], json!(execution));
}

#[tokio::test]
async fn nested_human_pending_event_and_frozen_prompt_share_the_complete_path() {
    let mut definition: Value = serde_yaml::from_str(&nested_loop_yaml()).unwrap();
    let draft = &mut definition["runtime"]["state_machine"]["nodes"]["outer"]["loop"]
        ["nodes"]["inner"]["loop"]["nodes"]["draft"];
    *draft = json!({
        "kind": "human_input", "display_name": "Human draft",
        "assignee": {"type": "runtime_actor", "actor": "human_1001"},
        "notification": {"mode": "direct_assignee"},
        "instruction": "Review the nested draft.", "node_timeout_ms": 60000,
        "transitions": {"complete": {"targets": ["review"]}}
    });
    definition["runtime"]["state_machine"]["human_input_channel"] = json!({"channel_type": "dingtalk"});
    let mut h = Harness::new(&["approved", "done"]).await;
    let messages = Arc::new(MemoryMessageRepo::new());
    h.runtime = h.runtime.with_history_persistence(true)
        .with_history_cutoff_timestamp(u64::MAX).with_message_repo(messages.clone());
    let started = h.start(serde_yaml::to_string(&definition).unwrap(), true).await;
    h.finish(0, &started.view.run, "prepared").await;
    let pending = h.runtime.list_pending_human_nodes(ListPendingHumanNodesCommand {
        run_id: started.view.run.run_id.clone(), caller_actor_id: "human_1001".into(),
    }).await.unwrap().remove(0);
    let execution = pending.execution.as_ref().unwrap();
    assert_eq!(execution.loop_path.as_ref().unwrap().len(), 2);
    assert_eq!(pending.loop_context.as_ref().unwrap().loop_path.as_ref(), execution.loop_path.as_ref());
    let ready = h.channel.events.lock().await.iter().find(|event| event.node_id == pending.node_id)
        .cloned().unwrap();
    assert_eq!(ready.execution.as_ref(), Some(execution));
    assert_eq!(ready.loop_context.as_ref().unwrap().loop_path.as_ref(), execution.loop_path.as_ref());
    let prompt = h.store.get_history_message(&bcs_service_api::port::repo::StateMachineHistoryIdentity {
        session_id: started.view.run.session_id.clone(), run_id: started.view.run.run_id.clone(),
        node_id: pending.node_id.clone(), attempt: 0, event: "human_input_prompt".into(),
    }).await.unwrap().unwrap();
    assert_eq!(prompt.payload.message.content["metadata"]["state_machine"]["execution"], json!(execution));
}

#[tokio::test]
async fn human_output_history_preserves_audience_and_first_later_execution() {
    let mut h = Harness::new(&[]).await;
    let messages = Arc::new(MemoryMessageRepo::new());
    let factory = Arc::new(PersistingEventFactory::default());
    h.runtime = h.runtime.with_message_repo(messages.clone()).with_event_record_factory(factory.clone());
    let started = h.start(loop_yaml(2, false, true, 1), true).await;
    for iteration in 1..=2 {
        let pending = h.runtime.list_pending_human_nodes(ListPendingHumanNodesCommand {
            run_id: started.view.run.run_id.clone(), caller_actor_id: "human_1001".into(),
        }).await.unwrap().remove(0);
        h.runtime.respond_human_node(RespondHumanNodeCommand { run_id: started.view.run.run_id.clone(),
            node_id: pending.node_id.clone(), caller_actor_id: "human_1001".into(), content: format!("private-{iteration}"),
            source: HumanResponseSource::Http }).await.unwrap();
        let saved = persisted_output(messages.as_ref(), &started.view.run, &pending.node_id, 0).await;
        assert!(saved.message_id.len() <= 64, "human output ID must fit the MySQL primary key");
        assert_eq!(saved.content.pointer("/metadata/state_machine/execution/iteration"), Some(&json!(iteration)));
        assert_eq!(saved.audience, Some(MessageAudience::Directed { actor_ids: vec!["human_1001".into()] }));
        let other = HumanMessageView { actor_id: "human_other".into(), scope: MessageViewScope::Participant, allow_legacy_unclassified_chat: false };
        assert!(!other.allows(&saved));
    }
    let recorded = factory.events.lock().unwrap();
    let completed: Vec<_> = recorded.iter().filter(|event| event.event_type == "state_machine.node.completed").collect();
    assert_eq!(completed.len(), 2);
    assert_eq!(completed[0].data["execution"]["iteration"], 1);
    assert_eq!(completed[1].data["execution"]["iteration"], 2);
}

#[tokio::test]
async fn failed_output_write_stops_progression_and_recovery_writes_one_message() {
    let mut h = Harness::new(&["again"]).await;
    let messages = Arc::new(MemoryMessageRepo::new());
    h.runtime = h.runtime.with_message_repo(messages.clone());
    let started = h.start(loop_yaml(2, true, false, 1), false).await;
    h.runtime = h.runtime.with_message_repo(Arc::new(FailingAppendMessageRepo::default()));
    let delivery_id = h.delivery.commands.lock().await[0].run_id.clone();
    let error = h.runtime.handle_bot_terminal_event(HandleBotTerminalEventCommand {
        bot_id: "driver-bot".into(), run_id: delivery_id.clone(), event_type: "chat.event".into(),
        event_payload: json!({"run_id": delivery_id, "state": "final", "message": {"content": [{"type": "text", "text": "saved result"}]}}),
        state: ChatEventState::Final, bcs_session_id: Some(started.view.run.session_id.clone()),
    }).await.unwrap_err();
    assert!(error.to_string().contains("history"));
    assert_eq!(h.delivery.commands.lock().await.len(), 1);
    h.runtime = h.runtime.with_message_repo(messages.clone());
    h.runtime.recover_state_machine_progression(None, 10).await.unwrap();
    assert_eq!(h.delivery.commands.lock().await.len(), 2);
    let seq = messages.get_current_seq(&started.view.run.session_id).await.unwrap();
    h.runtime.recover_state_machine_progression(None, 10).await.unwrap();
    assert_eq!(messages.get_current_seq(&started.view.run.session_id).await.unwrap(), seq);
}
