use std::sync::Arc;
use std::time::Duration;

use bcs_service_api::{BotRuntimeConnectionService, BotRuntimeDisconnectCommand};
use bcs_ws::bot::BotConnectionRegistry;
use tracing::{debug, info, warn};

pub const DEFAULT_SCAN_INTERVAL: Duration = Duration::from_secs(60);
/// Disconnect bots 5 minutes before token expiry so they reconnect with a fresh token.
const EARLY_DISCONNECT_SECS: u64 = 300;
const MAX_JITTER_MS: u64 = 30_000;

pub async fn scan_once(
    bot_connections: &Arc<BotConnectionRegistry>,
    bot_runtime: &Arc<dyn BotRuntimeConnectionService>,
) -> u64 {
    scan_once_inner(bot_connections, bot_runtime, MAX_JITTER_MS).await
}

pub async fn scan_once_inner(
    bot_connections: &Arc<BotConnectionRegistry>,
    bot_runtime: &Arc<dyn BotRuntimeConnectionService>,
    max_jitter_ms: u64,
) -> u64 {
    let now_secs = current_unix_secs();
    let expired_bots = bot_connections.collect_expiring(now_secs, EARLY_DISCONNECT_SECS).await;

    if expired_bots.is_empty() {
        return 0;
    }

    let mut disconnected = 0u64;
    for bot_id in &expired_bots {
        if max_jitter_ms > 0 {
            let jitter = Duration::from_millis(fastrand::u64(0..max_jitter_ms));
            tokio::time::sleep(jitter).await;
        }

        bot_connections.disconnect(bot_id).await;
        if let Err(err) = bot_runtime
            .disconnect_streaming(BotRuntimeDisconnectCommand {
                bot_id: bot_id.clone(),
            })
            .await
        {
            warn!(
                bot_id = %bot_id,
                error = %err,
                "token_expiry: failed to record streaming disconnect"
            );
        }
        disconnected += 1;
        info!(
            target: "token_expiry_scanner",
            bot_id = %bot_id,
            "token_expiry: disconnected bot (token expiring soon, client will reconnect with fresh token)"
        );
    }

    disconnected
}

pub fn spawn(
    bot_connections: Arc<BotConnectionRegistry>,
    bot_runtime: Arc<dyn BotRuntimeConnectionService>,
    interval: Duration,
) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        info!(
            target: "token_expiry_scanner",
            event = "scanner.started",
            interval_secs = interval.as_secs(),
        );
        let mut ticker = tokio::time::interval(interval);
        ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        loop {
            ticker.tick().await;
            let n = scan_once(&bot_connections, &bot_runtime).await;
            if n > 0 {
                debug!(
                    target: "token_expiry_scanner",
                    event = "scanner.tick",
                    disconnected = n,
                );
            }
        }
    })
}

pub fn current_unix_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}
