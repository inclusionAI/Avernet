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
    async fn append_with_identity(
        &self,
        msg: NewMessage,
        message_id: String,
        stable: bool,
    ) -> Result<PersistedMessage, MessageRepoError> {
        validate_new_message_visibility(&msg)?;
        let error_projection = !stable && msg.message_type == bcs_domain::CHAT_ERROR_MESSAGE_TYPE;
        if error_projection && msg.run_id.is_empty() {
            return Err(MessageRepoError::StorageError("chat_error requires run_id".into()));
        }
        let mut sessions = self.sessions.write().await;
        if stable {
            if let Some(saved) = sessions.values().flat_map(|entry| &entry.messages).find(|saved| saved.message_id == message_id) {
                if saved.session_id != msg.session_id { return Err(MessageRepoError::StorageError("stable message id belongs to another Session".into())); }
                return Ok(saved.clone());
            }
        }
        let entry = sessions.entry(msg.session_id.clone()).or_default();

        if error_projection {
            if let Some(existing) = entry.messages.iter().find(|m| m.group_id == msg.group_id
                && m.sender_id == msg.sender_id && m.run_id == msg.run_id
                && m.message_type == bcs_domain::CHAT_ERROR_MESSAGE_TYPE) {
                return Ok(existing.clone());
            }
        }

        // Check idempotency
        if let Some(client_msg_id) = msg.client_msg_id.as_ref().filter(|_| !error_projection) {
            if let Some(existing) = entry.messages.iter().find(|m| {
                m.sender_id == msg.sender_id && m.client_msg_id.as_deref() == Some(client_msg_id)
            }) {
                return Ok(existing.clone());
            }
        }

        entry.seq += 1;
        let persisted = PersistedMessage {
            message_id,
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
    async fn resolve_history_window_start(&self, session: &str, anchor: i64, limit: u64) -> Result<i64, MessageRepoError> {
        let sessions = self.sessions.read().await;
        let mut window = super::mysql::history_window::HistoryWindow::new(anchor, limit);
        if let Some(entry) = sessions.get(session) {
            for message in entry.messages.iter().rev().filter(|m| m.session_seq <= anchor) {
                if let Some(start) = window.consume(message.session_seq, bcs_domain::state_machine_history::is_supplemental_history(message))? {
                    return Ok(start);
                }
            }
        }
        Ok(window.start())
    }
    async fn get_state_machine_messages_by_keys(&self, session_id: &str, keys: &[String]) -> Result<Vec<PersistedMessage>, MessageRepoError> {
        let sessions = self.sessions.read().await;
        let mut messages = std::collections::BTreeMap::new();
        for chunk in keys.chunks(200) {
            let keys: std::collections::HashSet<_> = chunk.iter().map(String::as_str).collect();
            for client_key in [false, true] {
                let found = sessions.get(session_id).into_iter().flat_map(|entry| &entry.messages)
                    .filter(|m| bcs_domain::state_machine_history::is_state_machine_history_type(&m.message_type)
                        && (if client_key { m.client_msg_id.as_deref() } else { Some(m.message_id.as_str()) })
                            .is_some_and(|key| keys.contains(key)))
                    .take(401).collect::<Vec<_>>();
                if found.len() > 400 {
                    return Err(MessageRepoError::StorageError("too many duplicate StateMachine producer keys".into()));
                }
                for message in found { messages.insert(message.message_id.clone(), message.clone()); }
            }
        }
        let mut messages: Vec<_> = messages.into_values().collect();
        messages.sort_by_key(|message| message.session_seq);
        Ok(messages)
    }

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
    async fn append_message(&self, msg: NewMessage) -> Result<PersistedMessage, MessageRepoError> {
        self.append_with_identity(msg, uuid::Uuid::new_v4().to_string(), false).await
    }
    async fn append_message_with_id(&self, message_id: String, msg: NewMessage) -> Result<PersistedMessage, MessageRepoError> {
        if message_id.is_empty() || message_id.len() > 256 {
            return Err(MessageRepoError::StorageError("invalid stable message id".into()));
        }
        self.append_with_identity(msg, message_id, true).await
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

    async fn list_state_machine_history(
        &self, group_id: &str, session_id: &str, human_view: Option<HumanMessageView>,
        before: Option<(u64, i64)>, limit: u32,
    ) -> Result<MessagePage, MessageRepoError> {
        if !(1..=1_000).contains(&limit) {
            return Err(MessageRepoError::StorageError("history limit must be between 1 and 1000".into()));
        }
        let sessions = self.sessions.read().await;
        let participant = human_view.as_ref().filter(|v| v.scope == bcs_domain::MessageViewScope::Participant);
        let mut messages: Vec<_> = sessions.get(session_id).into_iter().flat_map(|s| &s.messages)
            .filter(|m| m.group_id == group_id && bcs_domain::state_machine_history::is_state_machine_history_type(&m.message_type))
            .filter(|m| match participant {
                Some(view) => m.visibility_domain == Some(MessageVisibilityDomain::StateMachine) && view.allows(m),
                None => m.message_type != bcs_domain::STATE_MACHINE_HUMAN_INPUT_PROMPT_MESSAGE_TYPE,
            })
            .filter(|m| before.is_none_or(|cursor| (m.created_at, m.session_seq) < cursor))
            .collect();
        messages.sort_by(|a, b| (b.created_at, b.session_seq).cmp(&(a.created_at, a.session_seq)));
        let has_more = messages.len() > limit as usize;
        messages.truncate(limit as usize);
        let next_cursor = if has_more { messages.last().map(|m| (m.created_at, m.session_seq)) } else { None };
        Ok(MessagePage { messages: messages.into_iter().cloned().map(super::history_projection).collect(), has_more, next_cursor })
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

        if !query.human_view.as_ref().is_some_and(|view| view.scope == bcs_domain::MessageViewScope::Participant) {
            filtered.retain(|m| m.message_type != bcs_domain::STATE_MACHINE_HUMAN_INPUT_PROMPT_MESSAGE_TYPE);
        }

        // Apply cursor (timestamp-based)
        if let Some(cursor) = query.cursor {
            filtered.retain(|m| m.created_at < cursor);
        }

        // Apply visible_from_seq
        if let Some(visible_from) = query.visible_from_seq {
            filtered.retain(|m| m.session_seq >= visible_from || bcs_domain::state_machine_history::is_supplemental_history(m));
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
            MessageOwnerFilter::WorkerHistory(owner_bot_id) => {
                filtered.retain(|m| worker_history_visible(m, owner_bot_id));
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

        if !human_view.as_ref().is_some_and(|view| view.scope == bcs_domain::MessageViewScope::Participant) {
            filtered.retain(|m| m.message_type != bcs_domain::STATE_MACHINE_HUMAN_INPUT_PROMPT_MESSAGE_TYPE);
        }

        if let Some(visible_from) = visible_from_seq {
            filtered.retain(|m| m.session_seq >= visible_from || bcs_domain::state_machine_history::is_supplemental_history(m));
        }

        match &owner_filter {
            MessageOwnerFilter::Any => {}
            MessageOwnerFilter::IsNull => {
                filtered.retain(|m| m.owner_bot_id.is_none());
            }
            MessageOwnerFilter::Eq(owner) => {
                filtered.retain(|m| m.owner_bot_id.as_deref() == Some(owner.as_str()));
            }
            MessageOwnerFilter::WorkerHistory(owner) => {
                filtered.retain(|m| worker_history_visible(m, owner));
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

fn worker_history_visible(message: &PersistedMessage, worker_id: &str) -> bool {
    message.owner_bot_id.as_deref() == Some(worker_id)
        || (message.owner_bot_id.is_none()
            && message.sender_id == worker_id
            && message.message_type == "chat"
            && message.client_msg_id.as_deref().is_some_and(|id| id.starts_with("task-display:"))
            && message.visibility_domain == Some(MessageVisibilityDomain::ManagerWorker)
            && message.audience == Some(MessageAudience::FullOnly))
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
#[path = "memory_tests.rs"]
mod tests;

#[async_trait]
impl bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoPort for MemoryMessageRepo {
    async fn bounded_contexts(&self, carrier: &str, limit: usize) -> Result<bcs_service_api::port::repo::message_delivery::BoundDeliveryContexts, bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError> {
        let sessions = self.sessions.read().await;
        let mut rows: Vec<_> = sessions.values().flat_map(|s| &s.deliveries).filter(|d| d.bound_to_delivery_id.as_deref() == Some(carrier) && d.state.status == bcs_domain::message_delivery::MessageDeliveryStatus::Bound).collect();
        let total = rows.len() as u64;
        rows.sort_by(|a,b| (b.source_session_seq, &b.delivery_id).cmp(&(a.source_session_seq, &a.delivery_id)));
        let mut ordinary = 0;
        rows.retain(|d| {
            if d.semantic_projection_json.get("required_context").and_then(|v| v.as_bool()) == Some(true) { return true; }
            ordinary += 1;
            ordinary <= limit.min(1025)
        });
        Ok(bcs_service_api::port::repo::message_delivery::BoundDeliveryContexts { rows: rows.into_iter().cloned().collect(), total })
    }
    async fn lookup(&self, scope: bcs_service_api::port::repo::message_delivery::DeliveryLookup) -> Result<Vec<bcs_domain::message_delivery::PersistedMessageDelivery>, bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError> {
        use bcs_service_api::port::repo::message_delivery::DeliveryLookup as Q;
        let rows = self.list_deliveries(None).await?;
        Ok(rows.into_iter().filter(|d| match &scope {
            Q::Id(id) => d.delivery_id == *id,
            Q::Request(id) => d.request_id.as_ref() == Some(id),
            Q::Run { bot, alias } => d.target_bot_id == *bot && (d.run_id.as_ref() == Some(alias) || d.request_id.as_ref() == Some(alias) || d.transport_context_json.as_ref().and_then(|v| v.get("downstream_run_id")).and_then(|v| v.as_str()) == Some(alias.as_str())),
            Q::Bound(id) => d.bound_to_delivery_id.as_ref() == Some(id) && d.state.status == bcs_domain::message_delivery::MessageDeliveryStatus::Bound,
            Q::Lane { bot, session } | Q::LanePending { bot, session } => d.target_bot_id == *bot && d.session_id == *session && d.state.kind == bcs_domain::DeliveryType::Send && super::delivery::unfinished(d),
            Q::BotPending(bot) => d.target_bot_id == *bot && d.state.kind == bcs_domain::DeliveryType::Send && super::delivery::unfinished(d),
            Q::BotPendingContexts(bot) => d.target_bot_id == *bot && d.state.kind == bcs_domain::DeliveryType::Inject && d.state.status == bcs_domain::message_delivery::MessageDeliveryStatus::PendingContext,
            Q::LanePendingContexts { bot, session } => d.target_bot_id == *bot && d.session_id == *session && d.state.kind == bcs_domain::DeliveryType::Inject && d.state.status == bcs_domain::message_delivery::MessageDeliveryStatus::PendingContext,
            Q::LanePendingContextCarrier { bot, session, now_ms } => d.target_bot_id == *bot && d.session_id == *session && d.state.kind == bcs_domain::DeliveryType::Inject && d.state.status == bcs_domain::message_delivery::MessageDeliveryStatus::PendingContext && d.expire_at_ms.is_none_or(|at| at > *now_ms),
            Q::Message(id) => d.source_message_id == *id,
            Q::Successor { bot, session, after_seq, exclude, now_ms } => d.target_bot_id == *bot && d.session_id == *session && d.source_session_seq > *after_seq && d.delivery_id != *exclude && d.state.kind == bcs_domain::DeliveryType::Send && d.state.status == bcs_domain::message_delivery::MessageDeliveryStatus::Queued && !d.state.may_have_been_sent && d.expire_at_ms.is_none_or(|t| t > *now_ms),
        }).take(match scope { Q::BotPending(_) | Q::LanePending { .. } | Q::LanePendingContextCarrier { .. } | Q::Successor { .. } => 1, Q::BotPendingContexts(_) | Q::LanePendingContexts { .. } => 100, _ => usize::MAX }).collect())
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
        crate::memory_delivery_work::work_batch(self, kind, now, after, limit).await
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
        self.admit_batch(vec![command]).await?.pop()
            .ok_or_else(|| bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError::Storage("missing admission result".into()))
    }

    async fn admit_batch(&self, commands: Vec<bcs_service_api::port::repo::message_delivery::AdmitMessageDeliveries>) -> Result<Vec<bcs_service_api::port::repo::message_delivery::DeliveryAdmissionResult>, bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError> {
        self.delivery_transaction(Vec::new(), commands).await
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
        Ok(self.delivery_transaction(changes, reply.into_iter().collect()).await?.pop())
    }
}

impl MemoryMessageRepo {
    async fn delivery_transaction(&self,
        changes: Vec<bcs_service_api::port::repo::message_delivery::DeliveryCompareAndSet>,
        admission: Vec<bcs_service_api::port::repo::message_delivery::AdmitMessageDeliveries>,
    ) -> Result<Vec<bcs_service_api::port::repo::message_delivery::DeliveryAdmissionResult>,
        bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError> {
        use bcs_service_api::port::repo::message_delivery::{DeliveryAdmissionResult, MessageDeliveryRepoError as Error};
        if admission.windows(2).any(|w| w[0].message.session_id != w[1].message.session_id) { return Err(Error::Invalid("batch must share a session".into())); }
        let mut sessions = self.sessions.write().await;
        let mut staged = sessions.clone();
        let env = if self.env.is_empty() { "local" } else { &self.env };
        let mut rows: Vec<_> = staged.values().flat_map(|s| s.deliveries.iter().cloned()).collect();
        if changes.iter().any(|c| c.delivery.env != env) { return Err(Error::Invalid("environment mismatch".into())); }
        super::delivery::apply_changes(&mut rows, &changes)?;
        let mut result = Vec::new();
        let mut events = Vec::new();
        for command in admission {
            super::delivery::validate_admission(&command)?;
            let entry = staged.entry(command.message.session_id.clone()).or_default();
            if let Some(id) = command.message.client_msg_id.as_deref() {
                if let Some(message) = entry.messages.iter().find(|m| m.sender_id == command.message.sender_id && m.client_msg_id.as_deref() == Some(id)) {
                    if !changes.is_empty() { return Err(Error::Conflict); }
                    result.push(DeliveryAdmissionResult { message: message.clone(),
                        deliveries: rows.iter().filter(|d| d.source_message_id == message.message_id).cloned().collect(), duplicate: true });
                    continue;
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
            if let Some(event) = command.event.clone().or_else(|| command.display_message.as_ref().and_then(|d| d.event.clone())) { events.push(event); }
            result.push(DeliveryAdmissionResult { message, deliveries, duplicate: false });
        }
        for admission in &mut result {
            for delivery in &mut admission.deliveries {
                if let Some(current) = rows.iter().find(|d| d.delivery_id == delivery.delivery_id) { *delivery = current.clone(); }
            }
        }
        for session in staged.values_mut() { session.deliveries.clear(); }
        for row in rows { staged.entry(row.session_id.clone()).or_default().deliveries.push(row); }
        if !events.is_empty() {
            let store = self.event_store.as_ref().ok_or_else(|| Error::Storage("event store is not configured".into()))?;
            store.commit_business_mutations(&events, || {
                *sessions = staged;
                Ok(())
            }).await.map_err(|e| Error::Storage(e.to_string()))?;
        } else {
            *sessions = staged;
        }
        Ok(result)
    }
}
