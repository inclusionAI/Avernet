use super::*;

/// A Bot-only run has no HumanInput node, so there is never a pending Human
/// node to reveal. `list_pending_human_nodes` must answer the empty list for
/// ANY identity instead of demanding a Present Human participant, matching the
/// Bot-only carve-out `authorize_run_access` already applies to the same run.
/// Before the fix the Human check ran first, so the pending list of a Bot-only
/// run was unreadable by every caller (including the Bot that started it).
#[tokio::test]
async fn bot_only_run_answers_empty_pending_list_for_any_identity() {
    let store = Arc::new(MemoryCollaborationStore::new());
    let group = Arc::new(GroupStore::new());
    group
        .upsert(state_machine_test_group())
        .await
        .expect("seed group");
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group.clone(),
        test_sessions(),
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    );
    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(single_node_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"question": "bot only"}),
            caller_id: Some("driver-bot".to_string()),
            authenticated_human: None,
        })
        .await
        .expect("a Bot-only run starts without a Human participant");
    let run_id = started.view.run.run_id.clone();

    for caller in ["driver-bot", "human_1001", "human_unrelated"] {
        let pending = runtime
            .list_pending_human_nodes(ListPendingHumanNodesCommand {
                run_id: run_id.clone(),
                caller_actor_id: caller.to_string(),
            })
            .await
            .unwrap_or_else(|error| {
                panic!("Bot-only run must answer an empty pending list, got {error:?}")
            });
        assert!(
            pending.is_empty(),
            "Bot-only run exposes no pending Human node to {caller}"
        );
    }
}