//! In-process `ChatRunRepoPort` implementation.
//!
//! Behavior-equivalent to the pre-#1546 `ChatRunStore` backing: a
//! `RwLock<HashMap<run_id, ChatRunRecord>>` with an entry cap. The run state
//! machine (terminal guard, transition rules) is owned by the `ChatRunStore`
//! engine, so this impl only does persistence + compare-and-set + scan. It
//! never rejects a transition by state-rules — only by version/terminal guard,
//! which is the persistence-level CAS the engine relies on.

use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use tokio::sync::RwLock;

use bcs_service_api::port::repo::{
    CasOutcome, ChatRunRecord, ChatRunRepoError, ChatRunRepoPort, ChatRunState,
};
use bcs_service_api::{ChatRunMetricCount, DirectChatClientKind, DirectChatRunState};

/// In-process direct chat run repository.
#[derive(Debug, Default, Clone)]
pub struct MemoryChatRunRepo {
    inner: Arc<RwLock<Inner>>,
}

#[derive(Debug, Default)]
struct Inner {
    runs: HashMap<String, ChatRunRecord>,
    cap: usize,
}

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

fn metric_state(state: ChatRunState) -> DirectChatRunState {
    match state {
        ChatRunState::Pending => DirectChatRunState::Pending,
        ChatRunState::Submitted => DirectChatRunState::Submitted,
        ChatRunState::Running => DirectChatRunState::Running,
        ChatRunState::Completed => DirectChatRunState::Completed,
        ChatRunState::Failed => DirectChatRunState::Failed,
        ChatRunState::Cancelled => DirectChatRunState::Cancelled,
    }
}

fn client_kind(client: Option<&str>) -> DirectChatClientKind {
    match client.map(str::trim).filter(|s| !s.is_empty()) {
        None => DirectChatClientKind::None,
        Some("http-chat") => DirectChatClientKind::HttpChat,
        Some("http-chat-async") => DirectChatClientKind::HttpChatAsync,
        Some(raw) if raw.starts_with("bcs-cli") => DirectChatClientKind::BcsCli,
        Some(_) => DirectChatClientKind::Unknown,
    }
}

impl MemoryChatRunRepo {
    pub fn new() -> Self {
        Self::with_capacity(100_000)
    }

    pub fn with_capacity(cap: usize) -> Self {
        Self {
            inner: Arc::new(RwLock::new(Inner {
                runs: HashMap::new(),
                cap,
            })),
        }
    }
}

#[async_trait]
impl ChatRunRepoPort for MemoryChatRunRepo {
    async fn create(&self, record: ChatRunRecord) -> Result<(), ChatRunRepoError> {
        let mut guard = self.inner.write().await;
        if guard.cap > 0 && guard.runs.len() >= guard.cap {
            return Err(ChatRunRepoError::Capacity {
                max_entries: guard.cap,
            });
        }
        if guard.runs.contains_key(&record.run_id) {
            return Err(ChatRunRepoError::DuplicateRunId(record.run_id.clone()));
        }
        guard.runs.insert(record.run_id.clone(), record);
        Ok(())
    }

    async fn get(&self, run_id: &str) -> Result<Option<ChatRunRecord>, ChatRunRepoError> {
        Ok(self.inner.read().await.runs.get(run_id).cloned())
    }

    async fn compare_and_set_state(
        &self,
        run_id: &str,
        expected_version: u64,
        mut new: ChatRunRecord,
    ) -> Result<CasOutcome, ChatRunRepoError> {
        let mut guard = self.inner.write().await;
        let Some(current) = guard.runs.get(run_id) else {
            return Ok(CasOutcome::Conflict(None));
        };
        if current.state.is_terminal() {
            return Ok(CasOutcome::Terminal(Some(current.clone())));
        }
        if current.version != expected_version {
            return Ok(CasOutcome::Conflict(Some(current.clone())));
        }
        new.version = current.version + 1;
        new.updated_at_ms = now_ms();
        guard.runs.insert(run_id.to_string(), new.clone());
        Ok(CasOutcome::Applied(new))
    }

    async fn compare_and_set_terminal(
        &self,
        run_id: &str,
        expected_version: u64,
        mut new: ChatRunRecord,
    ) -> Result<CasOutcome, ChatRunRepoError> {
        let mut guard = self.inner.write().await;
        let Some(current) = guard.runs.get(run_id) else {
            return Ok(CasOutcome::Conflict(None));
        };
        if current.state.is_terminal() {
            return Ok(CasOutcome::Terminal(Some(current.clone())));
        }
        if current.version != expected_version {
            return Ok(CasOutcome::Conflict(Some(current.clone())));
        }
        new.version = current.version + 1;
        new.updated_at_ms = now_ms();
        if new.completed_at_ms.is_none() {
            new.completed_at_ms = Some(now_ms());
        }
        guard.runs.insert(run_id.to_string(), new.clone());
        Ok(CasOutcome::Applied(new))
    }

    async fn append_streaming_content(
        &self,
        run_id: &str,
        expected_version: u64,
        accumulated: String,
        truncated: bool,
    ) -> Result<bool, ChatRunRepoError> {
        let mut guard = self.inner.write().await;
        let Some(current) = guard.runs.get_mut(run_id) else {
            return Ok(false);
        };
        if current.state.is_terminal() || current.version != expected_version {
            return Ok(false);
        }
        if current.state == ChatRunState::Pending {
            current.state = ChatRunState::Running;
        }
        current.accumulated_content = accumulated;
        current.content_truncated = truncated;
        current.version += 1;
        current.updated_at_ms = now_ms();
        Ok(true)
    }

    async fn list_active(&self, now_ms: u64) -> Result<Vec<ChatRunRecord>, ChatRunRepoError> {
        Ok(self
            .inner
            .read()
            .await
            .runs
            .values()
            .filter(|record| !record.state.is_terminal() && record.expires_at_ms < now_ms)
            .cloned()
            .collect())
    }

    async fn delete_expired_terminal(
        &self,
        now_ms: u64,
        retention_ms: u64,
    ) -> Result<Vec<ChatRunRecord>, ChatRunRepoError> {
        let mut guard = self.inner.write().await;
        let drop_ids: Vec<String> = guard
            .runs
            .iter()
            .filter(|(_, record)| {
                record.state.is_terminal()
                    && record
                        .completed_at_ms
                        .map(|completed| now_ms.saturating_sub(completed) >= retention_ms)
                        .unwrap_or(false)
            })
            .map(|(key, _)| key.clone())
            .collect();
        let mut dropped = Vec::with_capacity(drop_ids.len());
        for key in &drop_ids {
            if let Some(record) = guard.runs.remove(key) {
                dropped.push(record);
            }
        }
        Ok(dropped)
    }

    async fn metric_counts(&self) -> Result<Vec<ChatRunMetricCount>, ChatRunRepoError> {
        let mut counts: Vec<ChatRunMetricCount> = Vec::new();
        for record in self.inner.read().await.runs.values() {
            // Only active (non-terminal) runs belong on the gauge; terminal
            // totals come from the lifecycle counter.
            if record.state.is_terminal() {
                continue;
            }
            let state = metric_state(record.state);
            let client_kind = client_kind(record.client.as_deref());
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
        Ok(counts)
    }
}