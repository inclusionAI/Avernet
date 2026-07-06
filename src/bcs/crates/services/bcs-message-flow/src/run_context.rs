use std::collections::{HashMap, HashSet};

use async_trait::async_trait;
use bcs_service_api::{BotRunContext, BotRunContextPort};
use tokio::sync::RwLock;

#[derive(Debug, Default)]
pub struct MemoryBotRunContextStore {
    runs: RwLock<HashMap<String, BotRunContext>>,
    processing: RwLock<HashSet<String>>,
}

impl MemoryBotRunContextStore {
    pub fn new() -> Self {
        Self::default()
    }
}

#[async_trait]
impl BotRunContextPort for MemoryBotRunContextStore {
    async fn put_context(&self, context: BotRunContext) {
        self.runs
            .write()
            .await
            .insert(context.run_id.clone(), context);
    }

    async fn get_context(&self, run_id: &str) -> Option<BotRunContext> {
        self.runs.read().await.get(run_id).cloned()
    }

    async fn try_begin_terminal(&self, run_id: &str) -> bool {
        let runs = self.runs.read().await;
        let Some(context) = runs.get(run_id) else {
            return false;
        };
        if context.terminal {
            return false;
        }
        drop(runs);

        let mut processing = self.processing.write().await;
        if processing.contains(run_id) {
            return false;
        }
        processing.insert(run_id.to_string());
        true
    }

    async fn mark_terminal(&self, run_id: &str) -> bool {
        let mut runs = self.runs.write().await;
        let Some(context) = runs.get_mut(run_id) else {
            return false;
        };
        if context.terminal {
            return false;
        }
        context.terminal = true;
        self.processing.write().await.remove(run_id);
        true
    }

    async fn release_terminal(&self, run_id: &str) {
        self.processing.write().await.remove(run_id);
    }

    async fn cleanup_expired(&self, now_ms: u64, retention_ms: u64) -> usize {
        let mut removed_run_ids = Vec::new();
        {
            let mut runs = self.runs.write().await;
            runs.retain(|run_id, context| {
                let remove_after_ms = context.deadline_ms.saturating_add(retention_ms);
                let should_remove = now_ms > remove_after_ms;
                if should_remove {
                    removed_run_ids.push(run_id.clone());
                }
                !should_remove
            });
        }

        if !removed_run_ids.is_empty() {
            let mut processing = self.processing.write().await;
            for run_id in &removed_run_ids {
                processing.remove(run_id);
            }
        }

        removed_run_ids.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn cleanup_expired_removes_contexts_after_retention_window() {
        let store = MemoryBotRunContextStore::new();
        store
            .put_context(BotRunContext {
                run_id: "expired".to_string(),
                bot_id: "bot-expired".to_string(),
                group_id: "group".to_string(),
                bcs_session_id: None,
                deadline_ms: 1_000,
                terminal: false,
            })
            .await;
        store
            .put_context(BotRunContext {
                run_id: "retained".to_string(),
                bot_id: "bot-retained".to_string(),
                group_id: "group".to_string(),
                bcs_session_id: None,
                deadline_ms: 9_000,
                terminal: false,
            })
            .await;

        let removed = store.cleanup_expired(7_000, 5_000).await;

        assert_eq!(removed, 1);
        assert!(store.get_context("expired").await.is_none());
        assert!(store.get_context("retained").await.is_some());
    }
}
