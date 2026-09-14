use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_message_store::{MemoryMessageRepo, MySqlMessageStore};
use bcs_test_support::contract::repo::message_delivery::message_delivery_repo_port_contract_tests;
use std::sync::Arc;

struct RecordingQueries { inner: Arc<dyn DbPlugin>, queries: std::sync::Mutex<Vec<(DbStatement, usize)>> }

#[tokio::test]
async fn sqlite_control_batches_are_indexed_fair_and_bounded() -> Result<(), Box<dyn std::error::Error>> {
    use bcs_service_api::port::repo::message_delivery::*;
    let db = Arc::new(LocalSqliteDbPlugin::new()?);
    migrations::run_sqlite_migrations(db.as_ref()).await?;
    db.execute(DbStatement::new("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<110000) INSERT INTO bcs_message_deliveries (delivery_id,env,source_message_id,target_bot_id,session_id,group_id,source_session_seq,flow_kind,kind,status,state_version,may_have_been_sent,available_at_ms,created_at_ms,updated_at_ms,attempt_no,semantic_projection_json) SELECT printf('history%06d',x),'dev',printf('history-m%d',x),'bot','session','g',x,'group','send','completed',1,0,1,1,1,0,'{}' FROM n")).await?;
    let recording = Arc::new(RecordingQueries { inner: db.clone(), queries: Default::default() });
    let repo = MySqlMessageStore::sqlite(recording.clone(), "dev".into());
    assert!(repo.work_batch(DeliveryWorkBatch::Control, 1000, "ignored", 32).await?.is_empty());
    let queries = recording.queries.lock().unwrap().clone();
    assert_eq!(queries.len(), 4);
    for ((query, count), index) in queries.iter().zip(["idx_delivery_run_deadline", "idx_delivery_run_deadline", "idx_delivery_pending_abort", "idx_delivery_cancel_deadline"]) {
        assert_eq!(*count, 0);
        let plan = db.query(DbStatement::with_params(format!("EXPLAIN QUERY PLAN {}", query.sql()), query.params().to_vec())).await?;
        let detail = plan.iter().map(|r| bcs_db_api::db_get_column::<String>(r, "detail").unwrap()).collect::<Vec<_>>().join("; ");
        eprintln!("control plan: {detail}");
        assert!(detail.contains(index), "{detail}");
        assert!(!detail.contains("USE TEMP B-TREE"), "{detail}");
    }
    db.execute(DbStatement::new("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<160) INSERT INTO bcs_message_deliveries (delivery_id,env,source_message_id,target_bot_id,session_id,group_id,source_session_seq,flow_kind,kind,status,state_version,may_have_been_sent,available_at_ms,created_at_ms,updated_at_ms,attempt_no,semantic_projection_json,run_deadline_at_ms,cancel_deadline_at_ms,abort_request_id) SELECT printf('due%03d',x),'dev',printf('due-m%d',x),'bot','session','g',x,'group','send',CASE WHEN x<=40 THEN 'dispatching' WHEN x<=80 THEN 'running' ELSE 'cancelling' END,1,1,1,1,1,1,'{}',1000-x,1000-x,CASE WHEN x>120 THEN printf('abort%d',x) ELSE NULL END FROM n")).await?;
    let page = repo.work_batch(DeliveryWorkBatch::Control, 1000, "zzzz", 32).await?;
    assert_eq!(page.len(), 32);
    assert_eq!(page.iter().map(|d| &d.delivery_id).collect::<std::collections::BTreeSet<_>>().len(), 32);
    // Four equal shares, oldest deadline first rather than delivery-ID order.
    assert_eq!(page[0].delivery_id, "due040");
    assert_eq!(page[8].delivery_id, "due080");
    assert_eq!(page[16].delivery_id, "due081");
    assert_eq!(page[24].delivery_id, "due160");
    db.execute(DbStatement::new("UPDATE bcs_message_deliveries SET status='completed' WHERE delivery_id LIKE 'due%' AND status != 'running'")).await?;
    let page = repo.work_batch(DeliveryWorkBatch::Control, 1000, "", 32).await?;
    assert_eq!(page.len(), 32, "unused class shares must be lent");
    assert!(page.iter().all(|d| d.state.status == bcs_domain::message_delivery::MessageDeliveryStatus::Running));
    assert_eq!(repo.work_batch(DeliveryWorkBatch::Control, 1000, "", 1).await?.len(), 1);
    assert!(repo.work_batch(DeliveryWorkBatch::Control, 1000, "", 0).await?.is_empty());
    Ok(())
}

#[tokio::test]
async fn sqlite_bound_context_page_does_not_read_unbounded_bodies() -> Result<(), Box<dyn std::error::Error>> {
    use bcs_service_api::port::repo::message_delivery::*;
    let db = Arc::new(LocalSqliteDbPlugin::new()?);
    migrations::run_sqlite_migrations(db.as_ref()).await?;
    db.execute(DbStatement::new("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<10000) INSERT INTO bcs_message_deliveries (delivery_id,env,source_message_id,target_bot_id,session_id,group_id,source_session_seq,flow_kind,kind,status,state_version,may_have_been_sent,available_at_ms,created_at_ms,updated_at_ms,attempt_no,semantic_projection_json,bound_to_delivery_id) SELECT printf('context%05d',x),'dev',printf('message%05d',x),'bot','session','g',x,'group','inject','bound',2,0,1,1,1,0,'{}','carrier' FROM n")).await?;
    let recording = Arc::new(RecordingQueries { inner: db.clone(), queries: Default::default() });
    let repo = MySqlMessageStore::sqlite(recording.clone(), "dev".into());
    let page = repo.bounded_contexts("carrier", 25).await?;
    assert_eq!(page.total, 10000); assert_eq!(page.rows.len(), 25);
    assert_eq!(page.rows[0].source_session_seq, 10000);
    assert_eq!(page.rows[24].source_session_seq, 9976);
    let queries = recording.queries.lock().unwrap().clone();
    assert_eq!(queries.len(), 2);
    assert!(queries.iter().all(|(q,n)| *n <= 25 && !q.sql().contains("FROM bcs_messages ")));
    let query = &queries[1].0;
    let plan = db.query(DbStatement::with_params(format!("EXPLAIN QUERY PLAN {}", query.sql()), query.params().to_vec())).await?;
    let plan = plan.iter().map(|r| bcs_db_api::db_get_column::<String>(r, "detail").unwrap()).collect::<Vec<_>>().join("; ");
    assert!(plan.contains("idx_delivery_bound_seq"), "{plan}");
    assert!(!plan.contains("USE TEMP B-TREE"), "{plan}");
    Ok(())
}
#[async_trait::async_trait]
impl DbPlugin for RecordingQueries {
    async fn query(&self, statement: DbStatement) -> bcs_db_api::DbResult<Vec<bcs_db_api::DbRow>> {
        let result = self.inner.query(statement.clone()).await?;
        self.queries.lock().unwrap().push((statement, result.len())); Ok(result)
    }
    async fn execute(&self, statement: DbStatement) -> bcs_db_api::DbResult<bcs_db_api::DbExecuteResult> { self.inner.execute(statement).await }
    async fn transaction(&self, steps: Vec<bcs_db_api::DbTransactionStep>) -> bcs_db_api::DbResult<Vec<bcs_db_api::DbTransactionStepResult>> { self.inner.transaction(steps).await }
    async fn health_check(&self) -> bcs_db_api::DbResult<bcs_db_api::DbHealth> { self.inner.health_check().await }
}

#[path = "../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod migrations;

#[tokio::test]
async fn conformance_memory_delivery_repo() -> Result<(), Box<dyn std::error::Error>> {
    message_delivery_repo_port_contract_tests(
        &MemoryMessageRepo::new().with_environment("dev".into()),
    )
    .await
}

#[tokio::test]
async fn conformance_sqlite_delivery_repo() -> Result<(), Box<dyn std::error::Error>> {
    let db = Arc::new(LocalSqliteDbPlugin::new()?);
    migrations::run_sqlite_migrations(db.as_ref()).await?;
    db.execute(DbStatement::new("INSERT INTO bcs_group_sessions (session_id, group_id, env, participants) VALUES ('contract-group:abcd1234', 'contract-group', 'dev', '[]')")).await?;
    message_delivery_repo_port_contract_tests(&MySqlMessageStore::sqlite(db, "dev".into())).await
}

#[tokio::test]
async fn pooled_file_sqlite_preserves_contract_and_concurrent_capacity() -> Result<(), Box<dyn std::error::Error>> {
    use bcs_domain::{DeliveryType, NewMessage, SenderType};
    use bcs_domain::message_delivery::{DeliveryFlowKind, MessageDeliveryStatus as Status};
    use bcs_service_api::port::repo::message_delivery::{AdmitMessageDeliveries, DeliveryAdmissionTarget, MessageDeliveryRepoPort};
    let dir = tempfile::tempdir()?;
    let db = Arc::new(LocalSqliteDbPlugin::new_file(dir.path().join("deliveries.db"))?);
    migrations::run_sqlite_migrations(db.as_ref()).await?;
    db.execute(DbStatement::new("INSERT INTO bcs_group_sessions (session_id, group_id, env, participants) VALUES ('contract-group:abcd1234', 'contract-group', 'dev', '[]')")).await?;
    let repo = MySqlMessageStore::sqlite(db.clone(), "dev".into());
    message_delivery_repo_port_contract_tests(&repo).await?;
    let make = |i: usize, session: String, kind: DeliveryType| AdmitMessageDeliveries {
        display_message: None, message_id: format!("concurrent-{i}"),
        message: NewMessage { visibility_domain: bcs_domain::MessageVisibilityDomain::Chat, audience: None,
            group_id: "contract-group".into(), session_id: session,
            sender_id: "human".into(), sender_type: SenderType::Human,
            message_type: "chat".into(), content: serde_json::json!({"text":"concurrent"}),
            client_msg_id: Some(format!("concurrent-{i}")), owner_bot_id: None,
            created_at: 100, run_id: String::new(),
        },
        flow_kind: DeliveryFlowKind::Group, now_ms: 100, expire_at_ms: None, event: None,
        targets: vec![DeliveryAdmissionTarget { rejection: None,
            target_bot_id: "capacity-bot".into(), kind, max_queued: 3,
            semantic_projection_json: serde_json::json!({"version":1}),
        }],
    };
    let mut tasks = tokio::task::JoinSet::new();
    for i in 0..20 {
        let session = format!("concurrent-session-{i}");
        db.execute(DbStatement::with_params("INSERT INTO bcs_group_sessions (session_id, group_id, env, participants) VALUES (?, 'contract-group', 'dev', '[]')", vec![session.clone().into()])).await?;
        let command = make(i, session, DeliveryType::Send);
        let repo = repo.clone();
        tasks.spawn(async move { repo.admit(command).await });
    }
    let mut queued = 0;
    let mut rejected = 0;
    while let Some(result) = tasks.join_next().await {
        match result??.deliveries[0].state.status {
            Status::Queued => queued += 1,
            Status::RejectedCapacity => rejected += 1,
            other => panic!("unexpected admission {other:?}"),
        }
    }
    assert_eq!((queued, rejected), (3, 17));
    // Same-session writes and duplicate requests remain serialized even when
    // no Send capacity key participates (Inject-only admission).
    for i in 20..40 {
        for _ in 0..2 {
            let command = make(i, "concurrent-session-0".into(), DeliveryType::Inject);
            let repo = repo.clone();
            tasks.spawn(async move { repo.admit(command).await });
        }
    }
    let mut seqs = std::collections::BTreeSet::new();
    let mut duplicates = 0;
    while let Some(result) = tasks.join_next().await {
        let admitted = result??;
        if admitted.duplicate { duplicates += 1; } else { assert!(seqs.insert(admitted.message.session_seq)); }
    }
    assert_eq!(duplicates, 20);
    assert_eq!(seqs.len(), 20);
    Ok(())
}

#[tokio::test]
async fn sqlite_large_backlog_is_bounded_and_alias_reads_are_indexed() -> Result<(), Box<dyn std::error::Error>> {
    use bcs_service_api::port::repo::message_delivery::*;
    let db = Arc::new(LocalSqliteDbPlugin::new()?);
    migrations::run_sqlite_migrations(db.as_ref()).await?;
    // Synthetic persistent backlog: query tests never touch the running stack.
    db.execute(DbStatement::new("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<10000) INSERT INTO bcs_message_deliveries (delivery_id,env,source_message_id,target_bot_id,session_id,group_id,source_session_seq,flow_kind,kind,status,state_version,may_have_been_sent,available_at_ms,created_at_ms,updated_at_ms,attempt_no,semantic_projection_json) SELECT printf('d%05d',x),'dev',printf('m%05d',x),printf('bot%03d',x%80),printf('s%05d',x),'g',x,'group',CASE WHEN x<=9000 THEN 'inject' ELSE 'send' END,CASE WHEN x<=9000 THEN 'pending_context' ELSE 'queued' END,1,0,1,1,1,0,'{}' FROM n")).await?;
    let recording = Arc::new(RecordingQueries { inner: db.clone(), queries: Default::default() });
    let repo = MySqlMessageStore::sqlite(recording.clone(), "dev".into());
    let page = repo.queued_bots("", 32).await?;
    assert_eq!(page.len(), 32);
    let next = repo.queued_bots(page.last().unwrap(), 32).await?;
    assert_eq!(next.len(), 32); assert!(page.iter().all(|b| !next.contains(b)));
    let heads = repo.queued_heads(&page[0], "", 8).await?;
    assert_eq!(heads.len(), 8);
    assert_eq!(repo.active_count(&page[0]).await?, 0);
    let statistics = repo.queue_statistics().await?;
    assert_eq!(statistics.iter().filter(|s| s.status == "queued").map(|s| s.count).sum::<u64>(), 1000);
    assert_eq!(statistics.iter().filter(|s| s.status == "pending_context").map(|s| s.count).sum::<u64>(), 9000);
    db.execute(DbStatement::new("UPDATE bcs_message_deliveries SET kind='send', status='unknown', may_have_been_sent=1 WHERE delivery_id='d00001'")).await?;
    assert_eq!(repo.active_count("bot001").await?, 1);
    db.execute(DbStatement::new("UPDATE bcs_message_deliveries SET session_id='s00001' WHERE delivery_id='d09041'")).await?;
    let blocked = repo.queued_heads("bot001", "", 64).await?;
    assert!(blocked.iter().all(|d| d.delivery_id != "d09041"));
    let statements = recording.queries.lock().unwrap().clone();
    for (statement, count) in &statements {
        assert!(!statement.sql().contains("SELECT *"), "scheduler loaded payload: {}", statement.sql());
        assert!(*count <= 64, "unbounded scheduler response: {count}");
    }
    let (head_query, _) = statements.iter().find(|(s,_)| s.sql().contains("SELECT q.delivery_id")).unwrap();
    let plan = db.query(DbStatement::with_params(format!("EXPLAIN QUERY PLAN {}", head_query.sql()), head_query.params().to_vec())).await?;
    let detail = plan.iter().map(|r| bcs_db_api::db_get_column::<String>(r, "detail").unwrap()).collect::<Vec<_>>().join("; ");
    eprintln!("candidate plan: {detail}");
    assert!(detail.contains("idx_delivery_heads"));
    for (query, expected) in [
        ("SELECT DISTINCT target_bot_id FROM bcs_message_deliveries WHERE env='dev' AND kind='send' AND status='queued' AND target_bot_id>'' ORDER BY target_bot_id LIMIT 32", "idx_delivery_queued_bots"),
        ("SELECT * FROM bcs_message_deliveries WHERE env='dev' AND target_bot_id='bot001' AND downstream_run_id='alias'", "idx_delivery_run_alias"),
    ] {
        let plan = db.query(DbStatement::new(format!("EXPLAIN QUERY PLAN {query}"))).await?;
        let detail = plan.iter().map(|r| bcs_db_api::db_get_column::<String>(r, "detail").unwrap()).collect::<Vec<_>>().join("; ");
        eprintln!("{detail}"); assert!(detail.contains(expected), "{detail}"); assert!(!detail.contains("USE TEMP B-TREE"), "{detail}");
    }
    Ok(())
}
