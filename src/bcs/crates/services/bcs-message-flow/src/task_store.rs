use std::collections::HashMap;

use bcs_domain::LedgerSummary;
use bcs_service_api::ChatResponseMode;
use tokio::sync::RwLock;

pub const TASK_TTL_MS: u64 = 5 * 60 * 1000;

#[derive(Debug, Clone)]
pub struct TaskEntry {
    pub task_id: String,
    pub group_id: String,
    pub session_id: Option<String>,
    pub driver_bot: String,
    pub target_bot: String,
    pub target_bot_name: Option<String>,
    pub created_at_ms: u64,
    pub response_mode: ChatResponseMode,
    pub status: TaskLedgerStatus,
    pub response_content: String,
    response_full_content: String,
    response_strip_prefix: String,
    response_seen_tool_call: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TaskLedgerStatus {
    Dispatched,
    Replied,
    Failed,
    TimedOut,
}

impl Default for TaskLedgerStatus {
    fn default() -> Self {
        Self::Dispatched
    }
}

#[derive(Debug, Default)]
pub struct TaskStore {
    tasks: RwLock<HashMap<String, TaskEntry>>,
    aliases: RwLock<HashMap<String, String>>,
}

impl TaskStore {
    pub fn new() -> Self {
        Self::default()
    }

    pub async fn register(&self, entry: TaskEntry) {
        let task_id = entry.task_id.clone();
        self.tasks.write().await.insert(task_id, entry);
    }

    pub async fn record_response_text(&self, task_id: &str, text: &str) {
        if text.is_empty() {
            return;
        }
        let mut tasks = self.tasks.write().await;
        let Some(entry) = tasks.get_mut(task_id) else {
            return;
        };
        if entry.response_mode == ChatResponseMode::Full {
            return;
        }
        if !entry.response_seen_tool_call {
            merge_snapshot_or_delta(&mut entry.response_full_content, text);
            entry.response_content = entry.response_full_content.clone();
            return;
        }

        let window_text = if !entry.response_strip_prefix.is_empty()
            && text.starts_with(&entry.response_strip_prefix)
        {
            text[entry.response_strip_prefix.len()..].to_string()
        } else {
            text.to_string()
        };

        if !entry.response_strip_prefix.is_empty()
            && text.starts_with(&entry.response_strip_prefix)
        {
            entry.response_full_content = text.to_string();
        } else {
            merge_snapshot_or_delta(&mut entry.response_full_content, text);
        }
        merge_snapshot_or_delta(&mut entry.response_content, &window_text);
    }

    pub async fn record_response_tool_call(&self, task_id: &str) {
        let mut tasks = self.tasks.write().await;
        let Some(entry) = tasks.get_mut(task_id) else {
            return;
        };
        if entry.response_mode == ChatResponseMode::Full {
            return;
        }
        entry.response_seen_tool_call = true;
        entry.response_strip_prefix = entry.response_full_content.clone();
        entry.response_content.clear();
    }

    pub async fn register_alias(&self, run_id: String, task_id: String) {
        self.aliases.write().await.insert(run_id, task_id);
    }

    pub async fn register_alias_for_dispatched_target(
        &self,
        task_id: &str,
        run_id: &str,
        bot_id: &str,
    ) -> Option<bool> {
        {
            let tasks = self.tasks.read().await;
            let entry = tasks.get(task_id)?;
            if entry.status != TaskLedgerStatus::Dispatched || entry.target_bot != bot_id {
                return Some(false);
            }
        }
        let mut aliases = self.aliases.write().await;
        aliases.insert(run_id.to_string(), task_id.to_string());
        Some(true)
    }

    pub async fn resolve_task_id(&self, run_id: &str) -> Option<String> {
        if self.tasks.read().await.contains_key(run_id) {
            return Some(run_id.to_string());
        }
        self.aliases.read().await.get(run_id).cloned()
    }

    pub async fn is_task_run(&self, run_id: &str) -> bool {
        self.resolve_task_id(run_id).await.is_some()
    }

    pub async fn get(&self, task_id: &str) -> Option<TaskEntry> {
        self.tasks.read().await.get(task_id).cloned()
    }

    async fn set_status(&self, task_id: &str, status: TaskLedgerStatus) {
        let mut tasks = self.tasks.write().await;
        let Some(entry) = tasks.get_mut(task_id) else {
            return;
        };
        entry.status = status;
    }

    pub async fn mark_replied(&self, task_id: &str) {
        self.set_status(task_id, TaskLedgerStatus::Replied).await;
    }

    pub async fn mark_failed(&self, task_id: &str) {
        self.set_status(task_id, TaskLedgerStatus::Failed).await;
    }

    pub async fn mark_timed_out(&self, task_id: &str) {
        self.set_status(task_id, TaskLedgerStatus::TimedOut).await;
    }

    fn entry_in_scope(entry: &TaskEntry, group_id: &str, session_id: Option<&str>) -> bool {
        if entry.group_id != group_id {
            return false;
        }
        match session_id {
            Some(session_id) => entry.session_id.as_deref() == Some(session_id),
            None => entry.session_id.is_none(),
        }
    }

    pub async fn pending_targets(&self, group_id: &str, session_id: Option<&str>) -> Vec<String> {
        let mut targets: Vec<_> = self.tasks.read().await
            .values()
            .filter(|entry| Self::entry_in_scope(entry, group_id, session_id))
            .filter(|entry| entry.status == TaskLedgerStatus::Dispatched)
            .map(target_name)
            .collect();
        targets.sort();
        targets
    }

    pub async fn pending_targets_at(
        &self,
        group_id: &str,
        session_id: Option<&str>,
        now_ms: u64,
    ) -> Vec<String> {
        self.ledger_summary_at(group_id, session_id, now_ms).await.pending
    }

    pub async fn has_pending(&self, group_id: &str, session_id: Option<&str>) -> bool {
        self.tasks.read().await
            .values()
            .any(|entry| {
                Self::entry_in_scope(entry, group_id, session_id)
                    && entry.status == TaskLedgerStatus::Dispatched
            })
    }

    pub async fn ledger_summary(&self, group_id: &str, session_id: Option<&str>) -> LedgerSummary {
        let mut summary = LedgerSummary::default();
        for entry in self.tasks.read().await.values() {
            if !Self::entry_in_scope(entry, group_id, session_id) {
                continue;
            }
            match entry.status {
                TaskLedgerStatus::Dispatched => summary.pending.push(target_name(entry)),
                TaskLedgerStatus::Replied => summary.replied.push(target_name(entry)),
                TaskLedgerStatus::Failed => summary.failed.push(target_name(entry)),
                TaskLedgerStatus::TimedOut => summary.timed_out.push(target_name(entry)),
            }
        }
        summary.pending.sort();
        summary.replied.sort();
        summary.failed.sort();
        summary.timed_out.sort();
        summary
    }

    pub async fn ledger_summary_at(
        &self,
        group_id: &str,
        session_id: Option<&str>,
        now_ms: u64,
    ) -> LedgerSummary {
        let mut summary = LedgerSummary::default();
        for entry in self.tasks.read().await.values() {
            if !Self::entry_in_scope(entry, group_id, session_id) {
                continue;
            }
            match status_at(entry, now_ms) {
                TaskLedgerStatus::Dispatched => summary.pending.push(target_name(entry)),
                TaskLedgerStatus::Replied => summary.replied.push(target_name(entry)),
                TaskLedgerStatus::Failed => summary.failed.push(target_name(entry)),
                TaskLedgerStatus::TimedOut => summary.timed_out.push(target_name(entry)),
            }
        }
        summary.pending.sort();
        summary.replied.sort();
        summary.failed.sort();
        summary.timed_out.sort();
        summary
    }

    pub async fn remove(&self, task_id: &str) -> Option<TaskEntry> {
        let mut aliases = self.aliases.write().await;
        aliases.retain(|_, value| value != task_id);
        drop(aliases);
        self.tasks.write().await.remove(task_id)
    }
}

pub fn new_task_entry(
    task_id: String,
    group_id: String,
    session_id: Option<String>,
    driver_bot: String,
    target_bot: String,
    target_bot_name: Option<String>,
    created_at_ms: u64,
    response_mode: ChatResponseMode,
) -> TaskEntry {
    TaskEntry {
        task_id,
        group_id,
        session_id,
        driver_bot,
        target_bot,
        target_bot_name,
        created_at_ms,
        response_mode,
        status: TaskLedgerStatus::Dispatched,
        response_content: String::new(),
        response_full_content: String::new(),
        response_strip_prefix: String::new(),
        response_seen_tool_call: false,
    }
}

fn target_name(entry: &TaskEntry) -> String {
    entry
        .target_bot_name
        .clone()
        .unwrap_or_else(|| entry.target_bot.clone())
}

fn status_at(entry: &TaskEntry, now_ms: u64) -> TaskLedgerStatus {
    if entry.status == TaskLedgerStatus::Dispatched
        && now_ms.saturating_sub(entry.created_at_ms) > TASK_TTL_MS
    {
        return TaskLedgerStatus::TimedOut;
    }
    entry.status
}

fn merge_snapshot_or_delta(current: &mut String, incoming: &str) {
    if incoming.starts_with(current.as_str()) {
        current.clear();
        current.push_str(incoming);
    } else {
        current.push_str(incoming);
    }
}

#[cfg(test)]
mod status_tests {
    use super::*;

    fn sample_entry(task_id: &str) -> TaskEntry {
        new_task_entry(
            task_id.to_string(),
            "g1".to_string(),
            None,
            "m1".to_string(),
            "w1".to_string(),
            Some("worker-1".to_string()),
            0,
            ChatResponseMode::AfterLastToolCall,
        )
    }

    #[tokio::test]
    async fn mark_replied_transitions_status() {
        let store = TaskStore::new();
        store.register(sample_entry("t1")).await;
        store.mark_replied("t1").await;
        assert_eq!(store.get("t1").await.unwrap().status, TaskLedgerStatus::Replied);
    }

    #[tokio::test]
    async fn pending_targets_lists_only_dispatched_in_session() {
        let store = TaskStore::new();
        let mut e1 = sample_entry("t1");
        e1.session_id = Some("s1".into());
        store.register(e1).await;
        let mut e2 = sample_entry("t2");
        e2.target_bot_name = Some("worker-2".into());
        e2.session_id = Some("s1".into());
        store.register(e2).await;
        let mut e3 = sample_entry("t3");
        e3.target_bot_name = Some("worker-3".into());
        e3.session_id = Some("s2".into());
        store.register(e3).await;
        store.mark_replied("t1").await;
        let pending = store.pending_targets("g1", Some("s1")).await;
        assert_eq!(pending, vec!["worker-2".to_string()]);
    }

    #[tokio::test]
    async fn mark_timed_out_removes_pending_and_updates_summary() {
        let store = TaskStore::new();
        let mut timed_out = sample_entry("t1");
        timed_out.session_id = Some("s1".into());
        store.register(timed_out).await;
        let mut still_pending = sample_entry("t2");
        still_pending.target_bot_name = Some("worker-2".into());
        still_pending.session_id = Some("s1".into());
        store.register(still_pending).await;
        let mut other_session = sample_entry("t3");
        other_session.target_bot_name = Some("worker-3".into());
        other_session.session_id = Some("s2".into());
        store.register(other_session).await;

        store.mark_timed_out("t1").await;

        assert_eq!(store.get("t1").await.unwrap().status, TaskLedgerStatus::TimedOut);
        assert!(store.has_pending("g1", Some("s1")).await);
        let pending = store.pending_targets("g1", Some("s1")).await;
        assert_eq!(pending, vec!["worker-2".to_string()]);
        let summary = store.ledger_summary("g1", Some("s1")).await;
        assert_eq!(summary.pending, vec!["worker-2".to_string()]);
        assert_eq!(summary.timed_out, vec!["worker-1".to_string()]);

        let other_summary = store.ledger_summary("g1", Some("s2")).await;
        assert_eq!(other_summary.pending, vec!["worker-3".to_string()]);
        assert!(other_summary.timed_out.is_empty());
    }

    #[tokio::test]
    async fn pending_past_ttl_reported_as_timed_out() {
        let store = TaskStore::new();
        let mut timed_out = sample_entry("t1");
        timed_out.session_id = Some("s1".into());
        timed_out.created_at_ms = 0;
        store.register(timed_out).await;

        let summary = store
            .ledger_summary_at("g1", Some("s1"), TASK_TTL_MS + 1)
            .await;
        assert!(summary.pending.is_empty());
        assert_eq!(summary.timed_out, vec!["worker-1".to_string()]);
        assert!(store
            .pending_targets_at("g1", Some("s1"), TASK_TTL_MS + 1)
            .await
            .is_empty());
    }

    #[tokio::test]
    async fn none_session_scope_excludes_session_tasks() {
        let store = TaskStore::new();
        let mut group_only = sample_entry("t1");
        group_only.target_bot_name = Some("group-worker".into());
        store.register(group_only).await;
        let mut session_scoped = sample_entry("t2");
        session_scoped.target_bot_name = Some("session-worker".into());
        session_scoped.session_id = Some("s1".into());
        store.register(session_scoped).await;

        let pending = store.pending_targets("g1", None).await;
        assert_eq!(pending, vec!["group-worker".to_string()]);
        let summary = store.ledger_summary("g1", None).await;
        assert_eq!(summary.pending, vec!["group-worker".to_string()]);
    }

    #[tokio::test]
    async fn ledger_summary_groups_by_status_and_scope() {
        let store = TaskStore::new();
        let mut e1 = sample_entry("t1");
        e1.session_id = Some("s1".into());
        store.register(e1).await;
        let mut e2 = sample_entry("t2");
        e2.target_bot_name = Some("worker-2".into());
        e2.session_id = Some("s1".into());
        store.register(e2).await;
        store.mark_replied("t1").await;
        store.mark_failed("t2").await;

        let summary = store.ledger_summary("g1", Some("s1")).await;
        assert_eq!(summary.replied, vec!["worker-1".to_string()]);
        assert_eq!(summary.failed, vec!["worker-2".to_string()]);
        assert!(summary.pending.is_empty());
        assert!(summary.timed_out.is_empty());
    }
}
