use bcs_collaboration_store::MemoryCollaborationStore;
use bcs_domain::{
    CollaborationDefinition, StateMachineDeliveryCorrelation, StateMachineRun,
    StateMachineRunStatus,
};
use bcs_service_api::{StateMachineDefinitionRepoPort, StateMachineRunRepoPort};
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

fn test_run(run_id: &str, session_id: &str, created_at: u64) -> StateMachineRun {
    StateMachineRun {
        run_id: run_id.to_string(),
        definition_id: "sm_memory_definition".to_string(),
        definition_version: 1,
        group_id: "group-1".to_string(),
        group_version: 1,
        session_id: session_id.to_string(),
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
