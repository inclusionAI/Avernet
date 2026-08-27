//! Direct Chat async run state-machine engine.
//!
//! `ChatRunStore` owns the run lifecycle rules (terminal guard, allowed
//! transitions, version bumps, content truncation) and delegates persistence
//! + compare-and-set + scan to a [`ChatRunRepoPort`] impl. The default
//! constructor wires [`MemoryChatRunRepo`] (behavior-equivalent to the
//! pre-#1546 in-process store); a [`ChatRunStore::with_repo`] constructor lets
//! bootstrap select a persistent (MySQL + Redis) implementation.
//!
//! The node-local `Notify` registry is a latency optimization for `wait_update`
//! only — correctness comes from re-reading the repo. See
//! `docs/superpowers/specs/2026-08-27-bcs-run-governance-design.md`.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use tokio::sync::{Notify, RwLock};
use tracing::error;

use bcs_chat_run_store::MemoryChatRunRepo;
use bcs_service_api::port::repo::{CasOutcome, ChatRunRepoError, ChatRunRepoPort};
use bcs_service_api::{ChatRunMetricCount, DirectChatClientKind, DirectChatRunReason};

pub use bcs_service_api::port::repo::{
    ChatRunCompletionPolicy, ChatRunRecord, ChatRunState, MAX_CONTENT_BYTES,
};

/// Run-specific completion policy.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ChatRunStoreError {
    CapacityExceeded { max_entries: usize },
    DuplicateRunId { run_id: String },
    Backend(String),
}

impl ChatRunStoreError {
    pub(crate) fn direct_chat_reason(&self) -> DirectChatRunReason {
        match self {
            ChatRunStoreError::CapacityExceeded { .. } => DirectChatRunReason::StoreCapacity,
            ChatRunStoreError::DuplicateRunId { .. } => DirectChatRunReason::InternalError,
            ChatRunStoreError::Backend(_) => DirectChatRunReason::InternalError,
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
            ChatRunStoreError::Backend(msg) => {
                write!(f, "chat run store backend error: {msg}")
            }
        }
    }
}

impl std::error::Error for ChatRunStoreError {}

pub struct ChatRunStore {
    repo: Arc<dyn ChatRunRepoPort>,
    #[allow(dead_code)]
    notifiers: Arc<RwLock<HashMap<String, Arc<Notify>>>>,
}

impl std::fmt::Debug for ChatRunStore {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ChatRunStore").finish_non_exhaustive()
    }
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
        Self::with_repo(Arc::new(MemoryChatRunRepo::with_capacity(max_entries)))
    }

    /// Build the engine over an explicit repository implementation. Used by
    /// bootstrap to select a persistent (MySQL + Redis) store via config.
    pub fn with_repo(repo: Arc<dyn ChatRunRepoPort>) -> Self {
        Self {
            repo,
            notifiers: Arc::new(RwLock::new(HashMap::new())),
        }
    }

    async fn notifier(&self, run_id: &str) -> Arc<Notify> {
        if let Some(existing) = self.notifiers.read().await.get(run_id) {
            return existing.clone();
        }
        let mut guard = self.notifiers.write().await;
        guard
            .entry(run_id.to_string())
            .or_insert_with(|| Arc::new(Notify::new()))
            .clone()
    }

    async fn notify_waiters(&self, run_id: &str) {
        if let Some(notify) = self.notifiers.read().await.get(run_id) {
            notify.notify_waiters();
        }
    }

    async fn drop_notifier(&self, run_id: &str) {
        self.notifiers.write().await.remove(run_id);
    }

    pub async fn create(&self, record: ChatRunRecord) -> Result<(), ChatRunStoreError> {
        self.repo.create(record).await.map_err(|err| match err {
            ChatRunRepoError::Capacity { max_entries } => {
                ChatRunStoreError::CapacityExceeded { max_entries }
            }
            ChatRunRepoError::DuplicateRunId(run_id) => ChatRunStoreError::DuplicateRunId { run_id },
            other => ChatRunStoreError::Backend(other.to_string()),
        })
    }

    pub async fn get(&self, run_id: &str) -> Option<ChatRunRecord> {
        match self.repo.get(run_id).await {
            Ok(option) => option,
            Err(err) => {
                error!(run_id, error = %err, "chat run get failed");
                None
            }
        }
    }

    pub(crate) async fn metric_counts(&self) -> Vec<ChatRunMetricCount> {
        self.repo
            .metric_counts()
            .await
            .unwrap_or_else(|err| {
                error!(error = %err, "chat run metric_counts failed");
                Vec::new()
            })
    }

    /// Apply a state transition under version CAS. Returns false on conflict,
    /// terminal, missing, or backend error (logged). Single attempt — mirrors
    /// the pre-refactor single-lock mutate semantics.
    async fn apply_state_cas(
        &self,
        run_id: &str,
        expected_version: u64,
        new: ChatRunRecord,
    ) -> bool {
        match self.repo.compare_and_set_state(run_id, expected_version, new).await {
            Ok(CasOutcome::Applied(_)) => {
                self.notify_waiters(run_id).await;
                true
            }
            Ok(_) => false,
            Err(err) => {
                error!(run_id, error = %err, "chat run state cas failed");
                false
            }
        }
    }

    /// Apply a terminal transition under version CAS. Clears the node-local
    /// notifier since the run will not change again.
    async fn apply_terminal_cas(
        &self,
        run_id: &str,
        expected_version: u64,
        new: ChatRunRecord,
    ) -> bool {
        match self
            .repo
            .compare_and_set_terminal(run_id, expected_version, new)
            .await
        {
            Ok(CasOutcome::Applied(_)) => {
                self.notify_waiters(run_id).await;
                self.drop_notifier(run_id).await;
                true
            }
            Ok(_) => false,
            Err(err) => {
                error!(run_id, error = %err, "chat run terminal cas failed");
                false
            }
        }
    }

    pub async fn mark_running(&self, run_id: &str) -> bool {
        let Some(current) = self.get(run_id).await else {
            return false;
        };
        if current.state.is_terminal() || current.state != ChatRunState::Pending {
            return false;
        }
        let mut new = current.clone();
        new.state = ChatRunState::Running;
        self.apply_state_cas(run_id, current.version, new).await
    }

    pub async fn mark_submitted(&self, run_id: &str) -> bool {
        let Some(current) = self.get(run_id).await else {
            return false;
        };
        if current.state.is_terminal() || current.state != ChatRunState::Pending {
            return false;
        }
        let mut new = current.clone();
        new.state = ChatRunState::Submitted;
        self.apply_state_cas(run_id, current.version, new).await
    }

    pub async fn mark_detach_delivery_acknowledged(&self, run_id: &str) -> bool {
        let Some(current) = self.get(run_id).await else {
            return false;
        };
        if current.state.is_terminal() {
            return false;
        }
        if current.completion_policy != ChatRunCompletionPolicy::DetachDeliveryAck {
            return false;
        }
        let mut new = current.clone();
        let mut changed = false;
        if matches!(new.state, ChatRunState::Pending | ChatRunState::Submitted) {
            new.state = ChatRunState::Running;
            changed = true;
        }
        if new.delivery_ack_at_ms.is_none() {
            new.delivery_ack_at_ms = Some(now_ms());
            changed = true;
        }
        if !changed {
            return false;
        }
        self.apply_state_cas(run_id, current.version, new).await
    }

    pub async fn append_delta(&self, run_id: &str, chunk: &str) -> bool {
        if chunk.is_empty() {
            return false;
        }
        let Some(current) = self.get(run_id).await else {
            return false;
        };
        if current.state.is_terminal() {
            return false;
        }

        let mut accumulated = current.accumulated_content.clone();
        let mut truncated = current.content_truncated;
        let remaining = MAX_CONTENT_BYTES.saturating_sub(accumulated.len());
        if remaining == 0 {
            truncated = true;
        } else if chunk.len() <= remaining {
            accumulated.push_str(chunk);
        } else {
            let mut boundary = remaining;
            while boundary > 0 && !chunk.is_char_boundary(boundary) {
                boundary -= 1;
            }
            accumulated.push_str(&chunk[..boundary]);
            truncated = true;
        }

        match self
            .repo
            .append_streaming_content(run_id, current.version, accumulated, truncated)
            .await
        {
            Ok(true) => {
                self.notify_waiters(run_id).await;
                true
            }
            Ok(false) => false,
            Err(err) => {
                error!(run_id, error = %err, "chat run append_delta failed");
                false
            }
        }
    }

    pub async fn replace_content(&self, run_id: &str, content: &str) -> bool {
        let Some(current) = self.get(run_id).await else {
            return false;
        };
        if current.state.is_terminal() {
            return false;
        }
        let was_pending = current.state == ChatRunState::Pending;

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

        let changed =
            current.accumulated_content != next || current.content_truncated != truncated || was_pending;
        if !changed {
            return false;
        }

        match self
            .repo
            .append_streaming_content(run_id, current.version, next, truncated)
            .await
        {
            Ok(true) => {
                self.notify_waiters(run_id).await;
                true
            }
            Ok(false) => false,
            Err(err) => {
                error!(run_id, error = %err, "chat run replace_content failed");
                false
            }
        }
    }

    pub async fn mark_completed(&self, run_id: &str, final_text: Option<&str>) -> bool {
        let Some(current) = self.get(run_id).await else {
            return false;
        };
        if current.state.is_terminal() {
            return false;
        }
        let mut new = current.clone();
        if let Some(text) = final_text {
            if !text.is_empty() && new.accumulated_content.is_empty() {
                new.accumulated_content.push_str(text);
            }
        }
        new.state = ChatRunState::Completed;
        new.completed_at_ms = Some(now_ms());
        self.apply_terminal_cas(run_id, current.version, new).await
    }

    pub async fn mark_failed(&self, run_id: &str, error: impl Into<String>) -> bool {
        let message = error.into();
        let Some(current) = self.get(run_id).await else {
            return false;
        };
        if current.state.is_terminal() {
            return false;
        }
        let mut new = current.clone();
        new.state = ChatRunState::Failed;
        new.error_message = Some(message);
        new.completed_at_ms = Some(now_ms());
        self.apply_terminal_cas(run_id, current.version, new).await
    }

    pub async fn mark_cancelled(&self, run_id: &str) -> bool {
        let Some(current) = self.get(run_id).await else {
            return false;
        };
        if current.state.is_terminal() {
            return false;
        }
        let mut new = current.clone();
        new.state = ChatRunState::Cancelled;
        new.completed_at_ms = Some(now_ms());
        self.apply_terminal_cas(run_id, current.version, new).await
    }

    pub async fn wait_update(
        &self,
        run_id: &str,
        since_version: u64,
        timeout: Duration,
    ) -> Option<ChatRunRecord> {
        let notify = self.notifier(run_id).await;
        let deadline = tokio::time::Instant::now() + timeout;
        loop {
            let current = self.get(run_id).await?;
            if current.version > since_version || current.state.is_terminal() {
                return Some(current);
            }
            let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
            if remaining.is_zero() {
                return self.get(run_id).await;
            }
            let notified = notify.notified();
            tokio::pin!(notified);
            match tokio::time::timeout(remaining, notified).await {
                Ok(()) => continue,
                Err(_) => return self.get(run_id).await,
            }
        }
    }

    /// Mark an overdue non-terminal run failed, retrying through version
    /// conflicts so a concurrent delta on another replica cannot foil the
    /// timeout transition. Returns true only if this call applied the failure.
    async fn force_fail(&self, run_id: &str, error: &str) -> bool {
        for _ in 0..4 {
            let Some(current) = self.get(run_id).await else {
                return false;
            };
            if current.state.is_terminal() {
                return false;
            }
            let mut new = current.clone();
            new.state = ChatRunState::Failed;
            new.error_message = Some(error.to_string());
            new.completed_at_ms = Some(now_ms());
            match self
                .repo
                .compare_and_set_terminal(run_id, current.version, new)
                .await
            {
                Ok(CasOutcome::Applied(_)) => {
                    self.notify_waiters(run_id).await;
                    self.drop_notifier(run_id).await;
                    return true;
                }
                Ok(_) => continue,
                Err(err) => {
                    error!(run_id, error = %err, "chat run force_fail failed");
                    return false;
                }
            }
        }
        false
    }

    pub async fn cleanup_expired(
        &self,
        now_ms_v: u64,
        retention_ms: u64,
    ) -> (
        Vec<(String, DirectChatClientKind)>,
        Vec<(String, DirectChatClientKind)>,
    ) {
        let mut expired = Vec::new();
        let active = match self.repo.list_active(now_ms_v).await {
            Ok(active) => active,
            Err(err) => {
                error!(error = %err, "chat run list_active failed");
                Vec::new()
            }
        };
        for record in active {
            if self.force_fail(&record.run_id, "timeout").await {
                expired.push((
                    record.run_id.clone(),
                    direct_chat_client_kind(record.client.as_deref()),
                ));
            }
        }

        let dropped = match self.repo.delete_expired_terminal(now_ms_v, retention_ms).await {
            Ok(records) => records
                .into_iter()
                .map(|record| {
                    (
                        record.run_id.clone(),
                        direct_chat_client_kind(record.client.as_deref()),
                    )
                })
                .collect(),
            Err(err) => {
                error!(error = %err, "chat run delete_expired_terminal failed");
                Vec::new()
            }
        };

        (expired, dropped)
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