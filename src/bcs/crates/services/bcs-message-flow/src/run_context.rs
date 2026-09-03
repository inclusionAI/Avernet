use std::collections::{HashMap, HashSet};

use async_trait::async_trait;
use bcs_service_api::{
    ActiveBotRunContext, BotRunContext, BotRunContextPort, BotRunScope, ProviderRunTransport,
    ServiceError, ServiceResult,
};
use tokio::sync::RwLock;

#[derive(Debug, Clone, Copy)]
struct ProviderTransportEntry {
    state: ProviderRunTransport,
    deadline_ms: u64,
}

#[derive(Debug, Default)]
pub struct MemoryBotRunContextStore {
    runs: RwLock<HashMap<String, BotRunContext>>,
    processing: RwLock<HashSet<String>>,
    provider_transports: RwLock<HashMap<String, ProviderTransportEntry>>,
    active_runs: RwLock<HashMap<String, ActiveBotRunContext>>,
    active_aliases: RwLock<HashMap<String, String>>,
}

impl MemoryBotRunContextStore {
    pub fn new() -> Self {
        Self::default()
    }
}

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
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

    async fn register_active_run(&self, context: ActiveBotRunContext) -> ServiceResult<()> {
        let run = self
            .runs
            .read()
            .await
            .get(&context.canonical_run_id)
            .cloned()
            .ok_or_else(|| ServiceError::InvalidOperation {
                message: "active run requires a registered BotRunContext".to_string(),
                request_id: Some(context.canonical_run_id.clone()),
            })?;
        let expected_session = run
            .bcs_session_id
            .clone()
            .unwrap_or_else(|| run.group_id.clone());
        if run.group_id != context.scope.group_id
            || expected_session != context.scope.session_id
            || run.bot_id != context.scope.bot_id
        {
            return Err(ServiceError::InvalidOperation {
                message: "active run scope does not match BotRunContext".to_string(),
                request_id: Some(context.canonical_run_id),
            });
        }
        self.active_aliases.write().await.insert(
            context.downstream_run_id.clone(),
            context.canonical_run_id.clone(),
        );
        self.active_aliases.write().await.insert(
            context.canonical_run_id.clone(),
            context.canonical_run_id.clone(),
        );
        self.active_runs
            .write()
            .await
            .insert(context.canonical_run_id.clone(), context);
        Ok(())
    }

    async fn list_active_runs(
        &self,
        scope: &BotRunScope,
    ) -> ServiceResult<Vec<ActiveBotRunContext>> {
        let runs = self.runs.read().await;
        let active = self.active_runs.read().await;
        Ok(active
            .values()
            .filter(|context| &context.scope == scope)
            .filter(|context| now_ms() <= context.deadline_ms)
            .filter(|context| {
                runs.get(&context.canonical_run_id)
                    .is_some_and(|run| !run.terminal)
            })
            .cloned()
            .collect())
    }

    async fn find_active_run(&self, run_id: &str) -> ServiceResult<Option<ActiveBotRunContext>> {
        let canonical = self
            .active_aliases
            .read()
            .await
            .get(run_id)
            .cloned()
            .unwrap_or_else(|| run_id.to_string());
        let context = self.active_runs.read().await.get(&canonical).cloned();
        let Some(context) = context else {
            return Ok(None);
        };
        if now_ms() <= context.deadline_ms
            && self
                .runs
                .read()
                .await
                .get(&canonical)
                .is_some_and(|run| !run.terminal)
        {
            Ok(Some(context))
        } else {
            Ok(None)
        }
    }

    async fn bind_downstream_run_id(
        &self,
        canonical_run_id: &str,
        downstream_run_id: &str,
    ) -> ServiceResult<bool> {
        let mut active = self.active_runs.write().await;
        let Some(context) = active.get_mut(canonical_run_id) else {
            return Ok(false);
        };
        let previous = std::mem::replace(
            &mut context.downstream_run_id,
            downstream_run_id.to_string(),
        );
        let mut aliases = self.active_aliases.write().await;
        if previous != canonical_run_id {
            aliases.remove(&previous);
        }
        aliases.insert(downstream_run_id.to_string(), canonical_run_id.to_string());
        Ok(true)
    }

    async fn remove_active_run(
        &self,
        scope: &BotRunScope,
        canonical_run_id: &str,
    ) -> ServiceResult<bool> {
        let mut active = self.active_runs.write().await;
        let matches_scope = active
            .get(canonical_run_id)
            .is_some_and(|context| &context.scope == scope);
        if !matches_scope {
            return Ok(false);
        }
        let Some(context) = active.remove(canonical_run_id) else {
            return Ok(false);
        };
        let mut aliases = self.active_aliases.write().await;
        aliases.remove(canonical_run_id);
        aliases.remove(&context.downstream_run_id);
        Ok(true)
    }

    async fn begin_provider_transport(&self, run_id: &str, deadline_ms: u64) -> bool {
        let mut entries = self.provider_transports.write().await;
        if entries.contains_key(run_id) {
            return false;
        }
        entries.insert(
            run_id.to_string(),
            ProviderTransportEntry {
                state: ProviderRunTransport::Negotiating,
                deadline_ms,
            },
        );
        true
    }

    async fn bind_provider_transport(&self, run_id: &str, transport: ProviderRunTransport) -> bool {
        if !matches!(
            transport,
            ProviderRunTransport::Sse | ProviderRunTransport::Callback
        ) {
            return false;
        }
        let mut entries = self.provider_transports.write().await;
        let Some(entry) = entries.get_mut(run_id) else {
            return false;
        };
        if entry.state != ProviderRunTransport::Negotiating {
            return entry.state == transport;
        }
        entry.state = transport;
        true
    }

    async fn get_provider_transport(&self, run_id: &str) -> Option<ProviderRunTransport> {
        self.provider_transports
            .read()
            .await
            .get(run_id)
            .map(|entry| entry.state)
    }

    async fn mark_provider_transport_terminal(&self, run_id: &str) {
        if let Some(entry) = self.provider_transports.write().await.get_mut(run_id) {
            entry.state = ProviderRunTransport::Terminal;
        }
    }

    async fn clear_provider_transport(&self, run_id: &str) {
        self.provider_transports.write().await.remove(run_id);
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
            let mut active = self.active_runs.write().await;
            let mut aliases = self.active_aliases.write().await;
            for run_id in &removed_run_ids {
                if let Some(context) = active.remove(run_id) {
                    aliases.remove(run_id);
                    aliases.remove(&context.downstream_run_id);
                }
            }
        }

        self.provider_transports
            .write()
            .await
            .retain(|_, entry| now_ms <= entry.deadline_ms.saturating_add(retention_ms));

        removed_run_ids.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    async fn register_active(
        store: &MemoryBotRunContextStore,
        canonical_run_id: &str,
        downstream_run_id: &str,
        session_id: &str,
        bot_id: &str,
    ) {
        store
            .put_context(BotRunContext {
                run_id: canonical_run_id.to_string(),
                bot_id: bot_id.to_string(),
                group_id: "group".to_string(),
                bcs_session_id: Some(session_id.to_string()),
                deadline_ms: u64::MAX,
                terminal: false,
            })
            .await;
        store
            .register_active_run(ActiveBotRunContext {
                canonical_run_id: canonical_run_id.to_string(),
                downstream_run_id: downstream_run_id.to_string(),
                downstream_session_key: Some(session_id.to_string()),
                scope: BotRunScope {
                    group_id: "group".to_string(),
                    session_id: session_id.to_string(),
                    bot_id: bot_id.to_string(),
                },
                transport_owner: bcs_service_api::BotRunTransportOwner::WebSocket,
                deadline_ms: u64::MAX,
            })
            .await
            .unwrap();
    }

    #[tokio::test]
    async fn active_index_isolates_scope_and_resolves_downstream_alias() {
        let store = MemoryBotRunContextStore::new();
        register_active(&store, "bcs-1", "plugin-1", "session-1", "bot-1").await;
        register_active(&store, "bcs-2", "plugin-2", "session-1", "bot-2").await;

        let scope = BotRunScope {
            group_id: "group".to_string(),
            session_id: "session-1".to_string(),
            bot_id: "bot-1".to_string(),
        };
        let active = store.list_active_runs(&scope).await.unwrap();
        assert_eq!(active.len(), 1);
        assert_eq!(active[0].canonical_run_id, "bcs-1");
        assert_eq!(
            store
                .find_active_run("plugin-1")
                .await
                .unwrap()
                .unwrap()
                .canonical_run_id,
            "bcs-1"
        );

        assert!(store.mark_terminal("bcs-1").await);
        assert!(store.list_active_runs(&scope).await.unwrap().is_empty());
        assert!(store.find_active_run("plugin-1").await.unwrap().is_none());
    }

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

    #[tokio::test]
    async fn provider_transport_binds_once_and_rejects_mixed_sources() {
        let store = MemoryBotRunContextStore::new();
        assert!(store.begin_provider_transport("run", 10_000).await);

        assert_eq!(
            store.get_provider_transport("run").await,
            Some(ProviderRunTransport::Negotiating)
        );
        assert!(
            store
                .bind_provider_transport("run", ProviderRunTransport::Sse)
                .await
        );
        assert!(
            !store
                .bind_provider_transport("run", ProviderRunTransport::Callback)
                .await
        );
        assert!(!store.begin_provider_transport("run", 20_000).await);
        assert_eq!(
            store.get_provider_transport("run").await,
            Some(ProviderRunTransport::Sse)
        );
    }

    #[tokio::test]
    async fn provider_transport_is_cleaned_after_deadline_retention() {
        let store = MemoryBotRunContextStore::new();
        assert!(store.begin_provider_transport("expired", 1_000).await);
        assert!(store.begin_provider_transport("retained", 9_000).await);

        store.cleanup_expired(7_000, 5_000).await;

        assert!(store.get_provider_transport("expired").await.is_none());
        assert_eq!(
            store.get_provider_transport("retained").await,
            Some(ProviderRunTransport::Negotiating)
        );
    }
}
