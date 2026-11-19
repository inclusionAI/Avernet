//! Opt-in Singlebox companion: real Worker services, explicitly in-memory Store
//! mock, existing SQLite Result/Blob provider. No persistence/failover claim for
//! the Store mock; those cases run separately against the authorized PROXY.
#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "explicit test companion"
)]
mod assembly;
mod commands;
mod dispatch;
mod gateway;
mod storage;
use bcs_dist_test_runner as workload;
use clap::Parser;
use std::{io::Write, path::PathBuf};
use tokio::io::{AsyncBufReadExt, BufReader};
#[derive(Parser)]
struct Args {
    #[arg(long)]
    archive: PathBuf,
}
#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let args = Args::parse();
    let mut host = assembly::Host::new(&args.archive).await;
    let mut lines = BufReader::new(tokio::io::stdin()).lines();
    println!(
        "{}",
        serde_json::json!({"ready":true,"store":"memory_mock","archive":"sqlite","durable_store":false})
    );
    std::io::stdout().flush()?;
    while let Some(line) = lines.next_line().await? {
        if line.len() > 2 * 1024 * 1024 {
            anyhow::bail!("test command exceeds frame budget");
        }
        let request: serde_json::Value = serde_json::from_str(&line)?;
        if request["op"] == "quit" {
            break;
        }
        let reply = match host.command(request).await {
            Ok(value) => serde_json::json!({"ok":true,"value":value}),
            Err(error) => serde_json::json!({"ok":false,"error":error.to_string()}),
        };
        println!("{reply}");
        std::io::stdout().flush()?;
    }
    Ok(())
}
