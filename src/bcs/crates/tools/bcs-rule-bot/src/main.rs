use std::path::PathBuf;

use anyhow::Result;
use bcs_rule_bot::config::Manifest;
use bcs_rule_bot::host::{HostConfig, run_host};
use clap::{Parser, Subcommand};
use tracing_subscriber::EnvFilter;

#[derive(Debug, Parser)]
#[command(name = "bcs-rule-bot")]
#[command(about = "Rule-driven BCS bot runtime")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Validate a version 2 profile manifest.
    Validate {
        #[arg(long)]
        manifest: PathBuf,
    },
    /// Run every rule bot declared in a profile manifest.
    Run {
        #[arg(long)]
        manifest: PathBuf,
        #[arg(long)]
        bcs_url: String,
        #[arg(long)]
        profile_root: PathBuf,
        #[arg(long, default_value = "")]
        profile_prefix: String,
        #[arg(long)]
        status_file: PathBuf,
    },
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| EnvFilter::new("bcs_rule_bot=info")),
        )
        .with_target(false)
        .init();

    match Cli::parse().command {
        Command::Validate { manifest } => {
            let parsed = Manifest::load(&manifest)?;
            println!(
                "valid rule bot manifest: {} ({} bot(s))",
                manifest.display(),
                parsed.rule_bots().count()
            );
            Ok(())
        }
        Command::Run {
            manifest,
            bcs_url,
            profile_root,
            profile_prefix,
            status_file,
        } => {
            run_host(HostConfig {
                manifest_path: manifest,
                bcs_url,
                profile_root,
                profile_prefix,
                status_file,
            })
            .await
        }
    }
}
