//! Deterministic queue faults must not surface as retryable storage errors:
//! the delivery scheduler retries `Storage` without bound.
use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_db_migrations as migrations;
use bcs_domain::message_delivery::{DeliveryFlowKind, MessageDeliveryStatus as Status};
use bcs_domain::{DeliveryType, NewMessage, SenderType};
use bcs_message_store::MySqlMessageStore;
use bcs_service_api::port::repo::message_delivery::*;
use std::sync::Arc;

fn admission(id: &str) -> AdmitMessageDeliveries {
    AdmitMessageDeliveries {
        display_message: None, message_id: id.into(), flow_kind: DeliveryFlowKind::System, now_ms: 1, expire_at_ms: Some(10), event: None,
        message: NewMessage { visibility_domain: bcs_domain::MessageVisibilityDomain::Chat, audience: None, group_id: "g".into(), session_id: "doomed".into(), sender_id: "system".into(), sender_type: SenderType::System, message_type: "system".into(), content: serde_json::json!({"text": id}), client_msg_id: Some(id.into()), owner_bot_id: None, created_at: 1, run_id: String::new() },
        targets: vec![DeliveryAdmissionTarget { rejection: None, target_bot_id: "bot".into(), kind: DeliveryType::Send, max_queued: 10, semantic_projection_json: serde_json::json!({}), run_id: None }],
    }
}

async fn store() -> Result<(Arc<LocalSqliteDbPlugin>, MySqlMessageStore), Box<dyn std::error::Error>> {
    let db = Arc::new(LocalSqliteDbPlugin::new()?);
    migrations::run_sqlite_migrations(db.as_ref()).await?;
    db.execute(DbStatement::new("INSERT INTO bcs_group_sessions (session_id, group_id, env, participants) VALUES ('doomed', 'g', 'dev', '[]')")).await?;
    let repo = MySqlMessageStore::sqlite(db.clone(), "dev".into());
    Ok((db, repo))
}

#[tokio::test]
async fn deleted_session_reply_is_a_deterministic_session_missing_error() -> Result<(), Box<dyn std::error::Error>> {
    let (db, repo) = store().await?;
    let row = repo.admit(admission("first")).await?.deliveries.remove(0);
    db.execute(DbStatement::new("DELETE FROM bcs_group_sessions WHERE session_id = 'doomed'")).await?;

    let error = repo.admit(admission("after-delete")).await.unwrap_err();
    assert!(error.is_session_missing(), "{error:?}");

    // A transition carrying a notice into the deleted Session fails the same
    // way, atomically: the row's own CAS must not have committed.
    let mut next = row.clone();
    next.state.status = Status::Cancelled;
    next.state.state_version += 1;
    let change = || vec![DeliveryCompareAndSet { expected_state_version: row.state.state_version, delivery: next.clone() }];
    let error = repo.commit_transition(change(), Some(admission("notice"))).await.unwrap_err();
    assert!(error.is_session_missing(), "{error:?}");
    assert_eq!(repo.get_delivery(&row.delivery_id).await?.unwrap().state.status, Status::Queued);

    // Without the reply the row still converges.
    assert!(repo.commit_transition(change(), None).await?.is_none());
    assert_eq!(repo.get_delivery(&row.delivery_id).await?.unwrap().state.status, Status::Cancelled);
    Ok(())
}

#[tokio::test]
async fn one_corrupt_row_does_not_poison_scheduler_batches() -> Result<(), Box<dyn std::error::Error>> {
    let (db, repo) = store().await?;
    let good = repo.admit(admission("good")).await?.deliveries.remove(0);
    // Sorts before the good row on (expire_at_ms, delivery_id). The column
    // is valid SQL data but not a known flow kind, so only decoding fails.
    db.execute(DbStatement::new("INSERT INTO bcs_message_deliveries (delivery_id,env,source_message_id,target_bot_id,session_id,group_id,source_session_seq,flow_kind,kind,status,state_version,may_have_been_sent,available_at_ms,created_at_ms,updated_at_ms,attempt_no,semantic_projection_json,expire_at_ms) VALUES ('aaa-corrupt','dev','m','bot','doomed','g',99,'not-a-flow-kind','send','queued',1,0,1,1,1,0,'{}',1)")).await?;

    // A one-row page: the corrupt row is excluded and the page re-read, so
    // it cannot keep hiding the healthy row behind it.
    let expired = repo.work_batch(DeliveryWorkBatch::Expired, 100, "", 1).await;
    let expired = expired.expect("a corrupt row must not fail the whole batch");
    assert_eq!(expired.iter().map(|d| d.delivery_id.as_str()).collect::<Vec<_>>(), vec![good.delivery_id.as_str()]);

    let error = repo.get_delivery("aaa-corrupt").await.unwrap_err();
    assert!(matches!(error, MessageDeliveryRepoError::Invalid(_)), "corrupt row is not an outage: {error:?}");
    Ok(())
}

/// Fails the admission's commit transaction (not its single-step sequence
/// read) with a fixed backend error.
struct FailingTransactions { inner: Arc<LocalSqliteDbPlugin>, error: fn() -> bcs_db_api::DbError }

#[async_trait::async_trait]
impl DbPlugin for FailingTransactions {
    async fn query(&self, statement: DbStatement) -> bcs_db_api::DbResult<Vec<bcs_db_api::DbRow>> { self.inner.query(statement).await }
    async fn execute(&self, statement: DbStatement) -> bcs_db_api::DbResult<bcs_db_api::DbExecuteResult> { self.inner.execute(statement).await }
    async fn transaction(&self, steps: Vec<bcs_db_api::DbTransactionStep>) -> bcs_db_api::DbResult<Vec<bcs_db_api::DbTransactionStepResult>> {
        if steps.len() == 1 { return self.inner.transaction(steps).await; }
        Err((self.error)())
    }
    async fn health_check(&self) -> bcs_db_api::DbResult<bcs_db_api::DbHealth> { self.inner.health_check().await }
}

#[tokio::test]
async fn only_backend_outages_stay_retryable_storage_errors() -> Result<(), Box<dyn std::error::Error>> {
    use bcs_db_api::DbError;
    let (db, _) = store().await?;
    let cases: [(fn() -> DbError, fn(&MessageDeliveryRepoError) -> bool); 4] = [
        (|| DbError::InvalidInput("bad step".into()), |e| matches!(e, MessageDeliveryRepoError::Invalid(_))),
        (|| DbError::Conversion("bad value".into()), |e| matches!(e, MessageDeliveryRepoError::Invalid(_))),
        (|| DbError::Backend("UNIQUE constraint failed: bcs_messages.message_id".into()), |e| matches!(e, MessageDeliveryRepoError::Conflict)),
        (|| DbError::Backend("connection reset".into()), |e| matches!(e, MessageDeliveryRepoError::Storage(_))),
    ];
    for (error, expected) in cases {
        let repo = MySqlMessageStore::sqlite(Arc::new(FailingTransactions { inner: db.clone(), error }), "dev".into());
        let got = repo.admit(admission("classified")).await.unwrap_err();
        assert!(expected(&got), "{:?} classified as {got:?}", error());
    }
    Ok(())
}

#[tokio::test]
async fn corrupt_overdue_rows_cannot_hide_healthy_control_work() -> Result<(), Box<dyn std::error::Error>> {
    let (db, repo) = store().await?;
    // Seven undecodable overdue Dispatching rows sort ahead of a healthy one
    // and would fill the category's share of a 32-row control page.
    for n in 0..7 {
        db.execute(DbStatement::new(format!("INSERT INTO bcs_message_deliveries (delivery_id,env,source_message_id,target_bot_id,session_id,group_id,source_session_seq,flow_kind,kind,status,state_version,may_have_been_sent,available_at_ms,created_at_ms,updated_at_ms,attempt_no,semantic_projection_json,run_deadline_at_ms) VALUES ('bad-{n}','dev','m{n}','bot','doomed','g',{n},'not-a-flow-kind','send','dispatching',1,1,1,1,1,1,'{{}}',{n})"))).await?;
    }
    db.execute(DbStatement::new("INSERT INTO bcs_message_deliveries (delivery_id,env,source_message_id,target_bot_id,session_id,group_id,source_session_seq,flow_kind,kind,status,state_version,may_have_been_sent,available_at_ms,created_at_ms,updated_at_ms,attempt_no,semantic_projection_json,run_deadline_at_ms) VALUES ('healthy','dev','mh','bot','doomed','g',50,'group','send','dispatching',1,1,1,1,1,1,'{}',50)")).await?;
    let page = repo.work_batch(DeliveryWorkBatch::Control, 1000, "", 32).await?;
    assert!(page.iter().any(|d| d.delivery_id == "healthy"), "healthy overdue work must still be scanned");
    assert!(page.iter().all(|d| !d.delivery_id.starts_with("bad-")));
    // Recovery pagination also advances past an all-corrupt prefix.
    let recovery = repo.work_batch(DeliveryWorkBatch::Recovery, 1000, "", 7).await?;
    assert_eq!(recovery.iter().map(|d| d.delivery_id.as_str()).collect::<Vec<_>>(), vec!["healthy"]);
    Ok(())
}

#[tokio::test]
async fn scheduler_isolated_rows_are_excluded_from_ordered_scans() -> Result<(), Box<dyn std::error::Error>> {
    let (db, repo) = store().await?;
    // 32 decodable overdue Running rows the scheduler has isolated, sorting
    // ahead of a healthy overdue one.
    for n in 0..32 {
        db.execute(DbStatement::new(format!("INSERT INTO bcs_message_deliveries (delivery_id,env,source_message_id,target_bot_id,session_id,group_id,source_session_seq,flow_kind,kind,status,state_version,may_have_been_sent,available_at_ms,created_at_ms,updated_at_ms,attempt_no,semantic_projection_json,run_deadline_at_ms) VALUES ('stuck-{n:02}','dev','m{n}','bot','doomed','g',{n},'group','send','running',1,1,1,1,1,1,'{{}}',{n})"))).await?;
    }
    db.execute(DbStatement::new("INSERT INTO bcs_message_deliveries (delivery_id,env,source_message_id,target_bot_id,session_id,group_id,source_session_seq,flow_kind,kind,status,state_version,may_have_been_sent,available_at_ms,created_at_ms,updated_at_ms,attempt_no,semantic_projection_json,run_deadline_at_ms) VALUES ('healthy','dev','mh','bot','doomed','g',99,'group','send','running',1,1,1,1,1,1,'{}',99)")).await?;
    let hidden = repo.work_batch(DeliveryWorkBatch::Control, 1000, "", 32).await?;
    assert!(!hidden.iter().any(|d| d.delivery_id == "healthy"), "without exclusions the stuck rows fill the page");
    let excluded: Vec<String> = (0..32).map(|n| format!("stuck-{n:02}")).collect();
    let page = repo.work_batch_excluding(DeliveryWorkBatch::Control, 1000, "", 32, &excluded).await?;
    assert_eq!(page.iter().map(|d| d.delivery_id.as_str()).collect::<Vec<_>>(), vec!["healthy"]);
    Ok(())
}
