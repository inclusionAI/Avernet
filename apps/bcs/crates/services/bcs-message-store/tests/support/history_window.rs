use super::*;
use bcs_domain::{HumanMessageView, MessageAudience, MessageOwnerFilter, MessageVisibilityDomain, MessageViewScope, NewMessage, SenderType};
use bcs_service_api::port::repo::MessageRepoPort;
use serde_json::json;

fn message(session: &str, id: &str, supplemental: bool) -> NewMessage {
    NewMessage {
        group_id: "contract-group".into(), session_id: session.into(), sender_id: "human_a".into(),
        sender_type: SenderType::Human, message_type: if supplemental { "state_machine_output" } else { "chat" }.into(),
        content: if supplemental { json!({"text":id,"metadata":{"state_machine":{"history_schema_version":1}}}) } else { json!({"text":id}) },
        client_msg_id: Some(id.into()), owner_bot_id: None,
        visibility_domain: if supplemental { MessageVisibilityDomain::StateMachine } else { MessageVisibilityDomain::Chat },
        audience: supplemental.then_some(MessageAudience::FullOnly), created_at: 1, run_id: String::new(),
    }
}

async fn visible(repo: &dyn MessageRepoPort, session: &str, from: i64) -> Vec<String> {
    repo.list_session_history(session, MessageOwnerFilter::Any, Some(from), None, None, 1000)
        .await.unwrap().messages.into_iter().filter(|m| m.message_type == "chat")
        .map(|m| m.client_msg_id.unwrap()).collect()
}

async fn window_contract(repo: &dyn MessageRepoPort, session: &str) {
    for n in 1..=10 {
        repo.append_message_with_id(format!("ordinary-{n}"), message(session, &format!("ordinary-{n}"), false)).await.unwrap();
    }
    assert_eq!(repo.resolve_history_window_start(session, 10, 5).await.unwrap(), 6);
    let original = visible(repo, session, 6).await;
    for n in 1..=25 {
        let id = format!("supplemental-{n}");
        let row = repo.append_message_with_id(id.clone(), message(session, &id, true)).await.unwrap();
        let replay = repo.append_message_with_id(id.clone(), message(session, &id, true)).await.unwrap();
        assert_eq!(row.session_seq, replay.session_seq);
    }
    assert_eq!(repo.get_current_seq(session).await.unwrap(), 35);
    assert_eq!(repo.resolve_history_window_start(session, 35, 5).await.unwrap(), 6);
    assert_eq!(repo.resolve_history_window_start(session, 10, 5).await.unwrap(), 6);
    assert_eq!(visible(repo, session, 6).await, original);
    repo.append_message_with_id("ordinary-11".into(), message(session, "ordinary-11", false)).await.unwrap();
    assert_eq!(repo.resolve_history_window_start(session, 36, 5).await.unwrap(), 7);
    assert_eq!(repo.resolve_history_window_start(session, 35, 5).await.unwrap(), 6, "a fixed join anchor never moves with later writes");
    let view = Some(HumanMessageView { actor_id: "human_a".into(), scope: MessageViewScope::Participant, allow_legacy_unclassified_chat: true });
    let page = repo.list_session_history(session, MessageOwnerFilter::Any, Some(7), view.clone(), None, 1000).await.unwrap();
    assert_eq!(page.messages.len(), 5, "supplemental status must not bypass audience");
    let queried = repo.query_messages(bcs_domain::MessageQuery {
        group_id: "contract-group".into(), session_id: session.into(), cursor: None, limit: 1000,
        keyword: None, sender_id: None, message_type: None, owner_filter: MessageOwnerFilter::Any,
        time_range: None, visible_from_seq: Some(7), human_view: view,
    }).await.unwrap();
    assert_eq!(queried.messages.len(), 5);
    // Legacy V2 was already durable: preserve its original slot, even if the
    // new writer retries the same ID with the supplemental metadata.
    let mut legacy = message(session, "legacy-v2", true);
    legacy.content = json!({"text":"legacy-v2"});
    repo.append_message_with_id("legacy-v2".into(), legacy).await.unwrap();
    let replay = repo.append_message_with_id("legacy-v2".into(), message(session, "legacy-v2", true)).await.unwrap();
    assert_eq!(replay.session_seq, 37);
    assert!(replay.content["metadata"].is_null());
    repo.append_message_with_id("last".into(), message(session, "last", true)).await.unwrap();
    assert_eq!(repo.resolve_history_window_start(session, 38, 5).await.unwrap(), 8);
    assert_eq!(repo.resolve_history_window_start(session, 38, 0).await.unwrap(), 39);
}

#[tokio::test]
async fn memory_history_window_skips_only_new_projections() {
    window_contract(&MemoryMessageRepo::new(), "contract-group:abcd1234").await;
}

#[tokio::test]
async fn sqlite_history_window_uses_original_schema() {
    let db = sqlite_db().await;
    for table in ["bcs_messages", "bcs_group_sessions"] {
        let cols = db.query(DbStatement::new(format!("PRAGMA table_info({table})"))).await.unwrap();
        assert!(cols.iter().all(|row| !row.get_string("name").unwrap().unwrap().contains("participant_history_seq")));
    }
    let indexes = db.query(DbStatement::new("PRAGMA index_list(bcs_messages)")).await.unwrap();
    for removed in ["idx_messages_history_window", "idx_messages_session_client", "idx_messages_session_run"] {
        assert!(indexes.iter().all(|row| row.get_string("name").unwrap().unwrap() != removed));
    }
    window_contract(&MySqlMessageStore::sqlite(db, "dev".into()), "contract-group:abcd1234").await;
}

#[tokio::test]
async fn sqlite_window_preserves_deleted_ordinary_positions_and_non_json_legacy_content() {
    let db = sqlite_db().await;
    let session = "contract-group:abcd1234";
    let repo = MySqlMessageStore::sqlite(db.clone(), "dev".into());
    for n in 1..=10 { repo.append_message(message(session, &format!("old-{n}"), false)).await.unwrap(); }
    db.execute(DbStatement::new("DELETE FROM bcs_messages WHERE session_seq=5")).await.unwrap();
    db.execute(DbStatement::new("UPDATE bcs_messages SET content='legacy plain text' WHERE session_seq=6")).await.unwrap();
    repo.append_message_with_id("new".into(), message(session, "new", true)).await.unwrap();
    assert_eq!(repo.resolve_history_window_start(session, 11, 6).await.unwrap(), 5, "a missing old slot must not compact into an older visible message");
    let page = repo.list_session_history(session, MessageOwnerFilter::Any, Some(5), None, None, 100).await.unwrap();
    assert_eq!(page.messages.iter().filter(|m| m.message_type == "chat").count(), 5);
    assert_eq!(MySqlMessageStore::sqlite(db, "other-env".into()).resolve_history_window_start(session, 11, 6).await.unwrap(), 6, "another environment cannot affect compensation");
}

struct CountReads { inner: Arc<dyn DbPlugin>, reads: std::sync::atomic::AtomicUsize }
#[async_trait::async_trait]
impl DbPlugin for CountReads {
    async fn query(&self, stmt: DbStatement) -> bcs_db_api::DbResult<Vec<bcs_db_api::DbRow>> {
        assert!(stmt.sql().contains("LIMIT 512") && !stmt.sql().contains("SELECT *"));
        self.reads.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        self.inner.query(stmt).await
    }
    async fn execute(&self, _: DbStatement) -> bcs_db_api::DbResult<bcs_db_api::DbExecuteResult> { panic!("window must be read only") }
    async fn transaction(&self, _: Vec<bcs_db_api::DbTransactionStep>) -> bcs_db_api::DbResult<Vec<bcs_db_api::DbTransactionStepResult>> { panic!("window must be read only") }
    async fn health_check(&self) -> bcs_db_api::DbResult<bcs_db_api::DbHealth> { self.inner.health_check().await }
}

#[tokio::test]
async fn sqlite_window_bounds_dense_projection_reads_and_fails_without_shifting_visibility() {
    use std::sync::atomic::Ordering;
    let db = sqlite_db().await;
    let session = "contract-group:abcd1234";
    let writer = MySqlMessageStore::sqlite(db.clone(), "dev".into());
    for n in 1..=10 { writer.append_message(message(session, &format!("old-{n}"), false)).await.unwrap(); }
    for start in (11..=16_395).step_by(200) {
        let values = (start..(start+200).min(16_396)).map(|n|format!("('dense-{n}','contract-group','{session}',{n},'dev','bot','bot','state_machine_output','{{\"metadata\":{{\"state_machine\":{{\"history_schema_version\":1}}}}}}','state_machine',1)")).collect::<Vec<_>>().join(",");
        db.execute(DbStatement::new(format!("INSERT INTO bcs_messages (message_id,group_id,session_id,session_seq,env,sender_id,sender_type,message_type,content,visibility_domain,created_at) VALUES {values}"))).await.unwrap();
    }
    let counted = Arc::new(CountReads { inner: db.clone(), reads: 0.into() });
    let repo = MySqlMessageStore::sqlite(counted.clone(), "dev".into());
    for (anchor, expected_reads) in [(10, 1), (1035, 3), (16_394, 33)] {
        counted.reads.store(0, Ordering::Relaxed);
        assert_eq!(repo.resolve_history_window_start(session, anchor, 5).await.unwrap(), 6);
        assert_eq!(counted.reads.load(Ordering::Relaxed), expected_reads);
    }
    counted.reads.store(0, Ordering::Relaxed);
    assert!(repo.resolve_history_window_start(session, 16_395, 5).await.unwrap_err().to_string().contains("16384"));
    assert_eq!(counted.reads.load(Ordering::Relaxed), 33);
    let plan = db.query(DbStatement::new(format!("EXPLAIN QUERY PLAN SELECT session_seq FROM bcs_messages WHERE env='dev' AND session_id='{session}' AND session_seq<=1035 ORDER BY session_seq DESC LIMIT 512"))).await.unwrap();
    assert!(plan.iter().any(|row| row.get_string("detail").unwrap().unwrap().contains("INDEX")));
    db.execute(DbStatement::new("DROP TABLE bcs_messages")).await.unwrap();
    assert!(repo.resolve_history_window_start(session, 10, 5).await.is_err());
}
