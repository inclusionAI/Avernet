//! The auth plugin trait and the priority-ordered chain that runs it.

use async_trait::async_trait;
use axum::http::HeaderMap;

use crate::types::{AuthError, AuthPrincipal, AuthResult};

/// One authentication source. `can_authenticate` is a pure header-shape check
/// (no IO); `authenticate` does the real work.
#[async_trait]
pub trait AuthPlugin: Send + Sync {
    /// Pure format check, no IO. If false, the chain skips this plugin.
    fn can_authenticate(&self, headers: &HeaderMap) -> bool;

    /// `None` = skip (not my request), `Some` = hit, `Err` = hard failure.
    async fn authenticate(
        &self,
        headers: &HeaderMap,
    ) -> Result<Option<AuthPrincipal>, AuthError>;

    /// Lower = higher priority.
    fn priority(&self) -> u8;

    fn name(&self) -> &'static str;
}

/// Priority-ordered chain. Constructed sorted; runs first-writer-wins.
pub struct AuthPluginChain {
    plugins: Vec<Box<dyn AuthPlugin>>,
}

impl AuthPluginChain {
    pub fn new(mut plugins: Vec<Box<dyn AuthPlugin>>) -> Self {
        plugins.sort_by_key(|p| p.priority());
        Self { plugins }
    }

    /// Run the chain. First plugin to return `Some` wins. All `None` => anonymous.
    pub async fn authenticate(&self, headers: &HeaderMap) -> Result<AuthResult, AuthError> {
        let chain_started = std::time::Instant::now();
        for (plugin_index, plugin) in self.plugins.iter().enumerate() {
            let name = plugin.name();
            if !plugin.can_authenticate(headers) {
                bcs_observability::count("auth.plugin", "skipped");
                tracing::debug!(plugin = name, "auth: plugin skipped (header shape mismatch)");
                continue;
            }
            let started = std::time::Instant::now();
            let result = bcs_observability::observe_result("auth.plugin", plugin.authenticate(headers)).await;
            tracing::info!(target: "bcs_observation", request_id = %bcs_observability::current_request_id(), trace_id = %bcs_observability::current_trace_id(), plugin = name, plugin_index,
                duration_ms = started.elapsed().as_secs_f64() * 1000.0,
                chain_elapsed_ms = chain_started.elapsed().as_secs_f64() * 1000.0,
                outcome = match &result { Ok(Some(_)) => "success", Ok(None) => "no_match", Err(_) => "error" },
                "auth.plugin.finished");
            match result {
                Ok(Some(principal)) => {
                    tracing::info!(
                        plugin = name,
                        user_id = principal.user_id.as_deref().unwrap_or(""),
                        bot_uuid = principal.bot_uuid.as_deref().unwrap_or(""),
                        owner_id = principal.owner_id.as_deref().unwrap_or(""),
                        "auth: success"
                    );
                    return Ok(AuthResult {
                        principal: Some(principal),
                    });
                }
                Ok(None) => {
                    tracing::debug!(plugin = name, "auth: plugin no match, trying next");
                }
                Err(e) => {
                    tracing::warn!(plugin = name, outcome = "error", "auth: plugin failed");
                    return Err(e);
                }
            }
        }
        tracing::info!(duration_ms = chain_started.elapsed().as_secs_f64() * 1000.0, "auth: anonymous (no plugin matched)");
        Ok(AuthResult { principal: None })
    }

    /// Plugin names in run order (for diagnostics/tests).
    pub fn plugin_names(&self) -> Vec<&'static str> {
        self.plugins.iter().map(|p| p.name()).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::AuthSource;

    struct FixedPlugin {
        prio: u8,
        name: &'static str,
        can: bool,
        result: Option<String>, // Some(user_id) => hit
    }

    #[async_trait]
    impl AuthPlugin for FixedPlugin {
        fn can_authenticate(&self, _h: &HeaderMap) -> bool {
            self.can
        }
        async fn authenticate(
            &self,
            _h: &HeaderMap,
        ) -> Result<Option<AuthPrincipal>, AuthError> {
            Ok(self.result.clone().map(|uid| {
                let mut p = AuthPrincipal::new(AuthSource::Local);
                p.user_id = Some(uid);
                p
            }))
        }
        fn priority(&self) -> u8 {
            self.prio
        }
        fn name(&self) -> &'static str {
            self.name
        }
    }

    #[tokio::test]
    async fn sorts_by_priority() {
        let chain = AuthPluginChain::new(vec![
            Box::new(FixedPlugin { prio: 30, name: "c", can: false, result: None }),
            Box::new(FixedPlugin { prio: 10, name: "a", can: false, result: None }),
            Box::new(FixedPlugin { prio: 20, name: "b", can: false, result: None }),
        ]);
        assert_eq!(chain.plugin_names(), vec!["a", "b", "c"]);
    }

    #[tokio::test]
    async fn first_writer_wins() {
        let chain = AuthPluginChain::new(vec![
            Box::new(FixedPlugin { prio: 20, name: "second", can: true, result: Some("two".into()) }),
            Box::new(FixedPlugin { prio: 10, name: "first", can: true, result: Some("one".into()) }),
        ]);
        let r = chain.authenticate(&HeaderMap::new()).await.unwrap();
        assert_eq!(r.principal.unwrap().user_id.as_deref(), Some("one"));
    }

    #[tokio::test]
    async fn skips_cannot_authenticate() {
        let chain = AuthPluginChain::new(vec![
            Box::new(FixedPlugin { prio: 10, name: "skip", can: false, result: Some("nope".into()) }),
            Box::new(FixedPlugin { prio: 20, name: "hit", can: true, result: Some("yes".into()) }),
        ]);
        let r = chain.authenticate(&HeaderMap::new()).await.unwrap();
        assert_eq!(r.principal.unwrap().user_id.as_deref(), Some("yes"));
    }

    #[tokio::test]
    async fn all_none_is_anonymous() {
        let chain = AuthPluginChain::new(vec![
            Box::new(FixedPlugin { prio: 10, name: "a", can: true, result: None }),
            Box::new(FixedPlugin { prio: 20, name: "b", can: false, result: Some("x".into()) }),
        ]);
        let r = chain.authenticate(&HeaderMap::new()).await.unwrap();
        assert!(r.principal.is_none());
    }
}
