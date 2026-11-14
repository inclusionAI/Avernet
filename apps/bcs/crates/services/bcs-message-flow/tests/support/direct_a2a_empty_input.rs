use super::*;
use std::sync::atomic::{AtomicUsize, Ordering::SeqCst};

#[derive(Default)]
pub(super) struct AdmissionEvents {
    registered: AtomicUsize,
}

#[async_trait::async_trait]
impl ChatRunEventPort for AdmissionEvents {
    async fn register(&self, _: String, _: String, _: tokio::sync::mpsc::Sender<String>, _: Option<String>, _: Option<String>) {
        self.registered.fetch_add(1, SeqCst);
    }
    async fn unregister(&self, _: &str) {}
}

#[async_trait::async_trait]
impl ChatRunCleanupPort for AdmissionEvents {
    async fn unregister(&self, _: &str) {}
}

fn command(message: &str) -> AsyncA2aChatCommand {
    AsyncA2aChatCommand {
        caller: caller(), target_bot_id: "bot-observer".into(), message: message.into(), from_actor_id: None,
        run_channel_from: None, authenticated_staff_id: None, run_id: "input-run".into(),
        session_key: "input-session".into(), timeout_ms: 60_000, client: Some("bcs-cli/test".into()),
        tags: vec!["tag".into()], response_mode: ChatResponseMode::Full,
        caller_wait_mode: None, organization_code: None, provider_bypass_headers: vec![],
    }
}

#[tokio::test]
async fn empty_message_is_rejected_before_admission_for_both_service_entries() {
    for sql in [false, true] {
        for queued in [false, true] {
            for entry in ["async", "chat-async", "chat-blocking"] {
                let h = Harness::new(sql).await;
                h.policy.snapshot.write().await.policy.flow_enabled.direct_a2a = queued;
                let calls_before = h.db.as_ref().map(|db| db.calls.load(SeqCst));
                let cmd = command("");
                let result = if entry == "async" {
                    h.direct.start_async_chat(cmd).await.map(|_| ())
                } else {
                    A2aChatService::chat(h.direct.as_ref(), A2aChatCommand {
                        caller: cmd.caller, target_bot_id: cmd.target_bot_id, message: cmd.message,
                        from_actor_id: cmd.from_actor_id, authenticated_staff_id: cmd.authenticated_staff_id,
                        run_id: Some(cmd.run_id), async_mode: entry == "chat-async", session_key: Some(cmd.session_key),
                        timeout_ms: Some(cmd.timeout_ms), client: cmd.client, tags: cmd.tags,
                        response_mode: cmd.response_mode, caller_wait_mode: cmd.caller_wait_mode,
                        organization_code: cmd.organization_code, provider_bypass_headers: cmd.provider_bypass_headers,
                    }).await.map(|_| ())
                };
                assert!(matches!(result, Err(ServiceError::InvalidOperation { ref message, .. })
                    if message == "message must not be empty"), "sql={sql} queued={queued} entry={entry}: {result:?}");
                // Check before assertion reads: rejection itself must not access SQLite.
                assert_eq!(h.db.as_ref().map(|db| db.calls.load(SeqCst)), calls_before);
                assert_eq!(h.admission_events.registered.load(SeqCst), 0, "not register-then-cleanup");
                assert!(h.contexts.get_context("input-run").await.is_none());
                assert!(h.contexts.find_active_run("input-run").await.unwrap().is_none());
                assert!(h.sessions.session_registration("input-session").await.unwrap().is_none());
                assert!(h.direct.run_store().get("input-run").await.is_none());
                assert!(h.service.snapshot(None).await.unwrap().is_empty());
                assert!(h.support.bot_delivery.frames().await.is_empty());
                assert!(h.support.bot_delivery.aborts().await.is_empty());
                if let Some(db) = &h.db {
                    for table in ["bcs_chat_runs", "bcs_messages", "bcs_message_deliveries"] {
                        let rows = db.query(DbStatement::new(format!("SELECT COUNT(*) AS n FROM {table}"))).await.unwrap();
                        assert_eq!(bcs_db_api::db_get_column::<i64>(&rows[0], "n").unwrap(), 0, "{table}");
                    }
                }
            }
        }
    }
}

#[tokio::test]
async fn nonempty_message_bytes_are_preserved_in_legacy_and_queued_delivery() {
    for sql in [false, true] {
        for queued in [false, true] {
            for text in ["  hello\n世界  ", " \t\n"] {
                let h = Harness::new(sql).await;
                h.policy.snapshot.write().await.policy.flow_enabled.direct_a2a = queued;
                let accepted = h.direct.start_async_chat(command(text)).await.unwrap();
                assert_eq!(accepted.session_id, "input-session");
                if queued {
                    let row = h.row("input-run").await;
                    let message = h.messages.get_message_by_id("input-session", &row.source_message_id).await.unwrap().unwrap();
                    assert_eq!(message.content, json!({"text": text}));
                    assert!(h.support.bot_delivery.frames().await.is_empty());
                } else {
                    let frames = h.support.bot_delivery.frames().await;
                    let bcs_protocol::BcsFrame::Request(frame) = &frames[0] else { panic!("expected chat.send") };
                    let params = frame.params.as_ref().unwrap();
                    assert_eq!(params["message"]["content"][0]["text"], text);
                    assert_eq!(params["tags"], json!(["tag"]));
                    assert_eq!(h.admission_events.registered.load(SeqCst), 1);
                }
            }
        }
    }
}
