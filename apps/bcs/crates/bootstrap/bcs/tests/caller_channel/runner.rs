//! Test-only executable: real BCS composition with a synthetic channel boundary.
mod provider;
mod cache_fixture;
mod network_tracing;
mod custom_binding;

#[derive(clap::Parser)]
struct Args {
    #[arg(short, long)]
    config_dir: Option<std::path::PathBuf>,
    #[arg(long)]
    print_config: bool,
    #[arg(long)]
    cache_fixture_port: Option<u16>,
    #[arg(long)]
    trace_output: Option<std::path::PathBuf>,
    #[arg(long)]
    a2a_peer_url: Option<String>,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    use clap::Parser;
    let args = Args::parse();
    if args.print_config {
        println!("{}", serde_json::to_string(&bcs::BcsConfig::default())?);
        return Ok(());
    }
    if let Some(url) = args.a2a_peer_url { custom_binding::install(url)?; }
    if let Some(port) = args.cache_fixture_port { cache_fixture::start(port).await?; }
    let recorder = args.trace_output.as_ref().map(|_| network_tracing::install()).transpose()?;
    let result = bcs::run_from_env_with_config_dir(args.config_dir.as_ref()).await;
    if let (Some(recorder), Some(path)) = (recorder, args.trace_output) { recorder.write(&path)?; }
    Ok(result?)
}
