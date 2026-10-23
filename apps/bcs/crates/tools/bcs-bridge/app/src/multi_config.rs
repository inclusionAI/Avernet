//! Versioned document envelope. Provider runtimes remain independently scoped.
use std::{collections::HashSet, path::Path};

use anyhow::{Context, bail};
use bcs_bridge_core::config::{BridgeConfig, ProviderConfig};

use crate::{config::{self, Bootstrap, RegistrationSettings}, credentials::CredentialSource};

pub(crate) struct Document {
    pub groups: Vec<toml::Table>,
    pub v2: bool,
}

impl Document {
    pub fn parse(text: &str) -> anyhow::Result<Self> {
        let mut root: toml::Table = toml::from_str(text)
            .map_err(|_| anyhow::anyhow!("Invalid bridge TOML configuration"))?;
        if root.contains_key("schema_version") || root.contains_key("provider") {
            if root.remove("schema_version").and_then(|v| v.as_integer()) != Some(2) {
                bail!("Unsupported bridge schema_version; expected 2");
            }
            let values = root.remove("provider").context("V2 configuration requires [[provider]]")?;
            if !root.is_empty() { bail!("V2 configuration cannot mix root Provider fields with [[provider]]"); }
            let groups: Vec<toml::Table> = values.as_array().context("provider must be an array")?.iter()
                .map(|v| v.as_table().cloned().context("Invalid Provider entry")).collect::<anyhow::Result<_>>()?;
            if groups.is_empty() { bail!("Bridge configuration requires at least one Provider"); }
            let mut ids = HashSet::new();
            for group in &groups {
                let id = group.get("provider_id").and_then(toml::Value::as_str)
                    .filter(|id| !id.trim().is_empty()).context("Provider requires provider_id")?;
                if !ids.insert(id) { bail!("Duplicate provider_id in bridge configuration"); }
            }
            Ok(Self { groups, v2: true })
        } else if root.contains_key("provider_id") || root.contains_key("bot") {
            Ok(Self { groups: vec![root], v2: false })
        } else {
            settings(&root)?;
            root.remove("registration");
            root.remove("bcs_to_provider_token");
            if !root.is_empty() { bail!("Before registration, configuration may contain only [registration] settings"); }
            Ok(Self { groups: Vec::new(), v2: false })
        }
    }

    pub fn load(path: &Path) -> anyhow::Result<Self> {
        Self::parse(&std::fs::read_to_string(path).context("Cannot read bridge configuration")?)
    }

    pub fn endpoints(&self) -> Vec<(Option<String>, Option<String>)> {
        self.groups.iter().map(|group| (
            group.get("api_url").or_else(|| group.get("registration").and_then(|r| r.get("api_url")))
                .or_else(|| group.get("bot").and_then(toml::Value::as_array)?.first()?.get("registration")?.get("api_url"))
                .and_then(toml::Value::as_str).map(str::to_owned),
            group.get("plugin").and_then(|p| p.get("url")).and_then(toml::Value::as_str).map(str::to_owned),
        )).collect()
    }

    pub fn bots_document(&self) -> toml::Table {
        let bots = self.groups.iter().filter_map(|g| g.get("bot").and_then(toml::Value::as_array))
            .flatten().cloned().collect();
        toml::Table::from_iter([("bot".into(), toml::Value::Array(bots))])
    }
}

pub(crate) fn settings(group: &toml::Table) -> anyhow::Result<RegistrationSettings> {
    group.get("registration").cloned().map(|value| value.try_into()
        .map_err(|_| anyhow::anyhow!("Invalid registration settings")))
        .unwrap_or_else(|| Ok(RegistrationSettings::default()))
}

pub(crate) struct LoadedProvider {
    pub config: ProviderConfig,
    pub registrations: Vec<RegistrationSettings>,
}

pub(crate) fn load(path: &Path, credentials: &dyn CredentialSource) -> anyhow::Result<Vec<LoadedProvider>> {
    load_document(&Document::load(path)?, path, credentials)
}

pub(crate) fn load_document(document: &Document, path: &Path, credentials: &dyn CredentialSource) -> anyhow::Result<Vec<LoadedProvider>> {
    if document.groups.is_empty() { bail!("Bridge configuration has no registered Providers"); }
    let mut providers = Vec::new();
    for group in &document.groups {
        let mut defaults = settings(group)?;
        if defaults.api_url.is_none() { defaults.api_url = group.get("api_url").and_then(toml::Value::as_str).map(str::to_owned); }
        let registrations = group.get("bot").and_then(toml::Value::as_array).context("Provider has no Bots")?.iter()
            .map(|bot| {
                let bot = bot.as_table().context("Invalid Bot configuration")?;
                let mut settings = if bot.contains_key("registration") { settings(bot)? } else { defaults.clone() };
                if settings.api_url.is_none() { settings.api_url = defaults.api_url.clone(); }
                Ok(settings)
            }).collect::<anyhow::Result<Vec<_>>>()?;
        let token = if group.get("mode").and_then(toml::Value::as_str) == Some("plugin") { None }
            else { Some(credentials.webhook_token(group.get("provider_id").and_then(toml::Value::as_str)
                .context("Gateway configuration requires provider_id")?)?) };
        providers.push(LoadedProvider { config: config::runtime(group, path, token.as_deref())?, registrations });
    }
    BridgeConfig::new(providers.iter().map(|p| p.config.clone()).collect())?;
    Ok(providers)
}

/// Records where a single-provider registration result belongs in the full file.
pub(crate) struct Edit {
    pub source: Document,
    pub target: Option<usize>,
    pub bot: Option<usize>,
}

impl Edit {
    pub fn finish(&self, mut group: toml::Table) -> anyhow::Result<toml::Table> {
        let mut groups = self.source.groups.clone();
        if let (Some(index), Some(bot_index)) = (self.target, self.bot) {
            let mut replacement = group.get("bot").and_then(toml::Value::as_array)
                .and_then(|bots| bots.first()).cloned().context("Replacement has no Bot")?;
            let mut bots = groups[index].get("bot").and_then(toml::Value::as_array)
                .cloned().context("Provider has no Bots")?;
            if bots.len() > 1 {
                // A selected Bot's resolver must not become the new default for
                // its unannotated siblings, including hand-written V2 groups.
                if let Some(settings) = group.get("registration") {
                    replacement.as_table_mut().context("Invalid replacement Bot")?
                        .entry("registration").or_insert(settings.clone());
                }
                match self.source.groups[index].get("registration") {
                    Some(settings) => { group.insert("registration".into(), settings.clone()); },
                    None => { group.remove("registration"); },
                }
            }
            bots[bot_index] = replacement;
            group.insert("bot".into(), toml::Value::Array(bots));
        }
        match self.target { Some(index) => groups[index] = group, None => groups.push(group) }
        if !self.source.v2 && groups.len() == 1 { return Ok(groups.remove(0)); }
        for group in &mut groups {
            // Preserve V1's actual default, not the new Provider database default.
            group.entry("state_path").or_insert("~/.bcn-bridge/bridge-state.sqlite3".into());
            let registration = group.remove("registration");
            if let Some(bots) = group.get_mut("bot").and_then(toml::Value::as_array_mut) {
                for bot in bots {
                    let bot = bot.as_table_mut().context("Invalid Bot configuration")?;
                    if let Some(settings) = &registration { bot.entry("registration").or_insert(settings.clone()); }
                }
            }
        }
        Ok(toml::Table::from_iter([
            ("schema_version".into(), 2.into()),
            ("provider".into(), toml::Value::Array(groups.into_iter().map(toml::Value::Table).collect())),
        ]))
    }
}

pub(crate) fn read_for_registration(path: &Path, auth: &str, provider: &str, mode: &str, add: bool,
    replace: bool, bot_id: Option<&str>) -> anyhow::Result<Bootstrap> {
    let original = config::read_existing(path)?;
    let source = Document::parse(original.as_deref().unwrap_or(""))?;
    if bot_id.is_some() && !replace { bail!("--bot-id must select an existing Bot for reuse or replacement"); }
    if source.groups.is_empty() {
        if replace || bot_id.is_some() { bail!("There is no registered Bot to replace"); }
        return config::read_bootstrap(path);
    }
    let mut target = source.groups.iter().position(|g| g.get("provider_id").and_then(toml::Value::as_str) == Some(provider));
    // Preserve explicit whole-binding replacement for legacy single-Bot files only.
    if replace && target.is_none() && !source.v2 && source.groups.len() == 1 { target = Some(0); }
    if replace && target.is_none() { bail!("Replacement cannot change Provider in a multi-Provider configuration; register the new Provider instead"); }
    let mut selected_bot = None;
    let (settings, previous, document) = if let Some(index) = target {
        let mut group = source.groups[index].clone();
        group.entry("state_path").or_insert("~/.bcn-bridge/bridge-state.sqlite3".into());
        let mut defaults = settings(&group)?;
        if defaults.api_url.is_none() { defaults.api_url = group.get("api_url").and_then(toml::Value::as_str).map(str::to_owned); }
        if replace {
            let bots = group.get("bot").and_then(toml::Value::as_array).context("Provider has no Bots")?;
            let index = match bot_id {
                Some(id) => bots.iter().position(|bot| bot.get("bot_id").and_then(toml::Value::as_str) == Some(id))
                    .context("Selected Bot does not belong to the target Provider")?,
                None if bots.len() == 1 => 0,
                None => bail!("Replacement requires --bot-id when a Provider has multiple Bots"),
            };
            let bot = bots[index].clone();
            if let Some(meta) = bot.get("registration") { defaults = meta.clone().try_into().map_err(|_| anyhow::anyhow!("Invalid Bot registration settings"))?; }
            selected_bot = Some(index);
            group.insert("bot".into(), toml::Value::Array(vec![bot]));
            group.remove("registration");
            (defaults, Some(group), toml::Table::new())
        } else {
            if group.get("mode").and_then(toml::Value::as_str).unwrap_or("gateway") != mode
                || group.get("provider_auth").and_then(toml::Value::as_str) != Some(auth) {
                bail!("Appending requires the same Provider authentication and connection mode");
            }
            if auth != "agentpass-subbots" && !add { bail!("A bridge runtime configuration already exists; reuse it or use --add-bot to explicitly add a Bot"); }
            (defaults, None, group)
        }
    } else { (RegistrationSettings::default(), None, toml::Table::new()) };
    Ok(Bootstrap { settings, original, previous, document,
        edit: Some(Edit { source, target, bot: selected_bot }) })
}

#[cfg(test)]
#[path = "multi_config_tests.rs"]
mod tests;
