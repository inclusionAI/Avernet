//! Registration workflow and persisted gateway or plugin configuration.
//!
//! Plugin hook points, in order: identity preflight, engine executable lookup
//! (engine registry), webhook credential (gateway mode), endpoint defaults
//! (deploy profile), and the Bot identity, resolved just before the
//! registration request.
mod client;
mod token;
mod pending;
#[cfg(test)]
mod subbots_tests;

use std::{net::SocketAddr, path::{Path, PathBuf}};

use anyhow::{Context, bail};
use clap::Args;

pub use client::RegistrationClient;
pub use token::{Mode, registration_provider};

use crate::app::BridgeApp;
use crate::config::{self, PreparedConfig};
use crate::identity::IdentityContext;
use crate::utils;

#[derive(Args)]
pub struct RegisterArgs {
    /// Previously obtained Provider-scoped registration token
    #[arg(long, env = "BRIDGE_REGISTER_TOKEN", hide_env_values = true)]
    token: String,
    /// Display name for the registered Bot
    #[arg(long)]
    bot_name: String,
    /// Connection mode: plugin Bot WebSocket dialed to BCS (default, matching the
    /// BCS register API), or gateway webhook delivery
    #[arg(long, value_enum, default_value = "plugin")]
    mode: Mode,
    /// How the Provider authenticates its Bots; selects how the Bot identity is obtained
    #[arg(long)]
    provider_auth: String,
    /// Bot identity within its Provider, for Provider types that take it explicitly
    #[arg(long)]
    provider_bot_ref: Option<String>,
    /// Stable identifier under the AgentPass parent; generated and saved when omitted.
    #[arg(long)]
    bot_slug: Option<String>,
    /// Registration API prefix (default: [registration] api_url, then the deploy profile)
    #[arg(long)]
    api_url: Option<String>,
    /// Externally reachable webhook URL forwarded to this bridge's /webhook (gateway mode only)
    #[arg(long)]
    webhook_url: Option<String>,
    /// Local gateway listener (gateway mode only)
    #[arg(long, default_value = "0.0.0.0:8322")]
    listen: SocketAddr,
    /// BCS Bot WebSocket endpoint dialed by plugin mode (default: from the deploy profile)
    #[arg(long)]
    upstream_url: Option<String>,
    /// Engine id
    #[arg(long)]
    engine: String,
    /// Engine working directory (default: ~/workspace; created if missing)
    #[arg(long)]
    cwd: Option<PathBuf>,
    /// Model passed to the engine
    #[arg(long)]
    model: Option<String>,
    /// Override the engine executable (default: the engine's executable on PATH)
    #[arg(long)]
    engine_bin: Option<PathBuf>,
}

/// Registration API rule for `provider_bot_ref` values.
fn valid_provider_bot_ref(value: &str) -> bool {
    !value.is_empty() && value.len() <= 128
        && value.bytes().all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'.' | b':' | b'-'))
}

pub async fn register(args: RegisterArgs, path: &Path, app: &BridgeApp) -> anyhow::Result<()> {
    let identity = app.identity(&args.provider_auth)?;
    identity.preflight()?;
    let factory = app.engines().get(&args.engine).with_context(|| format!(
        "Unknown engine '{}'; available engines: {}", args.engine, app.engines().ids().collect::<Vec<_>>().join(", ")))?;
    if args.provider_auth == "agentpass-subbots" && !factory.supports_caller_env() {
        bail!("Engine '{}' cannot propagate subbot identity", args.engine);
    }
    let engine_bin = if factory.requires_bin() {
        Some(utils::resolve_engine_bin(args.engine_bin.as_deref(), factory.default_bin())?)
    } else {
        if args.engine_bin.is_some() { bail!("--engine-bin does not apply to engine '{}'", args.engine); }
        None
    };
    if args.token.trim().is_empty() || args.bot_name.trim().is_empty() { bail!("Registration token and Bot name must be nonempty"); }
    if args.provider_bot_ref.as_deref().is_some_and(|value| !valid_provider_bot_ref(value)) {
        bail!("--provider-bot-ref must be 1-128 characters of letters, digits, '_', '.', ':' or '-'");
    }
    let provider_id = registration_provider(&args.token, args.mode)?;
    let bootstrap = config::read_for_registration(path, &args.provider_auth, &provider_id, args.mode.name())?;
    let (webhook_url, upstream_url) = match args.mode {
        Mode::Gateway => {
            if args.upstream_url.is_some() { bail!("--upstream-url applies to plugin mode; gateway Bots receive work through webhook delivery"); }
            let webhook = args.webhook_url.as_deref().context("--webhook-url is required in gateway mode")?;
            client::http_url(webhook, true).context("Invalid --webhook-url")?;
            if args.listen.port() == 0 { bail!("--listen must specify a nonzero port for the configured webhook"); }
            (Some(webhook), None)
        }
        Mode::Plugin => {
            if args.webhook_url.is_some() { bail!("--webhook-url applies to gateway mode; plugin Bots receive work over their WebSocket connection"); }
            let url = match args.upstream_url.as_deref() {
                Some(url) => url.to_string(),
                None => bootstrap.document.get("plugin").and_then(|p| p.get("url")).and_then(toml::Value::as_str).map(str::to_owned).map(Ok).unwrap_or_else(|| app.profile().default_upstream_url())?,
            };
            client::ws_url(&url).context("Invalid --upstream-url")?;
            (None, Some(url))
        }
    };
    let cwd = match args.cwd.clone() {
        Some(cwd) => cwd,
        None => {
            let workspace = dirs::home_dir().context("Cannot locate home directory; specify --cwd")?.join("workspace");
            std::fs::create_dir_all(&workspace).context("Cannot create default engine working directory")?;
            workspace
        }
    }.canonicalize().context("Cannot resolve engine working directory")?;
    if !cwd.is_dir() { bail!("Engine working directory must be a directory"); }
    // Plugin mode has no webhook listener, so it needs no webhook credential.
    let webhook_token = match args.mode {
        Mode::Gateway => Some(app.credentials().webhook_token(&provider_id)?),
        Mode::Plugin => None,
    };
    let api_url = match args.api_url.clone().or_else(|| bootstrap.settings.api_url.clone()) {
        Some(url) => url,
        None => app.profile().default_api_url()?,
    };
    if !bootstrap.document.is_empty() {
        config::runtime(&bootstrap.document, path, webhook_token.as_deref())?;
        if bootstrap.document.get("api_url").and_then(toml::Value::as_str) != Some(api_url.as_str()) {
            bail!("Appending a Bot requires the saved API URL");
        }
        if let Some(url) = &upstream_url {
            if bootstrap.document.get("plugin").and_then(|p| p.get("url")).and_then(toml::Value::as_str) != Some(url.as_str()) {
                bail!("Appending a Bot requires the saved plugin upstream URL");
            }
        }
    }
    let client = RegistrationClient::new(&api_url).context("Invalid registration.api_url or HTTP client configuration")?;
    let mut bot = toml::Table::new();
    bot.insert("provider_bot_ref".into(), "pending-identity".into());
    bot.insert("engine".into(), args.engine.clone().into());
    bot.insert("cwd".into(), cwd.to_str().context("Engine working directory must be UTF-8")?.into());
    if let Some(model) = args.model { bot.insert("model".into(), model.into()); }
    if let Some(engine_bin) = engine_bin {
        bot.insert("engine_bin".into(), engine_bin.to_str().context("Engine executable path must be UTF-8")?.into());
    }
    let mut document = bootstrap.document;
    let append = document.contains_key("provider_id");
    let mut existing_bots = document.remove("bot").and_then(|v| v.as_array().cloned()).unwrap_or_default();
    document.insert("provider_id".into(), provider_id.clone().into());
    document.insert("mode".into(), args.mode.name().into());
    document.entry("state_path").or_insert("bridge-state.sqlite3".into());
    document.insert("provider_auth".into(), args.provider_auth.clone().into());
    document.insert("api_url".into(), api_url.clone().into());
    match args.mode {
        Mode::Gateway => { if !append { document.insert("listen".into(), args.listen.to_string().into()); } }
        // Timer defaults come from the runtime configuration schema.
        Mode::Plugin => {
            let url = upstream_url.clone().expect("plugin URL resolved during argument validation");
            let mut plugin = toml::Table::new();
            plugin.insert("url".into(), url.into());
            if !append { document.insert("plugin".into(), toml::Value::Table(plugin)); }
        }
    }
    document.insert("bot".into(), toml::Value::Array(vec![toml::Value::Table(bot.clone())]));
    // The plugin document cannot fully validate until the Bot identity is known
    // (plugin bots require bot_id); its URL was validated above.
    if webhook_token.is_some() && args.provider_auth != "agentpass-subbots" { config::runtime(&document, path, webhook_token.as_deref())?; }
    let prepared = PreparedConfig::new(path, bootstrap.original)?;
    #[cfg(unix)]
    let _runtime_guard = crate::instance::registration_guard(&crate::cli::runtime_dir()?)?;
    let context = IdentityContext { mode: args.mode, client: &client, explicit_ref: args.provider_bot_ref.as_deref() };
    let provider_bot_ref = identity.provider_bot_ref(&context).await?;
    if !valid_provider_bot_ref(&provider_bot_ref) { bail!("The Provider identity is not a valid provider_bot_ref"); }
    if append && existing_bots.iter().any(|item| item.get("agent_code").and_then(toml::Value::as_str) != Some(provider_bot_ref.as_str())) {
        bail!("Appending a Bot requires the same AgentPass parent");
    }
    let subbots = args.provider_auth == "agentpass-subbots";
    let slug = if subbots {
        let request = serde_json::to_string(&serde_json::json!({
            "provider_id":provider_id, "parent":provider_bot_ref, "api_url":api_url,
            "mode":args.mode.name(), "name":args.bot_name.trim(), "bot":bot,
            "webhook":webhook_url,
        }))?;
        Some(pending::prepare(path, &request, args.bot_slug.as_deref())?)
    } else { None };
    if let Some(slug) = &slug {
        if existing_bots.iter().any(|item| item.get("provider_bot_ref").and_then(toml::Value::as_str) == Some(format!("{provider_bot_ref}:{slug}").as_str())) {
            bail!("This Bot is already present in the local bridge configuration");
        }
    }
    if subbots { pending::submitted(path)?; }
    let registration = client.register(&args.token, args.bot_name.trim(), &provider_bot_ref, args.mode.name(), webhook_url, &provider_id, slug.as_deref()).await?;
    document.insert("provider_id".into(), registration.registration.provider_id.into());
    bot.insert("provider_bot_ref".into(), registration.registration.provider_bot_ref.into());
    if let Some(slug) = &slug {
        bot.insert("agent_code".into(), provider_bot_ref.into());
        bot.insert("bot_slug".into(), slug.clone().into());
    }
    bot.insert("bot_id".into(), registration.bot_uuid.into());
    bot.insert("token".into(), registration.bot_token.into());
    existing_bots.push(toml::Value::Table(bot));
    document.insert("bot".into(), toml::Value::Array(existing_bots));
    // Persist the resolved api_url so the saved configuration is self-contained.
    let resolved = config::RegistrationSettings { api_url: Some(api_url) };
    document.insert("registration".into(), toml::Value::try_from(resolved).context("Cannot encode API settings")?);
    let text = toml::to_string_pretty(&document).context("Cannot encode registered bridge configuration")?;
    if config::runtime(&document, path, webhook_token.as_deref()).is_err() {
        return prepared.save_recovery(&text, "runtime configuration validation failed");
    }
    prepared.save(&text)?;
    if subbots { pending::complete(path)?; }
    println!("Bot registered in {} mode. Configuration saved to {}. Start with bcs-bridge start --config {}", args.mode.name(), path.display(), path.display());
    Ok(())
}
