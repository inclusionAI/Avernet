use std::sync::Arc;
use bcs_channel_store::{MemoryHumanInputRequestRepo, DbHumanInputRequestStore};
use bcs_domain::{HumanInputRequest, HumanInputRequestStatus as Status};
use bcs_service_api::port::repo::HumanInputRequestRepoPort;
use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;

const SCHEMA: &str = "CREATE TABLE bcs_human_input_requests (
                request_id TEXT PRIMARY KEY,
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                session_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                binding_id TEXT NOT NULL,
                channel_type TEXT NOT NULL,
                account_ref TEXT NOT NULL,
                notification_mode TEXT NOT NULL,
                reply_scope_key TEXT NOT NULL,
                active_slot_key TEXT,
                assignee_actor_id TEXT NOT NULL,
                im_conversation_id TEXT NOT NULL,
                im_conversation_type TEXT NOT NULL,
                im_user_id TEXT,
                node_display_name TEXT NOT NULL,
                notification_text TEXT NOT NULL,
                deadline_ms INTEGER NOT NULL,
                status TEXT NOT NULL,
                provider_message_ref TEXT,
                delivery_attempts INTEGER NOT NULL DEFAULT 0,
                last_delivery_error TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                activated_at INTEGER,
                responded_at INTEGER
            )";

fn request(id: &str) -> HumanInputRequest {
    serde_json::from_value(serde_json::json!({
        "request_id": id, "session_id": "session", "run_id": format!("run-{id}"), "node_id": "node",
        "binding_id": "binding", "channel_type": "test", "account_ref": "test-account",
        "notification_mode": "direct_assignee", "reply_scope_key": format!("scope-{id}"), "active_slot_key": null,
        "assignee_actor_id": "human", "im_conversation_id": "conversation", "im_conversation_type": "private",
        "im_user_id": "user", "node_display_name": "Review", "notification_text": "original rendered text",
        "deadline_ms": 1000, "status": "queued", "provider_message_ref": null, "delivery_attempts": 0,
        "last_delivery_error": null, "created_at": 10, "activated_at": null, "responded_at": null
    })).unwrap()
}

async fn contract(a: &dyn HumanInputRequestRepoPort, b: &dyn HumanInputRequestRepoPort) {
    a.enqueue(request("race")).await.unwrap();
    assert_eq!(a.get("race").await.unwrap().unwrap().status, Status::NotificationPending);
    assert_eq!(a.find_occupying_by_scope("scope-race").await.unwrap().unwrap().request_id, "race");
    assert!(a.find_active_by_scope("scope-race").await.unwrap().is_none());
    assert!(!a.mark_active("race", Some("fake"), 20).await.unwrap());
    assert!(!a.mark_delivery_failed("race", "unsent").await.unwrap());
    let (x,y) = tokio::join!(a.begin_notification("race", 100), b.begin_notification("race", 100));
    assert_eq!(usize::from(x.unwrap()) + usize::from(y.unwrap()), 1);
    let saved = a.get("race").await.unwrap().unwrap();
    assert_eq!(saved.status, Status::Notifying); assert_eq!(saved.delivery_attempts, 1);
    assert_eq!(saved.notification_text, "original rendered text"); assert_eq!(saved.deadline_ms, 1000);
    assert!(!b.begin_notification("race", 101).await.unwrap());
    assert!(!b.mark_active("race", Some("late"), 1000).await.unwrap());
    a.close_for_run_node("run-race", "node", Status::Cancelled).await.unwrap();
    assert!(b.find_occupying_by_scope("scope-race").await.unwrap().is_none());
    assert!(!b.mark_active("race", Some("old-sender"), 500).await.unwrap());
    assert!(!b.mark_delivery_failed("race", "old-error").await.unwrap());
    a.enqueue(request("expired")).await.unwrap();
    assert!(!a.begin_notification("expired", 1000).await.unwrap());
    a.enqueue(request("ack")).await.unwrap(); assert!(a.begin_notification("ack", 100).await.unwrap());
    assert!(a.mark_active("ack", Some("provider-original"), 200).await.unwrap());
    assert!(!b.begin_notification("ack", 201).await.unwrap());
    assert_eq!(a.get("ack").await.unwrap().unwrap().provider_message_ref.as_deref(), Some("provider-original"));
    assert_eq!(a.count_queued("scope-ack").await.unwrap(), 0);
    for index in 0..70 {
        let mut queued = request(&format!("queued-{index}"));
        queued.reply_scope_key = "scope-ack".into();
        a.enqueue(queued).await.unwrap();
    }
    assert_eq!(b.count_queued("scope-ack").await.unwrap(), 70);
    assert_eq!(b.count_queued("scope-absent").await.unwrap(), 0);
}

async fn sqlite(path: Option<&std::path::Path>) -> Arc<LocalSqliteDbPlugin> {
    let db = Arc::new(match path { Some(path) => LocalSqliteDbPlugin::new_file(path.to_str().unwrap()).unwrap(), None => LocalSqliteDbPlugin::new().unwrap() });
    db.execute(DbStatement::new(SCHEMA)).await.unwrap();
    db.execute(DbStatement::new("CREATE UNIQUE INDEX uk_human_input_active_slot ON bcs_human_input_requests(active_slot_key)")).await.unwrap();
    db
}

#[tokio::test]
async fn memory_and_sqlite_send_barrier_contract() {
    let memory = MemoryHumanInputRequestRepo::new(); contract(&memory, &memory).await;
    let db = sqlite(None).await;
    contract(&DbHumanInputRequestStore::sqlite(db.clone()), &DbHumanInputRequestStore::sqlite(db.clone())).await;
    db.execute(DbStatement::new("UPDATE bcs_human_input_requests SET status = 'notifying', delivery_attempts = 0 WHERE request_id = 'expired'")).await.unwrap();
    assert!(!DbHumanInputRequestStore::sqlite(db).begin_notification("expired", 100).await.unwrap(), "legacy notifying is ambiguous even with zero attempts");
}

#[tokio::test]
async fn sqlite_marker_write_failure_is_retryable_and_saved_marker_survives_reopen() {
    let dir = tempfile::tempdir().unwrap(); let path = dir.path().join("human.sqlite");
    let db = sqlite(Some(&path)).await; let repo = DbHumanInputRequestStore::sqlite(db.clone());
    repo.enqueue(request("persist")).await.unwrap();
    db.execute(DbStatement::new("CREATE TRIGGER fail_marker BEFORE UPDATE ON bcs_human_input_requests BEGIN SELECT RAISE(ABORT, 'injected marker failure'); END")).await.unwrap();
    assert!(repo.begin_notification("persist", 10).await.is_err());
    assert_eq!(repo.get("persist").await.unwrap().unwrap().status, Status::NotificationPending);
    db.execute(DbStatement::new("DROP TRIGGER fail_marker")).await.unwrap();
    assert!(repo.begin_notification("persist", 20).await.unwrap()); drop(repo); drop(db);
    let reopened = DbHumanInputRequestStore::sqlite(Arc::new(LocalSqliteDbPlugin::new_file(path.to_str().unwrap()).unwrap()));
    assert_eq!(reopened.get("persist").await.unwrap().unwrap().status, Status::Notifying);
    assert!(!reopened.begin_notification("persist", 30).await.unwrap());
}

#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL pointing to a disposable MySQL database"]
async fn real_mysql_notification_barrier_contract() {
    use bcs_config_api::{MysqlDbConfig, StatementProtocol};
    use bcs_config_api::mysql::MysqlConnectionConfig;
    use bcs_db_mysql::{MysqlDbManager, MysqlDbPlugin};
    let url = std::env::var("BCS_TEST_MYSQL_URL").expect("disposable database required");
    let opts = mysql_async::Opts::from_url(&url).unwrap();
    for protocol in [StatementProtocol::Text, StatementProtocol::Prepared] {
        let config = MysqlDbConfig::new().with_database(opts.db_name().unwrap()).with_connection(MysqlConnectionConfig {
            connection_type: "direct".into(), host: Some(opts.ip_or_hostname().to_string()), port: Some(opts.tcp_port()),
            user: opts.user().map(str::to_string), password: opts.pass().map(str::to_string), extra: Default::default(),
        }).with_statement_protocol(protocol);
        let manager = MysqlDbManager::new(config).await.unwrap(); let db = Arc::new(MysqlDbPlugin::new(manager.clone(), "bcs"));
        let rows = db.query(DbStatement::new("SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'bcs_human_input_requests'")).await.unwrap();
        assert_eq!(bcs_db_api::db_get_column::<i64>(&rows[0], "n").unwrap(), 0, "requires empty disposable schema");
        db.execute(DbStatement::new(include_str!("../../../../migrations/mysql/008_human_input_im_requests.sql"))).await.unwrap();
        contract(&DbHumanInputRequestStore::mysql(db.clone()), &DbHumanInputRequestStore::mysql(db.clone())).await;
        db.execute(DbStatement::new("DROP TABLE bcs_human_input_requests")).await.unwrap(); manager.close().await;
    }
}
