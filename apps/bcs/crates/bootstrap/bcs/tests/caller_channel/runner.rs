//! Test-only executable: real BCS composition with a synthetic channel boundary.
mod provider;
mod cache_fixture;

#[derive(clap::Parser)]
struct Args {
    #[arg(short, long)]
    config_dir: Option<std::path::PathBuf>,
    #[arg(long)]
    print_config: bool,
    #[arg(long)]
    cache_fixture_port: Option<u16>,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    use clap::Parser;
    let args = Args::parse();
    if args.print_config {
        println!("{}", serde_json::to_string(&bcs::BcsConfig::default())?);
        return Ok(());
    }
    if let Some(port) = args.cache_fixture_port { cache_fixture::start(port).await?; }
    Ok(bcs::run_from_env_with_config_dir(args.config_dir.as_ref()).await?)
}
