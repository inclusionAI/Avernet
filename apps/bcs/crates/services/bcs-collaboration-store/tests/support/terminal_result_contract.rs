use bcs_domain::{StateMachineNodeRun, StateMachineNodeStatus, StateMachineRun, StateMachineRunStatus};
use bcs_service_api::port::repo::{AppendEventRecord, StateMachineEventfulTransition, StateMachineRunRepoPort};
use bcs_service_api::{
    StateMachineChatResultPayload, StateMachineResultPublishCommand,
    StateMachineRunResultPayload, StateMachineTerminalReason,
};
use bcs_service_api::port::NewEvent;
use bcs_service_api::types::{EVENT_SCHEMA_VERSION_V1, EventScope, EventSubject};
use serde_json::json;

fn run(run_id: &str) -> StateMachineRun {
    StateMachineRun {
        run_id: run_id.into(),
        root_run_id: Some(run_id.into()),
        rerun_of: None,
        definition_id: "terminal-result-contract".into(),
        definition_version: 1,
        group_id: "group-1".into(),
        group_version: 1,
        session_id: format!("group-1:{run_id}"),
        session_activation_count: None,
        created_by: Some("tester".into()),
        status: StateMachineRunStatus::Running,
        input: json!({"question": "terminal result"}),
        opening_message_override: None,
        output: None,
        error: None,
        created_at: 100,
        updated_at: 100,
        completed_at: None,
    }
}

fn completed_node(run_id: &str) -> StateMachineNodeRun {
    StateMachineNodeRun {
        run_id: run_id.into(),
        node_id: "answer".into(),
        status: StateMachineNodeStatus::Completed,
        attempt: 0,
        node_timeout_ms: None,
        timeout_deadline_ms: None,
        max_attempts: 1,
        assignee_bot_id: Some("tester".into()),
        outcome: Some("complete".into()),
        responded_by: None,
        delivery_request_id: None,
        bot_delivery_run_id: None,
        artifact_text: Some("completed artifact".into()),
        error: None,
        started_at: Some(100),
        completed_at: Some(101),
    }
}

fn event(run_id: &str, event_id: &str, event_type: &str) -> AppendEventRecord {
    AppendEventRecord {
        event: NewEvent {
            event_id: event_id.into(),
            event_type: event_type.into(),
            schema_version: EVENT_SCHEMA_VERSION_V1.into(),
            producer: "bcs-collaboration-runtime".into(),
            producer_key: event_id.into(),
            occurred_at: "2026-11-22T00:00:00.000Z".into(),
            subject: EventSubject { subject_type: "state_machine.run".into(), id: run_id.into() },
            scope: EventScope {
                group_id: Some("group-1".into()),
                session_id: Some(format!("group-1:{run_id}")),
                run_id: Some(run_id.into()),
                ..EventScope::default()
            },
            stream_key: format!("state-machine-run:{run_id}"),
            actor: None,
            correlation_id: Some(run_id.into()),
            causation_event_id: None,
            trace_id: None,
            data: Default::default(),
        },
        recorded_at: "2026-11-22T00:00:00.001Z".into(),
        retention_until_ms: 2_000_000_000_000,
        env: "test".into(),
    }
}

fn terminal_payload(run_id: &str, status: StateMachineRunStatus, reason: StateMachineTerminalReason) -> StateMachineChatResultPayload {
    let result = match status {
        StateMachineRunStatus::Failed => StateMachineRunResultPayload::failed(
            run_id.into(), "group-1".into(), format!("group-1:{run_id}"),
            "Terminal result contract".into(), "tester".into(), Some("answer".into()), Some(0),
            reason, vec!["answer".into()], 200,
        ),
        StateMachineRunStatus::Aborted => StateMachineRunResultPayload::aborted(
            run_id.into(), "group-1".into(), format!("group-1:{run_id}"),
            "Terminal result contract".into(), "tester".into(), reason, vec!["answer".into()], 200,
        ),
        _ => unreachable!("terminal result contract only covers failed and aborted"),
    };
    StateMachineChatResultPayload {
        command: StateMachineResultPublishCommand {
            run_id: run_id.into(),
            group_id: "group-1".into(),
            session_id: format!("group-1:{run_id}"),
            sender_bot_id: "tester".into(),
            content: result.render_manager_message(),
            terminal_result: Some(result),
            created_at_ms: 200,
        },
        deadline_ms: 90_200,
    }
}

pub(super) async fn terminal_result_contract(store: &dyn StateMachineRunRepoPort) {
    let failed_run = run("terminal-result-failed");
    store.create_run(failed_run.clone(), vec![completed_node(&failed_run.run_id)]).await.unwrap();
    let failed_payload = terminal_payload(&failed_run.run_id, StateMachineRunStatus::Failed, StateMachineTerminalReason::NodeFailed);
    assert!(store.commit_eventful_transition(StateMachineEventfulTransition::FailRun {
        run_id: failed_run.run_id.clone(),
        error: "internal failure".into(),
        completed_at_ms: 200,
        event: event(&failed_run.run_id, "event-terminal-failed", "state_machine.run.failed"),
        terminal_result: Some(failed_payload.clone()),
    }).await.unwrap());
    let saved_failed = store.get_chat_result(&failed_run.run_id).await.unwrap().unwrap();
    assert_eq!(saved_failed.payload, failed_payload);
    assert_eq!(saved_failed.status, bcs_service_api::StateMachineChatResultStatus::Pending);

    let aborted_run = run("terminal-result-aborted");
    store.create_run(aborted_run.clone(), vec![completed_node(&aborted_run.run_id)]).await.unwrap();
    let aborted_payload = terminal_payload(&aborted_run.run_id, StateMachineRunStatus::Aborted, StateMachineTerminalReason::UserAborted);
    assert!(store.commit_eventful_transition(StateMachineEventfulTransition::AbortRun {
        run_id: aborted_run.run_id.clone(),
        reason: Some("user cancellation".into()),
        completed_at_ms: 200,
        event: event(&aborted_run.run_id, "event-terminal-aborted", "state_machine.run.aborted"),
        terminal_result: Some(aborted_payload.clone()),
    }).await.unwrap());
    let saved_aborted = store.get_chat_result(&aborted_run.run_id).await.unwrap().unwrap();
    assert_eq!(saved_aborted.payload, aborted_payload);
    assert_eq!(saved_aborted.status, bcs_service_api::StateMachineChatResultStatus::Pending);

    let immutable_run = run("terminal-result-immutable");
    store.create_run(immutable_run.clone(), vec![completed_node(&immutable_run.run_id)]).await.unwrap();
    let legacy = StateMachineChatResultPayload {
        command: StateMachineResultPublishCommand {
            run_id: immutable_run.run_id.clone(),
            group_id: immutable_run.group_id.clone(),
            session_id: immutable_run.session_id.clone(),
            sender_bot_id: "tester".into(),
            content: "legacy result".into(),
            terminal_result: None,
            created_at_ms: 100,
        },
        deadline_ms: 90_100,
    };
    assert!(store.save_chat_result(legacy.clone()).await.unwrap());
    assert!(store.commit_eventful_transition(StateMachineEventfulTransition::AbortRun {
        run_id: immutable_run.run_id.clone(),
        reason: Some("user cancellation".into()),
        completed_at_ms: 200,
        event: event(&immutable_run.run_id, "event-terminal-immutable", "state_machine.run.aborted"),
        terminal_result: Some(terminal_payload(&immutable_run.run_id, StateMachineRunStatus::Aborted, StateMachineTerminalReason::UserAborted)),
    }).await.unwrap());
    assert_eq!(store.get_chat_result(&immutable_run.run_id).await.unwrap().unwrap().payload, legacy);
}



pub(super) async fn terminal_result_persistence_contract(store: &dyn StateMachineRunRepoPort) {
    let failed_run = run("terminal-result-persisted-failed");
    store.create_run(failed_run.clone(), vec![completed_node(&failed_run.run_id)]).await.unwrap();
    store.update_run_status(
        &failed_run.run_id,
        StateMachineRunStatus::Failed,
        None,
        Some("internal failure".into()),
        200,
        Some(200),
    ).await.unwrap();
    let failed_payload = terminal_payload(&failed_run.run_id, StateMachineRunStatus::Failed, StateMachineTerminalReason::NodeFailed);
    assert!(store.save_chat_result(failed_payload.clone()).await.unwrap());
    assert_eq!(store.get_chat_result(&failed_run.run_id).await.unwrap().unwrap().payload, failed_payload);

    let aborted_run = run("terminal-result-persisted-aborted");
    store.create_run(aborted_run.clone(), vec![completed_node(&aborted_run.run_id)]).await.unwrap();
    store.update_run_status(
        &aborted_run.run_id,
        StateMachineRunStatus::Aborted,
        None,
        Some("user cancellation".into()),
        200,
        Some(200),
    ).await.unwrap();
    let aborted_payload = terminal_payload(&aborted_run.run_id, StateMachineRunStatus::Aborted, StateMachineTerminalReason::UserAborted);
    assert!(store.save_chat_result(aborted_payload.clone()).await.unwrap());
    assert_eq!(store.get_chat_result(&aborted_run.run_id).await.unwrap().unwrap().payload, aborted_payload);

    let immutable_run = run("terminal-result-persisted-immutable");
    store.create_run(immutable_run.clone(), vec![completed_node(&immutable_run.run_id)]).await.unwrap();
    store.update_run_status(
        &immutable_run.run_id,
        StateMachineRunStatus::Aborted,
        None,
        Some("user cancellation".into()),
        200,
        Some(200),
    ).await.unwrap();
    let legacy = StateMachineChatResultPayload {
        command: StateMachineResultPublishCommand {
            run_id: immutable_run.run_id.clone(),
            group_id: immutable_run.group_id.clone(),
            session_id: immutable_run.session_id.clone(),
            sender_bot_id: "tester".into(),
            content: "legacy result".into(),
            terminal_result: None,
            created_at_ms: 100,
        },
        deadline_ms: 90_100,
    };
    assert!(store.save_chat_result(legacy.clone()).await.unwrap());
    assert!(store.save_chat_result(terminal_payload(&immutable_run.run_id, StateMachineRunStatus::Aborted, StateMachineTerminalReason::UserAborted)).await.is_err());
    assert_eq!(store.get_chat_result(&immutable_run.run_id).await.unwrap().unwrap().payload, legacy);
}
