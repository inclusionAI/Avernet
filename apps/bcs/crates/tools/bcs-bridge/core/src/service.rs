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
    serve_bridge_managed(crate::config::BridgeConfig::new(vec![config])?, engines, shutdown, ready,
        crate::status::BridgeStatus::default()).await
}

/// One lifecycle owner, with independent state and failure boundaries per Provider.
/// Gateway Providers on the same address share a listener, not credentials/state.
pub async fn serve_bridge_managed(
    config: crate::config::BridgeConfig,
    engines: &EngineRegistry,
    shutdown: CancellationToken,
    ready: Option<tokio::sync::oneshot::Sender<()>>,
    status: crate::status::BridgeStatus,
) -> anyhow::Result<()> {
    use std::collections::BTreeMap;
    use tokio::{net::TcpListener, task::JoinSet};
    // Validate aliases before any DB recovery, engine process or listener starts.
    let config = crate::config::BridgeConfig::new(config.providers)?;
    let mut listeners = BTreeMap::new();
    let mut failed_listeners = std::collections::BTreeSet::new();
    let mut first_error = None;
    for provider in &config.providers {
        status.register(provider);
        if provider.mode == ConnectionMode::Gateway {
            let listen = provider.listen.ok_or_else(|| anyhow::anyhow!("gateway listen required"))?;
            if let std::collections::btree_map::Entry::Vacant(entry) = listeners.entry(listen) {
                if !failed_listeners.contains(&listen) {
                    match TcpListener::bind(listen).await {
                        Ok(listener) => { entry.insert(listener); },
                        Err(error) => {
                            failed_listeners.insert(listen);
                            if first_error.is_none() { first_error = Some(error.into()); }
                        },
                    }
                }
            }
        }
    }
    let mut gateways: BTreeMap<_, Vec<Arc<AppState>>> = BTreeMap::new();
    let mut plugins = Vec::new();
    for provider in config.providers {
        let provider_id = provider.provider_id.clone();
        if provider.mode == ConnectionMode::Gateway && provider.listen.is_some_and(|listen| failed_listeners.contains(&listen)) {
            status.provider(&provider_id, "failed");
            continue;
        }
        match AppState::new(provider, engines) {
            Ok(state) => {
                crate::a2a::recover(&state).await;
                let state = Arc::new(state);
                match state.config.mode {
                    ConnectionMode::Plugin => plugins.push(state),
                    ConnectionMode::Gateway => gateways.entry(state.config.listen.expect("validated listener")).or_default().push(state),
                }
            }
            Err(error) => {
                status.provider(&provider_id, "failed");
                tracing::error!(%provider_id, "Provider initialization failed; other Providers are isolated");
                if first_error.is_none() { first_error = Some(error); }
            }
        }
    }
    let stop = shutdown.child_token();
    let mut tasks = JoinSet::new();
    for state in plugins {
        let stop = stop.clone();
        let status = status.clone();
        tasks.spawn(async move { plugin::serve_observed(state, stop, status).await });
    }
    for (listen, states) in gateways {
        let listener = listeners.remove(&listen).expect("listener bound before initialization");
        let app = webhook::multi_router(states.clone());
        let stop = stop.clone();
        let status = status.clone();
        for state in &states { status.provider(&state.config.provider_id, "listening"); }
        tasks.spawn(async move {
            let stopping = states.clone();
            let result = axum::serve(listener, app).with_graceful_shutdown(async move {
                stop.cancelled().await;
                for state in stopping { state.runs.abort_all("shutdown").await; }
            }).await;
            for state in states {
                status.provider(&state.config.provider_id, if result.is_ok() { "stopped" } else { "failed" });
                state.shutdown_engines().await;
            }
            result.map_err(Into::into)
        });
    }
    if tasks.is_empty() { return Err(first_error.unwrap_or_else(|| anyhow::anyhow!("No Provider could initialize"))); }
    if let Some(ready) = ready { let _ = ready.send(()); }
    let signal = shutdown_signal();
    tokio::pin!(signal);
    let mut stopping = false;
    loop {
        tokio::select! {
            _ = &mut signal, if !stopping => { stopping = true; stop.cancel(); },
            _ = shutdown.cancelled(), if !stopping => { stopping = true; stop.cancel(); },
            result = tasks.join_next() => match result {
                None => break,
                Some(Ok(Ok(()))) => {},
                Some(Ok(Err(error))) => { if first_error.is_none() { first_error = Some(error); } },
                Some(Err(error)) => { if first_error.is_none() { first_error = Some(error.into()); } },
            },
        }
    }
    match first_error { Some(error) => Err(error), None => Ok(()) }
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
