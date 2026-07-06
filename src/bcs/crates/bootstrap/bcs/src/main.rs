//! Bot Coordination Service (BCS) Binary
//!
//! A standalone service for multi-bot collaboration:
//! - Bot registry (bot_uuid → bot with heartbeat)
//! - Proposal generation for group chat creation
//! - Group session management (Agent/Fusion modes)
//! - Context fusion (single LLM call to merge bot contexts)
//! - Message routing between bots

use anyhow::Result;
use clap::Parser;
use std::path::PathBuf;

/// Bot Coordination Service (BCS)
#[derive(Parser, Debug)]
#[command(name = "bcs", version = bcs::BCS_VERSION, about, long_about = None)]
struct Args {
    /// Path to configuration directory or standalone config file
    ///
    /// A directory may contain:
    ///   - bcs-config.toml (or .json) - base config
    ///   - bcs-config-{env}.toml - environment-specific override or standalone config
    ///
    /// Environment is detected from SERVER_ENV, REAL_SERVER_ENV, or ALIPAY_APP_ENV.
    /// Supported environments: local, dev, pre, prod, gray
    ///
    /// Default: ./configs/
    #[arg(short, long, value_name = "DIR", env = "BCS_CONFIG_DIR")]
    config_dir: Option<PathBuf>,
}

#[tokio::main]
async fn main() -> Result<()> {
    let args = Args::parse();
    bcs::run_from_env_with_config_dir(args.config_dir.as_ref()).await?;
    Ok(())
}
