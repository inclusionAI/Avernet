use std::path::Path;

use anyhow::{Context, bail};

use super::{RegisterArgs, RegistrationClient, Mode, client};
use crate::config::{self, Bootstrap, PreparedConfig};

/// Saved webhook defaults apply only to the same gateway Bot, never a new binding.
pub(super) fn saved_webhook<'a>(args: &RegisterArgs, source: &'a Bootstrap, provider: &str, api: &str) -> Option<&'a str> {
    let previous = source.previous.as_ref()?;
    let bots = previous.get("bot")?.as_array()?;
    if previous.get("provider_id")?.as_str()? != provider
        || source.settings.api_url.as_deref()?.trim_end_matches('/') != api.trim_end_matches('/')
        || previous.get("mode")?.as_str()? != "gateway" || bots.len() != 1
        || bots[0].get("engine")?.as_str()? != args.engine
        || args.provider_bot_ref.as_deref().is_some_and(|reference|
            bots[0].get("provider_bot_ref").and_then(toml::Value::as_str) != Some(reference)) {
        return None;
    }
    source.settings.webhook_url.as_deref()
}

/// Retain credentials and durable state when replacing settings for the same binding.
pub(super) async fn update(
    args: &RegisterArgs, path: &Path, source: &Bootstrap, app: &crate::app::BridgeApp, provider: &str, api: &str,
    client: &RegistrationClient, webhook_token: Option<&str>, engine_bin: Option<&Path>,
    engine_options: &toml::Table,
) -> anyhow::Result<bool> {
    let Some(previous) = &source.previous else { return Ok(false); };
    let same_provider = previous.get("provider_id").and_then(toml::Value::as_str) == Some(provider);
    let same_api = source.settings.api_url.as_deref().is_some_and(|old|
        old.trim_end_matches('/') == api.trim_end_matches('/'));
    if !same_provider || !same_api {
        if source.edit.as_ref().is_some_and(|edit| edit.source.v2 || edit.source.groups.iter().any(|g| g.get("bot").and_then(toml::Value::as_array).is_some_and(|b| b.len() > 1))) { bail!("Replacement cannot change a Provider binding in a V2 configuration"); }
        return Ok(false);
    }
    let bots = previous.get("bot").and_then(toml::Value::as_array).context("Configuration has no Bots")?;
    if bots.len() != 1 { bail!("Replacement requires a single Bot configuration"); }
    let old_bot = bots[0].as_table().context("Invalid saved Bot")?;
    let reference = old_bot.get("provider_bot_ref").and_then(toml::Value::as_str).context("Missing saved Bot reference")?;
    if args.provider_bot_ref.as_deref().is_some_and(|requested| requested != reference) {
        if source.edit.as_ref().is_some_and(|edit| edit.source.v2 || edit.source.groups.iter().any(|g| g.get("bot").and_then(toml::Value::as_array).is_some_and(|b| b.len() > 1))) { bail!("Replacement cannot change a Bot reference in a V2 configuration"); }
        return Ok(false);
    }
    if previous.get("mode").and_then(toml::Value::as_str) != Some(args.mode.name()) {
        bail!("Changing an existing Bot's connection mode is unsupported; use a new profile and Bot reference");
    }
    let bot_id = old_bot.get("bot_id").and_then(toml::Value::as_str).context("Missing saved Bot ID")?;
    let siblings = source.edit.as_ref().and_then(|edit| edit.target.map(|i| &edit.source.groups[i]))
        .and_then(|group| group.get("bot")).and_then(toml::Value::as_array).is_some_and(|bots| bots.len() > 1);
    if siblings && (args.upstream_url.as_deref().is_some_and(|url|
        previous.get("plugin").and_then(|p| p.get("url")).and_then(toml::Value::as_str) != Some(url))
        || (args.listen_is_explicit && previous.get("listen").and_then(toml::Value::as_str) != Some(args.listen.to_string().as_str()))) {
        bail!("A Bot replacement cannot change settings shared by other Bots in this Provider");
    }
    let mut document = previous.clone();
    let mut bot = old_bot.clone();
    let same_engine = bot.get("engine").and_then(toml::Value::as_str) == Some(args.engine.as_str());
    if !same_engine { bail!("Changing an existing Bot's engine is unsupported; use a new profile and Bot reference"); }
    if !engine_options.is_empty() { bot.insert("engine_options".into(), engine_options.clone().into()); }
    if let Some(cwd) = &args.cwd {
        let cwd = cwd.canonicalize().context("Cannot resolve requested working directory")?;
        bot.insert("cwd".into(), cwd.to_str().context("Working directory must be UTF-8")?.into());
    }
    if args.model_is_explicit {
        if let Some(model) = &args.model { bot.insert("model".into(), model.clone().into()); }
    }
    if args.engine_bin.is_some() {
        let executable = engine_bin.context("Missing requested engine executable")?;
        bot.insert("engine_bin".into(), executable.to_str().context("Executable must be UTF-8")?.into());
    }
    let webhook = if args.mode == Mode::Gateway {
        if args.listen_is_explicit { document.insert("listen".into(), args.listen.to_string().into()); }
        args.webhook_url.as_deref().or(source.settings.webhook_url.as_deref())
    } else { None };
    let webhook_changed = args.mode == Mode::Gateway && match (source.settings.webhook_url.as_deref(), webhook) {
        (Some(old), Some(new)) => client::http_url(old, true)? != client::http_url(new, true)?,
        _ => true,
    };
    let admin = if webhook_changed {
        Some(args.provider_admin_token.as_deref().filter(|token| !token.trim().is_empty())
            .context("Changing or verifying a gateway webhook requires --provider-admin-token (or BRIDGE_PROVIDER_ADMIN_TOKEN); registration tokens cannot update an existing Bot")?)
    } else { None };
    if let Some(upstream) = &args.upstream_url {
        document.get_mut("plugin").and_then(toml::Value::as_table_mut)
            .context("Missing saved plugin configuration")?.insert("url".into(), upstream.clone().into());
    }
    document.insert("bot".into(), toml::Value::Array(vec![toml::Value::Table(bot)]));
    config::runtime(&document, path, webhook_token)?;
    let settings = config::RegistrationSettings { api_url: Some(api.into()), webhook_url: webhook.map(str::to_owned), provider_auth: source.settings.provider_auth.clone() };
    let settings = toml::Value::try_from(settings)?;
    if let Some(bot) = document.get_mut("bot").and_then(toml::Value::as_array_mut).and_then(|bots| bots.first_mut()).and_then(toml::Value::as_table_mut) {
        if bot.contains_key("registration") { bot.insert("registration".into(), settings.clone()); }
    }
    document.insert("registration".into(), settings);
    let document = source.finish(document)?;
    let text = toml::to_string_pretty(&document)?;
    let merged = crate::multi_config::Document::parse(&text)?;
    let providers = crate::multi_config::load_document(&merged, path, app.credentials())?;
    app.engines().validate_resources(providers.iter().flat_map(|p| &p.config.bots))?;
    let prepared = PreparedConfig::new(path, source.original.clone())?;
    let backup = prepared.backup_original()?;
    if let Some(admin) = admin {
        client.update_webhook(provider, reference, bot_id, webhook.context("Missing gateway webhook")?, admin).await?;
    }
    prepared.save(&text)?;
    println!("Bot ID: {bot_id}");
    println!("Bridge settings updated; existing Bot identity, credentials and state retained. Backup: {}", backup.display());
    Ok(true)
}
