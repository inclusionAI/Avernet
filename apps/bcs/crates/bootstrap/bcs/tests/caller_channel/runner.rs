//! Test-only executable: real BCS composition with a synthetic channel boundary.
mod provider;
#[path = "../agent_invoke_e2e/host.rs"]
mod agent_invoke;
mod cache_fixture;
mod network_tracing;
mod custom_binding;
mod request_headers;
#[path = "../a2a_http_identity/plugin.rs"]
mod app_identity;

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
    #[arg(long)]
    provider_request_headers: bool,
    #[arg(long)]
    agent_invoke_fixture_port: Option<u16>,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    use clap::Parser;
    let args = Args::parse();
    if args.print_config {
        println!("{}", serde_json::to_string(&bcs::BcsConfig::default())?);
        return Ok(());
    }
    if let Some(port) = args.agent_invoke_fixture_port {
        return agent_invoke::run(args.config_dir.as_ref(), port).await;
    }
    if args.provider_request_headers { request_headers::install()?; }
    if let Some(url) = args.a2a_peer_url { custom_binding::install(url)?; }
    if let Some(port) = args.cache_fixture_port { cache_fixture::start(port).await?; }
    let recorder = args.trace_output.as_ref().map(|_| network_tracing::install()).transpose()?;
    let result = bcs::run_from_env_with_config_dir(args.config_dir.as_ref()).await;
    if let (Some(recorder), Some(path)) = (recorder, args.trace_output) { recorder.write(&path)?; }
    Ok(result?)
}
