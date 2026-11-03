//! Runs after the actual migration chain on SQLite and the disposable MySQL CI
//! database. Does not treat static DDL checks as real MySQL validation.
use std::sync::Arc;
use anyhow::Result;
use bcs_db_api::{DbPlugin, DbStatement};
use bcs_message_store::MySqlMessageStore;
use bcs_service_api::port::repo::message_delivery::{DeliveryLookup, MessageDeliveryRepoPort};

pub async fn verify(db: Arc<dyn DbPlugin>, sqlite: bool) -> Result<()> {
    let repo = if sqlite { MySqlMessageStore::sqlite(db.clone(), "task-handle-chain".into()) }
        else { MySqlMessageStore::new(db.clone(), "task-handle-chain".into()) };
    let ids = (1..=4).map(|n| format!("aabbccdd{n:024x}")).collect::<Vec<_>>();
    for (i, id) in ids.iter().enumerate() {
        let projection = serde_json::json!({"task_uncertain_id":id});
        insert(db.as_ref(), &format!("marker-{i}"), "system", projection).await?;
    }
    insert(db.as_ref(), "legacy-result", "task", serde_json::json!({"task":{
        "leg":"result", "task_id":"12345678-0000-0000-0000-000000000000",
        "assignment_intent_id":format!("bcs_intent_{}",ids[3])}})).await?;
    let rows = repo.lookup(DeliveryLookup::TaskPrefix { prefix:"aabbccdd".into(), retained_after_ms:100,
        legacy_intents:ids[..2].to_vec() }).await?;
    assert_eq!(rows.len(), 1, "suppression must precede candidate limiting");
    assert_eq!(rows[0].semantic_projection_json["task_uncertain_id"], ids[2]);
    for suffix in ["one", "duplicate"] {
        insert(db.as_ref(), suffix, "task", serde_json::json!({"task":{
            "leg":"dispatch", "task_id":"87654321-0000-0000-0000-000000000000",
            "assignment_intent_id":"bcs_intent_fedcba98000000000000000000000000"}})).await?;
    }
    let prefixes = std::iter::once("fedcba98".to_string()).chain((1..20).map(|n| format!("{n:08x}"))).collect();
    let rows = repo.lookup(DeliveryLookup::TaskPrefixes { prefixes, retained_after_ms:100,
        legacy_intents:(1..=256).map(|n| format!("{n:032x}")).collect() }).await?;
    assert_eq!(rows.len(), 1, "max batch deduplicates full task identity");
    db.execute(DbStatement::with_params("DELETE FROM bcs_message_deliveries WHERE env = ?",
        vec!["task-handle-chain".into()])).await?;
    Ok(())
}

async fn insert(db: &dyn DbPlugin, suffix: &str, flow: &str, projection: serde_json::Value) -> Result<()> {
    let id = format!("task-handle-chain-{suffix}");
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_message_deliveries (delivery_id,env,source_message_id,target_bot_id,session_id,group_id,source_session_seq,flow_kind,kind,status,state_version,may_have_been_sent,available_at_ms,created_at_ms,updated_at_ms,attempt_no,semantic_projection_json) VALUES (?,'task-handle-chain',?,'manager','s','g',1,?,'send','queued',1,0,1,1,1,0,?)",
        vec![id.as_str().into(),id.as_str().into(),flow.into(),projection.to_string().into()])).await?;
    Ok(())
}
