//! Test-only executable: real BCS composition with a synthetic channel boundary.
mod provider;

#[derive(clap::Parser)]
struct Args {
    #[arg(short, long)]
    config_dir: Option<std::path::PathBuf>,
    #[arg(long)]
    print_config: bool,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    use clap::Parser;
    let args = Args::parse();
    if args.print_config {
        println!("{}", serde_json::to_string(&bcs::BcsConfig::default())?);
        return Ok(());
    }
    Ok(bcs::run_from_env_with_config_dir(args.config_dir.as_ref()).await?)
}
