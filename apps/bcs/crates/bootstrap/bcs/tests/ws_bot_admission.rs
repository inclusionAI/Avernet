//! Integration tests for `[bot_ws_admission]` rate limiting on `/ws/bot`.

mod helpers;

use helpers::{create_temp_bots_dir, create_test_config, start_test_server_with_config};
use tokio_tungstenite::tungstenite::Error as WsError;

/// Upgrades beyond the bucket are rejected with 429 + Retry-After before the WS upgrade.
#[tokio::test]
async fn test_ws_bot_upgrades_beyond_burst_are_rate_limited() {
    let temp_dir = create_temp_bots_dir();
    let mut config = create_test_config(&temp_dir.path().to_path_buf());
    config.bot_ws_admission.enabled = true;
    config.bot_ws_admission.rate_per_sec = 1;
    config.bot_ws_admission.burst = 1;
    let (addr, _handle) = start_test_server_with_config(config).await;
    let url = format!("ws://{}/ws/bot", addr);

    let first = tokio_tungstenite::connect_async(&url).await;
    assert!(first.is_ok(), "First upgrade should be admitted");

    match tokio_tungstenite::connect_async(&url).await {
        Err(WsError::Http(response)) => {
            assert_eq!(response.status(), 429);
            assert_eq!(
                response
                    .headers()
                    .get("retry-after")
                    .and_then(|v| v.to_str().ok()),
                Some("1")
            );
        }
        other => panic!("Expected HTTP 429 rejection, got {other:?}"),
    }
}

/// Admission control is disabled by default, so back-to-back upgrades are accepted.
#[tokio::test]
async fn test_ws_bot_upgrades_are_not_limited_by_default() {
    let temp_dir = create_temp_bots_dir();
    let config = create_test_config(&temp_dir.path().to_path_buf());
    assert!(!config.bot_ws_admission.enabled);
    let (addr, _handle) = start_test_server_with_config(config).await;
    let url = format!("ws://{}/ws/bot", addr);

    let mut sockets = Vec::new();
    for _ in 0..5 {
        let (socket, _) = tokio_tungstenite::connect_async(&url)
            .await
            .expect("upgrade should be admitted");
        sockets.push(socket);
    }
}
