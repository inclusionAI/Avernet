use std::path::PathBuf;

use anyhow::{Context, bail};
use clap::{Args, CommandFactory, FromArgMatches, Parser, Subcommand};

use crate::{app::BridgeApp, registration};

#[derive(Parser)]
#[command(name = "bcs-bridge", version)]
pub struct Cli {
    /// Configuration file (default: ~/.bcn-bridge/bridge.toml)
    #[arg(long, global = true, env = "BRIDGE_CONFIG")]
    config: Option<PathBuf>,
    /// Named configuration for register/init/start; status/stop refer to the active instance
    #[arg(long, global = true, conflicts_with = "config")]
    profile: Option<String>,
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Register this agent as a BCS Bot and save a gateway or plugin configuration
    Register(Box<registration::RegisterArgs>),
    /// Initialize registered engine configuration and print desktop connection instructions
    Init(InitArgs),
    /// Remove owned engine Hooks; keep Bot registration and runtime configuration
    Deinit(InitArgs),
    /// Run the bridge in the foreground, or detach with --daemon
    Start(StartArgs),
    /// Show this user's bridge instance without loading configuration
    Status(StatusArgs),
    /// Gracefully stop this user's bridge instance without loading configuration
    Stop,
}

#[derive(Args)]
struct StatusArgs {
    /// Print credential-free process and per-Bot observations as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
struct InitArgs {
    #[arg(long)]
    engine: String,
    /// Optional initializer setting, e.g. database_path=/absolute/path/agents.db
    #[arg(long = "option")]
    options: Vec<String>,
}

#[derive(Args)]
struct StartArgs {
    /// Override the working directory for one native Codex Bot, for this run only
    #[arg(long)]
    cwd: Option<PathBuf>,
    /// Run in the background and wait for successful initialization (Unix only)
    #[arg(long)]
    daemon: bool,
    #[arg(long, hide = true, conflicts_with = "daemon")]
    daemon_child: bool,
}

/// Parse the command line, applying the application's help text and defaults.
pub fn parse(app: &BridgeApp) -> Cli {
    let defaults = app.defaults();
    let mut command = Cli::command().about(defaults.about).mut_subcommand("register", |register| {
        let register = register
            .mut_arg("engine", |arg| arg.required(false).default_value(defaults.engine));
        match defaults.model {
            Some(model) => register.mut_arg("model", |arg| arg.default_value(model)),
            None => register,
        }
    });
    let matches = command.get_matches_mut();
    from_matches(app, &mut command, &matches)
}

fn from_matches(app: &BridgeApp, command: &mut clap::Command, matches: &clap::ArgMatches) -> Cli {
    let omit_model_default = matches.subcommand().is_some_and(|(name, registration)| {
        name == "register"
            && registration.get_one::<String>("engine").is_some_and(|engine| !app.uses_registration_model_default(engine))
            && registration.value_source("model") == Some(clap::parser::ValueSource::DefaultValue)
    });
    let mut cli = Cli::from_arg_matches(&matches).unwrap_or_else(|error| error.format(command).exit());
    if let Commands::Register(args) = &mut cli.command {
        args.model_is_explicit = matches.subcommand().is_some_and(|(_, registration)|
            registration.value_source("model") == Some(clap::parser::ValueSource::CommandLine));
        args.listen_is_explicit = matches.subcommand().is_some_and(|(_, registration)|
            registration.value_source("listen") == Some(clap::parser::ValueSource::CommandLine));
    }
    if omit_model_default {
        if let Commands::Register(args) = &mut cli.command { args.model = None; }
    }
    cli
}

/// Detach a newly executed child before initializing the multithreaded Tokio runtime.
pub fn prepare_process(cli: &Cli) -> anyhow::Result<()> {
    if matches!(&cli.command, Commands::Start(StartArgs { daemon_child: true, .. })) {
        #[cfg(unix)]
        rustix::process::setsid().context("Cannot detach bridge from the terminal")?;
        #[cfg(not(unix))]
        bail!("Background mode requires Unix");
    }
    Ok(())
}

pub async fn run(cli: Cli, app: &BridgeApp) -> anyhow::Result<()> {
    match &cli.command {
        #[cfg(unix)]
        Commands::Status(args) => return crate::instance::status(&runtime_dir()?, args.json).await,
        #[cfg(unix)]
        Commands::Stop => return crate::instance::stop(&runtime_dir()?).await,
        #[cfg(not(unix))]
        Commands::Status(_) | Commands::Stop => bail!("Instance management requires Unix"),
        _ => {}
    }
    let named_profile = cli.profile.is_some();
    let config_path = match (cli.profile, cli.config) {
        (Some(name), None) => crate::profiles::configuration(
            &dirs::home_dir().context("Cannot locate home directory")?, &name)?,
        (None, Some(path)) => path,
        (None, None) => dirs::home_dir().context("Cannot locate home directory; pass --config")?
            .join(".bcn-bridge/bridge.toml"),
        _ => bail!("Use either --profile or --config"),
    };
    match cli.command {
        Commands::Register(args) => {
            if (named_profile || args.bot_id.is_some() || crate::multi_config::Document::load(&config_path).is_ok_and(|doc| doc.v2))
                && args.reuse_profile(&config_path, app)? { return Ok(()); }
            registration::register(*args, &config_path, app).await
        }
        command @ (Commands::Init(_) | Commands::Deinit(_)) => {
            #[cfg(unix)]
            let _runtime_guard = crate::instance::reserve_registration(&runtime_dir()?)?;
            let original = crate::config::read_existing(&config_path)?;
            let _config_guard = crate::config::PreparedConfig::new(&config_path, original)?;
            let cleanup = matches!(command, Commands::Deinit(_));
            let args = match command { Commands::Init(args) | Commands::Deinit(args) => args, _ => unreachable!() };
            let mut options = toml::Table::new();
            for option in args.options {
                let (key, value) = option.split_once('=').context("--option must use key=value")?;
                if key.is_empty() || value.is_empty() || options.insert(key.to_owned(), value.into()).is_some() {
                    bail!("Initializer options must be nonempty and unique");
                }
            }
            let message = if cleanup { app.deinitialize(&args.engine, &config_path, &options)? }
                else { app.initialize(&args.engine, &config_path, &options)? };
            println!("{message}");
            Ok(())
        }
        Commands::Start(args) => {
            if !config_path.try_exists().context("Cannot inspect bridge configuration")? {
                bail!("Configuration not found at {}. Provide bridge.toml or pass --config.", config_path.display());
            }
            #[cfg(unix)] {
                let config_path = config_path.canonicalize().context("Cannot resolve bridge configuration path")?;
                if args.daemon { load_start(&config_path, app, args.cwd.as_deref()).await?; return crate::instance::start_daemon(&config_path, &runtime_dir()?, args.cwd.as_deref()).await; }
                crate::instance::run(app, &config_path, &runtime_dir()?, args.cwd.as_deref()).await
            }
            #[cfg(not(unix))] {
                if args.daemon || args.daemon_child { bail!("Background mode requires Unix"); }
                tracing_subscriber::fmt()
                    .with_env_filter(tracing_subscriber::EnvFilter::try_from_default_env()
                        .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")))
                    .init();
                let config = load_start(&config_path, app, args.cwd.as_deref()).await?;
                bcs_bridge_core::service::serve_bridge_managed(config, app.engines(),
                    tokio_util::sync::CancellationToken::new(), None, bcs_bridge_core::status::BridgeStatus::default()).await
            }
        }
        Commands::Status(_) | Commands::Stop => unreachable!("handled before configuration resolution"),
    }
}

pub(crate) async fn load_start(config_path: &std::path::Path, app: &BridgeApp, cwd_override: Option<&std::path::Path>) -> anyhow::Result<bcs_bridge_core::config::BridgeConfig> {
    let document = crate::multi_config::Document::load(config_path)?;
    let mut providers = crate::multi_config::load_document(&document, config_path, app.credentials())
        .map_err(|error| anyhow::anyhow!("Cannot load bridge configuration at {}: {error}", config_path.display()))?;
    crate::profile::validate_environment(app.profile(), &document.endpoints())?;
    if let Some(cwd) = cwd_override {
        if providers.len() != 1 || providers[0].config.bots.len() != 1
            || !matches!(providers[0].config.bots[0].engine.as_str(), "codex" | "codex-desktop") {
            bail!("--cwd requires a configuration containing one native Codex Bot");
        }
        let cwd = cwd.canonicalize().context("Cannot resolve requested working directory")?;
        if !cwd.is_dir() { bail!("Working directory must be a directory"); }
        if providers[0].registrations[0].provider_auth.is_none() {
            bail!("This legacy configuration has no recorded identity policy; set registration.provider_auth to this Bot's registered identity policy before using --cwd");
        }
        providers[0].config.bots[0].cwd = cwd;
    }
    app.engines().validate_resources(providers.iter().flat_map(|p| &p.config.bots))?;
    for provider in &providers {
        for (bot, settings) in provider.config.bots.iter().zip(&provider.registrations) {
            app.engines().build(bot).map_err(anyhow::Error::msg)?.preflight().await?;
            if matches!(bot.engine.as_str(), "codex" | "codex-desktop") {
                if let Some(policy) = &settings.provider_auth {
                    app.identity(policy)?.validate_workspace(bot, settings.api_url.as_deref()).await?;
                }
            }
        }
    }
    bcs_bridge_core::config::BridgeConfig::new(providers.into_iter().map(|p| p.config).collect())
}

/// Instance runtime directory (lock, control socket, PID record, daemon log).
#[cfg(unix)]
pub(crate) fn runtime_dir() -> anyhow::Result<PathBuf> {
    Ok(dirs::home_dir().context("Cannot locate home directory for bridge instance management")?.join(".bcn-bridge"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn engine_model_default_policy_preserves_explicit_models_and_other_engines() {
        let app = BridgeApp::builder().without_registration_model_default("codex").build();
        for (engine, explicit, expected) in [
            ("codex", None, None),
            ("codex", Some("deployment-model"), Some("deployment-model")),
            ("codex", Some("explicit-model"), Some("explicit-model")),
            ("claude-code", None, Some("deployment-model")),
        ] {
            let mut command = Cli::command().mut_subcommand("register", |register| {
                register.mut_arg("model", |arg| arg.default_value("deployment-model"))
            });
            let mut args = vec!["bcs-bridge", "register", "--token", "test-token", "--bot-name", "Test",
                "--engine", engine];
            if let Some(model) = explicit { args.extend(["--model", model]); }
            let matches = command.clone().try_get_matches_from(args).unwrap();
            let cli = from_matches(&app, &mut command, &matches);
            let Commands::Register(args) = cli.command else { panic!("expected registration"); };
            assert_eq!(args.model.as_deref(), expected);
        }
    }

    #[test]
    fn start_rejects_misspelled_daemon_flag() {
        let error = match Cli::try_parse_from(["bcs-bridge", "start", "--deamon"]) {
            Err(error) => error,
            Ok(_) => panic!("the misspelled --deamon flag must be rejected"),
        };
        assert_eq!(error.kind(), clap::error::ErrorKind::UnknownArgument);
        assert!(error.to_string().contains("--deamon"));
    }
}
