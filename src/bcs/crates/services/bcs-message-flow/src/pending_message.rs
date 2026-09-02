use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::{
    BotRunContextPort, PendingGroupMessage, PendingGroupMessageKind, PendingGroupMessagePort,
};

use crate::message_tracker::MessageTracker;

pub(crate) struct PendingMessageReader {
    tracker: Arc<MessageTracker>,
    run_context: Arc<dyn BotRunContextPort>,
}

impl PendingMessageReader {
    pub(crate) fn new(
        tracker: Arc<MessageTracker>,
        run_context: Arc<dyn BotRunContextPort>,
    ) -> Self {
        Self {
            tracker,
            run_context,
        }
    }

    async fn matching_context(
        &self,
        run_id: &str,
        group_id: &str,
        session_id: Option<&str>,
    ) -> Option<bcs_service_api::BotRunContext> {
        let context = self.run_context.get_context(run_id).await?;
        if context.terminal || context.group_id != group_id {
            return None;
        }
        if let Some(expected_session_id) = session_id
            && context.bcs_session_id.as_deref() != Some(expected_session_id)
        {
            return None;
        }
        Some(context)
    }
}

#[async_trait]
impl PendingGroupMessagePort for PendingMessageReader {
    async fn list_pending(
        &self,
        group_id: &str,
        session_id: Option<&str>,
    ) -> Vec<PendingGroupMessage> {
        let snapshot_at_ms = bcs_protocol::now_ms();
        let chat_bufs = self.tracker.snapshot_chat_bufs().await;
        let tool_starts = self.tracker.snapshot_tool_call_starts().await;
        let mut pending = Vec::with_capacity(chat_bufs.len() + tool_starts.len());

        for (run_id, text) in chat_bufs {
            if text.is_empty() {
                continue;
            }
            let Some(context) = self
                .matching_context(&run_id, group_id, session_id)
                .await
            else {
                continue;
            };
            pending.push(PendingGroupMessage {
                run_id,
                bot_id: context.bot_id,
                session_id: context.bcs_session_id,
                created_at_ms: snapshot_at_ms,
                kind: PendingGroupMessageKind::Chat { text },
            });
        }

        for (tool_call_id, info) in tool_starts {
            let Some(context) = self
                .matching_context(&info.run_id, group_id, session_id)
                .await
            else {
                continue;
            };
            if !info.session_id.is_empty()
                && context.bcs_session_id.as_deref() != Some(info.session_id.as_str())
            {
                continue;
            }
            pending.push(PendingGroupMessage {
                run_id: info.run_id,
                bot_id: context.bot_id,
                session_id: context.bcs_session_id,
                created_at_ms: info.created_at_ms,
                kind: PendingGroupMessageKind::ToolCall {
                    tool_call_id,
                    tool_name: info.name,
                    tool_args: info.args,
                },
            });
        }

        pending
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_service_api::{BotRunContext, PendingGroupMessageKind};

    use crate::run_context::MemoryBotRunContextStore;

    #[tokio::test]
    async fn reader_filters_scope_missing_and_terminal_contexts() {
        let tracker = Arc::new(MessageTracker::new());
        let contexts = Arc::new(MemoryBotRunContextStore::new());
        for (run_id, group_id, session_id, terminal) in [
            ("match", "group-1", "session-1", false),
            ("other-group", "group-2", "session-1", false),
            ("other-session", "group-1", "session-2", false),
            ("terminal", "group-1", "session-1", true),
        ] {
            contexts
                .put_context(BotRunContext {
                    run_id: run_id.to_string(),
                    bot_id: "bot-1".to_string(),
                    group_id: group_id.to_string(),
                    bcs_session_id: Some(session_id.to_string()),
                    deadline_ms: u64::MAX,
                    terminal,
                })
                .await;
            tracker
                .buffer_chat_text(run_id, format!("text-{run_id}"))
                .await;
        }
        tracker
            .buffer_chat_text("missing", "no context".to_string())
            .await;
        tracker
            .cache_tool_call_start(
                "tool-1".to_string(),
                crate::message_tracker::ToolCallStartInfo {
                    run_id: "match".to_string(),
                    session_id: "session-1".to_string(),
                    name: "search".to_string(),
                    args: serde_json::json!({"q": "pending"}),
                    created_at_ms: bcs_protocol::now_ms(),
                },
            )
            .await;

        let reader = PendingMessageReader::new(tracker, contexts);
        let pending = reader
            .list_pending("group-1", Some("session-1"))
            .await;

        assert_eq!(pending.len(), 2);
        assert!(pending.iter().all(|message| message.run_id == "match"));
        assert!(pending.iter().any(|message| matches!(
            message.kind,
            PendingGroupMessageKind::Chat { .. }
        )));
        assert!(pending.iter().any(|message| matches!(
            message.kind,
            PendingGroupMessageKind::ToolCall { .. }
        )));
    }
}
