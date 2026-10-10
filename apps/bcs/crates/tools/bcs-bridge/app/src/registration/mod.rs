//! Registration workflow and persisted gateway or plugin configuration.
//!
//! Plugin hook points, in order: identity preflight, engine executable lookup
//! (engine registry), webhook credential (gateway mode), endpoint defaults
//! (deploy profile), and the Bot identity, resolved just before the
//! registration request.
mod client;
mod token;

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
    /// Connection mode: gateway webhook delivery, or plugin Bot WebSocket dialed to BCS
    #[arg(long, value_enum, default_value = "gateway")]
    mode: Mode,
    /// How the Provider authenticates its Bots; selects how the Bot identity is obtained
    #[arg(long)]
    provider_auth: String,
    /// Bot identity within its Provider, for Provider types that take it explicitly
    #[arg(long)]
    provider_bot_ref: Option<String>,
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
    let engine_bin = utils::resolve_engine_bin(args.engine_bin.as_deref(), factory.default_bin())?;
    if args.token.trim().is_empty() || args.bot_name.trim().is_empty() { bail!("Registration token and Bot name must be nonempty"); }
    if args.provider_bot_ref.as_deref().is_some_and(|value| !valid_provider_bot_ref(value)) {
        bail!("--provider-bot-ref must be 1-128 characters of letters, digits, '_', '.', ':' or '-'");
    }
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
                None => app.profile().default_upstream_url()?,
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
    let bootstrap = config::read_bootstrap(path)?;
    let provider_id = registration_provider(&args.token, args.mode)?;
    // Plugin mode has no webhook listener, so it needs no webhook credential.
    let webhook_token = match args.mode {
        Mode::Gateway => Some(app.credentials().webhook_token(&provider_id)?),
        Mode::Plugin => None,
    };
    let api_url = match args.api_url.clone().or_else(|| bootstrap.settings.api_url.clone()) {
        Some(url) => url,
        None => app.profile().default_api_url()?,
    };
    let client = RegistrationClient::new(&api_url).context("Invalid registration.api_url or HTTP client configuration")?;
    let mut bot = toml::Table::new();
    bot.insert("provider_bot_ref".into(), "pending-identity".into());
    bot.insert("engine".into(), args.engine.clone().into());
    bot.insert("cwd".into(), cwd.to_str().context("Engine working directory must be UTF-8")?.into());
    if let Some(model) = args.model { bot.insert("model".into(), model.into()); }
    bot.insert("engine_bin".into(), engine_bin.to_str().context("Engine executable path must be UTF-8")?.into());
    let mut document = toml::Table::new();
    document.insert("provider_id".into(), provider_id.clone().into());
    document.insert("mode".into(), args.mode.name().into());
    document.insert("state_path".into(), "bridge-state.sqlite3".into());
    match args.mode {
        Mode::Gateway => { document.insert("listen".into(), args.listen.to_string().into()); }
        // Timer defaults come from the runtime configuration schema.
        Mode::Plugin => {
            let url = upstream_url.clone().expect("plugin URL resolved during argument validation");
            let mut plugin = toml::Table::new();
            plugin.insert("url".into(), url.into());
            document.insert("plugin".into(), toml::Value::Table(plugin));
        }
    }
    document.insert("bot".into(), toml::Value::Array(vec![toml::Value::Table(bot.clone())]));
    // The plugin document cannot fully validate until the Bot identity is known
    // (plugin bots require bot_id); its URL was validated above.
    if webhook_token.is_some() { config::runtime(&document, path, webhook_token.as_deref())?; }
    let prepared = PreparedConfig::new(path, bootstrap.original)?;
    let context = IdentityContext { mode: args.mode, client: &client, explicit_ref: args.provider_bot_ref.as_deref() };
    let provider_bot_ref = identity.provider_bot_ref(&context).await?;
    if !valid_provider_bot_ref(&provider_bot_ref) { bail!("The Provider identity is not a valid provider_bot_ref"); }
    let registration = client.register(&args.token, args.bot_name.trim(), &provider_bot_ref, args.mode.name(), webhook_url, &provider_id).await?;
    document.insert("provider_id".into(), registration.registration.provider_id.into());
    bot.insert("provider_bot_ref".into(), provider_bot_ref.into());
    bot.insert("bot_id".into(), registration.bot_uuid.into());
    bot.insert("token".into(), registration.bot_token.into());
    document.insert("bot".into(), toml::Value::Array(vec![toml::Value::Table(bot)]));
    config::runtime(&document, path, webhook_token.as_deref())?;
    // Persist the resolved api_url so the saved configuration is self-contained.
    let resolved = config::RegistrationSettings { api_url: Some(api_url) };
    document.insert("registration".into(), toml::Value::try_from(resolved).context("Cannot encode API settings")?);
    let text = toml::to_string_pretty(&document).context("Cannot encode registered bridge configuration")?;
    prepared.save(&text)?;
    println!("Bot registered in {} mode. Configuration saved to {}. Start with bcs-bridge start --config {}", args.mode.name(), path.display(), path.display());
    Ok(())
}
