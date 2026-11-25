//! Live-process OAuth E2E entry. No fake auth services or credential-seeding APIs.
//! SQLite uses the normal startup; memory uses the existing local constructor.
use clap::Parser;

#[derive(Parser)]
struct Args {
    #[arg(short, long)]
    config_dir: Option<std::path::PathBuf>,
    #[arg(long)]
    print_config: bool,
    #[arg(long)]
    memory: bool,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let args = Args::parse();
    if args.print_config {
        println!("{}", serde_json::to_string(&bcs::BcsConfig::default())?);
        return Ok(());
    }
    let config =
        bcs::BcsConfig::try_load_with_env(args.config_dir.as_ref()).map_err(anyhow::Error::msg)?;
    if args.memory {
        bcs::BcsServer::new(config).run().await?;
    } else {
        bcs::run_with_config(config).await?;
    }
    Ok(())
}
