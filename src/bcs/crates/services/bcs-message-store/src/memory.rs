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
            MessageOwnerFilter::PublicOrOwner(owner_bot_id) => {
                filtered.retain(|m| {
                    m.owner_bot_id.is_none()
                        || m.owner_bot_id.as_deref() == Some(owner_bot_id.as_str())
                });
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
            filtered.last().map(|m| (m.created_at, m.session_seq))
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

    /// Direct-read session history with full visibility predicates + cursor
    /// pagination (legacy `created_at DESC, session_seq DESC` order).
    ///
    /// `env` is a no-op here because [`MemoryMessageRepo`] does not tag
    /// messages with an env; the MySQL store enforces env isolation on read
    /// where the column exists (VUlao).
    async fn list_session_history(
        &self,
        session_id: &str,
        owner_filter: MessageOwnerFilter,
        visible_from_seq: Option<i64>,
        before: Option<(u64, i64)>,
        limit: u32,
    ) -> ServiceResult<MessagePage> {
        let sessions = self.sessions.read().await;
        let entry = match sessions.get(session_id) {
            Some(e) => e,
            None => {
                return Ok(MessagePage {
                    messages: Vec::new(),
                    next_cursor: None,
                    has_more: false,
                });
            }
        };

        let limit = limit as usize;
        let mut filtered: Vec<&PersistedMessage> = entry.messages.iter().collect();

        if let Some(visible_from) = visible_from_seq {
            filtered.retain(|m| m.session_seq >= visible_from);
        }

        match &owner_filter {
            MessageOwnerFilter::Any => {}
            MessageOwnerFilter::IsNull => {
                filtered.retain(|m| m.owner_bot_id.is_none());
            }
            MessageOwnerFilter::Eq(owner) => {
                filtered.retain(|m| m.owner_bot_id.as_deref() == Some(owner.as_str()));
            }
            MessageOwnerFilter::PublicOrOwner(owner) => {
                filtered.retain(|m| {
                    m.owner_bot_id.is_none()
                        || m.owner_bot_id.as_deref() == Some(owner.as_str())
                });
            }
        }

        // VYQHI: composite (created_at, session_seq) cursor so messages sharing
        // a created_at at a page boundary are not permanently skipped on the
        // next page. The cursor is an exclusive strict-lexicographic bound.
        if let Some((cursor_ts, cursor_seq)) = before {
            filtered
                .retain(|m| (m.created_at, m.session_seq) < (cursor_ts, cursor_seq));
        }

        // Legacy order: created_at DESC, session_seq DESC.
        filtered.sort_by(|a, b| {
            b.created_at
                .cmp(&a.created_at)
                .then(b.session_seq.cmp(&a.session_seq))
        });

        let has_more = filtered.len() > limit;
        if has_more {
            filtered.truncate(limit);
        }
        let next_cursor = if has_more {
            filtered.last().map(|m| (m.created_at, m.session_seq))
        } else {
            None
        };

        let count = filtered.len();
        info!(
            session_id = %session_id,
            count,
            has_more,
            "session history listed (memory)"
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

    /// `list_session_history` must mirror the legacy direct-read contract:
    /// `created_at DESC, session_seq DESC` order, the full 3-state
    /// `MessageOwnerFilter` (incl. `IsNull`), `visible_from_seq` cutoff, an
    /// exclusive composite `(created_at, session_seq)` `before` cursor
    /// (VYQHI), and `has_more` + `next_cursor` instead of a count estimate.
    #[tokio::test]
    async fn list_session_history_desc_cutoff_and_cursor() {
        let repo = MemoryMessageRepo::new();
        // Seed: seq 1..5, created_at = seq * 1000 so order is unambiguous.
        // Mix owner_bot_id: odd seqs are NULL-owned, even seqs owned by "bot-w".
        {
            let mut sessions = repo.sessions.write().await;
            let entry = sessions.entry("s3".to_string()).or_default();
            for seq in 1..=5i64 {
                let mut m = make_msg("s3", &format!("h{seq}"), seq);
                m.created_at = seq as u64 * 1000;
                m.owner_bot_id = if seq % 2 == 0 {
                    Some("bot-w".to_string())
                } else {
                    None
                };
                entry.messages.push(m);
            }
            entry.seq = 5;
        }

        // Plain list: DESC by created_at (= seq DESC), all 5, no more.
        let page = repo
            .list_session_history("s3", MessageOwnerFilter::Any, None, None, 50)
            .await
            .unwrap();
        assert!(!page.has_more);
        assert!(page.next_cursor.is_none());
        assert_eq!(
            page.messages.iter().map(|m| m.session_seq).collect::<Vec<_>>(),
            vec![5, 4, 3, 2, 1]
        );

        // IsNull filter: only NULL-owned (odd seqs) survive, still DESC.
        let page = repo
            .list_session_history("s3", MessageOwnerFilter::IsNull, None, None, 50)
            .await
            .unwrap();
        assert_eq!(
            page.messages.iter().map(|m| m.session_seq).collect::<Vec<_>>(),
            vec![5, 3, 1]
        );

        // Eq filter: only bot-w-owned (even seqs) survive.
        let page = repo
            .list_session_history(
                "s3",
                MessageOwnerFilter::Eq("bot-w".to_string()),
                None,
                None,
                50,
            )
            .await
            .unwrap();
        assert_eq!(
            page.messages.iter().map(|m| m.session_seq).collect::<Vec<_>>(),
            vec![4, 2]
        );

        // visible_from_seq=3: drop seqs 1,2; DESC → [5,4,3].
        let page = repo
            .list_session_history("s3", MessageOwnerFilter::Any, Some(3), None, 50)
            .await
            .unwrap();
        assert_eq!(
            page.messages.iter().map(|m| m.session_seq).collect::<Vec<_>>(),
            vec![5, 4, 3]
        );

        // before=(3000, i64::MIN) (exclusive created_at == 3000): only
        // created_at < 3000 → [2,1]. The MIN session_seq sentinel makes the
        // composite bound behave like the legacy created_at-only strict-less.
        let page = repo
            .list_session_history(
                "s3",
                MessageOwnerFilter::Any,
                None,
                Some((3000, i64::MIN)),
                50,
            )
            .await
            .unwrap();
        assert_eq!(
            page.messages.iter().map(|m| m.session_seq).collect::<Vec<_>>(),
            vec![2, 1]
        );

        // limit=2 with has_more + next_cursor = (4000, 4).
        let page = repo
            .list_session_history("s3", MessageOwnerFilter::Any, None, None, 2)
            .await
            .unwrap();
        assert!(page.has_more);
        assert_eq!(page.next_cursor, Some((4000, 4)));
        assert_eq!(
            page.messages.iter().map(|m| m.session_seq).collect::<Vec<_>>(),
            vec![5, 4]
        );

        // Follow the cursor: before=(4000,4) → [3,2], still has_more (1 left).
        let page = repo
            .list_session_history(
                "s3",
                MessageOwnerFilter::Any,
                None,
                Some((4000, 4)),
                2,
            )
            .await
            .unwrap();
        assert!(page.has_more);
        assert_eq!(page.next_cursor, Some((2000, 2)));
        assert_eq!(
            page.messages.iter().map(|m| m.session_seq).collect::<Vec<_>>(),
            vec![3, 2]
        );

        // Final page: before=(2000,2) → [1], no more.
        let page = repo
            .list_session_history(
                "s3",
                MessageOwnerFilter::Any,
                None,
                Some((2000, 2)),
                2,
            )
            .await
            .unwrap();
        assert!(!page.has_more);
        assert!(page.next_cursor.is_none());
        assert_eq!(
            page.messages.iter().map(|m| m.session_seq).collect::<Vec<_>>(),
            vec![1]
        );

        // Unknown session → empty page.
        let page = repo
            .list_session_history("nope", MessageOwnerFilter::Any, None, None, 10)
            .await
            .unwrap();
        assert!(page.messages.is_empty());
        assert!(!page.has_more);
    }

    /// VYQHI regression: messages sharing the same `created_at` at a page
    /// boundary must not be skipped when following the composite cursor.
    #[tokio::test]
    async fn list_session_history_tied_created_at_no_skip() {
        let repo = MemoryMessageRepo::new();
        // Seed 5 messages ALL with the same created_at; session_seq breaks ties.
        {
            let mut sessions = repo.sessions.write().await;
            let entry = sessions.entry("stie".to_string()).or_default();
            for seq in 1..=5i64 {
                let mut m = make_msg("stie", &format!("t{seq}"), seq);
                m.created_at = 9_000; // identical for every message
                entry.messages.push(m);
            }
            entry.seq = 5;
        }

        // Page 1 (limit 2): [5, 4], next_cursor = (9000, 4).
        let page = repo
            .list_session_history("stie", MessageOwnerFilter::Any, None, None, 2)
            .await
            .unwrap();
        assert!(page.has_more);
        assert_eq!(page.next_cursor, Some((9_000, 4)));
        assert_eq!(
            page.messages.iter().map(|m| m.session_seq).collect::<Vec<_>>(),
            vec![5, 4]
        );

        // Page 2: before=(9000,4) → [3, 2], next_cursor = (9000, 2).
        let page = repo
            .list_session_history(
                "stie",
                MessageOwnerFilter::Any,
                None,
                Some((9_000, 4)),
                2,
            )
            .await
            .unwrap();
        assert!(page.has_more);
        assert_eq!(page.next_cursor, Some((9_000, 2)));
        assert_eq!(
            page.messages.iter().map(|m| m.session_seq).collect::<Vec<_>>(),
            vec![3, 2]
        );

        // Page 3: before=(9000,2) → [1], no more.
        let page = repo
            .list_session_history(
                "stie",
                MessageOwnerFilter::Any,
                None,
                Some((9_000, 2)),
                2,
            )
            .await
            .unwrap();
        assert!(!page.has_more);
        assert!(page.next_cursor.is_none());
        assert_eq!(
            page.messages.iter().map(|m| m.session_seq).collect::<Vec<_>>(),
            vec![1]
        );
    }
}
