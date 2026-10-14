//! Registration workflow and persisted gateway or plugin configuration.
//!
//! Plugin hook points, in order: identity preflight, engine executable lookup
//! (engine registry), webhook credential (gateway mode), endpoint defaults
//! (deploy profile), and the Bot identity, resolved just before the
//! registration request.
mod client;
mod token;
mod replacement;

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
    /// Back up and replace existing runtime configuration after stopping the bridge
    #[arg(long)]
    replace_existing: bool,
    /// Provider administrator credential, required only to change an existing gateway webhook
    #[arg(long, env = "BRIDGE_PROVIDER_ADMIN_TOKEN", hide_env_values = true)]
    provider_admin_token: Option<String>,
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
    /// Registration API prefix (default: [registration] api_url, then the deploy profile)
    #[arg(long)]
    api_url: Option<String>,
    /// Externally reachable webhook URL forwarded to this bridge's /webhook (gateway mode only)
    #[arg(long)]
    webhook_url: Option<String>,
    /// Local gateway listener (gateway mode only)
    #[arg(long, default_value = "0.0.0.0:8322")]
    listen: SocketAddr,
    #[arg(skip)]
    pub(crate) listen_is_explicit: bool,
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
    pub(crate) model: Option<String>,
    #[arg(skip)]
    pub(crate) model_is_explicit: bool,
    /// Override the engine executable (default: the engine's executable on PATH)
    #[arg(long)]
    engine_bin: Option<PathBuf>,
}

impl RegisterArgs {
    /// A repeated profile command keeps the existing identity and never contacts registration.
    pub(crate) fn reuse_profile(&self, path: &Path, app: &BridgeApp) -> anyhow::Result<bool> {
        if self.replace_existing || !path.try_exists().context("Cannot inspect profile configuration")? { return Ok(false); }
        let source = config::read_registration_source(path, true)?;
        let Some(previous) = source.previous else { return Ok(false); };
        let provider_id = registration_provider(&self.token, self.mode)?;
        let bots = previous.get("bot").and_then(toml::Value::as_array).context("Profile has no Bots")?;
        let matches = previous.get("provider_id").and_then(toml::Value::as_str) == Some(provider_id.as_str())
            && previous.get("mode").and_then(toml::Value::as_str) == Some(self.mode.name())
            && bots.len() == 1 && bots[0].get("engine").and_then(toml::Value::as_str) == Some(self.engine.as_str());
        if !matches { bail!("Profile belongs to another provider, mode or engine; choose another --profile or stop and use --replace-existing"); }
        if let Some(reference) = self.provider_bot_ref.as_deref() {
            if !valid_provider_bot_ref(reference) { bail!("Invalid --provider-bot-ref"); }
            if bots[0].get("provider_bot_ref").and_then(toml::Value::as_str) != Some(reference) {
                bail!("Profile has a different provider Bot reference; choose another --profile or stop and use --replace-existing");
            }
        }
        let api = match self.api_url.clone().or(source.settings.api_url.clone()) {
            Some(api) => api, None => app.profile().default_api_url()?,
        };
        let same_api = source.settings.api_url.as_deref().is_some_and(|old| old.trim_end_matches('/') == api.trim_end_matches('/'));
        let same_upstream = match self.mode {
            Mode::Plugin => {
                let desired = match self.upstream_url.clone() { Some(url) => url, None => app.profile().default_upstream_url()? };
                previous.get("plugin").and_then(|p| p.get("url")).and_then(toml::Value::as_str)
                    .is_some_and(|old| old.trim_end_matches('/') == desired.trim_end_matches('/'))
            }
            Mode::Gateway => {
                let original = source.settings.webhook_url.as_deref()
                    .context("Cannot verify this profile's gateway webhook; stop and register with --replace-existing")?;
                let desired = self.webhook_url.as_deref().unwrap_or(original);
                client::http_url(original, true).context("Invalid saved gateway webhook")?
                    == client::http_url(desired, true).context("Invalid --webhook-url")?
            }
        };
        if !same_api || !same_upstream { bail!("Profile targets a different BCS endpoint or gateway webhook; choose another --profile or stop and use --replace-existing"); }
        if let Some(cwd) = &self.cwd {
            let desired = cwd.canonicalize().context("Cannot resolve requested working directory")?;
            let original = bots[0].get("cwd").and_then(toml::Value::as_str).context("Profile has no working directory")?;
            if desired != Path::new(original).canonicalize().context("Cannot resolve profile working directory")? {
                bail!("Profile has a different working directory; choose another --profile or use --replace-existing");
            }
        }
        if self.model_is_explicit && self.model.as_deref().is_some_and(|model| bots[0].get("model").and_then(toml::Value::as_str) != Some(model)) {
            bail!("Profile has a different model; use --replace-existing to change its settings");
        }
        if let Some(engine_bin) = self.engine_bin.as_deref() {
            let factory = app.engines().get(&self.engine).context("Unknown profile engine")?;
            if !factory.requires_bin() { bail!("--engine-bin does not apply to engine '{}'", self.engine); }
            let desired = utils::resolve_engine_bin(Some(engine_bin), factory.default_bin())?;
            let original = bots[0].get("engine_bin").and_then(toml::Value::as_str)
                .context("Profile has no saved engine executable; use --replace-existing")?;
            // Keep executable path identity: symlink names can select wrapper behavior.
            if desired != Path::new(original) {
                bail!("Profile has a different engine executable; choose another --profile or use --replace-existing");
            }
        }
        if self.mode == Mode::Gateway && self.listen_is_explicit {
            let original: SocketAddr = previous.get("listen").and_then(toml::Value::as_str)
                .context("Profile has no gateway listener")?.parse().context("Invalid profile gateway listener")?;
            if self.listen != original {
                bail!("Profile has a different gateway listener; choose another --profile or use --replace-existing");
            }
        }
        config::load_runtime(path, app.credentials())?;
        println!("Profile is already registered. Existing Bot identity, engine settings and state are retained; no registration request was sent. Use init/start with the same --profile.");
        Ok(true)
    }
}

/// Registration API rule for `provider_bot_ref` values.
fn valid_provider_bot_ref(value: &str) -> bool {
    !value.is_empty() && value.len() <= 128
        && value.bytes().all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'.' | b':' | b'-'))
}

pub async fn register(args: RegisterArgs, path: &Path, app: &BridgeApp) -> anyhow::Result<()> {
    let identity = app.identity(&args.provider_auth)?;
    identity.preflight()?;
    let bootstrap = if args.replace_existing { config::read_registration_source(path, true)? } else { config::read_bootstrap(path)? };
    let saved_bin = bootstrap.previous.as_ref().and_then(|previous| previous.get("bot"))
        .and_then(toml::Value::as_array).filter(|bots| bots.len() == 1)
        .filter(|bots| bots[0].get("engine").and_then(toml::Value::as_str) == Some(args.engine.as_str()))
        .and_then(|bots| bots[0].get("engine_bin")).and_then(toml::Value::as_str).map(Path::new);
    let factory = app.engines().get(&args.engine).with_context(|| format!(
        "Unknown engine '{}'; available engines: {}", args.engine, app.engines().ids().collect::<Vec<_>>().join(", ")))?;
    let engine_bin = if factory.requires_bin() {
        Some(utils::resolve_engine_bin(args.engine_bin.as_deref().or(saved_bin), factory.default_bin())?)
    } else {
        if args.engine_bin.is_some() { bail!("--engine-bin does not apply to engine '{}'", args.engine); }
        None
    };
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
    #[cfg(unix)]
    let _runtime_reservation = if bootstrap.previous.is_some() {
        let directory = dirs::home_dir().context("Cannot locate home directory")?.join(".bcn-bridge");
        Some(crate::instance::reserve_registration(&directory)?)
    } else { None };
    #[cfg(not(unix))]
    if bootstrap.previous.is_some() { bail!("Replacement registration requires Unix runtime locking"); }
    let provider_id = registration_provider(&args.token, args.mode)?;
    // Plugin mode has no webhook listener, so it needs no webhook credential.
    let webhook_token = match args.mode {
        Mode::Gateway => Some(app.credentials().webhook_token(&provider_id)?),
        Mode::Plugin => None,
    };
    let api_url = match args.api_url.clone().or(bootstrap.settings.api_url.clone()) {
        Some(url) => url,
        None => app.profile().default_api_url()?,
    };
    let client = RegistrationClient::new(&api_url).context("Invalid registration.api_url or HTTP client configuration")?;
    if replacement::update(&args, path, &bootstrap, &provider_id, &api_url, &client, webhook_token.as_deref(), engine_bin.as_deref()).await? { return Ok(()); }
    let mut bot = toml::Table::new();
    bot.insert("provider_bot_ref".into(), "pending-identity".into());
    bot.insert("engine".into(), args.engine.clone().into());
    bot.insert("cwd".into(), cwd.to_str().context("Engine working directory must be UTF-8")?.into());
    if let Some(model) = args.model { bot.insert("model".into(), model.into()); }
    if let Some(mode) = app.registration_permission_mode(&args.engine) {
        bot.insert("permission_mode".into(), mode.into());
    }
    if let Some(engine_bin) = engine_bin {
        bot.insert("engine_bin".into(), engine_bin.to_str().context("Engine executable path must be UTF-8")?.into());
    }
    // Desktop/channel settings are independent of BCN identity and survive replacement.
    if let Some(previous) = &bootstrap.previous {
        if let Some(bots) = previous.get("bot").and_then(toml::Value::as_array) {
            if bots.len() == 1 && bots[0].get("engine").and_then(toml::Value::as_str) == Some(args.engine.as_str()) {
                if let Some(options) = bots[0].get("engine_options") { bot.insert("engine_options".into(), options.clone()); }
            }
        }
    }
    #[cfg(unix)]
    if bootstrap.previous.is_none() && !bot.contains_key("engine_options") {
        let runtime_dir = dirs::home_dir().context("Cannot locate home directory")?.join(".bcn-bridge");
        if let Some(active) = crate::instance::current_configuration(&runtime_dir).await? {
            let source = config::read_registration_source(&active, true)?;
            if let Some(previous) = source.previous {
                if let Some(bots) = previous.get("bot").and_then(toml::Value::as_array) {
                    if bots.len() == 1 && bots[0].get("engine").and_then(toml::Value::as_str) == Some(args.engine.as_str()) {
                        if let Some(options) = bots[0].get("engine_options") { bot.insert("engine_options".into(), options.clone()); }
                    }
                }
            }
        }
    }
    let mut document = toml::Table::new();
    document.insert("provider_id".into(), provider_id.clone().into());
    document.insert("mode".into(), args.mode.name().into());
    let state_path = if bootstrap.previous.is_some() {
        format!("bridge-state-{}.sqlite3", uuid::Uuid::new_v4())
    } else { "bridge-state.sqlite3".into() };
    document.insert("state_path".into(), state_path.into());
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
    let replacing = bootstrap.previous.is_some();
    let prepared = PreparedConfig::new(path, bootstrap.original)?;
    if replacing {
        let backup = prepared.backup_original()?;
        println!("Existing runtime configuration backed up to {}. Original state is retained.", backup.display());
    }
    let resolved_ref = identity.registration_ref(args.provider_bot_ref.as_deref(), &args.engine, &cwd).await?;
    if resolved_ref.as_deref().is_some_and(|reference| !valid_provider_bot_ref(reference)) {
        bail!("The Provider identity is not a valid provider_bot_ref");
    }
    let client = identity.registration_client(client, resolved_ref.as_deref()).await?;
    let context = IdentityContext { mode: args.mode, client: &client, explicit_ref: resolved_ref.as_deref() };
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
    let resolved = config::RegistrationSettings { api_url: Some(api_url), webhook_url: webhook_url.map(str::to_owned) };
    document.insert("registration".into(), toml::Value::try_from(resolved).context("Cannot encode API settings")?);
    let text = toml::to_string_pretty(&document).context("Cannot encode registered bridge configuration")?;
    prepared.save(&text)?;
    println!("Bot registered in {} mode. Configuration saved to {}. Start with bcs-bridge start --config {}", args.mode.name(), path.display(), path.display());
    Ok(())
}
