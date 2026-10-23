//! Registration workflow and persisted gateway or plugin configuration.
//!
//! Plugin hook points, in order: identity preflight, engine executable lookup
//! (engine registry), webhook credential (gateway mode), endpoint defaults
//! (deploy profile), and the Bot identity, resolved just before the
//! registration request.
mod client;
mod token;
mod replacement;
mod desktop;
mod subbots;
mod workspace;
#[cfg(test)]
mod subbots_tests;

use std::{net::SocketAddr, path::{Path, PathBuf}};

use anyhow::{Context, bail};
use clap::Args;

pub use client::RegistrationClient;

/// Registration route and success format selected by the distribution.
#[derive(Clone, Copy, Default)]
pub enum RegistrationApi { #[default] OpenApi, Root }
pub use token::{Mode, RegistrationIdentity, registration_identity, registration_provider};

use crate::app::BridgeApp;
use crate::config::{self, PreparedConfig};
use crate::identity::{IdentityContext, IdentityResolver};
use crate::utils;

#[derive(Args)]
pub struct RegisterArgs {
    /// Previously obtained Provider-scoped registration token
    #[arg(long, env = "BRIDGE_REGISTER_TOKEN", hide_env_values = true)]
    token: String,
    /// Back up and replace existing runtime configuration after stopping the bridge
    #[arg(long)]
    replace_existing: bool,
    /// Explicitly create another Bot in an ordinary Provider (never changes its identity policy)
    #[arg(long, conflicts_with = "replace_existing")]
    add_bot: bool,
    /// Select one existing Bot for reuse or replacement
    #[arg(long, conflicts_with = "add_bot")]
    pub(crate) bot_id: Option<String>,
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
    /// Identity resolver override (default: Token auth_mode, then the distribution legacy resolver)
    #[arg(long)]
    provider_auth: Option<String>,
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
    /// Engine working directory (default: ~/.bcn-bridge/bots/<bot_uuid> for subbots, ~/workspace otherwise)
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
    /// String engine option, e.g. socket=/absolute/path (repeatable); replacement overrides only supplied keys
    #[arg(long, value_name = "NAME=VALUE")]
    engine_option: Vec<String>,
}

impl RegisterArgs {
    fn identity<'a>(&self, app: &'a BridgeApp, scope: &RegistrationIdentity) -> anyhow::Result<&'a dyn IdentityResolver> {
        match (self.provider_auth.as_deref(), scope.auth_mode.as_deref()) {
            (Some(id), _) => app.identity(id),
            (None, Some(mode)) => app.registration_identity(mode),
            (None, None) => app.identity(app.defaults().provider_auth),
        }
    }

    /// A repeated profile command keeps the existing identity and never contacts registration.
    pub(crate) fn reuse_profile(&self, path: &Path, app: &BridgeApp) -> anyhow::Result<bool> {
        let scope = registration_identity(&self.token, self.mode)?;
        let identity = self.identity(app, &scope)?;
        if (scope.auth_mode.as_deref().unwrap_or_else(|| identity.runtime_auth()) == "agentpass-subbots" && self.bot_id.is_none()) || self.add_bot || self.replace_existing || !path.try_exists().context("Cannot inspect profile configuration")? { return Ok(false); }
        let source = crate::multi_config::Document::load(path)?;
        let provider_id = scope.provider_id;
        let Some(previous) = source.groups.iter().find(|group|
            group.get("provider_id").and_then(toml::Value::as_str) == Some(provider_id.as_str())) else { return Ok(false); };
        if previous.get("provider_auth").and_then(toml::Value::as_str).is_some_and(|auth|
            scope.auth_mode.as_deref().is_some_and(|requested| auth != requested)) {
            bail!("Reusing a Bot requires the saved Provider authentication policy");
        }
        let all_bots = previous.get("bot").and_then(toml::Value::as_array).context("Profile has no Bots")?;
        let candidates: Vec<_> = all_bots.iter().filter(|bot|
            bot.get("engine").and_then(toml::Value::as_str) == Some(self.engine.as_str())
            && self.bot_id.as_deref().is_none_or(|id| bot.get("bot_id").and_then(toml::Value::as_str) == Some(id))
            && (all_bots.len() == 1 || self.provider_bot_ref.as_deref().is_none_or(|reference|
                bot.get("provider_bot_ref").and_then(toml::Value::as_str) == Some(reference)))).collect();
        if previous.get("mode").and_then(toml::Value::as_str).unwrap_or("gateway") != self.mode.name() || candidates.len() != 1 {
            bail!("Cannot uniquely reuse a Bot with this mode and engine; select --bot-id, use --add-bot, or stop and use --replace-existing");
        }
        let bots = candidates;
        let mut settings = if bots[0].get("registration").is_some() {
            crate::multi_config::settings(bots[0].as_table().context("Invalid Bot")?)?
        } else { crate::multi_config::settings(previous)? };
        if settings.api_url.is_none() { settings.api_url = previous.get("api_url").and_then(toml::Value::as_str).map(str::to_owned); }
        if let Some(reference) = self.provider_bot_ref.as_deref() {
            if !valid_provider_bot_ref(reference) { bail!("Invalid --provider-bot-ref"); }
            if bots[0].get("provider_bot_ref").and_then(toml::Value::as_str) != Some(reference) {
                bail!("Profile has a different provider Bot reference; choose another --profile or stop and use --replace-existing");
            }
        }
        let api = match self.api_url.clone().or(settings.api_url.clone()) {
            Some(api) => api, None => app.profile().default_api_url()?,
        };
        let same_api = settings.api_url.as_deref().is_some_and(|old| old.trim_end_matches('/') == api.trim_end_matches('/'));
        let same_upstream = match self.mode {
            Mode::Plugin => {
                let desired = match self.upstream_url.clone() {
                    Some(url) => url,
                    None => previous.get("plugin").and_then(|p| p.get("url")).and_then(toml::Value::as_str)
                        .map(str::to_owned).context("Profile has no plugin URL")?,
                };
                previous.get("plugin").and_then(|p| p.get("url")).and_then(toml::Value::as_str)
                    .is_some_and(|old| old.trim_end_matches('/') == desired.trim_end_matches('/'))
            }
            Mode::Gateway => {
                let original = settings.webhook_url.as_deref()
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
            if desired != utils::resolve_engine_bin(Some(Path::new(original)), factory.default_bin())? {
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
        crate::multi_config::load(path, app.credentials())?;
        crate::profile::validate_environment(app.profile(), &source.endpoints())?;
        println!("Bot ID: {}", bots[0].get("bot_id").and_then(toml::Value::as_str).context("Missing saved Bot ID")?);
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
    let scope = registration_identity(&args.token, args.mode)?;
    let identity = args.identity(app, &scope)?;
    // Provider mode controls registration semantics; the resolver supplies identity and credentials.
    // Historical Tokens have no mode metadata, so retain the selected resolver policy.
    let provider_auth = scope.auth_mode.as_deref().unwrap_or_else(|| identity.runtime_auth());
    identity.preflight()?;
    let subbots = provider_auth == "agentpass-subbots";
    if args.replace_existing && subbots { bail!("Subbot registration appends Bots; --replace-existing is unsupported for this mode"); }
    let bootstrap = crate::multi_config::read_for_registration(path, provider_auth, &scope.provider_id,
        args.mode.name(), args.add_bot, args.replace_existing, args.bot_id.as_deref())?;
    if let Some(edit) = &bootstrap.edit {
        crate::multi_config::load_document(&edit.source, path, app.credentials())?;
    }
    let provider_id = scope.provider_id;
    let api_url = match args.api_url.clone().or(bootstrap.settings.api_url.clone()) {
        Some(url) => url,
        None => app.profile().default_api_url()?,
    };
    let saved_bot = bootstrap.previous.as_ref().and_then(|previous| previous.get("bot"))
        .and_then(toml::Value::as_array).filter(|bots| bots.len() == 1)
        .filter(|bots| bots[0].get("engine").and_then(toml::Value::as_str) == Some(args.engine.as_str()))
        .map(|bots| &bots[0]);
    let saved_bin = saved_bot.and_then(|bot| bot.get("engine_bin")).and_then(toml::Value::as_str).map(Path::new);
    let factory = app.engines().get(&args.engine).with_context(|| format!(
        "Unknown engine '{}'; available engines: {}", args.engine, app.engines().ids().collect::<Vec<_>>().join(", ")))?;
    if provider_auth == "agentpass-subbots" && !factory.supports_caller_env() {
        bail!("Engine '{}' cannot propagate subbot identity", args.engine);
    }
    let engine_bin = if factory.requires_bin() {
        Some(utils::resolve_engine_bin(args.engine_bin.as_deref().or(saved_bin), factory.default_bin())?)
    } else {
        if args.engine_bin.is_some() { bail!("--engine-bin does not apply to engine '{}'", args.engine); }
        None
    };
    let mut engine_options = toml::Table::new();
    for option in &args.engine_option {
        let (name, value) = option.split_once('=')
            .filter(|(name, _)| !name.trim().is_empty())
            .context("--engine-option requires NAME=VALUE")?;
        if engine_options.insert(name.to_owned(), value.into()).is_some() {
            bail!("Duplicate --engine-option name");
        }
    }
    // Resolve the effective settings before validation and share them across both
    // replacement paths. Explicit CLI keys win; unrelated saved keys keep their types.
    if let Some(saved) = saved_bot.and_then(|bot| bot.get("engine_options")) {
        let mut merged = saved.as_table().context("Invalid saved engine options")?.clone();
        merged.extend(engine_options);
        engine_options = merged;
    }
    if !engine_options.is_empty() || factory.registration_requires_options() {
        let engine = factory.build(engine_bin.clone().unwrap_or_default(), &engine_options)
            .map_err(|error| anyhow::anyhow!("Invalid engine options: {error}"))?;
        engine.preflight().await?;
    }
    if args.token.trim().is_empty() || args.bot_name.trim().is_empty() { bail!("Registration token and Bot name must be nonempty"); }
    if args.provider_bot_ref.as_deref().is_some_and(|value| !valid_provider_bot_ref(value)) {
        bail!("--provider-bot-ref must be 1-128 characters of letters, digits, '_', '.', ':' or '-'");
    }
    let (webhook_url, upstream_url) = match args.mode {
        Mode::Gateway => {
            if args.upstream_url.is_some() { bail!("--upstream-url applies to plugin mode; gateway Bots receive work through webhook delivery"); }
            let webhook = args.webhook_url.as_deref().or_else(|| replacement::saved_webhook(&args, &bootstrap, &provider_id, &api_url))
                .context("--webhook-url is required in gateway mode")?;
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
    let mut endpoints = bootstrap.edit.as_ref().map(|edit| edit.source.endpoints()).unwrap_or_default();
    if let Some(index) = bootstrap.edit.as_ref().and_then(|edit| edit.target) { endpoints.remove(index); }
    endpoints.push((Some(api_url.clone()), upstream_url.clone()));
    crate::profile::validate_environment(app.profile(), &endpoints)?;
    let workspace = workspace::Workspace::prepare(args.cwd.as_deref(), subbots)?;
    if subbots { workspace.check_explicit(&bootstrap.all_bots(), path)?; }
    let cwd = workspace.identity_cwd();
    #[cfg(unix)]
    let _runtime_reservation = if bootstrap.modifying_runtime() {
        let directory = dirs::home_dir().context("Cannot locate home directory")?.join(".bcn-bridge");
        Some(crate::instance::reserve_registration(&directory)?)
    } else { None };
    #[cfg(not(unix))]
    if bootstrap.modifying_runtime() { bail!("Modifying a registered configuration requires Unix runtime locking"); }
    // Plugin mode has no webhook listener, so it needs no webhook credential.
    let webhook_token = match args.mode {
        Mode::Gateway => Some(app.credentials().webhook_token(&provider_id)?),
        Mode::Plugin => None,
    };
    if !bootstrap.document.is_empty() {
        config::runtime(&bootstrap.document, path, webhook_token.as_deref())?;
        if bootstrap.document.get("api_url").and_then(toml::Value::as_str).or(bootstrap.settings.api_url.as_deref()) != Some(api_url.as_str()) {
            bail!("Appending a Bot requires the saved API URL");
        }
        if args.mode == Mode::Gateway && args.listen_is_explicit
            && bootstrap.document.get("listen").and_then(toml::Value::as_str) != Some(args.listen.to_string().as_str()) {
            bail!("Appending a Bot requires the saved gateway listener");
        }
        if let Some(url) = &upstream_url {
            if bootstrap.document.get("plugin").and_then(|p| p.get("url")).and_then(toml::Value::as_str) != Some(url.as_str()) {
                bail!("Appending a Bot requires the saved plugin upstream URL");
            }
        }
    }
    let client = RegistrationClient::new(&api_url).context("Invalid registration.api_url or HTTP client configuration")?;
    let client = match app.registration_api() {
        RegistrationApi::OpenApi => client,
        RegistrationApi::Root => client.with_legacy_registration(),
    };
    if args.replace_existing && bootstrap.previous.as_ref().and_then(|g| g.get("provider_auth")).and_then(toml::Value::as_str).is_some_and(|auth| auth != provider_auth) {
        bail!("Replacement cannot change the saved Provider authentication policy");
    }
    if replacement::update(&args, path, &bootstrap, app, &provider_id, &api_url, &client, webhook_token.as_deref(), engine_bin.as_deref(), &engine_options).await? { return Ok(()); }
    let mut bot = toml::Table::new();
    bot.insert("provider_bot_ref".into(), "pending-identity".into());
    bot.insert("engine".into(), args.engine.clone().into());
    if !engine_options.is_empty() { bot.insert("engine_options".into(), engine_options.into()); }
    bot.insert("cwd".into(), cwd.to_str().context("Engine working directory must be UTF-8")?.into());
    if let Some(model) = args.model { bot.insert("model".into(), model.into()); }
    if let Some(mode) = app.registration_permission_mode(&args.engine) {
        bot.insert("permission_mode".into(), mode.into());
    }
    if let Some(engine_bin) = engine_bin {
        bot.insert("engine_bin".into(), engine_bin.to_str().context("Engine executable path must be UTF-8")?.into());
    }
    if !bootstrap.modifying_runtime() && !bot.contains_key("engine_options") {
        let runtime_dir = dirs::home_dir().context("Cannot locate home directory")?.join(".bcn-bridge");
        if let Some(options) = desktop::existing_options(&runtime_dir, path, &args.engine).await? {
            bot.insert("engine_options".into(), options.into());
        }
    }
    if let Some(edit) = &bootstrap.edit {
        let existing = crate::multi_config::load_document(&edit.source, path, app.credentials())?;
        let candidate: bcs_bridge_core::config::BotConfig = toml::Value::Table(bot.clone()).try_into()
            .map_err(|_| anyhow::anyhow!("Invalid new Bot configuration"))?;
        let replacing = bootstrap.previous.is_some();
        app.engines().validate_resources(existing.iter().enumerate().flat_map(|(index, p)| p.config.bots.iter().enumerate().filter(move |(bot_index, _)| !(replacing && edit.target == Some(index) && edit.bot == Some(*bot_index))).map(|(_, bot)| bot)).chain(std::iter::once(&candidate)))?;
        if !subbots {
            for provider in existing.iter().filter(|p| p.config.provider_auth.as_deref() == Some("agentpass-subbots")) {
                if provider.config.bots.iter().any(|bot| bot.cwd.canonicalize().ok().as_deref() == Some(cwd)) {
                    bail!("Working directory is already owned by a subbot");
                }
            }
        }
    }
    let mut document = bootstrap.document.clone();
    let append = document.contains_key("provider_id");
    let mut existing_bots = document.remove("bot").and_then(|v| v.as_array().cloned()).unwrap_or_default();
    document.insert("provider_id".into(), provider_id.clone().into());
    document.insert("mode".into(), args.mode.name().into());
    if bootstrap.previous.is_some() || bootstrap.edit.as_ref().is_some_and(|edit| edit.target.is_none()) {
        document.insert("state_path".into(), format!("bridge-state-{}.sqlite3", uuid::Uuid::new_v4()).into());
    } else {
        document.entry("state_path").or_insert("bridge-state.sqlite3".into());
    }
    document.insert("provider_auth".into(), provider_auth.to_owned().into());
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
    if webhook_token.is_some() && provider_auth != "agentpass-subbots" { config::runtime(&document, path, webhook_token.as_deref())?; }
    let prepared = PreparedConfig::new(path, bootstrap.original.clone())?;
    // Appending changes an existing runtime configuration. A new profile only
    // copies desktop options and must remain registrable while another runs.
    #[cfg(unix)]
    let _runtime_guard = if append && _runtime_reservation.is_none() {
        Some(crate::instance::registration_guard(&crate::cli::runtime_dir()?)?)
    } else { None };
    if bootstrap.modifying_runtime() {
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
    if append && subbots && existing_bots.iter().any(|item| item.get("agent_code").and_then(toml::Value::as_str) != Some(provider_bot_ref.as_str())) {
        bail!("Appending a Bot requires the same AgentPass parent");
    }
    if !subbots && existing_bots.iter().any(|bot| bot.get("provider_bot_ref").and_then(toml::Value::as_str) == Some(provider_bot_ref.as_str())) {
        bail!("This Provider Bot reference is already configured; reuse the existing Bot");
    }
    // Only a confirmed duplicate ref permits another attempt; unknown outcomes never replay.
    let mut slugs = subbots.then(|| subbots::Slugs::new(&existing_bots));
    let mut attempts = 0;
    let (slug, registration) = loop {
        let slug = slugs.as_mut().map(subbots::Slugs::next).transpose()?;
        attempts += 1;
        match client.register(&args.token, args.bot_name.trim(), &provider_bot_ref, args.mode.name(), webhook_url, &provider_id, slug.as_deref()).await {
            Ok(registration) => break (slug, registration),
            Err(error) if subbots && error.is::<client::RegistrationConflict>() && attempts < subbots::REGISTRATION_ATTEMPTS => continue,
            Err(error) => return Err(error),
        }
    };
    let assigned_cwd = workspace.assigned_cwd(&registration.bot_uuid);
    bot.insert("cwd".into(), assigned_cwd.to_str().context("Engine working directory must be UTF-8")?.into());
    document.insert("provider_id".into(), registration.registration.provider_id.into());
    bot.insert("provider_bot_ref".into(), registration.registration.provider_bot_ref.into());
    if let Some(slug) = &slug {
        bot.insert("agent_code".into(), provider_bot_ref.into());
        bot.insert("bot_slug".into(), slug.clone().into());
    }
    let registered_bot_id = registration.bot_uuid.clone();
    bot.insert("bot_id".into(), registration.bot_uuid.into());
    bot.insert("token".into(), registration.bot_token.into());
    existing_bots.push(toml::Value::Table(bot));
    document.insert("bot".into(), toml::Value::Array(existing_bots));
    // Persist the resolved api_url so the saved configuration is self-contained.
    let resolved = config::RegistrationSettings { api_url: Some(api_url), webhook_url: webhook_url.map(str::to_owned), provider_auth: Some(identity.runtime_auth().to_owned()) };
    let resolved = toml::Value::try_from(resolved).context("Cannot encode API settings")?;
    if let Some(edit) = &bootstrap.edit {
        if edit.source.v2 || edit.target.is_none() {
            if let Some(bot) = document.get_mut("bot").and_then(toml::Value::as_array_mut).and_then(|bots| bots.last_mut()).and_then(toml::Value::as_table_mut) {
                bot.insert("registration".into(), resolved.clone());
            }
        }
    }
    // V1 can retain its original Bot tables when the resolver is unchanged.
    // When it changes, pin the previous per-Bot policy before changing defaults.
    if append {
        let old = bootstrap.document.get("registration").cloned().unwrap_or_else(|| toml::Value::Table(toml::Table::new()));
        if old != resolved {
            if let Some(bots) = document.get_mut("bot").and_then(toml::Value::as_array_mut) {
                let existing_count = bots.len().saturating_sub(1);
                for bot in &mut bots[..existing_count] {
                    if let Some(bot) = bot.as_table_mut() { bot.entry("registration").or_insert(old.clone()); }
                }
            }
        }
    }
    document.insert("registration".into(), resolved);
    let document = bootstrap.finish(document)?;
    let text = toml::to_string_pretty(&document).context("Cannot encode registered bridge configuration")?;
    if subbots {
        if let Err(error) = workspace.create_assigned(&assigned_cwd, &bootstrap.all_bots(), path) {
            return prepared.save_recovery(&text, &format!("cannot prepare the exclusive Bot workspace: {error:#}; resolve the saved cwd before starting"));
        }
    }
    if crate::multi_config::Document::parse(&text).and_then(|document|
        crate::multi_config::load_document(&document, path, app.credentials())).is_err() {
        return prepared.save_recovery(&text, "runtime configuration validation failed");
    }
    prepared.save(&text)?;
    println!("Bot ID: {registered_bot_id}");
    println!("Bot registered in {} mode. Configuration saved to {}. Start with bcs-bridge start --config {}", args.mode.name(), path.display(), path.display());
    Ok(())
}
