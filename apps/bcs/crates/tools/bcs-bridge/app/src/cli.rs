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
    Register(registration::RegisterArgs),
    /// Run the bridge in the foreground, or detach with --daemon
    Start(StartArgs),
    /// Show this user's bridge instance without loading configuration
    Status,
    /// Gracefully stop this user's bridge instance without loading configuration
    Stop,
}

#[derive(Args)]
struct StartArgs {
    /// Run in the background and wait for successful initialization (Unix only)
    #[arg(long, visible_alias = "deamon")]
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
    Cli::from_arg_matches(&matches).unwrap_or_else(|error| error.format(&mut command).exit())
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
        Commands::Register(args) => registration::register(args, &config_path, app).await,
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
