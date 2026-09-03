//! Redis-backed `BotRunContextPort` implementation.
//!
//! Governs the Provider downlink run context (callback routing, terminal
//! claim, transport binding) so it survives restart and is consistent across
//! replicas, per issue #1546. Backed by `CachePlugin` (the production path
//! wires `bcs-cache-redis`; tests use `bcs-cache-local`). Redis-side TTLs
//! replace the in-memory sweep, so `cleanup_expired` is a no-op.
//!
//! Terminal idempotency: a dedicated `term:{run_id}` key is set with `NX`, so
//! exactly one `mark_terminal` call wins across replicas; `get_context`
//! overlays the term key so callers observe terminal even before the context
//! JSON is rewritten. `try_begin_terminal` claims a short-lived `claim:{run_id}`
//! key with `NX` to gate the begin→mark sequence.

use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use bcs_cache_api::{CachePlugin, CacheSetMode, CacheTtl};
use bcs_service_api::{
    ActiveBotRunContext, BotRunContext, BotRunContextPort, BotRunScope, ProviderRunTransport,
    ServiceError, ServiceResult,
};

const CLAIM_TTL_MS: u64 = 60_000;

/// Redis/cache-backed provider run context store.
pub struct RedisBotRunContextStore {
    cache: Arc<dyn CachePlugin>,
    key_prefix: String,
    /// Grace past `deadline_ms` that context/transport/term keys are retained,
    /// so a provider callback arriving just past the BCS timeout (or a
    /// restart/relocation within the grace) can still route. The live TTL of
    /// each entry is `deadline_ms + retention` (see `lifecycle_ttl`), not a
    /// flat interval from insertion — otherwise long provider runs are evicted
    /// mid-flight (C2).
    retention_ms: u64,
}

impl RedisBotRunContextStore {
    pub fn new(cache: Arc<dyn CachePlugin>, key_prefix: String, retention_ms: u64) -> Self {
        Self {
            cache,
            key_prefix,
            retention_ms: retention_ms.max(1),
        }
    }

    fn ctx_key(&self, run_id: &str) -> String {
        format!("{}botrun:{}", self.key_prefix, run_id)
    }
    fn claim_key(&self, run_id: &str) -> String {
        format!("{}botrun:claim:{}", self.key_prefix, run_id)
    }
    fn term_key(&self, run_id: &str) -> String {
        format!("{}botrun:term:{}", self.key_prefix, run_id)
    }
    fn transport_key(&self, run_id: &str) -> String {
        format!("{}botrun:transport:{}", self.key_prefix, run_id)
    }
    fn active_key(&self, canonical_run_id: &str) -> String {
        format!("{}botrun:active:{}", self.key_prefix, canonical_run_id)
    }
    fn active_alias_key(&self, run_id: &str) -> String {
        format!("{}botrun:active-alias:{}", self.key_prefix, run_id)
    }
    fn active_scope_key(&self, scope: &BotRunScope) -> ServiceResult<String> {
        serde_json::to_string(scope)
            .map(|scope| format!("{}botrun:active-scope:{}", self.key_prefix, scope))
            .map_err(|error| {
                ServiceError::InternalError(format!("serialize active scope: {error}"))
            })
    }

    fn cache_error(operation: &str, error: impl std::fmt::Display) -> ServiceError {
        ServiceError::InternalError(format!("active run cache {operation} failed: {error}"))
    }

    async fn extend_scope_ttl(&self, key: &str, ttl: Duration) -> ServiceResult<()> {
        let should_extend = match self
            .cache
            .ttl(key)
            .await
            .map_err(|error| Self::cache_error("ttl", error))?
        {
            CacheTtl::Missing | CacheTtl::Persistent => true,
            CacheTtl::ExpiresIn(current) => current < ttl,
        };
        if should_extend {
            self.cache
                .expire(key, ttl)
                .await
                .map_err(|error| Self::cache_error("expire", error))?;
        }
        Ok(())
    }

    /// TTL for a lifecycle key: keep the entry until the run's `deadline_ms`
    /// plus the retention grace, measured from now. Matches the memory store's
    /// "deadline + retention" lifecycle so long provider runs are not evicted
    /// before they finish (C2).
    fn lifecycle_ttl(&self, deadline_ms: u64) -> Duration {
        let now = now_ms();
        let target = deadline_ms.saturating_add(self.retention_ms);
        Duration::from_millis(target.saturating_sub(now).max(1000))
    }

    async fn read_context(&self, run_id: &str) -> Option<BotRunContext> {
        let bytes = self.cache.get_value(&self.ctx_key(run_id)).await.ok()??;
        let mut context: BotRunContext = serde_json::from_slice(&bytes).ok()?;
        // Overlay the terminal flag so callers observe terminal even before the
        // context JSON is rewritten by a concurrent mark_terminal.
        if self
            .cache
            .get_value(&self.term_key(run_id))
            .await
            .ok()
            .flatten()
            .is_some()
        {
            context.terminal = true;
        }
        Some(context)
    }

    async fn write_context(&self, context: &BotRunContext) {
        if let Ok(bytes) = serde_json::to_vec(context) {
            let ttl = self.lifecycle_ttl(context.deadline_ms);
            let _ = self
                .cache
                .set_value(
                    &self.ctx_key(&context.run_id),
                    bytes,
                    Some(ttl),
                    CacheSetMode::Upsert,
                )
                .await;
        }
    }

    async fn read_active_context(
        &self,
        canonical_run_id: &str,
    ) -> ServiceResult<Option<ActiveBotRunContext>> {
        let Some(payload) = self
            .cache
            .get_value(&self.active_key(canonical_run_id))
            .await
            .map_err(|error| Self::cache_error("get context", error))?
        else {
            return Ok(None);
        };
        serde_json::from_slice(&payload)
            .map(Some)
            .map_err(|error| ServiceError::InternalError(format!("decode active run: {error}")))
    }
}

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

fn transport_str(transport: ProviderRunTransport) -> &'static str {
    match transport {
        ProviderRunTransport::Negotiating => "negotiating",
        ProviderRunTransport::Sse => "sse",
        ProviderRunTransport::Callback => "callback",
        ProviderRunTransport::Terminal => "terminal",
    }
}

fn parse_transport(value: &str) -> Option<ProviderRunTransport> {
    match value {
        "negotiating" => Some(ProviderRunTransport::Negotiating),
        "sse" => Some(ProviderRunTransport::Sse),
        "callback" => Some(ProviderRunTransport::Callback),
        "terminal" => Some(ProviderRunTransport::Terminal),
        _ => None,
    }
}

#[derive(Serialize, Deserialize)]
struct TransportEntry {
    state: String,
    deadline_ms: u64,
}

#[async_trait]
impl BotRunContextPort for RedisBotRunContextStore {
    async fn put_context(&self, context: BotRunContext) {
        self.write_context(&context).await;
    }

    async fn get_context(&self, run_id: &str) -> Option<BotRunContext> {
        self.read_context(run_id).await
    }

    async fn try_begin_terminal(&self, run_id: &str) -> bool {
        let Some(context) = self.read_context(run_id).await else {
            return false;
        };
        if context.terminal {
            return false;
        }
        // Claim with NX so only one replica proceeds into terminal processing.
        let acquired = self
            .cache
            .set_value(
                &self.claim_key(run_id),
                b"1".to_vec(),
                Some(Duration::from_millis(CLAIM_TTL_MS)),
                CacheSetMode::InsertOnly,
            )
            .await
            .unwrap_or(false);
        acquired
    }

    async fn mark_terminal(&self, run_id: &str) -> bool {
        let Some(context) = self.read_context(run_id).await else {
            return false;
        };
        let ttl = self.lifecycle_ttl(context.deadline_ms);
        // First writer wins; everyone else observes terminal via the term key.
        let applied = self
            .cache
            .set_value(
                &self.term_key(run_id),
                b"1".to_vec(),
                Some(ttl),
                CacheSetMode::InsertOnly,
            )
            .await
            .unwrap_or(false);
        if applied {
            let mut context = context;
            context.terminal = true;
            self.write_context(&context).await;
            let _ = self.cache.delete(&self.claim_key(run_id)).await;
        }
        applied
    }

    async fn release_terminal(&self, run_id: &str) {
        let _ = self.cache.delete(&self.claim_key(run_id)).await;
    }

    async fn register_active_run(&self, context: ActiveBotRunContext) -> ServiceResult<()> {
        let run = self
            .read_context(&context.canonical_run_id)
            .await
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

        let payload = serde_json::to_vec(&context).map_err(|error| {
            ServiceError::InternalError(format!("serialize active run: {error}"))
        })?;
        let ttl = self.lifecycle_ttl(context.deadline_ms);
        let scope_key = self.active_scope_key(&context.scope)?;
        self.cache
            .set_value(
                &self.active_key(&context.canonical_run_id),
                payload.clone(),
                Some(ttl),
                CacheSetMode::Upsert,
            )
            .await
            .map_err(|error| Self::cache_error("set context", error))?;
        for alias in [&context.canonical_run_id, &context.downstream_run_id] {
            self.cache
                .set_value(
                    &self.active_alias_key(alias),
                    context.canonical_run_id.as_bytes().to_vec(),
                    Some(ttl),
                    CacheSetMode::Upsert,
                )
                .await
                .map_err(|error| Self::cache_error("set alias", error))?;
        }
        self.cache
            .hash_set(&scope_key, &context.canonical_run_id, payload)
            .await
            .map_err(|error| Self::cache_error("set scope", error))?;
        self.extend_scope_ttl(&scope_key, ttl).await
    }

    async fn list_active_runs(
        &self,
        scope: &BotRunScope,
    ) -> ServiceResult<Vec<ActiveBotRunContext>> {
        let scope_key = self.active_scope_key(scope)?;
        let entries = self
            .cache
            .hash_get_all(&scope_key)
            .await
            .map_err(|error| Self::cache_error("list scope", error))?;
        let mut active = Vec::new();
        for (canonical_run_id, payload) in entries {
            let context: ActiveBotRunContext =
                serde_json::from_slice(&payload).map_err(|error| {
                    ServiceError::InternalError(format!("decode active run context: {error}"))
                })?;
            // COSEC: cache entries are treated as untrusted persistence data;
            // never allow a mismatched serialized scope to cross isolation.
            if context.scope != *scope || context.canonical_run_id != canonical_run_id {
                return Err(ServiceError::InternalError(
                    "active run scope index is inconsistent".to_string(),
                ));
            }
            let still_active = self
                .read_context(&canonical_run_id)
                .await
                .is_some_and(|run| !run.terminal && now_ms() <= context.deadline_ms);
            if still_active {
                active.push(context);
            } else {
                let _ = self.cache.hash_delete(&scope_key, &canonical_run_id).await;
            }
        }
        Ok(active)
    }

    async fn find_active_run(&self, run_id: &str) -> ServiceResult<Option<ActiveBotRunContext>> {
        let Some(canonical) = self
            .cache
            .get_value(&self.active_alias_key(run_id))
            .await
            .map_err(|error| Self::cache_error("get alias", error))?
        else {
            return Ok(None);
        };
        let canonical = String::from_utf8(canonical).map_err(|error| {
            ServiceError::InternalError(format!("decode active alias: {error}"))
        })?;
        let Some(context) = self.read_active_context(&canonical).await? else {
            return Ok(None);
        };
        if self
            .read_context(&canonical)
            .await
            .is_some_and(|run| !run.terminal && now_ms() <= context.deadline_ms)
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
        let Some(mut context) = self.find_active_run(canonical_run_id).await? else {
            return Ok(false);
        };
        let previous = std::mem::replace(
            &mut context.downstream_run_id,
            downstream_run_id.to_string(),
        );
        self.register_active_run(context).await?;
        if previous != canonical_run_id && previous != downstream_run_id {
            self.cache
                .delete(&self.active_alias_key(&previous))
                .await
                .map_err(|error| Self::cache_error("delete old alias", error))?;
        }
        Ok(true)
    }

    async fn remove_active_run(
        &self,
        scope: &BotRunScope,
        canonical_run_id: &str,
    ) -> ServiceResult<bool> {
        // Read the raw active context rather than `find_active_run`: terminal
        // CAS happens before cleanup, and the filtered lookup intentionally
        // hides terminal runs.
        let Some(context) = self.read_active_context(canonical_run_id).await? else {
            return Ok(false);
        };
        if context.scope != *scope {
            return Ok(false);
        }
        let scope_key = self.active_scope_key(scope)?;
        self.cache
            .hash_delete(&scope_key, canonical_run_id)
            .await
            .map_err(|error| Self::cache_error("delete scope", error))?;
        self.cache
            .delete(&self.active_key(canonical_run_id))
            .await
            .map_err(|error| Self::cache_error("delete context", error))?;
        for alias in [canonical_run_id, context.downstream_run_id.as_str()] {
            self.cache
                .delete(&self.active_alias_key(alias))
                .await
                .map_err(|error| Self::cache_error("delete alias", error))?;
        }
        Ok(true)
    }

    async fn begin_provider_transport(&self, run_id: &str, deadline_ms: u64) -> bool {
        let entry = TransportEntry {
            state: transport_str(ProviderRunTransport::Negotiating).to_string(),
            deadline_ms,
        };
        let Ok(bytes) = serde_json::to_vec(&entry) else {
            return false;
        };
        self.cache
            .set_value(
                &self.transport_key(run_id),
                bytes,
                Some(self.lifecycle_ttl(deadline_ms)),
                CacheSetMode::InsertOnly,
            )
            .await
            .unwrap_or(false)
    }

    async fn bind_provider_transport(&self, run_id: &str, transport: ProviderRunTransport) -> bool {
        if !matches!(
            transport,
            ProviderRunTransport::Sse | ProviderRunTransport::Callback
        ) {
            return false;
        }
        let Some(bytes) = self
            .cache
            .get_value(&self.transport_key(run_id))
            .await
            .ok()
            .flatten()
        else {
            return false;
        };
        let Ok(entry) = serde_json::from_slice::<TransportEntry>(&bytes) else {
            return false;
        };
        let current = parse_transport(&entry.state);
        match current {
            Some(ProviderRunTransport::Negotiating) => {
                let updated = TransportEntry {
                    state: transport_str(transport).to_string(),
                    deadline_ms: entry.deadline_ms,
                };
                if let Ok(payload) = serde_json::to_vec(&updated) {
                    let _ = self
                        .cache
                        .set_value(
                            &self.transport_key(run_id),
                            payload,
                            Some(self.lifecycle_ttl(entry.deadline_ms)),
                            CacheSetMode::Upsert,
                        )
                        .await;
                }
                true
            }
            Some(current) if current == transport => true,
            _ => false,
        }
    }

    async fn get_provider_transport(&self, run_id: &str) -> Option<ProviderRunTransport> {
        let bytes = self
            .cache
            .get_value(&self.transport_key(run_id))
            .await
            .ok()??;
        let entry: TransportEntry = serde_json::from_slice(&bytes).ok()?;
        parse_transport(&entry.state)
    }

    async fn mark_provider_transport_terminal(&self, run_id: &str) {
        if let Some(bytes) = self
            .cache
            .get_value(&self.transport_key(run_id))
            .await
            .ok()
            .flatten()
        {
            if let Ok(mut entry) = serde_json::from_slice::<TransportEntry>(&bytes) {
                entry.state = transport_str(ProviderRunTransport::Terminal).to_string();
                if let Ok(payload) = serde_json::to_vec(&entry) {
                    let _ = self
                        .cache
                        .set_value(
                            &self.transport_key(run_id),
                            payload,
                            Some(self.lifecycle_ttl(entry.deadline_ms)),
                            CacheSetMode::Upsert,
                        )
                        .await;
                }
            }
        }
    }

    async fn clear_provider_transport(&self, run_id: &str) {
        let _ = self.cache.delete(&self.transport_key(run_id)).await;
    }

    async fn cleanup_expired(&self, _now_ms: u64, _retention_ms: u64) -> usize {
        // TTL-driven; nothing to sweep.
        0
    }
}
