//! In-memory message repository for local dev and testing.

use std::collections::HashMap;

use async_trait::async_trait;
use tokio::sync::RwLock;
use tracing::info;

use bcs_domain::{MessageOwnerFilter, MessagePage, MessageQuery, NewMessage, PersistedMessage, PersistedMessageStatus};
use bcs_service_api::port::repo::{MessageRepoError, MessageRepoPort};
use bcs_service_api::ServiceResult;

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

    /// List messages for a session ordered by `session_seq` ASCENDING with
    /// offset/limit pagination, plus the total count for the session (before
    /// pagination). Does NOT replace `query_messages` (compat).
    ///
    /// VSN7A/VHxMU: `visible_from_seq` + `owner_bot_id` are applied BEFORE
    /// pagination so pages stay consistent and `total` is the filtered count.
    async fn list_session_messages_by_seq(
        &self,
        session_id: &str,
        offset: u64,
        limit: u64,
        visible_from_seq: Option<i64>,
        owner_bot_id: Option<&str>,
    ) -> ServiceResult<(Vec<PersistedMessage>, u64)> {
        let sessions = self.sessions.read().await;
        let entry = match sessions.get(session_id) {
            Some(e) => e,
            None => return Ok((Vec::new(), 0)),
        };
        // Clone then sort by session_seq ASCENDING so out-of-order storage
        // (e.g. direct seeding in tests) still yields chronological order.
        let mut filtered: Vec<PersistedMessage> = entry.messages.iter().cloned().collect();
        filtered.sort_by(|a, b| a.session_seq.cmp(&b.session_seq));
        // Apply visibility predicates before pagination + count so pages and
        // `total` stay consistent with the viewer's scope.
        if let Some(visible_from) = visible_from_seq {
            filtered.retain(|m| m.session_seq >= visible_from);
        }
        if let Some(owner) = owner_bot_id {
            filtered.retain(|m| m.owner_bot_id.as_deref() == Some(owner));
        }
        let total = filtered.len() as u64;
        let paged = filtered
            .into_iter()
            .skip(offset as usize)
            .take(limit as usize)
            .collect();
        Ok((paged, total))
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

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_domain::SenderType;

    fn make_msg(session_id: &str, message_id: &str, session_seq: i64) -> PersistedMessage {
        PersistedMessage {
            message_id: message_id.to_string(),
            group_id: "g1".to_string(),
            session_id: session_id.to_string(),
            session_seq,
            sender_id: "bot1".to_string(),
            sender_type: SenderType::Bot,
            message_type: "chat".to_string(),
            content: serde_json::json!(format!("msg-{message_id}")),
            client_msg_id: None,
            owner_bot_id: None,
            status: PersistedMessageStatus::Normal,
            created_at: session_seq as u64 * 1000,
            run_id: String::new(),
        }
    }

    /// `list_session_messages_by_seq` must return messages ordered by
    /// `session_seq` ASCENDING even when stored out of order, with the correct
    /// total (pre-pagination) and offset/limit behavior.
    #[tokio::test]
    async fn list_session_messages_by_seq_orders_ascending_and_paginates() {
        let repo = MemoryMessageRepo::new();
        // Seed messages with out-of-order session_seq values directly so we can
        // assert the method sorts ASC regardless of insertion order.
        {
            let mut sessions = repo.sessions.write().await;
            let entry = sessions.entry("s1".to_string()).or_default();
            entry.messages.push(make_msg("s1", "m1", 3));
            entry.messages.push(make_msg("s1", "m2", 1));
            entry.messages.push(make_msg("s1", "m3", 2));
            entry.messages.push(make_msg("s1", "m4", 5));
            entry.messages.push(make_msg("s1", "m5", 4));
            entry.seq = 5;
        }

        // Full list, ASC order
        let (msgs, total) = repo
            .list_session_messages_by_seq("s1", 0, 100, None, None)
            .await
            .unwrap();
        assert_eq!(total, 5);
        assert_eq!(msgs.len(), 5);
        assert_eq!(
            msgs.iter().map(|m| m.session_seq).collect::<Vec<_>>(),
            vec![1, 2, 3, 4, 5]
        );

        // Pagination: offset=1, limit=2 → seqs [2, 3], total still 5
        let (msgs, total) = repo
            .list_session_messages_by_seq("s1", 1, 2, None, None)
            .await
            .unwrap();
        assert_eq!(total, 5);
        assert_eq!(msgs.len(), 2);
        assert_eq!(msgs[0].session_seq, 2);
        assert_eq!(msgs[1].session_seq, 3);

        // offset beyond total → empty page, total still 5
        let (msgs, total) = repo
            .list_session_messages_by_seq("s1", 10, 5, None, None)
            .await
            .unwrap();
        assert!(msgs.is_empty());
        assert_eq!(total, 5);

        // limit=0 → empty page, total still 5
        let (msgs, total) = repo
            .list_session_messages_by_seq("s1", 0, 0, None, None)
            .await
            .unwrap();
        assert!(msgs.is_empty());
        assert_eq!(total, 5);

        // Unknown session → empty, total 0
        let (msgs, total) = repo
            .list_session_messages_by_seq("nope", 0, 10, None, None)
            .await
            .unwrap();
        assert!(msgs.is_empty());
        assert_eq!(total, 0);
    }

    /// `query_messages` (old compat API) must remain DESC-by-created_at and
    /// unaffected by the new ASC method.
    #[tokio::test]
    async fn query_messages_still_desc_after_new_method() {
        let repo = MemoryMessageRepo::new();
        {
            let mut sessions = repo.sessions.write().await;
            let entry = sessions.entry("s2".to_string()).or_default();
            entry.messages.push(make_msg("s2", "a", 1));
            entry.messages.push(make_msg("s2", "b", 2));
            entry.messages.push(make_msg("s2", "c", 3));
            entry.seq = 3;
        }
        let page = repo
            .query_messages(MessageQuery {
                group_id: "g1".to_string(),
                session_id: "s2".to_string(),
                cursor: None,
                limit: 10,
                keyword: None,
                sender_id: None,
                message_type: None,
                owner_filter: MessageOwnerFilter::Any,
                time_range: None,
                visible_from_seq: None,
            })
            .await
            .unwrap();
        // created_at DESC, session_seq DESC → [3, 2, 1]
        assert_eq!(
            page.messages.iter().map(|m| m.session_seq).collect::<Vec<_>>(),
            vec![3, 2, 1]
        );
    }
}
