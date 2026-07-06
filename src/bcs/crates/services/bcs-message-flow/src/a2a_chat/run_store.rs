use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use serde::Serialize;
use tokio::sync::{Notify, RwLock};

use bcs_service_api::{
    ChatResponseMode, ChatRunMetricCount, DirectChatClientKind, DirectChatRunReason,
    DirectChatRunState,
};

pub const MAX_CONTENT_BYTES: usize = 1_024 * 1_024;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum ChatRunState {
    Pending,
    Submitted,
    Running,
    Completed,
    Failed,
    Cancelled,
}

impl ChatRunState {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Pending => "pending",
            Self::Submitted => "submitted",
            Self::Running => "running",
            Self::Completed => "completed",
            Self::Failed => "failed",
            Self::Cancelled => "cancelled",
        }
    }

    pub fn is_terminal(self) -> bool {
        matches!(self, Self::Completed | Self::Failed | Self::Cancelled)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ChatRunCompletionPolicy {
    WaitForFinal,
    DetachDeliveryAck,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ChatRunStoreError {
    CapacityExceeded { max_entries: usize },
    DuplicateRunId { run_id: String },
}

impl ChatRunStoreError {
    pub(crate) fn direct_chat_reason(&self) -> DirectChatRunReason {
        match self {
            ChatRunStoreError::CapacityExceeded { .. } => DirectChatRunReason::StoreCapacity,
            ChatRunStoreError::DuplicateRunId { .. } => DirectChatRunReason::InternalError,
        }
    }
}

impl std::fmt::Display for ChatRunStoreError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ChatRunStoreError::CapacityExceeded { max_entries } => {
                write!(f, "chat run store at capacity ({max_entries} entries)")
            }
            ChatRunStoreError::DuplicateRunId { run_id } => {
                write!(f, "run_id {run_id} already exists")
            }
        }
    }
}

impl std::error::Error for ChatRunStoreError {}

#[derive(Debug, Clone, Serialize)]
pub struct ChatRunRecord {
    pub run_id: String,
    pub bot_uuid: String,
    pub from_bot_id: String,
    pub session_key: String,
    pub state: ChatRunState,
    pub accumulated_content: String,
    pub error_message: Option<String>,
    pub created_at_ms: u64,
    pub updated_at_ms: u64,
    pub completed_at_ms: Option<u64>,
    pub expires_at_ms: u64,
    pub version: u64,
    pub content_truncated: bool,
    pub client: Option<String>,
    pub response_mode: ChatResponseMode,
    #[serde(skip_serializing)]
    pub completion_policy: ChatRunCompletionPolicy,
    #[serde(skip_serializing)]
    pub delivery_ack_at_ms: Option<u64>,
}

impl ChatRunRecord {
    pub fn new(
        run_id: String,
        bot_uuid: String,
        from_bot_id: String,
        session_key: String,
        now_ms: u64,
        expires_at_ms: u64,
        client: Option<String>,
        response_mode: ChatResponseMode,
        completion_policy: ChatRunCompletionPolicy,
    ) -> Self {
        Self {
            run_id,
            bot_uuid,
            from_bot_id,
            session_key,
            state: ChatRunState::Pending,
            accumulated_content: String::new(),
            error_message: None,
            created_at_ms: now_ms,
            updated_at_ms: now_ms,
            completed_at_ms: None,
            expires_at_ms,
            version: 1,
            content_truncated: false,
            client,
            response_mode,
            completion_policy,
            delivery_ack_at_ms: None,
        }
    }
}

#[derive(Debug)]
struct Slot {
    record: RwLock<ChatRunRecord>,
    notify: Notify,
}

#[derive(Debug)]
pub struct ChatRunStore {
    slots: RwLock<HashMap<String, Arc<Slot>>>,
    max_entries: usize,
}

impl Default for ChatRunStore {
    fn default() -> Self {
        Self::new()
    }
}

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

impl ChatRunStore {
    pub fn new() -> Self {
        Self::with_capacity(100_000)
    }

    pub fn with_capacity(max_entries: usize) -> Self {
        Self {
            slots: RwLock::new(HashMap::new()),
            max_entries,
        }
    }

    pub async fn create(&self, record: ChatRunRecord) -> Result<(), ChatRunStoreError> {
        let mut slots = self.slots.write().await;
        if self.max_entries > 0 && slots.len() >= self.max_entries {
            return Err(ChatRunStoreError::CapacityExceeded {
                max_entries: self.max_entries,
            });
        }
        if slots.contains_key(&record.run_id) {
            return Err(ChatRunStoreError::DuplicateRunId {
                run_id: record.run_id,
            });
        }
        slots.insert(record.run_id.clone(), Arc::new(Slot {
            record: RwLock::new(record),
            notify: Notify::new(),
        }));
        Ok(())
    }

    async fn with_slot(&self, run_id: &str) -> Option<Arc<Slot>> {
        self.slots.read().await.get(run_id).cloned()
    }

    pub async fn get(&self, run_id: &str) -> Option<ChatRunRecord> {
        let slot = self.with_slot(run_id).await?;
        Some(slot.record.read().await.clone())
    }

    pub(crate) async fn metric_counts(&self) -> Vec<ChatRunMetricCount> {
        let slots_snapshot: Vec<Arc<Slot>> = {
            let slots = self.slots.read().await;
            slots.values().cloned().collect()
        };
        let mut counts: Vec<ChatRunMetricCount> = Vec::new();
        for slot in slots_snapshot {
            let rec = slot.record.read().await;
            let state = direct_chat_metric_state(rec.state);
            let client_kind = direct_chat_client_kind(rec.client.as_deref());
            if let Some(existing) = counts
                .iter_mut()
                .find(|count| count.state == state && count.client_kind == client_kind)
            {
                existing.count = existing.count.saturating_add(1);
            } else {
                counts.push(ChatRunMetricCount {
                    state,
                    client_kind,
                    count: 1,
                });
            }
        }
        counts
    }

    pub(crate) async fn metric_client_kinds(&self) -> HashMap<String, DirectChatClientKind> {
        let slots_snapshot: Vec<(String, Arc<Slot>)> = {
            let slots = self.slots.read().await;
            slots.iter().map(|(k, v)| (k.clone(), v.clone())).collect()
        };
        let mut client_kinds = HashMap::new();
        for (run_id, slot) in slots_snapshot {
            let rec = slot.record.read().await;
            client_kinds.insert(run_id, direct_chat_client_kind(rec.client.as_deref()));
        }
        client_kinds
    }

    async fn mutate(
        &self,
        run_id: &str,
        f: impl FnOnce(&mut ChatRunRecord) -> bool,
    ) -> bool {
        let Some(slot) = self.with_slot(run_id).await else {
            return false;
        };
        let mut rec = slot.record.write().await;
        if rec.state.is_terminal() {
            return false;
        }
        let changed = f(&mut rec);
        if changed {
            rec.version = rec.version.saturating_add(1);
            rec.updated_at_ms = now_ms();
            slot.notify.notify_waiters();
        }
        changed
    }

    pub async fn mark_running(&self, run_id: &str) -> bool {
        self.mutate(run_id, |rec| {
            if rec.state == ChatRunState::Pending {
                rec.state = ChatRunState::Running;
                true
            } else {
                false
            }
        })
        .await
    }

    pub async fn mark_submitted(&self, run_id: &str) -> bool {
        self.mutate(run_id, |rec| {
            if rec.state == ChatRunState::Pending {
                rec.state = ChatRunState::Submitted;
                true
            } else {
                false
            }
        })
        .await
    }

    pub async fn mark_detach_delivery_acknowledged(&self, run_id: &str) -> bool {
        self.mutate(run_id, |rec| {
            if rec.completion_policy != ChatRunCompletionPolicy::DetachDeliveryAck {
                return false;
            }
            let mut changed = false;
            if matches!(rec.state, ChatRunState::Pending | ChatRunState::Submitted) {
                rec.state = ChatRunState::Running;
                changed = true;
            }
            if rec.delivery_ack_at_ms.is_none() {
                rec.delivery_ack_at_ms = Some(now_ms());
                changed = true;
            }
            changed
        })
        .await
    }

    pub async fn append_delta(&self, run_id: &str, chunk: &str) -> bool {
        if chunk.is_empty() {
            return false;
        }
        self.mutate(run_id, |rec| {
            if rec.state == ChatRunState::Pending {
                rec.state = ChatRunState::Running;
            }
            let remaining = MAX_CONTENT_BYTES.saturating_sub(rec.accumulated_content.len());
            if remaining == 0 {
                rec.content_truncated = true;
                return true;
            }
            if chunk.len() <= remaining {
                rec.accumulated_content.push_str(chunk);
            } else {
                let mut boundary = remaining;
                while boundary > 0 && !chunk.is_char_boundary(boundary) {
                    boundary -= 1;
                }
                rec.accumulated_content.push_str(&chunk[..boundary]);
                rec.content_truncated = true;
            }
            true
        })
        .await
    }

    pub async fn replace_content(&self, run_id: &str, content: &str) -> bool {
        self.mutate(run_id, |rec| {
            let was_pending = rec.state == ChatRunState::Pending;
            if was_pending {
                rec.state = ChatRunState::Running;
            }

            let mut next = String::new();
            let mut truncated = false;
            if content.len() <= MAX_CONTENT_BYTES {
                next.push_str(content);
            } else {
                let mut boundary = MAX_CONTENT_BYTES;
                while boundary > 0 && !content.is_char_boundary(boundary) {
                    boundary -= 1;
                }
                next.push_str(&content[..boundary]);
                truncated = true;
            }

            let changed = rec.accumulated_content != next || rec.content_truncated != truncated;
            rec.accumulated_content = next;
            rec.content_truncated = truncated;
            changed || was_pending
        })
        .await
    }

    pub async fn mark_completed(&self, run_id: &str, final_text: Option<&str>) -> bool {
        self.mutate(run_id, |rec| {
            if let Some(text) = final_text {
                if !text.is_empty() && rec.accumulated_content.is_empty() {
                    rec.accumulated_content.push_str(text);
                }
            }
            rec.state = ChatRunState::Completed;
            rec.completed_at_ms = Some(now_ms());
            true
        })
        .await
    }

    pub async fn mark_failed(&self, run_id: &str, error: impl Into<String>) -> bool {
        let error = error.into();
        self.mutate(run_id, |rec| {
            rec.state = ChatRunState::Failed;
            rec.error_message = Some(error);
            rec.completed_at_ms = Some(now_ms());
            true
        })
        .await
    }

    pub async fn mark_cancelled(&self, run_id: &str) -> bool {
        self.mutate(run_id, |rec| {
            rec.state = ChatRunState::Cancelled;
            rec.completed_at_ms = Some(now_ms());
            true
        })
        .await
    }

    pub async fn wait_update(
        &self,
        run_id: &str,
        since_version: u64,
        timeout: Duration,
    ) -> Option<ChatRunRecord> {
        let slot = self.with_slot(run_id).await?;
        let deadline = tokio::time::Instant::now() + timeout;
        loop {
            {
                let rec = slot.record.read().await;
                if rec.version > since_version || rec.state.is_terminal() {
                    return Some(rec.clone());
                }
            }
            let notified = slot.notify.notified();
            let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
            if remaining.is_zero() {
                return Some(slot.record.read().await.clone());
            }
            if tokio::time::timeout(remaining, notified).await.is_err() {
                return Some(slot.record.read().await.clone());
            }
        }
    }

    pub async fn cleanup_expired(
        &self,
        now_ms_v: u64,
        retention_ms: u64,
    ) -> (Vec<String>, Vec<String>) {
        let slots_snapshot: Vec<(String, Arc<Slot>)> = {
            let slots = self.slots.read().await;
            slots.iter().map(|(k, v)| (k.clone(), v.clone())).collect()
        };

        let mut to_fail = Vec::new();
        let mut to_drop = Vec::new();
        for (run_id, slot) in &slots_snapshot {
            let rec = slot.record.read().await;
            if rec.state.is_terminal() {
                if let Some(completed) = rec.completed_at_ms {
                    if now_ms_v.saturating_sub(completed) >= retention_ms {
                        to_drop.push(run_id.clone());
                    }
                }
            } else if rec.completion_policy == ChatRunCompletionPolicy::DetachDeliveryAck
                && rec.state == ChatRunState::Running
            {
                if let Some(ack_at) = rec.delivery_ack_at_ms {
                    if now_ms_v.saturating_sub(ack_at) >= retention_ms {
                        to_drop.push(run_id.clone());
                    }
                }
            } else if now_ms_v >= rec.expires_at_ms {
                to_fail.push(run_id.clone());
            }
        }

        let mut expired = Vec::new();
        for run_id in to_fail {
            if self.mark_failed(&run_id, "timeout").await {
                expired.push(run_id);
            }
        }

        if !to_drop.is_empty() {
            let mut slots = self.slots.write().await;
            for run_id in &to_drop {
                slots.remove(run_id);
            }
        }

        (expired, to_drop)
    }
}

fn direct_chat_metric_state(state: ChatRunState) -> DirectChatRunState {
    match state {
        ChatRunState::Pending => DirectChatRunState::Pending,
        ChatRunState::Submitted => DirectChatRunState::Submitted,
        ChatRunState::Running => DirectChatRunState::Running,
        ChatRunState::Completed => DirectChatRunState::Completed,
        ChatRunState::Failed => DirectChatRunState::Failed,
        ChatRunState::Cancelled => DirectChatRunState::Cancelled,
    }
}

pub(crate) fn direct_chat_client_kind(client: Option<&str>) -> DirectChatClientKind {
    match client.map(str::trim).filter(|s| !s.is_empty()) {
        None => DirectChatClientKind::None,
        Some("http-chat") => DirectChatClientKind::HttpChat,
        Some("http-chat-async") => DirectChatClientKind::HttpChatAsync,
        Some(raw) if raw.starts_with("bcs-cli") => DirectChatClientKind::BcsCli,
        Some(_) => DirectChatClientKind::Unknown,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn direct_chat_client_kind_uses_closed_low_cardinality_mapping() {
        assert_eq!(direct_chat_client_kind(None), DirectChatClientKind::None);
        assert_eq!(direct_chat_client_kind(Some("   ")), DirectChatClientKind::None);
        assert_eq!(
            direct_chat_client_kind(Some("http-chat")),
            DirectChatClientKind::HttpChat
        );
        assert_eq!(
            direct_chat_client_kind(Some("http-chat-async")),
            DirectChatClientKind::HttpChatAsync
        );
        assert_eq!(
            direct_chat_client_kind(Some("bcs-cli/0.1")),
            DirectChatClientKind::BcsCli
        );
        assert_eq!(
            direct_chat_client_kind(Some("custom-client")),
            DirectChatClientKind::Unknown
        );
    }
}
