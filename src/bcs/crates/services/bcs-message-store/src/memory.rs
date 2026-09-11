//! In-memory message repository for local dev and testing.

use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use tokio::sync::RwLock;
use tracing::info;

use bcs_event_store::MemoryEventStore;

use bcs_domain::{
    HumanMessageView, MessageAudience, MessageOwnerFilter, MessagePage, MessageQuery,
    MessageVisibilityDomain, NewMessage, PersistedMessage, PersistedMessageStatus,
};
use bcs_service_api::ServiceResult;
use bcs_service_api::port::repo::{AppendMessageWithEvent, MessageRepoError, MessageRepoPort};

/// In-memory implementation of [`MessageRepoPort`].
#[derive(Debug, Default)]
pub struct MemoryMessageRepo {
    policy: RwLock<bcs_config_api::message_delivery::DeliveryPolicyRecord>,
    /// messages keyed by session_id, each session is a Vec ordered by session_seq.
    sessions: RwLock<HashMap<String, SessionMessages>>,
    event_store: Option<Arc<MemoryEventStore>>,
    env: String,
}

#[derive(Debug, Clone, Default)]
struct SessionMessages {
    seq: i64,
    messages: Vec<PersistedMessage>,
    deliveries: Vec<bcs_domain::message_delivery::PersistedMessageDelivery>,
}

impl MemoryMessageRepo {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_environment(mut self, env: String) -> Self {
        self.env = env;
        self
    }

    pub fn with_event_store(mut self, event_store: Arc<MemoryEventStore>) -> Self {
        self.event_store = Some(event_store);
        self
    }
}

#[async_trait]
impl MessageRepoPort for MemoryMessageRepo {
    async fn run_chat_segments(&self, session: &str, sender: &str, run: &str) -> Result<Vec<PersistedMessage>, MessageRepoError> {
        let sessions = self.sessions.read().await;
        let mut rows: Vec<_> = sessions.get(session).into_iter().flat_map(|s| &s.messages)
            .filter(|m| m.sender_id == sender && m.run_id == run && m.message_type == "chat").cloned().collect();
        rows.sort_by_key(|m| m.session_seq);
        Ok(rows)
    }
    async fn get_messages_by_ids(&self, session_id: &str, ids: &[String]) -> Result<Vec<PersistedMessage>, MessageRepoError> {
        let sessions = self.sessions.read().await;
        let ids: std::collections::BTreeSet<_> = ids.iter().collect();
        Ok(sessions.get(session_id).map(|s| s.messages.iter().filter(|m| ids.contains(&m.message_id)).cloned().collect()).unwrap_or_default())
    }
    fn delivery_repository(self: Arc<Self>) -> Option<Arc<dyn bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoPort>> {
        Some(self)
    }
    async fn append_message(
        &self,
        msg: NewMessage,
    ) -> Result<PersistedMessage, MessageRepoError> {
        validate_new_message_visibility(&msg)?;
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
            visibility_domain: Some(msg.visibility_domain),
            audience: msg.audience,
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

    async fn append_message_with_event(
        &self,
        command: AppendMessageWithEvent,
    ) -> Result<PersistedMessage, MessageRepoError> {
        validate_new_message_visibility(&command.message)?;
        let event_store = self.event_store.as_ref().ok_or_else(|| {
            MessageRepoError::StorageError(
                "Eventful Memory message persistence requires the shared Memory Event Store"
                    .to_string(),
            )
        })?;
        if command.event.event.subject.id != command.message_id
            || command.event.event.scope.group_id.as_deref()
                != Some(command.message.group_id.as_str())
            || command.event.event.scope.session_id.as_deref()
                != Some(command.message.session_id.as_str())
        {
            return Err(MessageRepoError::StorageError(
                "message Event does not match the persisted logical message".to_string(),
            ));
        }
        let mut sessions = self.sessions.write().await;
        let entry = sessions
            .entry(command.message.session_id.clone())
            .or_default();
        if let Some(client_msg_id) = command.message.client_msg_id.as_deref()
            && let Some(existing) = entry.messages.iter().find(|message| {
                message.sender_id == command.message.sender_id
                    && message.client_msg_id.as_deref() == Some(client_msg_id)
            })
        {
            return Ok(existing.clone());
        }
        let next_seq = entry.seq.checked_add(1).ok_or_else(|| {
            MessageRepoError::InvalidSequence("session sequence overflow".to_string())
        })?;
        let persisted = PersistedMessage {
            message_id: command.message_id,
            group_id: command.message.group_id,
            session_id: command.message.session_id,
            session_seq: next_seq,
            sender_id: command.message.sender_id,
            sender_type: command.message.sender_type,
            message_type: command.message.message_type,
            content: command.message.content,
            client_msg_id: command.message.client_msg_id,
            owner_bot_id: command.message.owner_bot_id,
            visibility_domain: Some(command.message.visibility_domain),
            audience: command.message.audience,
            status: PersistedMessageStatus::Normal,
            created_at: command.message.created_at,
            run_id: command.message.run_id,
        };
        event_store
            .commit_business_mutation(&command.event, || {
                entry.seq = next_seq;
                entry.messages.push(persisted.clone());
                Ok(())
            })
            .await
            .map_err(|error| MessageRepoError::StorageError(error.to_string()))?;
        Ok(persisted)
    }

    async fn query_messages(&self, query: MessageQuery) -> Result<MessagePage, MessageRepoError> {
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
        let mut filtered: Vec<&PersistedMessage> = entry.messages.iter().filter(|m| m.message_type != "run_reply").collect();

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

        if let Some(human_view) = query.human_view.as_ref() {
            filtered.retain(|message| human_view.allows(message));
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
            messages: filtered.into_iter().cloned().map(super::history_projection).collect(),
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
        human_view: Option<HumanMessageView>,
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
        let mut filtered: Vec<&PersistedMessage> = entry.messages.iter().filter(|m| m.message_type != "run_reply").collect();

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
                    m.owner_bot_id.is_none() || m.owner_bot_id.as_deref() == Some(owner.as_str())
                });
            }
        }

        if let Some(human_view) = human_view.as_ref() {
            filtered.retain(|message| human_view.allows(message));
        }

        // VYQHI: composite (created_at, session_seq) cursor so messages sharing
        // a created_at at a page boundary are not permanently skipped on the
        // next page. The cursor is an exclusive strict-lexicographic bound.
        if let Some((cursor_ts, cursor_seq)) = before {
            filtered.retain(|m| (m.created_at, m.session_seq) < (cursor_ts, cursor_seq));
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
            messages: filtered.into_iter().cloned().map(super::history_projection).collect(),
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

fn validate_new_message_visibility(msg: &NewMessage) -> Result<(), MessageRepoError> {
    if matches!(
        msg.visibility_domain,
        MessageVisibilityDomain::ManagerWorker | MessageVisibilityDomain::StateMachine
    ) && msg.audience.is_none()
    {
        return Err(MessageRepoError::StorageError(
            "ManagerWorker/StateMachine messages require an audience".to_string(),
        ));
    }
    if let Some(audience) = &msg.audience {
        audience
            .validate()
            .map_err(|error| MessageRepoError::StorageError(error.to_string()))?;
        if msg.owner_bot_id.is_some() && !matches!(audience, MessageAudience::Directed { .. }) {
            return Err(MessageRepoError::StorageError(
                "owner_bot_id requires a directed audience on classified messages".to_string(),
            ));
        }
        if let Some(owner_bot_id) = msg.owner_bot_id.as_deref()
            && !audience.contains(owner_bot_id)
        {
            return Err(MessageRepoError::StorageError(
                "owner_bot_id must be included in the directed audience".to_string(),
            ));
        }
    }
    Ok(())
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
            visibility_domain: Some(MessageVisibilityDomain::Chat),
            audience: None,
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
                human_view: None,
            })
            .await
            .unwrap();
        // created_at DESC, session_seq DESC → [3, 2, 1]
        assert_eq!(
            page.messages
                .iter()
                .map(|m| m.session_seq)
                .collect::<Vec<_>>(),
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
            .list_session_history("s3", MessageOwnerFilter::Any, None, None, None, 50)
            .await
            .unwrap();
        assert!(!page.has_more);
        assert!(page.next_cursor.is_none());
        assert_eq!(
            page.messages
                .iter()
                .map(|m| m.session_seq)
                .collect::<Vec<_>>(),
            vec![5, 4, 3, 2, 1]
        );

        // IsNull filter: only NULL-owned (odd seqs) survive, still DESC.
        let page = repo
            .list_session_history("s3", MessageOwnerFilter::IsNull, None, None, None, 50)
            .await
            .unwrap();
        assert_eq!(
            page.messages
                .iter()
                .map(|m| m.session_seq)
                .collect::<Vec<_>>(),
            vec![5, 3, 1]
        );

        // Eq filter: only bot-w-owned (even seqs) survive.
        let page = repo
            .list_session_history(
                "s3",
                MessageOwnerFilter::Eq("bot-w".to_string()),
                None,
                None,
                None,
                50,
            )
            .await
            .unwrap();
        assert_eq!(
            page.messages
                .iter()
                .map(|m| m.session_seq)
                .collect::<Vec<_>>(),
            vec![4, 2]
        );

        // visible_from_seq=3: drop seqs 1,2; DESC → [5,4,3].
        let page = repo
            .list_session_history("s3", MessageOwnerFilter::Any, Some(3), None, None, 50)
            .await
            .unwrap();
        assert_eq!(
            page.messages
                .iter()
                .map(|m| m.session_seq)
                .collect::<Vec<_>>(),
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
                None,
                Some((3000, i64::MIN)),
                50,
            )
            .await
            .unwrap();
        assert_eq!(
            page.messages
                .iter()
                .map(|m| m.session_seq)
                .collect::<Vec<_>>(),
            vec![2, 1]
        );

        // limit=2 with has_more + next_cursor = (4000, 4).
        let page = repo
            .list_session_history("s3", MessageOwnerFilter::Any, None, None, None, 2)
            .await
            .unwrap();
        assert!(page.has_more);
        assert_eq!(page.next_cursor, Some((4000, 4)));
        assert_eq!(
            page.messages
                .iter()
                .map(|m| m.session_seq)
                .collect::<Vec<_>>(),
            vec![5, 4]
        );

        // Follow the cursor: before=(4000,4) → [3,2], still has_more (1 left).
        let page = repo
            .list_session_history(
                "s3",
                MessageOwnerFilter::Any,
                None,
                None,
                Some((4000, 4)),
                2,
            )
            .await
            .unwrap();
        assert!(page.has_more);
        assert_eq!(page.next_cursor, Some((2000, 2)));
        assert_eq!(
            page.messages
                .iter()
                .map(|m| m.session_seq)
                .collect::<Vec<_>>(),
            vec![3, 2]
        );

        // Final page: before=(2000,2) → [1], no more.
        let page = repo
            .list_session_history(
                "s3",
                MessageOwnerFilter::Any,
                None,
                None,
                Some((2000, 2)),
                2,
            )
            .await
            .unwrap();
        assert!(!page.has_more);
        assert!(page.next_cursor.is_none());
        assert_eq!(
            page.messages
                .iter()
                .map(|m| m.session_seq)
                .collect::<Vec<_>>(),
            vec![1]
        );

        // Unknown session → empty page.
        let page = repo
            .list_session_history("nope", MessageOwnerFilter::Any, None, None, None, 10)
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
            .list_session_history("stie", MessageOwnerFilter::Any, None, None, None, 2)
            .await
            .unwrap();
        assert!(page.has_more);
        assert_eq!(page.next_cursor, Some((9_000, 4)));
        assert_eq!(
            page.messages
                .iter()
                .map(|m| m.session_seq)
                .collect::<Vec<_>>(),
            vec![5, 4]
        );

        // Page 2: before=(9000,4) → [3, 2], next_cursor = (9000, 2).
        let page = repo
            .list_session_history(
                "stie",
                MessageOwnerFilter::Any,
                None,
                None,
                Some((9_000, 4)),
                2,
            )
            .await
            .unwrap();
        assert!(page.has_more);
        assert_eq!(page.next_cursor, Some((9_000, 2)));
        assert_eq!(
            page.messages
                .iter()
                .map(|m| m.session_seq)
                .collect::<Vec<_>>(),
            vec![3, 2]
        );

        // Page 3: before=(9000,2) → [1], no more.
        let page = repo
            .list_session_history(
                "stie",
                MessageOwnerFilter::Any,
                None,
                None,
                Some((9_000, 2)),
                2,
            )
            .await
            .unwrap();
        assert!(!page.has_more);
        assert!(page.next_cursor.is_none());
        assert_eq!(
            page.messages
                .iter()
                .map(|m| m.session_seq)
                .collect::<Vec<_>>(),
            vec![1]
        );
    }

    #[tokio::test]
    async fn participant_audience_filter_runs_before_pagination() {
        let repo = MemoryMessageRepo::new();
        {
            let mut sessions = repo.sessions.write().await;
            let entry = sessions.entry("scoped".to_string()).or_default();
            let audiences = [
                MessageAudience::Public,
                MessageAudience::directed(["human_a"]).unwrap(),
                MessageAudience::FullOnly,
                MessageAudience::Public,
                MessageAudience::FullOnly,
            ];
            for (index, audience) in audiences.into_iter().enumerate() {
                let seq = index as i64 + 1;
                let mut message = make_msg("scoped", &format!("m{seq}"), seq);
                message.visibility_domain = Some(MessageVisibilityDomain::StateMachine);
                message.audience = Some(audience);
                entry.messages.push(message);
            }
            entry.seq = 5;
        }

        let view = HumanMessageView {
            actor_id: "human_a".to_string(),
            scope: bcs_domain::MessageViewScope::Participant,
            allow_legacy_unclassified_chat: false,
        };
        let first = repo
            .list_session_history(
                "scoped",
                MessageOwnerFilter::Any,
                None,
                Some(view.clone()),
                None,
                2,
            )
            .await
            .unwrap();

        assert_eq!(
            first
                .messages
                .iter()
                .map(|message| message.session_seq)
                .collect::<Vec<_>>(),
            vec![4, 2],
        );
        assert!(first.has_more);
        assert_eq!(first.next_cursor, Some((2_000, 2)));

        let second = repo
            .list_session_history(
                "scoped",
                MessageOwnerFilter::Any,
                None,
                Some(view),
                first.next_cursor,
                2,
            )
            .await
            .unwrap();
        assert_eq!(
            second
                .messages
                .iter()
                .map(|message| message.session_seq)
                .collect::<Vec<_>>(),
            vec![1],
        );
        assert!(!second.has_more);
    }
}
#[async_trait]
impl bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoPort for MemoryMessageRepo {
    async fn bounded_contexts(&self, carrier: &str, limit: usize) -> Result<bcs_service_api::port::repo::message_delivery::BoundDeliveryContexts, bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError> {
        let sessions = self.sessions.read().await;
        let mut rows: Vec<_> = sessions.values().flat_map(|s| &s.deliveries).filter(|d| d.bound_to_delivery_id.as_deref() == Some(carrier) && d.state.status == bcs_domain::message_delivery::MessageDeliveryStatus::Bound).collect();
        let total = rows.len() as u64;
        rows.sort_by(|a,b| (b.source_session_seq, &b.delivery_id).cmp(&(a.source_session_seq, &a.delivery_id)));
        Ok(bcs_service_api::port::repo::message_delivery::BoundDeliveryContexts { rows: rows.into_iter().take(limit.min(1025)).cloned().collect(), total })
    }
    async fn lookup(&self, scope: bcs_service_api::port::repo::message_delivery::DeliveryLookup) -> Result<Vec<bcs_domain::message_delivery::PersistedMessageDelivery>, bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError> {
        use bcs_service_api::port::repo::message_delivery::DeliveryLookup as Q;
        let rows = self.list_deliveries(None).await?;
        Ok(rows.into_iter().filter(|d| match &scope {
            Q::Id(id) => d.delivery_id == *id,
            Q::Request(id) => d.request_id.as_ref() == Some(id),
            Q::Run { bot, alias } => d.target_bot_id == *bot && (d.run_id.as_ref() == Some(alias) || d.request_id.as_ref() == Some(alias) || d.transport_context_json.as_ref().and_then(|v| v.get("downstream_run_id")).and_then(|v| v.as_str()) == Some(alias.as_str())),
            Q::Bound(id) => d.bound_to_delivery_id.as_ref() == Some(id) && d.state.status == bcs_domain::message_delivery::MessageDeliveryStatus::Bound,
            Q::Lane { bot, session } => d.target_bot_id == *bot && d.session_id == *session && d.state.kind == bcs_domain::DeliveryType::Send && super::delivery::unfinished(d),
            Q::BotPending(bot) => d.target_bot_id == *bot && super::delivery::unfinished(d),
            Q::Message(id) => d.source_message_id == *id,
            Q::Successor { bot, session, after_seq, exclude, now_ms } => d.target_bot_id == *bot && d.session_id == *session && d.source_session_seq > *after_seq && d.delivery_id != *exclude && d.state.kind == bcs_domain::DeliveryType::Send && d.state.status == bcs_domain::message_delivery::MessageDeliveryStatus::Queued && !d.state.may_have_been_sent && d.expire_at_ms.is_none_or(|t| t > *now_ms),
        }).take(if matches!(scope, Q::BotPending(_) | Q::Successor { .. }) { 1 } else { usize::MAX }).collect())
    }
    async fn queued_bots(&self, after: &str, limit: usize) -> Result<Vec<String>, bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError> {
        Ok(self.list_deliveries(None).await?.into_iter().filter(|d| d.state.kind == bcs_domain::DeliveryType::Send && d.state.status == bcs_domain::message_delivery::MessageDeliveryStatus::Queued && d.target_bot_id.as_str() > after).map(|d| d.target_bot_id).collect::<std::collections::BTreeSet<_>>().into_iter().take(limit.min(256)).collect())
    }
    async fn active_count(&self, bot: &str) -> Result<u64, bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError> {
        Ok(self.list_deliveries(None).await?.iter().filter(|d| d.target_bot_id == bot && super::delivery::active(d)).count() as u64)
    }
    async fn lane_blocked(&self, row: &bcs_domain::message_delivery::PersistedMessageDelivery) -> Result<bool, bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError> {
        Ok(self.list_deliveries(Some(&row.session_id)).await?.iter().any(|d| d.target_bot_id == row.target_bot_id && d.delivery_id != row.delivery_id && d.state.kind == bcs_domain::DeliveryType::Send && (super::delivery::active(d) || (d.state.status == bcs_domain::message_delivery::MessageDeliveryStatus::Queued && d.source_session_seq < row.source_session_seq))))
    }
    async fn queued_heads(&self, bot: &str, after: &str, limit: usize) -> Result<Vec<bcs_service_api::core::message_delivery::DeliveryScheduleEntry>, bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError> {
        use bcs_domain::{DeliveryType, message_delivery::MessageDeliveryStatus as S};
        let rows = self.list_deliveries(None).await?;
        let mut heads: Vec<_> = rows.iter().filter(|q| q.target_bot_id == bot && q.state.kind == DeliveryType::Send && q.state.status == S::Queued && q.session_id.as_str() > after && !rows.iter().any(|p| p.target_bot_id == bot && p.session_id == q.session_id && p.state.kind == DeliveryType::Send && super::delivery::unfinished(p) && p.source_session_seq < q.source_session_seq)).collect();
        heads.sort_by(|a,b| a.session_id.cmp(&b.session_id));
        Ok(heads.into_iter().take(limit.min(64)).map(|d| bcs_service_api::core::message_delivery::DeliveryScheduleEntry { delivery_id: d.delivery_id.clone(), session_id: d.session_id.clone(), source_session_seq: d.source_session_seq as u64, state: d.state, available_at_ms: d.available_at_ms, expire_at_ms: d.expire_at_ms }).collect())
    }
    async fn work_batch(&self, kind: bcs_service_api::port::repo::message_delivery::DeliveryWorkBatch, now: i64, after: &str, limit: usize) -> Result<Vec<bcs_domain::message_delivery::PersistedMessageDelivery>, bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError> {
        use bcs_service_api::port::repo::message_delivery::DeliveryWorkBatch as B;
        use bcs_domain::message_delivery::MessageDeliveryStatus as S;
        if matches!(kind, B::Control) {
            let all = self.list_deliveries(None).await?;
            let limit = limit.min(200);
            let mut result = Vec::new();
            let mut pages = Vec::new();
            for category in 0..4 {
                let mut rows: Vec<_> = all.iter().filter(|d| match category {
                    0 => d.state.status == S::Dispatching && d.run_deadline_at_ms.is_some_and(|t| t <= now),
                    1 => d.state.status == S::Running && d.run_deadline_at_ms.is_some_and(|t| t <= now),
                    2 => d.state.status == S::Cancelling && d.abort_request_id.is_none(),
                    _ => d.state.status == S::Cancelling && d.abort_request_id.is_some() && d.cancel_deadline_at_ms.is_some_and(|t| t <= now),
                }).cloned().collect();
                rows.sort_by(|a,b| {
                    let key = |d: &bcs_domain::message_delivery::PersistedMessageDelivery| match category {
                        0 | 1 => d.run_deadline_at_ms, 2 => None, _ => d.cancel_deadline_at_ms,
                    };
                    (key(a), &a.delivery_id).cmp(&(key(b), &b.delivery_id))
                });
                let quota = (limit / 4 + usize::from(category < limit % 4)).max(1).min(limit - result.len()).min(rows.len());
                result.extend(rows.drain(..quota)); pages.push(rows);
            }
            for rows in pages { result.extend(rows.into_iter().take(limit - result.len())); }
            return Ok(result);
        }
        if matches!(kind, B::Expired) {
            let all = self.list_deliveries(None).await?; let mut result = Vec::new(); let limit = limit.min(200);
            for (status, size) in [(S::Queued, limit.div_ceil(2)), (S::PendingContext, limit / 2)] {
                let size = if status == S::PendingContext && result.is_empty() { limit } else { size };
                let mut rows: Vec<_> = all.iter().filter(|d| d.state.status == status && d.expire_at_ms.is_some_and(|t| t <= now)).cloned().collect();
                rows.sort_by(|a,b| (a.expire_at_ms, &a.delivery_id).cmp(&(b.expire_at_ms, &b.delivery_id))); rows.truncate(size); result.extend(rows);
            }
            return Ok(result);
        }
        let mut rows: Vec<_> = self.list_deliveries(None).await?.into_iter().filter(|d| d.delivery_id.as_str() > after && match kind {
            B::Expired => matches!(d.state.status, S::Queued | S::PendingContext) && d.expire_at_ms.is_some_and(|t| t <= now),
            B::Control => unreachable!("control classes handled above"),
            B::Recovery => matches!(d.state.status, S::Dispatching | S::Running | S::Cancelling),
        }).collect();
        rows.sort_by(|a,b| a.delivery_id.cmp(&b.delivery_id)); rows.truncate(limit.min(200)); Ok(rows)
    }
    async fn queue_statistics(&self) -> Result<Vec<bcs_service_api::port::repo::message_delivery::DeliveryQueueStatistic>, bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError> {
        let mut groups = std::collections::BTreeMap::new();
        for d in self.list_deliveries(None).await?.into_iter().filter(super::delivery::unfinished) {
            let string = |v: serde_json::Value| v.as_str().unwrap_or("none").to_owned();
            let key = (string(serde_json::to_value(d.flow_kind).unwrap()), string(serde_json::to_value(d.state.kind).unwrap()), string(serde_json::to_value(d.state.status).unwrap()), string(serde_json::to_value(d.wait_reason).unwrap()));
            let r = groups.entry(key.clone()).or_insert(bcs_service_api::port::repo::message_delivery::DeliveryQueueStatistic { flow_kind: key.0, kind: key.1, status: key.2, wait_reason: key.3, count: 0, oldest_created_at_ms: None });
            r.count += 1; r.oldest_created_at_ms = Some(r.oldest_created_at_ms.map_or(d.created_at_ms, |old| old.min(d.created_at_ms)));
        }
        Ok(groups.into_values().collect())
    }
    async fn load_policy(&self) -> Result<bcs_config_api::message_delivery::DeliveryPolicyRecord, bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError> {
        Ok(self.policy.read().await.clone())
    }

    async fn replace_policy(&self, expected_version: u64, record: bcs_config_api::message_delivery::DeliveryPolicyRecord) -> Result<(), bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError> {
        use bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError;
        let mut current = self.policy.write().await;
        if current.version != expected_version || record.version != expected_version.saturating_add(1) || record.version > i64::MAX as u64 {
            return Err(MessageDeliveryRepoError::Conflict);
        }
        record.policy.validate().map_err(|e| MessageDeliveryRepoError::Invalid(e.to_string()))?;
        *current = record;
        Ok(())
    }
    async fn admit(
        &self,
        command: bcs_service_api::port::repo::message_delivery::AdmitMessageDeliveries,
    ) -> Result<bcs_service_api::port::repo::message_delivery::DeliveryAdmissionResult,
        bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError> {
        self.delivery_transaction(Vec::new(), Some(command)).await?
            .ok_or_else(|| bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError::Storage("missing admission result".into()))
    }

    async fn list_deliveries(&self, session_id: Option<&str>) -> Result<Vec<bcs_domain::message_delivery::PersistedMessageDelivery>,
        bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError> {
        let sessions = self.sessions.read().await;
        let mut rows: Vec<_> = sessions.iter().filter(|(id, _)| session_id.is_none_or(|s| s == id.as_str()))
            .flat_map(|(_, s)| s.deliveries.iter().cloned()).collect();
        rows.sort_by(|a, b| (a.source_session_seq, &a.delivery_id).cmp(&(b.source_session_seq, &b.delivery_id)));
        Ok(rows)
    }

    async fn commit_transition(&self,
        changes: Vec<bcs_service_api::port::repo::message_delivery::DeliveryCompareAndSet>,
        reply: Option<bcs_service_api::port::repo::message_delivery::AdmitMessageDeliveries>,
    ) -> Result<Option<bcs_service_api::port::repo::message_delivery::DeliveryAdmissionResult>,
        bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError> {
        self.delivery_transaction(changes, reply).await
    }
}

impl MemoryMessageRepo {
    async fn delivery_transaction(&self,
        changes: Vec<bcs_service_api::port::repo::message_delivery::DeliveryCompareAndSet>,
        admission: Option<bcs_service_api::port::repo::message_delivery::AdmitMessageDeliveries>,
    ) -> Result<Option<bcs_service_api::port::repo::message_delivery::DeliveryAdmissionResult>,
        bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError> {
        use bcs_service_api::port::repo::message_delivery::{DeliveryAdmissionResult, MessageDeliveryRepoError as Error};
        let mut sessions = self.sessions.write().await;
        let mut staged = sessions.clone();
        let env = if self.env.is_empty() { "local" } else { &self.env };
        let mut rows: Vec<_> = staged.values().flat_map(|s| s.deliveries.iter().cloned()).collect();
        if changes.iter().any(|c| c.delivery.env != env) { return Err(Error::Invalid("environment mismatch".into())); }
        super::delivery::apply_changes(&mut rows, &changes)?;
        let mut result = None;
        let event = admission.as_ref().and_then(|c| c.event.clone().or_else(|| c.display_message.as_ref().and_then(|d| d.event.clone())));
        if let Some(command) = admission {
            super::delivery::validate_admission(&command)?;
            let entry = staged.entry(command.message.session_id.clone()).or_default();
            if let Some(id) = command.message.client_msg_id.as_deref() {
                if let Some(message) = entry.messages.iter().find(|m| m.sender_id == command.message.sender_id && m.client_msg_id.as_deref() == Some(id)) {
                    if !changes.is_empty() { return Err(Error::Conflict); }
                    return Ok(Some(DeliveryAdmissionResult { message: message.clone(),
                        deliveries: rows.into_iter().filter(|d| d.source_message_id == message.message_id).collect(), duplicate: true }));
                }
            }
            if staged.values().any(|s| s.messages.iter().any(|m| m.message_id == command.message_id)) {
                return Err(Error::Conflict);
            }
            if let Some(display) = &command.display_message {
                if staged.values().any(|s| s.messages.iter().any(|m| m.message_id == display.message_id
                    || (m.session_id == display.message.session_id && m.sender_id == display.message.sender_id
                        && display.message.client_msg_id.is_some() && m.client_msg_id == display.message.client_msg_id))) {
                    return Err(Error::Conflict);
                }
            }
            let entry = staged.entry(command.message.session_id.clone()).or_default();
            entry.seq = entry.seq.checked_add(1 + i64::from(command.display_message.is_some())).ok_or_else(|| Error::Invalid("sequence exhausted".into()))?;
            let message = super::delivery::canonical(&command, entry.seq);
            let (deliveries, context_changes) = super::delivery::plan_admission(env, &command, entry.seq, &rows, None)?;
            super::delivery::apply_changes(&mut rows, &context_changes)?;
            rows.extend(deliveries.clone());
            if let Some(display) = super::delivery::canonical_display(&command, entry.seq - 1) {
                entry.messages.push(display);
            }
            entry.messages.push(message.clone());
            result = Some(DeliveryAdmissionResult { message, deliveries, duplicate: false });
        }
        for session in staged.values_mut() { session.deliveries.clear(); }
        for row in rows { staged.entry(row.session_id.clone()).or_default().deliveries.push(row); }
        if let Some(event) = event {
            let store = self.event_store.as_ref().ok_or_else(|| Error::Storage("event store is not configured".into()))?;
            store.commit_business_mutation(&event, || {
                *sessions = staged;
                Ok(())
            }).await.map_err(|e| Error::Storage(e.to_string()))?;
        } else {
            *sessions = staged;
        }
        Ok(result)
    }
}
