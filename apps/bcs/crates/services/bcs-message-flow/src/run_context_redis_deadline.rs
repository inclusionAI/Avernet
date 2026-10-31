//! Strict, bounded deadline changes for single-instance A2A execution budgeting.
use super::*;

impl RedisBotRunContextStore {
    pub(super) async fn reschedule_a2a_deadline(
        &self, scope: &BotRunScope, run: &str, expected: u64, deadline: u64,
    ) -> ServiceResult<bool> {
        let _guard = self.lifecycle.acquire(run).await;
        if expected <= now_ms() || deadline <= now_ms() { return Ok(false); }
        let keys = vec![self.ctx_key(run), self.term_key(run), self.claim_key(run),
            self.transport_key(run), self.active_key(run)];
        let values = self.cache.get_values(&keys).await.map_err(|e|Self::cache_error("deadline read",e))?;
        if values.len() != keys.len() { return Err(Self::cache_error("deadline read","invalid batch length")); }
        let [Some(context), terminal, claim, Some(transport), Some(active)] = values.as_slice() else { return Ok(false); };
        if terminal.is_some() || claim.is_some() { return Ok(false); }
        let mut context:BotRunContext = serde_json::from_slice(context).map_err(|e|Self::cache_error("decode deadline context",e))?;
        let mut active:ActiveBotRunContext = serde_json::from_slice(active).map_err(|e|Self::cache_error("decode deadline index",e))?;
        let mut transport:TransportEntry = serde_json::from_slice(transport).map_err(|e|Self::cache_error("decode deadline transport",e))?;
        if context.run_id != run || context.terminal || context.deadline_ms != expected
            || context.bot_id != scope.bot_id || context.group_id != scope.group_id
            || context.bcs_session_id.as_deref().unwrap_or(&context.group_id) != scope.session_id
            || active.canonical_run_id != run || active.scope != *scope
            || !matches!(active.transport_owner,bcs_service_api::BotRunTransportOwner::A2a {..})
            || !matches!(parse_transport(&transport.state),Some(ProviderRunTransport::A2a {..})) { return Ok(false); }
        let scope_key = self.active_scope_key(scope)?;
        let Some(scope_value) = self.cache.hash_get(&scope_key, run).await
            .map_err(|e|Self::cache_error("deadline scope read",e))? else { return Ok(false); };
        let indexed:ActiveBotRunContext = serde_json::from_slice(&scope_value)
            .map_err(|e|Self::cache_error("decode deadline scope",e))?;
        if indexed != active { return Ok(false); }
        let mut aliases = vec![run.to_string()];
        if active.downstream_run_id != run { aliases.push(active.downstream_run_id.clone()); }
        let alias_keys:Vec<_> = aliases.iter().map(|alias|self.active_alias_key(alias)).collect();
        let aliases = self.cache.get_values(&alias_keys).await.map_err(|e|Self::cache_error("deadline aliases read",e))?;
        if aliases.len() != alias_keys.len() || aliases.iter().any(|value|value.as_deref() != Some(run.as_bytes())) {
            return Ok(false);
        }
        context.deadline_ms = deadline; active.deadline_ms = deadline; transport.deadline_ms = deadline;
        let context_bytes = serde_json::to_vec(&context).map_err(|e|Self::cache_error("encode deadline context",e))?;
        let active_bytes = serde_json::to_vec(&active).map_err(|e|Self::cache_error("encode deadline index",e))?;
        let transport_bytes = serde_json::to_vec(&transport).map_err(|e|Self::cache_error("encode deadline transport",e))?;
        let ttl = self.lifecycle_ttl(deadline);
        // Update-only cannot resurrect expired transport/index/context keys.
        // Context is written last; any earlier failure prevents usable budget
        // publication and the caller must cancel rather than show an input card.
        for (key, bytes) in [(self.transport_key(run),transport_bytes),(self.active_key(run),active_bytes.clone())] {
            if !self.cache.set_value(&key,bytes,Some(ttl),CacheSetMode::UpdateOnly).await
                .map_err(|e|Self::cache_error("deadline update",e))? { return Ok(false); }
        }
        for key in &alias_keys {
            if !self.cache.expire(key,ttl).await.map_err(|e|Self::cache_error("deadline alias expiry",e))? { return Ok(false); }
        }
        self.cache.hash_set(&scope_key,run,active_bytes).await.map_err(|e|Self::cache_error("deadline scope update",e))?;
        let extend_scope = match self.cache.ttl(&scope_key).await.map_err(|e|Self::cache_error("deadline scope ttl",e))? {
            CacheTtl::Missing => return Ok(false),
            CacheTtl::Persistent => true,
            CacheTtl::ExpiresIn(current) => current < ttl,
        };
        if extend_scope && !self.cache.expire(&scope_key,ttl).await
            .map_err(|e|Self::cache_error("deadline scope expiry",e))? { return Ok(false); }
        if expected <= now_ms() { return Ok(false); }
        self.cache.set_value(&keys[0],context_bytes,Some(ttl),CacheSetMode::UpdateOnly).await
            .map_err(|e|Self::cache_error("deadline context update",e))
    }
}
