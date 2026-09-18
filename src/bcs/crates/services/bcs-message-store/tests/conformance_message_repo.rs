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
