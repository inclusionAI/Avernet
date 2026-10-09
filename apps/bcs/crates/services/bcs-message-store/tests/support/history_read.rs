use super::*;
use bcs_domain::{HumanMessageView, MessageAudience, MessageViewScope, MessageVisibilityDomain, NewMessage, SenderType};
use bcs_service_api::port::repo::MessageRepoPort;
use serde_json::json;

async fn contract(repo: &dyn MessageRepoPort) {
    let group = "contract-group";
    let session = "contract-group:abcd1234";
    let participant = HumanMessageView {
        actor_id: "human_1".into(), scope: MessageViewScope::Participant,
        allow_legacy_unclassified_chat: true,
    };
    let mut visible = Vec::new();
    for (i, kind, audience) in [
        (0, "state_machine_panel", MessageAudience::Public),
        (1, "state_machine_human_input_prompt", MessageAudience::Directed { actor_ids: vec!["human_1".into()] }),
        (2, "state_machine_human_input_response", MessageAudience::Directed { actor_ids: vec!["human_1".into()] }),
        (3, "state_machine_output", MessageAudience::FullOnly),
        (4, "state_machine_output", MessageAudience::Directed { actor_ids: vec!["human_2".into()] }),
    ] {
        let row = repo.append_message(NewMessage {
            group_id: group.into(), session_id: session.into(), sender_id: "sender".into(),
            sender_type: SenderType::Bot, message_type: kind.into(),
            content: json!({"text": "frozen body", "metadata": {"state_machine": {"run_id": "old-run", "node_id": format!("n{i}"), "attempt": 0}}}),
            client_msg_id: Some(format!("legacy-{i}")), owner_bot_id: None,
            created_at: 10, run_id: "old-run".into(),
            visibility_domain: MessageVisibilityDomain::StateMachine, audience: Some(audience),
        }).await.unwrap();
        if i < 3 { visible.push(row.message_id); }
    }
    // Newer ordinary rows and hidden prompts cannot consume the visible page.
    for i in 0..105 {
        repo.append_message(NewMessage {
            group_id: group.into(), session_id: session.into(), sender_id: "sender".into(),
            sender_type: SenderType::Bot,
            message_type: if i % 2 == 0 { "chat" } else { "state_machine_human_input_prompt" }.into(),
            content: json!("not in this view"), client_msg_id: None, owner_bot_id: None,
            created_at: 20, run_id: "other".into(),
            visibility_domain: MessageVisibilityDomain::StateMachine,
            audience: Some(MessageAudience::Directed { actor_ids: vec!["human_2".into()] }),
        }).await.unwrap();
    }
    let page = repo.list_state_machine_history(group, session, Some(participant.clone()), None, 2).await.unwrap();
    assert_eq!(page.messages.iter().map(|m| m.message_id.clone()).collect::<Vec<_>>(), vec![visible[2].clone(), visible[1].clone()]);
    assert!(page.has_more);
    let last = repo.list_state_machine_history(group, session, Some(participant.clone()), page.next_cursor, 2).await.unwrap();
    assert_eq!(last.messages.len(), 1);
    assert_eq!(last.messages[0].message_id, visible[0]);
    assert!(!last.has_more);
    assert!(repo.list_state_machine_history(group, session, Some(participant), Some((10, 0)), 2).await.unwrap().messages.is_empty(), "public millisecond before remains exclusive");
    let full = repo.list_state_machine_history(group, session, None, None, 1_000).await.unwrap();
    assert_eq!(full.messages.len(), 4);
    assert!(full.messages.iter().all(|m| m.message_type != "state_machine_human_input_prompt"));
    assert!(repo.list_state_machine_history("wrong-group", session, None, None, 10).await.unwrap().messages.is_empty());
    assert!(repo.list_state_machine_history(group, "wrong-session", None, None, 10).await.unwrap().messages.is_empty());
    for limit in [0, 1_001] { assert!(repo.list_state_machine_history(group, session, None, None, limit).await.is_err()); }
    // Exercise the actual maximum page, including lookahead and continuation.
    for i in 0..1_001 {
        repo.append_message(NewMessage {
            group_id: group.into(), session_id: session.into(), sender_id: "sender".into(),
            sender_type: SenderType::Bot, message_type: "state_machine_output".into(),
            content: json!({"text": "maximum-page output"}), client_msg_id: None, owner_bot_id: None,
            created_at: 30, run_id: format!("scale-{i}"), visibility_domain: MessageVisibilityDomain::StateMachine,
            audience: Some(MessageAudience::FullOnly),
        }).await.unwrap();
    }
    let maximum = repo.list_state_machine_history(group, session, None, None, 1_000).await.unwrap();
    assert_eq!(maximum.messages.len(), 1_000);
    assert!(maximum.has_more);
    let rest = repo.list_state_machine_history(group, session, None, maximum.next_cursor, 1_000).await.unwrap();
    assert_eq!(rest.messages.len(), 5);
    assert!(!rest.has_more);
}

#[tokio::test]
async fn memory_messages_only_history_contract() {
    contract(&MemoryMessageRepo::new()).await;
}

#[tokio::test]
async fn sqlite_messages_only_history_contract() {
    let db = sqlite_db().await;
    contract(&MySqlMessageStore::sqlite(db.clone(), "dev".into())).await;
    assert!(MySqlMessageStore::sqlite(db, "other-env".into())
        .list_state_machine_history("contract-group", "contract-group:abcd1234", None, None, 10)
        .await.unwrap().messages.is_empty());
}
