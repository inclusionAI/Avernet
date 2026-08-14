use std::path::PathBuf;

use anyhow::{Context, Result, bail};
use tokio::sync::mpsc;
use tokio::task::JoinSet;
use tokio_util::sync::CancellationToken;
use tracing::{error, info, warn};

use crate::config::Manifest;
use crate::instance::{InstanceConfig, run_instance};
use crate::status::{StatusCommand, StatusReporter};

#[derive(Debug, Clone)]
pub struct HostConfig {
    pub manifest_path: PathBuf,
    pub bcs_url: String,
    pub profile_root: PathBuf,
    pub profile_prefix: String,
    pub status_file: PathBuf,
}

pub async fn run_host(config: HostConfig) -> Result<()> {
    let manifest = Manifest::load(&config.manifest_path)?;
    let rule_bots = manifest.rule_bots().cloned().collect::<Vec<_>>();
    if rule_bots.is_empty() {
        bail!("manifest does not contain any rule bots");
    }

    let cancellation = CancellationToken::new();
    let (status_tx, mut status_rx) = mpsc::unbounded_channel();
    let mut reporter =
        StatusReporter::new(config.status_file.clone(), config.manifest_path.clone());
    let mut tasks = JoinSet::new();

    for bot in rule_bots {
        let instance_config = InstanceConfig {
            scopes: bot.effective_scopes(&manifest).to_string(),
            profile_dir: config
                .profile_root
                .join(format!("{}{}", config.profile_prefix, bot.profile)),
            bcs_url: config.bcs_url.clone(),
            bot,
        };
        let instance_cancellation = cancellation.child_token();
        let instance_status = status_tx.clone();
        tasks.spawn(async move {
            let profile = instance_config.bot.profile.clone();
            let result =
                run_instance(instance_config, instance_cancellation, instance_status).await;
            (profile, result)
        });
    }
    drop(status_tx);

    info!(
        manifest = %config.manifest_path.display(),
        bot_count = tasks.len(),
        "BCS Rule Bot host started"
    );

    loop {
        tokio::select! {
            () = shutdown_signal() => {
                info!("shutdown signal received");
                break;
            }
            command = status_rx.recv() => {
                match command {
                    Some(StatusCommand::Update(update)) => {
                        if let Err(error) = reporter.apply(update) {
                            warn!(error = %error, "failed to update rule bot status file");
                        }
                    }
                    Some(StatusCommand::Touch(profile)) => {
                        if let Err(error) = reporter.touch(&profile) {
                            warn!(error = %error, "failed to touch rule bot status file");
                        }
                    }
                    None => {
                        bail!("all rule bot status channels closed");
                    }
                }
            }
            joined = tasks.join_next() => {
                match joined {
                    Some(Ok((profile, Ok(())))) => {
                        if !cancellation.is_cancelled() {
                            bail!("rule bot instance exited unexpectedly: {profile}");
                        }
                    }
                    Some(Ok((profile, Err(error)))) => {
                        error!(profile, error = %error, "rule bot instance failed");
                        bail!("rule bot instance failed: {profile}: {error}");
                    }
                    Some(Err(error)) => {
                        return Err(error).context("rule bot instance task panicked");
                    }
                    None => bail!("all rule bot instances exited"),
                }
            }
        }
    }

    cancellation.cancel();
    while let Some(joined) = tasks.join_next().await {
        match joined {
            Ok((profile, Ok(()))) => {
                info!(profile, "rule bot instance stopped");
            }
            Ok((profile, Err(error))) => {
                warn!(profile, error = %error, "rule bot instance stopped with error");
            }
            Err(error) => {
                warn!(error = %error, "rule bot instance task join failed");
            }
        }
    }
    Ok(())
}

async fn shutdown_signal() {
    #[cfg(unix)]
    {
        use tokio::signal::unix::{SignalKind, signal};

        let mut terminate = match signal(SignalKind::terminate()) {
            Ok(signal) => signal,
            Err(_) => {
                let _ = tokio::signal::ctrl_c().await;
                return;
            }
        };
        tokio::select! {
            result = tokio::signal::ctrl_c() => {
                let _ = result;
            }
            _ = terminate.recv() => {}
        }
    }
    #[cfg(not(unix))]
    {
        let _ = tokio::signal::ctrl_c().await;
    }
}
