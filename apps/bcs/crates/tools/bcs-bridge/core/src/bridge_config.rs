//! Validated, single-deployment collection of independently owned Provider runtimes.
use std::{collections::HashSet, path::{Component, Path, PathBuf}};

use anyhow::bail;
use crate::config::ProviderConfig;

#[derive(Clone)]
pub struct BridgeConfig { pub providers: Vec<ProviderConfig> }

impl BridgeConfig {
    pub fn new(providers: Vec<ProviderConfig>) -> anyhow::Result<Self> {
        if providers.is_empty() { bail!("Bridge requires at least one Provider"); }
        Self::validate_capacity(providers.len(), providers.iter().map(|p| p.bots.len()).sum())?;
        let mut ids = HashSet::new();
        let mut bot_ids = HashSet::new();
        let mut paths = HashSet::new();
        #[cfg(unix)]
        let mut files = HashSet::new();
        let mut workspaces = Vec::new();
        for provider in &providers {
            if !ids.insert(&provider.provider_id) { bail!("Duplicate Provider identity"); }
            if !paths.insert(resolved_path(&provider.state_path)?) { bail!("Providers must use separate state databases"); }
            #[cfg(unix)]
            if let Ok(metadata) = std::fs::metadata(&provider.state_path) {
                use std::os::unix::fs::MetadataExt;
                if !files.insert((metadata.dev(), metadata.ino())) { bail!("Providers must not alias the same state database"); }
            }
            for bot in &provider.bots {
                if let Some(id) = &bot.bot_id {
                    if !bot_ids.insert(id) { bail!("Duplicate Bot ID across Provider bindings"); }
                }
                let subbot = provider.provider_auth.as_deref() == Some("agentpass-subbots");
                let cwd = resolved_path(&bot.cwd)?;
                if workspaces.iter().any(|(other, isolated)| other == &cwd && (subbot || *isolated)) {
                    bail!("Subbots must use exclusive working directories across all Providers");
                }
                workspaces.push((cwd, subbot));
            }
        }
        Ok(Self { providers })
    }

    /// Share runtime limits with registration preflight, before allocating a remote Bot.
    pub fn validate_capacity(providers: usize, bots: usize) -> anyhow::Result<()> {
        if providers > 64 || bots > 256 {
            bail!("Bridge supports at most 64 Providers and 256 Bots per configuration");
        }
        Ok(())
    }

    pub fn bot_count(&self) -> usize { self.providers.iter().map(|p| p.bots.len()).sum() }
}

/// Resolve aliases even when the final database file has not been created yet.
pub fn resolved_path(path: &Path) -> anyhow::Result<PathBuf> {
    let absolute = if path.is_absolute() { path.to_owned() } else { std::env::current_dir()?.join(path) };
    let mut result = PathBuf::new();
    for component in absolute.components() {
        match component {
            Component::CurDir => {},
            Component::ParentDir => { result.pop(); },
            component => {
                result.push(component.as_os_str());
                match std::fs::canonicalize(&result) {
                    Ok(canonical) => result = canonical,
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound => {},
                    Err(_) => bail!("Cannot resolve configured resource path"),
                }
            }
        }
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn provider(id: &str, state: &str, bot: &str) -> ProviderConfig {
        ProviderConfig::from_toml(&format!("provider_id='{id}'\nmode='plugin'\nstate_path='{state}'\n[plugin]\nurl='ws://localhost/ws/bot'\n[[bot]]\nprovider_bot_ref='same-ref'\nbot_id='{bot}'\nengine='codex'\ncwd='/tmp'"), Path::new("/tmp/bridge.toml")).unwrap()
    }
    #[test]
    fn equal_refs_in_different_providers_are_isolated_but_ids_and_databases_are_not_reusable() {
        let a = provider("a", "a.db", "a");
        let b = provider("b", "b.db", "b");
        assert!(BridgeConfig::new(vec![a.clone(), b]).is_ok());
        assert!(BridgeConfig::new(vec![a.clone(), provider("b", "a.db", "b")]).is_err());
        assert!(BridgeConfig::new(vec![a.clone(), provider("b", "b.db", "a")]).is_err());
        assert!(BridgeConfig::new(vec![a.clone(), a]).is_err());
    }
    #[test]
    #[cfg(unix)]
    fn database_aliases_are_rejected_before_opening() {
        let temp = tempfile::tempdir().unwrap();
        let db = temp.path().join("state.db");
        std::fs::write(&db, "untouched").unwrap();
        let alias = temp.path().join("alias.db");
        std::fs::hard_link(&db, &alias).unwrap();
        assert!(BridgeConfig::new(vec![provider("a", db.to_str().unwrap(), "a"), provider("b", alias.to_str().unwrap(), "b")]).is_err());
        assert_eq!(std::fs::read_to_string(db).unwrap(), "untouched");
    }
}
