use bcs_collaboration_store::MemoryCollaborationStore;
use bcs_domain::{
    CollaborationDefinition, StateMachineDeliveryCorrelation, StateMachineNodeRun,
    StateMachineNodeStatus, StateMachineRun, StateMachineRunStatus,
};
use bcs_event_store::MemoryEventStore;
use bcs_session_store::MemorySessionRepo;
use bcs_service_api::port::NewEvent;
use bcs_service_api::port::repo::{
    AppendEventRecord, EventRepoPort, NewSessionParams, SessionRepoPort,
    StateMachineEventfulTransition,
};
use bcs_service_api::types::{EVENT_SCHEMA_VERSION_V1, EventScope, EventSubject};
use bcs_service_api::{
    CreateStateMachineRerun, CreateStateMachineRerunOutcome,
    SessionKind, SessionStatus, StateMachineDefinitionRepoPort, StateMachineRunRepoPort,
};
use serde_json::json;

#[tokio::test]
async fn definition_upsert_is_idempotent_for_same_content() {
    let store = MemoryCollaborationStore::new();
    let definition = test_definition("Say hello.");

    StateMachineDefinitionRepoPort::upsert(&store, definition.clone())
        .await
        .expect("first upsert");
    StateMachineDefinitionRepoPort::upsert(&store, definition.clone())
        .await
        .expect("same content upsert");

    let loaded = StateMachineDefinitionRepoPort::get(&store, &definition.id, definition.version)
        .await
        .expect("get definition")
        .expect("definition");
    assert_eq!(loaded.name, definition.name);
}

#[tokio::test]
async fn definition_upsert_conflicts_for_same_id_version_with_different_content() {
    let store = MemoryCollaborationStore::new();

    StateMachineDefinitionRepoPort::upsert(&store, test_definition("Say hello."))
        .await
        .expect("first upsert");
    let err = StateMachineDefinitionRepoPort::upsert(&store, test_definition("Say goodbye."))
        .await
        .expect_err("different content should conflict");

    assert!(err.to_string().contains("already exists with different content"));
}

#[tokio::test]
async fn delivery_correlation_resolves_request_id_and_bot_run_alias() {
    let store = MemoryCollaborationStore::new();
    let correlation = StateMachineDeliveryCorrelation {
        state_machine_run_id: "sm-run-1".to_string(),
        node_id: "review".to_string(),
        attempt: 1,
        assignee_bot_id: "bot-a".to_string(),
        delivery_request_id: "delivery-1".to_string(),
        bot_delivery_run_id: None,
    };

    store
        .upsert_delivery_correlation(correlation.clone())
        .await
        .expect("store correlation");

    assert_eq!(
        store
            .lookup_delivery_correlation("delivery-1")
            .await
            .expect("lookup request id"),
        Some(correlation.clone())
    );

    store
        .register_delivery_alias("delivery-1", "bot-run-9".to_string())
        .await
        .expect("register alias");

    let alias = store
        .lookup_delivery_correlation("bot-run-9")
        .await
        .expect("lookup bot run id")
        .expect("alias correlation");
    assert_eq!(alias.delivery_request_id, "delivery-1");
    assert_eq!(alias.bot_delivery_run_id.as_deref(), Some("bot-run-9"));
}

#[tokio::test]
async fn run_lookup_by_session_id_returns_latest_session_run() {
    let store = MemoryCollaborationStore::new();
    let mut older = test_run("sm-run-older", "group-1:abcdef12", 1);
    let newer = test_run("sm-run-newer", "group-1:abcdef12", 2);
    older.updated_at = 3;

    store
        .create_run(older, Vec::new())
        .await
        .expect("create older run");
    store
        .create_run(newer, Vec::new())
        .await
        .expect("create newer run");

    let loaded = store
        .get_run_by_session_id("group-1:abcdef12")
        .await
        .expect("lookup by session")
        .expect("run");
    assert_eq!(loaded.run_id, "sm-run-newer");
    let all_runs = store
        .list_runs_by_session_id("group-1:abcdef12")
        .await
        .expect("list runs by session");
    assert_eq!(
        all_runs
            .into_iter()
            .map(|run| run.run_id)
            .collect::<Vec<_>>(),
        vec!["sm-run-newer", "sm-run-older"]
    );
}

#[tokio::test]
async fn session_idle_create_atomically_allows_only_one_active_run() {
    let store = MemoryCollaborationStore::new();
    let first = test_run("sm-run-first", "group-1:abcdef12", 1);
    let second = test_run("sm-run-second", "group-1:abcdef12", 2);

    let (first_created, second_created) = tokio::join!(
        store.create_run_if_session_idle(first, Vec::new()),
        store.create_run_if_session_idle(second, Vec::new()),
    );
    let first_created = first_created.expect("create first run");
    let second_created = second_created.expect("create second run");

    assert_ne!(first_created, second_created);
    let active = store
        .get_run_by_session_id("group-1:abcdef12")
        .await
        .expect("lookup active run")
        .expect("active run");
    store
        .update_run_status(
            &active.run_id,
            StateMachineRunStatus::Completed,
            None,
            None,
            3,
            Some(3),
        )
        .await
        .expect("complete active run");
    assert!(
        store
            .create_run_if_session_idle(
                test_run("sm-run-third", "group-1:abcdef12", 4),
                Vec::new(),
            )
            .await
            .expect("create run after completion")
    );
}

#[tokio::test]
async fn rerun_create_is_source_idempotent_and_rejects_another_active_session_run() {
    let store = MemoryCollaborationStore::new();
    let definition = test_definition("Say hello.");
    let mut source = test_run("sm-run-source", "group-1:abcdef12", 1);
    source.status = StateMachineRunStatus::Failed;
    source.error = Some("source failed".to_string());
    source.completed_at = Some(2);
    store
        .create_run(source.clone(), Vec::new())
        .await
        .expect("create source run");
    store
        .save_run_snapshot(&source, 1, &definition, None)
        .await
        .expect("save source snapshot");

    let mut child = test_run("sm-run-child", "group-1:abcdef12", 3);
    child.root_run_id = Some(source.run_id.clone());
    child.rerun_of = Some(source.run_id.clone());
    child.status = StateMachineRunStatus::Pending;
    let command = CreateStateMachineRerun {
        source_run_id: source.run_id.clone(),
        run: child.clone(),
        nodes: Vec::new(),
        reactivate_service_session: false,
    };

    assert!(matches!(
        store
            .create_rerun_if_session_idle(command.clone())
            .await
            .expect("create rerun"),
        CreateStateMachineRerunOutcome::Created
    ));
    match store
        .create_rerun_if_session_idle(command)
        .await
        .expect("repeat rerun")
    {
        CreateStateMachineRerunOutcome::Existing(existing) => {
            assert_eq!(existing.run_id, child.run_id);
            assert_eq!(existing.rerun_of.as_deref(), Some(source.run_id.as_str()));
        }
        other => panic!("expected existing direct child, got {other:?}"),
    }

    let mut other_source = test_run("sm-run-other-source", "group-1:abcdef12", 4);
    other_source.status = StateMachineRunStatus::Failed;
    other_source.error = Some("other source failed".to_string());
    other_source.completed_at = Some(5);
    store
        .create_run(other_source.clone(), Vec::new())
        .await
        .expect("create other terminal source");
    store
        .save_run_snapshot(&other_source, 1, &definition, None)
        .await
        .expect("save other source snapshot");
    let mut other_child = test_run("sm-run-other-child", "group-1:abcdef12", 6);
    other_child.root_run_id = Some(other_source.run_id.clone());
    other_child.rerun_of = Some(other_source.run_id.clone());
    other_child.status = StateMachineRunStatus::Pending;
    assert!(matches!(
        store
            .create_rerun_if_session_idle(CreateStateMachineRerun {
                source_run_id: other_source.run_id,
                run: other_child,
                nodes: Vec::new(),
                reactivate_service_session: false,
            })
            .await
            .expect("reject concurrent session rerun"),
        CreateStateMachineRerunOutcome::Conflict
    ));
}

#[tokio::test]
async fn rerun_create_rejects_completed_and_aborted_sources() {
    for (suffix, status) in [
        ("completed", StateMachineRunStatus::Completed),
        ("aborted", StateMachineRunStatus::Aborted),
    ] {
        let store = MemoryCollaborationStore::new();
        let definition = test_definition("Say hello.");
        let session_id = format!("group-1:{suffix}");
        let mut source = test_run(&format!("sm-run-{suffix}"), &session_id, 1);
        source.status = status;
        source.completed_at = Some(2);
        store
            .create_run(source.clone(), Vec::new())
            .await
            .expect("create non-rerunnable source");
        store
            .save_run_snapshot(&source, 1, &definition, None)
            .await
            .expect("save source snapshot");

        let mut child = test_run(&format!("sm-run-{suffix}-child"), &session_id, 3);
        child.root_run_id = Some(source.run_id.clone());
        child.rerun_of = Some(source.run_id.clone());
        child.status = StateMachineRunStatus::Pending;
        assert!(matches!(
            store
                .create_rerun_if_session_idle(CreateStateMachineRerun {
                    source_run_id: source.run_id,
                    run: child,
                    nodes: Vec::new(),
                    reactivate_service_session: false,
                })
                .await
                .expect("reject non-failed source"),
            CreateStateMachineRerunOutcome::Conflict
        ));
    }
}

#[tokio::test]
async fn stale_service_rerun_does_not_reactivate_session_without_a_child_run() {
    let session_repo = std::sync::Arc::new(MemorySessionRepo::new());
    let session = session_repo
        .create(
            "group-1",
            NewSessionParams {
                id: Some("group-1:abcdef12".to_string()),
                session_kind: SessionKind::ServiceInvocation,
                ..Default::default()
            },
        )
        .await
        .expect("create service Session");
    session_repo
        .complete_if_running(&session.id, None, None)
        .await
        .expect("complete first activation");
    session_repo
        .update_callback_status(&session.id, "not_applicable")
        .await
        .expect("complete first callback");

    let store = MemoryCollaborationStore::new().with_session_repo(session_repo.clone());
    let definition = test_definition("Say hello.");
    let mut source = test_run("sm-run-stale-source", &session.id, 1);
    source.status = StateMachineRunStatus::Failed;
    source.session_activation_count = Some(1);
    source.completed_at = Some(2);
    store
        .create_run(source.clone(), Vec::new())
        .await
        .expect("create source Run");
    store
        .save_run_snapshot(&source, 1, &definition, None)
        .await
        .expect("save source snapshot");

    session_repo
        .reactivate(&session.id, None)
        .await
        .expect("advance concurrent activation");
    session_repo
        .complete_if_running(&session.id, None, None)
        .await
        .expect("complete concurrent activation");
    session_repo
        .update_callback_status(&session.id, "not_applicable")
        .await
        .expect("complete concurrent callback");

    let mut child = test_run("sm-run-stale-child", &session.id, 3);
    child.status = StateMachineRunStatus::Pending;
    child.root_run_id = Some(source.run_id.clone());
    child.rerun_of = Some(source.run_id.clone());
    child.session_activation_count = Some(2);
    assert!(matches!(
        store
            .create_rerun_if_session_idle(CreateStateMachineRerun {
                source_run_id: source.run_id,
                run: child.clone(),
                nodes: Vec::new(),
                reactivate_service_session: true,
            })
            .await
            .expect("reject stale activation"),
        CreateStateMachineRerunOutcome::Conflict
    ));

    let preserved = session_repo
        .get(&session.id)
        .await
        .expect("preserve Session");
    assert_eq!(preserved.status, SessionStatus::Completed);
    assert_eq!(preserved.activation_count, 2);
    assert!(store
        .get_run(&child.run_id)
        .await
        .expect("query child")
        .is_none());
}

#[tokio::test]
async fn identical_human_response_is_idempotent_while_completion_is_retried() {
    let store = MemoryCollaborationStore::new();
    let run = test_run("sm-run-human", "group-1:abcdef12", 1);
    let node = StateMachineNodeRun {
        run_id: run.run_id.clone(),
        node_id: "review".to_string(),
        status: StateMachineNodeStatus::Running,
        attempt: 1,
        node_timeout_ms: Some(60_000),
        timeout_deadline_ms: Some(60_001),
        max_attempts: 1,
        assignee_bot_id: None,
        outcome: None,
        responded_by: None,
        delivery_request_id: None,
        bot_delivery_run_id: None,
        artifact_text: None,
        error: None,
        started_at: Some(1),
        completed_at: None,
    };
    store
        .create_run(run, vec![node])
        .await
        .expect("create human run");

    assert!(
        store
            .record_human_response_if_running(
                "sm-run-human",
                "review",
                1,
                "approve".to_string(),
                "human-1".to_string(),
            )
            .await
            .expect("record first response")
    );
    assert!(
        store
            .record_human_response_if_running(
                "sm-run-human",
                "review",
                1,
                "approve".to_string(),
                "human-1".to_string(),
            )
            .await
            .expect("retry identical response")
    );
    assert!(
        !store
            .record_human_response_if_running(
                "sm-run-human",
                "review",
                1,
                "reject".to_string(),
                "human-1".to_string(),
            )
            .await
            .expect("reject conflicting response")
    );
}

#[tokio::test]
async fn run_start_and_ordered_public_events_commit_atomically() {
    let events = std::sync::Arc::new(MemoryEventStore::new());
    let store = MemoryCollaborationStore::new().with_event_store(events.clone());
    let mut run = test_run("sm-run-eventful", "group-1:abcdef12", 1);
    run.status = StateMachineRunStatus::Pending;
    store
        .create_run(run, Vec::new())
        .await
        .expect("create pending run");

    assert!(
        store
            .commit_eventful_transition(StateMachineEventfulTransition::StartRun {
                run_id: "sm-run-eventful".to_string(),
                started_at_ms: 2,
                events: vec![
                    public_event("evt-run-created", "state_machine.run.created", None),
                    public_event(
                        "evt-run-started",
                        "state_machine.run.started",
                        Some("evt-run-created"),
                    ),
                ],
            })
            .await
            .expect("commit eventful start")
    );

    let stored = store
        .get_run("sm-run-eventful")
        .await
        .expect("load run")
        .expect("run");
    assert_eq!(stored.status, StateMachineRunStatus::Running);
    assert_eq!(stored.updated_at, 2);
    let created = events
        .get_event("evt-run-created", "test")
        .await
        .expect("load created Event")
        .expect("created Event");
    let started = events
        .get_event("evt-run-started", "test")
        .await
        .expect("load started Event")
        .expect("started Event");
    assert_eq!(created.envelope.stream.sequence, 1);
    assert_eq!(started.envelope.stream.sequence, 2);
}

#[tokio::test]
async fn event_append_failure_rolls_back_run_start_and_prior_event() {
    let events = std::sync::Arc::new(MemoryEventStore::new());
    let store = MemoryCollaborationStore::new().with_event_store(events.clone());
    let mut run = test_run("sm-run-rollback", "group-1:abcdef12", 1);
    run.status = StateMachineRunStatus::Pending;
    store
        .create_run(run, Vec::new())
        .await
        .expect("create pending run");

    let error = store
        .commit_eventful_transition(StateMachineEventfulTransition::StartRun {
            run_id: "sm-run-rollback".to_string(),
            started_at_ms: 2,
            events: vec![
                public_event("evt-rollback-created", "state_machine.run.created", None),
                public_event(
                    "evt-rollback-started",
                    "state_machine.run.started",
                    Some("missing-cause"),
                ),
            ],
        })
        .await
        .expect_err("invalid Event batch must fail");
    assert!(error.to_string().contains("causation"));

    let stored = store
        .get_run("sm-run-rollback")
        .await
        .expect("load run")
        .expect("run");
    assert_eq!(stored.status, StateMachineRunStatus::Pending);
    assert!(
        events
            .get_event("evt-rollback-created", "test")
            .await
            .expect("load rolled-back Event")
            .is_none()
    );
}

fn public_event(
    event_id: &str,
    event_type: &str,
    causation_event_id: Option<&str>,
) -> AppendEventRecord {
    AppendEventRecord {
        event: NewEvent {
            event_id: event_id.to_string(),
            event_type: event_type.to_string(),
            schema_version: EVENT_SCHEMA_VERSION_V1.to_string(),
            producer: "bcs-collaboration-runtime".to_string(),
            producer_key: event_id.to_string(),
            occurred_at: "2026-08-19T00:00:00.000Z".to_string(),
            subject: EventSubject {
                subject_type: "state_machine.run".to_string(),
                id: "sm-run-eventful".to_string(),
            },
            scope: EventScope {
                group_id: Some("group-1".to_string()),
                session_id: Some("group-1:abcdef12".to_string()),
                run_id: Some("sm-run-eventful".to_string()),
                ..EventScope::default()
            },
            stream_key: "state-machine-run:sm-run-eventful".to_string(),
            actor: None,
            correlation_id: None,
            causation_event_id: causation_event_id.map(str::to_string),
            trace_id: None,
            data: Default::default(),
        },
        recorded_at: "2026-08-19T00:00:00.001Z".to_string(),
        retention_until_ms: 2_000_000_000_000,
        env: "test".to_string(),
    }
}

fn test_run(run_id: &str, session_id: &str, created_at: u64) -> StateMachineRun {
    StateMachineRun {
        run_id: run_id.to_string(),
        root_run_id: Some(run_id.to_string()),
        rerun_of: None,
        definition_id: "sm_memory_definition".to_string(),
        definition_version: 1,
        group_id: "group-1".to_string(),
        group_version: 1,
        session_id: session_id.to_string(),
        session_activation_count: None,
        created_by: Some("tester".to_string()),
        status: StateMachineRunStatus::Running,
        input: json!({"question": "hello"}),
        output: None,
        error: None,
        created_at,
        updated_at: created_at,
        completed_at: None,
    }
}

fn test_definition(instruction: &str) -> CollaborationDefinition {
    serde_yaml::from_str(&format!(
        r#"
api_version: bcs.collaboration/v1
id: sm_memory_definition
version: 1
name: Memory Definition
participants:
  driver:
    bot_id: driver-bot
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      answer:
        kind: bot_task
        display_name: Answer
        assignee:
          type: bot_binding
          binding: driver
        instruction: {instruction}
        final_output: true
"#
    ))
    .expect("valid definition")
}
