use super::*;
use std::sync::Arc;
use bcs_domain::{MessageAudience, MessageOwnerFilter, MessageVisibilityDomain, NewMessage, Participant, ParticipantRole, SenderType};
use bcs_message_store::MySqlMessageStore;
use bcs_service_api::port::repo::{MessageRepoPort, NewSessionParams, SessionRepoPort};
use bcs_session_store::MySqlSessionStore;

fn message(session: &str, id: &str, history: bool) -> NewMessage {
    NewMessage { group_id: "window-group".into(), session_id: session.into(), sender_id: "bot".into(),
        sender_type: SenderType::Bot, message_type: if history { "state_machine_output" } else { "chat" }.into(),
        content: if history { serde_json::json!({"text":id,"metadata":{"state_machine":{"history_schema_version":1}}}) } else { serde_json::json!({"text":id}) }, client_msg_id: Some(id.into()), owner_bot_id: None,
        visibility_domain: if history { MessageVisibilityDomain::StateMachine } else { MessageVisibilityDomain::Chat },
        audience: history.then_some(MessageAudience::FullOnly), created_at: 1, run_id: String::new() }
}

pub(super) async fn verify_history_windows(global: &MigrateGlobalArgs) -> Result<()> {
    let configured = open_configured_mysql_db(global).await?;
    let db: Arc<dyn DbPlugin> = Arc::new(configured.plugin);
    let result = async { verify(db.clone()).await?; verify_history_reads(db).await }.await;
    configured.manager.close().await;
    result
}

async fn verify(db: Arc<dyn DbPlugin>) -> Result<()> {
    let messages = Arc::new(MySqlMessageStore::new(db.clone(), "window-test".into()));
    let sessions = Arc::new(MySqlSessionStore::new(db.clone(), "window-test".into()));
    let session = sessions.create("window-group", NewSessionParams::default()).await?;
    for n in 0..10 { messages.append_message(message(&session.id, &format!("ordinary-{n}"), false)).await?; }
    let barrier = Arc::new(tokio::sync::Barrier::new(12));
    let mut tasks = Vec::new();
    for _ in 0..12 {
        let repo = messages.clone(); let barrier = barrier.clone(); let session = session.id.clone();
        tasks.push(tokio::spawn(async move {
            barrier.wait().await;
            repo.append_message_with_id("same-history".into(), message(&session, "same-history", true)).await
        }));
    }
    for task in tasks { assert_eq!(task.await??.session_seq, 11); }
    assert_eq!(messages.resolve_history_window_start(&session.id, 11, 5).await?, 6);
    let joined = sessions.add_participant(&session.id, Participant::human("human_viewer", ParticipantRole::Observer)).await?;
    assert_eq!(joined.participant_join_seq.unwrap()["human_viewer"], 11);
    let page = messages.list_session_history(&session.id, MessageOwnerFilter::Any, Some(6), None, None, 100).await?;
    assert_eq!(page.messages.iter().filter(|m| m.message_type == "chat").count(), 5);
    // Existing join anchors remain physical positions.
    let append_repo = messages.clone(); let append_session = session.id.clone();
    let history_repo = messages.clone(); let history_session = session.id.clone();
    let join_repo = sessions.clone(); let join_session = session.id.clone();
    let (ordinary, history, joined) = tokio::join!(
        async move { append_repo.append_message(message(&append_session, "racing-chat", false)).await },
        async move { history_repo.append_message_with_id("racing-history".into(), message(&history_session, "racing-history", true)).await },
        async move { join_repo.add_participant(&join_session, Participant::human("human_race", ParticipantRole::Observer)).await },
    );
    ordinary?; history?;
    let join_seq = joined?.participant_join_seq.unwrap()["human_race"].as_i64().unwrap();
    assert!((11..=13).contains(&join_seq), "join must use a physical position: {join_seq}");
    // A failed insert must roll sequence allocation back. Use a temporary CHECK in
    // this disposable schema; triggers require SUPER when binary logging is enabled.
    let before = messages.get_current_seq(&session.id).await?;
    db.execute(DbStatement::new("ALTER TABLE bcs_messages ADD CONSTRAINT reject_history_window CHECK (message_id <> 'reject-window')")).await?;
    let error = messages.append_message_with_id("reject-window".into(), message(&session.id, "reject-window", true)).await.expect_err("injected message insert must fail");
    assert!(error.to_string().contains("reject_history_window"), "{error}");
    assert_eq!(messages.get_current_seq(&session.id).await?, before);
    assert!(messages.get_message_by_id(&session.id, "reject-window").await?.is_none());
    db.execute(DbStatement::new("ALTER TABLE bcs_messages DROP CHECK reject_history_window")).await?;
    let retried = messages.append_message_with_id("reject-window".into(), message(&session.id, "reject-window", true)).await?;
    assert_eq!(retried.session_seq, before + 1);
    let columns = db.query(DbStatement::new("SELECT COUNT(*) AS n FROM information_schema.columns WHERE table_schema=DATABASE() AND column_name IN ('participant_history_seq','current_participant_history_seq')")).await?;
    assert_eq!(db_get_column::<i64>(&columns[0], "n")?, 0);
    let indexes = db.query(DbStatement::new("SELECT COUNT(*) AS n FROM information_schema.statistics WHERE table_schema=DATABASE() AND index_name IN ('idx_messages_history_window','idx_messages_session_client','idx_messages_session_run')")).await?;
    assert_eq!(db_get_column::<i64>(&indexes[0], "n")?, 0);
    let plan = db.query(DbStatement::new("EXPLAIN SELECT session_seq FROM bcs_messages WHERE env='window-test' AND session_id='window-legacy' AND session_seq <= 205 ORDER BY session_seq DESC LIMIT 512")).await?;
    assert!(plan.iter().any(|row| db_get_column::<String>(row, "key").is_ok_and(|key| !key.is_empty())));
    Ok(())
}


async fn verify_history_reads(db: Arc<dyn DbPlugin>) -> Result<()> {
    use bcs_domain::{HumanMessageView, MessageViewScope};
    let repo = MySqlMessageStore::new(db.clone(), "read-test".into());
    let session = MySqlSessionStore::new(db.clone(), "read-test".into())
        .create("window-group", NewSessionParams::default()).await?;
    for (i, kind, audience) in [(0, "state_machine_panel", MessageAudience::Public),
        (1, "state_machine_human_input_prompt", MessageAudience::Directed { actor_ids: vec!["human_1".into()] }),
        (2, "state_machine_human_input_response", MessageAudience::Directed { actor_ids: vec!["human_1".into()] }),
        (3, "state_machine_output", MessageAudience::FullOnly)] {
        let mut row = message(&session.id, &format!("read-{i}"), true);
        row.message_type = kind.into(); row.audience = Some(audience);
        repo.append_message_with_id(format!("read-{i}"), row).await?;
    }
    let view = Some(HumanMessageView { actor_id: "human_1".into(), scope: MessageViewScope::Participant, allow_legacy_unclassified_chat: false });
    let first = repo.list_state_machine_history("window-group", &session.id, view.clone(), None, 2).await?;
    assert_eq!(first.messages.iter().map(|m| m.message_id.as_str()).collect::<Vec<_>>(), ["read-2", "read-1"]);
    assert!(first.has_more);
    let last = repo.list_state_machine_history("window-group", &session.id, view, first.next_cursor, 2).await?;
    assert_eq!(last.messages.len(), 1); assert_eq!(last.messages[0].message_id, "read-0"); assert!(!last.has_more);
    let full = repo.list_state_machine_history("window-group", &session.id, None, None, 100).await?;
    assert_eq!(full.messages.len(), 3); assert!(full.messages.iter().all(|m| m.message_type != "state_machine_human_input_prompt"));
    assert!(MySqlMessageStore::new(db.clone(), "other".into()).list_state_machine_history("window-group", &session.id, None, None, 100).await?.messages.is_empty());
    // The preceding migration test populated this mixed Session with 5,000 rows.
    let plan = db.query(DbStatement::new("EXPLAIN SELECT message_id, content FROM bcs_messages WHERE env='test' AND group_id='history-plan-group' AND session_id='history-plan-session' AND message_type IN ('state_machine_panel', 'state_machine_output', 'state_machine_human_input_prompt', 'state_machine_human_input_response') AND message_type <> 'state_machine_human_input_prompt' ORDER BY created_at DESC, session_seq DESC LIMIT 101")).await?;
    assert!(plan.iter().any(|row| bcs_db_api::db_get_column_opt::<String>(row, "key").ok().flatten().is_some()), "history query must use a scoped index");
    for row in plan { println!("messages history plan: key={:?} rows={:?} extra={:?}", bcs_db_api::db_get_column_opt::<String>(&row, "key")?, db_get_column::<i64>(&row, "rows")?, bcs_db_api::db_get_column_opt::<String>(&row, "Extra")?); }
    Ok(())
}
