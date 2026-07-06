//! In-memory message repository for local dev and testing.

use std::collections::HashMap;

use async_trait::async_trait;
use tokio::sync::RwLock;
use tracing::info;

use bcs_domain::{MessageOwnerFilter, MessagePage, MessageQuery, NewMessage, PersistedMessage, PersistedMessageStatus};
use bcs_service_api::port::repo::{MessageRepoError, MessageRepoPort};

/// In-memory implementation of [`MessageRepoPort`].
#[derive(Debug, Default)]
pub struct MemoryMessageRepo {
    /// messages keyed by session_id, each session is a Vec ordered by session_seq.
    sessions: RwLock<HashMap<String, SessionMessages>>,
}

#[derive(Debug, Default)]
struct SessionMessages {
    seq: i64,
    messages: Vec<PersistedMessage>,
}

impl MemoryMessageRepo {
    pub fn new() -> Self {
        Self::default()
    }
}

#[async_trait]
impl MessageRepoPort for MemoryMessageRepo {
    async fn append_message(
        &self,
        msg: NewMessage,
    ) -> Result<PersistedMessage, MessageRepoError> {
        let mut sessions = self.sessions.write().await;
        let entry = sessions.entry(msg.session_id.clone()).or_default();

        // Check idempotency
        if let Some(ref client_msg_id) = msg.client_msg_id {
            if let Some(existing) = entry.messages.iter().find(|m| {
                m.sender_id == msg.sender_id && m.client_msg_id.as_deref() == Some(client_msg_id)
            }) {
                return Ok(existing.clone());
            }
        }

        entry.seq += 1;
        let persisted = PersistedMessage {
            message_id: uuid::Uuid::new_v4().to_string(),
            group_id: msg.group_id,
            session_id: msg.session_id,
            session_seq: entry.seq,
            sender_id: msg.sender_id,
            sender_type: msg.sender_type,
            message_type: msg.message_type,
            content: msg.content,
            client_msg_id: msg.client_msg_id,
            owner_bot_id: msg.owner_bot_id,
            status: PersistedMessageStatus::Normal,
            created_at: msg.created_at,
            run_id: msg.run_id,
        };
        entry.messages.push(persisted.clone());
        info!(
            session_id = %persisted.session_id,
            session_seq = persisted.session_seq,
            "message persisted (memory)"
        );
        Ok(persisted)
    }

    async fn query_messages(
        &self,
        query: MessageQuery,
    ) -> Result<MessagePage, MessageRepoError> {
        let sessions = self.sessions.read().await;
        let entry = match sessions.get(&query.session_id) {
            Some(e) => e,
            None => {
                return Ok(MessagePage {
                    messages: Vec::new(),
                    next_cursor: None,
                    has_more: false,
                });
            }
        };

        let limit = query.limit as usize;
        let mut filtered: Vec<&PersistedMessage> = entry.messages.iter().collect();

        // Apply cursor (timestamp-based)
        if let Some(cursor) = query.cursor {
            filtered.retain(|m| m.created_at < cursor);
        }

        // Apply visible_from_seq
        if let Some(visible_from) = query.visible_from_seq {
            filtered.retain(|m| m.session_seq >= visible_from);
        }

        // Apply keyword filter
        if let Some(ref keyword) = query.keyword {
            let kw = keyword.to_lowercase();
            filtered.retain(|m| content_text(&m.content).to_lowercase().contains(&kw));
        }

        // Apply sender filter
        if let Some(ref sender_id) = query.sender_id {
            filtered.retain(|m| m.sender_id == *sender_id);
        }

        // Apply message_type filter
        if let Some(ref msg_type) = query.message_type {
            filtered.retain(|m| m.message_type == *msg_type);
        }

        // Apply owner_bot_id filter
        match &query.owner_filter {
            MessageOwnerFilter::Any => {}
            MessageOwnerFilter::IsNull => {
                filtered.retain(|m| m.owner_bot_id.is_none());
            }
            MessageOwnerFilter::Eq(owner_bot_id) => {
                filtered.retain(|m| m.owner_bot_id.as_deref() == Some(owner_bot_id.as_str()));
            }
        }

        // Apply time_range filter
        if let Some((start, end)) = query.time_range {
            filtered.retain(|m| m.created_at >= start && m.created_at <= end);
        }

        // Sort by created_at DESC, session_seq DESC
        filtered.sort_by(|a, b| {
            b.created_at
                .cmp(&a.created_at)
                .then(b.session_seq.cmp(&a.session_seq))
        });

        let has_more = filtered.len() > limit;
        filtered.truncate(limit);

        let next_cursor = if has_more {
            filtered.last().map(|m| m.created_at)
        } else {
            None
        };

        let count = filtered.len();
        info!(
            session_id = %query.session_id,
            count,
            has_more,
            "messages queried (memory)"
        );
        Ok(MessagePage {
            messages: filtered.into_iter().cloned().collect(),
            next_cursor,
            has_more,
        })
    }

    async fn get_message_by_id(
        &self,
        session_id: &str,
        message_id: &str,
    ) -> Result<Option<PersistedMessage>, MessageRepoError> {
        let sessions = self.sessions.read().await;
        if let Some(entry) = sessions.get(session_id) {
            Ok(entry
                .messages
                .iter()
                .find(|m| m.message_id == message_id)
                .cloned())
        } else {
            Ok(None)
        }
    }

    async fn get_current_seq(&self, session_id: &str) -> Result<i64, MessageRepoError> {
        let sessions = self.sessions.read().await;
        Ok(sessions.get(session_id).map(|e| e.seq).unwrap_or(0))
    }
}

/// Extract searchable text from a JSON content value.
/// Unpacks JSON strings to avoid JSON-escaped quote interference.
fn content_text(value: &serde_json::Value) -> String {
    match value {
        serde_json::Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}
