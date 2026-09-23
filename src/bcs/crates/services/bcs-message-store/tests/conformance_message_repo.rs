use std::sync::Arc;

use bcs_db_api::{DbPlugin, DbStatement, DbValue};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_message_store::MemoryMessageRepo;
use bcs_message_store::MySqlMessageStore;
use bcs_test_support::contract::repo::message_repo_contract_tests;

#[path = "../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod bootstrap_migrations;

#[tokio::test]
async fn memory_message_repo_passes_contract() {
    let repo = MemoryMessageRepo::new();
    message_repo_contract_tests(&repo).await;
}

#[tokio::test]
async fn sqlite_message_repo_passes_contract() {
    let db = sqlite_db().await;
    let repo = MySqlMessageStore::sqlite(db, "dev".to_string());
    message_repo_contract_tests(&repo).await;
}

#[tokio::test]
async fn memory_worker_history_includes_only_own_task_display() {
    worker_history_includes_only_own_task_display(&MemoryMessageRepo::new()).await;
}

#[tokio::test]
async fn sqlite_worker_history_includes_only_own_task_display() {
    let repo = MySqlMessageStore::sqlite(sqlite_db().await, "dev".into());
    worker_history_includes_only_own_task_display(&repo).await;
}

async fn worker_history_includes_only_own_task_display(repo: &dyn bcs_service_api::port::repo::MessageRepoPort) {
    use bcs_domain::{MessageAudience, MessageOwnerFilter, MessageQuery, MessageVisibilityDomain, NewMessage, SenderType};
    let group = "contract-group";
    let session = "contract-group:abcd1234";
    for (sender, kind, owner, client, text) in [
        ("worker-a", "chat", Some("worker-a"), None, "owned-segment"),
        ("worker-a", "tool_call", Some("worker-a"), None, "owned-tool"),
        ("worker-a", "chat", None, Some("task-display:a"), "own-result"),
        ("worker-b", "chat", None, Some("task-display:b"), "other-result"),
        ("worker-a", "chat", None, None, "unrelated-ownerless"),
        ("worker-a", "run_reply", None, Some("task-result:a"), "internal-result"),
    ] {
        repo.append_message(NewMessage {
            group_id: group.into(), session_id: session.into(), sender_id: sender.into(),
            sender_type: SenderType::Bot, message_type: kind.into(),
            content: serde_json::json!(text), client_msg_id: client.map(str::to_string),
            owner_bot_id: owner.map(str::to_string),
            visibility_domain: MessageVisibilityDomain::ManagerWorker,
            audience: Some(match owner {
                Some(worker) => MessageAudience::directed([worker.to_string()]).unwrap(),
                None => MessageAudience::FullOnly,
            }),
            created_at: 100, run_id: "worker-run".into(),
        }).await.unwrap();
    }
    let filter = MessageOwnerFilter::WorkerHistory("worker-a".into());
    let query = repo.query_messages(MessageQuery {
        group_id: group.into(), session_id: session.into(), cursor: None, limit: 20,
        keyword: None, sender_id: None, message_type: None, owner_filter: filter.clone(),
        time_range: None, visible_from_seq: None, human_view: None,
    }).await.unwrap();
    let list = repo.list_session_history(session, filter, None, None, None, 20).await.unwrap();
    for page in [query, list] {
        let mut texts: Vec<_> = page.messages.iter().filter_map(|m| m.content.as_str()).collect();
        texts.sort_unstable();
        assert_eq!(texts, vec!["own-result", "owned-segment", "owned-tool"]);
    }
    let display_filter = MessageOwnerFilter::WorkerTaskDisplay("worker-a".into());
    let display_query = repo.query_messages(MessageQuery {
        group_id: group.into(), session_id: session.into(), cursor: None, limit: 20,
        keyword: None, sender_id: None, message_type: Some("chat".into()),
        owner_filter: display_filter.clone(), time_range: None, visible_from_seq: None,
        human_view: None,
    }).await.unwrap();
    let display_list = repo.list_session_history(session, display_filter, None, None, None, 20).await.unwrap();
    for page in [display_query, display_list] {
        assert_eq!(page.messages.len(), 1);
        assert_eq!(page.messages[0].content.as_str(), Some("own-result"));
    }
}

#[tokio::test]
async fn error_projection_concurrent_retries_use_database_primary_key() {
    use bcs_domain::{NewMessage, SenderType, MessageVisibilityDomain};
    use bcs_service_api::port::repo::MessageRepoPort;
    let dir = tempfile::tempdir().unwrap();
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new_file(dir.path().join("errors.db")).unwrap());
    bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    db.execute(DbStatement::new("INSERT INTO bcs_group_sessions (session_id, group_id, env, participants) VALUES ('errors', 'group', 'dev', '[]')")).await.unwrap();
    let repo = Arc::new(MySqlMessageStore::sqlite(db.clone(), "dev".into()));
    let barrier = Arc::new(tokio::sync::Barrier::new(16));
    let mut tasks = Vec::new();
    for i in 0..16 {
        let repo = repo.clone();
        let barrier = barrier.clone();
        tasks.push(tokio::spawn(async move {
            barrier.wait().await;
            repo.append_message(NewMessage {
                group_id: "group".into(), session_id: "errors".into(), sender_id: "bot".into(),
                sender_type: SenderType::Bot, message_type: bcs_domain::CHAT_ERROR_MESSAGE_TYPE.into(),
                content: serde_json::json!("失败"), client_msg_id: Some(format!("retry-{i}")),
                owner_bot_id: None, created_at: 1, run_id: "canonical-run".into(),
                visibility_domain: MessageVisibilityDomain::Chat, audience: None,
            }).await.unwrap()
        }));
    }
    let mut ids = std::collections::BTreeSet::new();
    for task in tasks {
        let row = task.await.unwrap();
        assert_eq!(row.session_seq, 1);
        ids.insert(row.message_id);
    }
    assert_eq!(ids.len(), 1);
    let rows = db.query(DbStatement::new("SELECT current_msg_seq FROM bcs_group_sessions WHERE session_id='errors'")).await.unwrap();
    assert_eq!(bcs_db_api::db_get_column::<i64>(&rows[0], "current_msg_seq").unwrap(), 1);
}

async fn sqlite_db() -> Arc<dyn DbPlugin> {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("run sqlite migrations");
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_group_sessions (session_id, group_id, env, participants) \
         VALUES (?, ?, ?, ?)",
        vec![
            DbValue::from("contract-group:abcd1234"),
            DbValue::from("contract-group"),
            DbValue::from("dev"),
            DbValue::from("[]"),
        ],
    ))
    .await
    .expect("seed contract session");
    db
}

#[path = "support/history_identity.rs"]
mod history_identity;

#[path = "support/history_window.rs"]
mod history_window;

#[path = "support/history_read.rs"]
mod history_read;
