use std::sync::Arc;
use bcs_domain::{MessageAudience, MessageVisibilityDomain, NewMessage, SenderType, STATE_MACHINE_OUTPUT_MESSAGE_TYPE};
use bcs_service_api::port::repo::MessageRepoPort;
use bcs_message_store::{MemoryMessageRepo, MySqlMessageStore};
use bcs_db_api::{DbPlugin, DbStatement};
use serde_json::json;

async fn contract(repo: &dyn MessageRepoPort) {
    let session = "contract-group:abcd1234";
    let key = "workflow:long-node:0:1-output";
    let row = NewMessage {
        group_id: "contract-group".into(), session_id: session.into(), sender_id: "human".into(),
        sender_type: SenderType::Human, message_type: STATE_MACHINE_OUTPUT_MESSAGE_TYPE.into(),
        content: json!({"text": "private output"}), client_msg_id: Some(key.into()), owner_bot_id: None,
        visibility_domain: MessageVisibilityDomain::StateMachine, audience: Some(MessageAudience::FullOnly),
        created_at: 1, run_id: "workflow".into(),
    };
    let saved = repo.append_message(row.clone()).await.unwrap();
    let keys = vec![key.into(), saved.message_id.clone()];
    let found = repo.get_state_machine_messages_by_keys(session, &keys).await.unwrap();
    assert_eq!(found.len(), 1);
    assert_eq!(found[0].message_id, saved.message_id);
    assert_eq!(found[0].audience, Some(MessageAudience::FullOnly), "lookup must precede permission filtering");
    assert!(repo.get_state_machine_messages_by_keys("other-session", &keys).await.unwrap().is_empty());
    assert!(repo.get_state_machine_messages_by_keys(session, &[]).await.unwrap().is_empty());
    let mut published = row;
    published.client_msg_id = Some("state-machine-result:workflow".into());
    published.message_type = "chat".into();
    let published = repo.append_message(published).await.unwrap();
    assert!(repo.get_state_machine_messages_by_keys(session,
        &[published.message_id, "state-machine-result:workflow".into()]).await.unwrap().is_empty());
    let mut keys = (0..450).map(|i| format!("missing-{i}")).collect::<Vec<_>>();
    keys.extend([key.into(), saved.message_id.clone()]);
    assert_eq!(repo.get_state_machine_messages_by_keys(session, &keys).await.unwrap().len(), 1);
}

#[tokio::test]
async fn memory_canonical_history_identity_contract() {
    contract(&MemoryMessageRepo::new()).await;
}

#[tokio::test]
async fn sqlite_canonical_history_identity_contract_and_index() {
    let db = super::sqlite_db().await;
    contract(&MySqlMessageStore::sqlite(db.clone(), "dev".into())).await;
    assert!(MySqlMessageStore::sqlite(db.clone(), "other".into())
        .get_state_machine_messages_by_keys("contract-group:abcd1234", &["workflow:long-node:0:1-output".into()])
        .await.unwrap().is_empty());
    assert_index(db).await;
}

async fn assert_index(db: Arc<dyn DbPlugin>) {
    let rows = db.query(DbStatement::new("EXPLAIN QUERY PLAN SELECT message_id FROM bcs_messages \
        WHERE env = 'dev' AND session_id = 'contract-group:abcd1234' AND client_msg_id IN ('key') \
        AND message_type IN ('state_machine_panel', 'state_machine_human_input_prompt', \
        'state_machine_human_input_response', 'state_machine_output') LIMIT 401")).await.unwrap();
    assert!(rows.iter().any(|row| row.get_string("detail").unwrap().unwrap_or_default().contains("USING INDEX")));
}
