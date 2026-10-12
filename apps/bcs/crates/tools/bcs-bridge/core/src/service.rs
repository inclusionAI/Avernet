//! Shared service entrypoint used by the provider binary and the BCS Bridge CLI.
use std::sync::Arc;

use crate::{config::{ConnectionMode, ProviderConfig}, engine::EngineRegistry, plugin, webhook, AppState};
use tokio_util::sync::CancellationToken;

/// Run the configured gateway or plugin service until graceful shutdown.
/// Every bot's `engine` must be registered in `engines`.
pub async fn serve(config: ProviderConfig, engines: &EngineRegistry) -> anyhow::Result<()> {
    serve_managed(config, engines, CancellationToken::new(), None).await
}

/// Run the same service with local lifecycle control and an initialization notification.
/// For plugin mode, ready means initialized, not connected to the remote BCS.
pub async fn serve_managed(
    config: ProviderConfig,
    engines: &EngineRegistry,
    shutdown: CancellationToken,
    ready: Option<tokio::sync::oneshot::Sender<()>>,
) -> anyhow::Result<()> {
    let mode = config.mode;
    let state = Arc::new(AppState::new(config, engines)?);
    match mode {
        ConnectionMode::Gateway => {
            let listen = state.config.listen.ok_or_else(|| anyhow::anyhow!("gateway listen required"))?;
            let app = webhook::router(state.clone());
            let listener = tokio::net::TcpListener::bind(listen).await?;
            tracing::info!(%listen, state_path = %state.config.state_path.display(), "bcs-bridge gateway listening");
            if let Some(ready) = ready { let _ = ready.send(()); }
            axum::serve(listener, app).with_graceful_shutdown(async move {
                tokio::select! { _ = shutdown_signal() => {}, _ = shutdown.cancelled() => {} }
                state.runs.abort_all("shutdown").await;
            }).await?;
        }
        ConnectionMode::Plugin => {
            let signal = shutdown.clone();
            let signal_task = tokio::spawn(async move { shutdown_signal().await; signal.cancel(); });
            if let Some(ready) = ready { let _ = ready.send(()); }
            let result = plugin::serve(state, shutdown).await;
            signal_task.abort();
            result?;
        }
    }
    Ok(())
}

/// Wait for SIGINT (ctrl_c) or — on Unix — SIGTERM, whichever arrives first.
///
/// Split by `#[cfg(unix)]` so non-Unix builds still compile against `ctrl_c`.
/// Installing the SIGTERM handler never panics; on the (effectively unreachable
/// for Linux) failure path it falls back to ctrl_c-only before returning.
#[cfg(unix)]
async fn shutdown_signal() {
    use tokio::signal::unix::{signal, SignalKind};
    let mut sigterm = match signal(SignalKind::terminate()) {
        Ok(s) => s,
        Err(e) => {
            tracing::warn!(error = %e, "failed to install SIGTERM handler; falling back to ctrl_c");
            let _ = tokio::signal::ctrl_c().await;
            tracing::info!(signal = "SIGINT", "graceful shutdown initiated");
            return;
        }
    };
    tokio::select! {
        _ = tokio::signal::ctrl_c() => tracing::info!(signal = "SIGINT", "graceful shutdown initiated"),
        _ = sigterm.recv() => tracing::info!(signal = "SIGTERM", "graceful shutdown initiated"),
    }
}

#[cfg(not(unix))]
async fn shutdown_signal() {
    let _ = tokio::signal::ctrl_c().await;
    tracing::info!(signal = "SIGINT", "graceful shutdown initiated");
}
