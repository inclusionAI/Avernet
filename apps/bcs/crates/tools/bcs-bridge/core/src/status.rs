//! Credential-free local observations. Process readiness is not remote connectivity.
use std::{collections::BTreeMap, sync::{Arc, RwLock}};
use serde::{Deserialize, Serialize};
use crate::config::ProviderConfig;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct BotStatus {
    pub provider_id: String,
    pub provider_bot_ref: String,
    pub bot_id: Option<String>,
    pub engine: String,
    pub state: String,
}

#[derive(Clone, Default)]
pub struct BridgeStatus(Arc<RwLock<BTreeMap<(String, String), BotStatus>>>);

impl BridgeStatus {
    pub fn register(&self, provider: &ProviderConfig) {
        let mut statuses = self.0.write().unwrap_or_else(|e| e.into_inner());
        for bot in &provider.bots {
            statuses.insert((provider.provider_id.clone(), bot.provider_bot_ref.clone()), BotStatus {
                provider_id: provider.provider_id.clone(), provider_bot_ref: bot.provider_bot_ref.clone(),
                bot_id: bot.bot_id.clone(), engine: bot.engine.clone(), state: "starting".into(),
            });
        }
    }
    pub fn set(&self, provider: &str, bot: &str, state: &str) {
        if let Some(status) = self.0.write().unwrap_or_else(|e| e.into_inner()).get_mut(&(provider.into(), bot.into())) {
            status.state = state.into();
        }
    }
    pub fn provider(&self, provider: &str, state: &str) {
        for status in self.0.write().unwrap_or_else(|e| e.into_inner()).values_mut() {
            if status.provider_id == provider { status.state = state.into(); }
        }
    }
    pub fn snapshot(&self) -> Vec<BotStatus> {
        self.0.read().unwrap_or_else(|e| e.into_inner()).values().cloned().collect()
    }
}
