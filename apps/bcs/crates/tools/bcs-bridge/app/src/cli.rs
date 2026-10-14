use std::path::PathBuf;

use anyhow::{Context, bail};
use clap::{Args, CommandFactory, FromArgMatches, Parser, Subcommand};

use crate::{app::BridgeApp, config, registration};

#[derive(Parser)]
#[command(name = "bcs-bridge", version)]
pub struct Cli {
    /// Configuration file (default: ~/.bcn-bridge/bridge.toml)
    #[arg(long, global = true, env = "BRIDGE_CONFIG")]
    config: Option<PathBuf>,
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
    Status,
    /// Gracefully stop this user's bridge instance without loading configuration
    Stop,
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
            .mut_arg("engine", |arg| arg.required(false).default_value(defaults.engine))
            .mut_arg("provider_auth", |arg| arg.required(false).default_value(defaults.provider_auth));
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
        Commands::Status => return crate::instance::status(&runtime_dir()?).await,
        #[cfg(unix)]
        Commands::Stop => return crate::instance::stop(&runtime_dir()?).await,
        #[cfg(not(unix))]
        Commands::Status | Commands::Stop => bail!("Instance management requires Unix"),
        _ => {}
    }
    let config_path = match cli.config {
        Some(path) => path,
        None => dirs::home_dir().context("Cannot locate home directory; pass --config")?
            .join(".bcn-bridge/bridge.toml"),
    };
    match cli.command {
        Commands::Register(args) => registration::register(*args, &config_path, app).await,
        command @ (Commands::Init(_) | Commands::Deinit(_)) => {
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
            let config = config::load_runtime(&config_path, app.credentials())
                // load_runtime sanitizes parser/credential errors. Retain its useful
                // message without exposing the underlying error chain or config text.
                .map_err(|error| anyhow::anyhow!("Cannot load bridge configuration at {}: {error}", config_path.display()))?;
            #[cfg(unix)] {
                let config_path = config_path.canonicalize().context("Cannot resolve bridge configuration path")?;
                if args.daemon { return crate::instance::start_daemon(&config_path, &runtime_dir()?).await; }
                crate::instance::run(config, app.engines(), &config_path, &runtime_dir()?).await
            }
            #[cfg(not(unix))] {
                if args.daemon || args.daemon_child { bail!("Background mode requires Unix"); }
                tracing_subscriber::fmt()
                    .with_env_filter(tracing_subscriber::EnvFilter::try_from_default_env()
                        .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")))
                    .init();
                bcs_bridge_core::service::serve(config, app.engines()).await
            }
        }
        Commands::Status | Commands::Stop => unreachable!("handled before configuration resolution"),
    }
}

/// Instance runtime directory (lock, control socket, PID record, daemon log).
#[cfg(unix)]
fn runtime_dir() -> anyhow::Result<PathBuf> {
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
                "--engine", engine, "--provider-auth", "static-bearer"];
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
