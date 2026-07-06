use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};

use async_trait::async_trait;
use bcs_service_api::{
    BotDeliveryTarget, BotRuntimeConnectCommand, BotRuntimeConnectOutcome,
    BotRuntimeConnectionService, BotRuntimeDisconnectCommand, BotRuntimeStatusCommand,
    BotRuntimeStatusOutcome, BotUseCaseError, ServiceResult,
};
use bcs_ws::bot::BotConnectionRegistry;
use tokio::sync::mpsc;

use bcs::token_expiry_scanner::{current_unix_secs, scan_once_inner};

fn make_jwt_with_exp(exp: u64) -> String {
    use base64::Engine;
    let header = r#"{"kid":"default","typ":"JWT","alg":"RS256"}"#;
    let payload = format!(
        r#"{{"cnl":"BUC","sub":"test_bot","op":"111954","iss":"bumng.alipay.com","nonce":"6429","sid":"313090","aud":"*","nbf":1780655921,"sno":"V00_test","tnt_id":"ALIPW3CN","name":"test_bot","exp":{exp},"iat":1780656041,"jti":"117d"}}"#
    );
    let header_b64 = base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(header);
    let payload_b64 = base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(&payload);
    let sig = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx";
    format!("{header_b64}.{payload_b64}.{sig}")
}

#[derive(Default)]
struct MockBotRuntime {
    disconnect_count: AtomicUsize,
}

#[async_trait]
impl BotRuntimeConnectionService for MockBotRuntime {
    async fn connect_streaming(
        &self,
        _cmd: BotRuntimeConnectCommand,
    ) -> Result<BotRuntimeConnectOutcome, BotUseCaseError> {
        unimplemented!()
    }
    async fn update_runtime_status(
        &self,
        _cmd: BotRuntimeStatusCommand,
    ) -> Result<BotRuntimeStatusOutcome, BotUseCaseError> {
        unimplemented!()
    }
    async fn disconnect_streaming(
        &self,
        _cmd: BotRuntimeDisconnectCommand,
    ) -> Result<(), BotUseCaseError> {
        self.disconnect_count.fetch_add(1, Ordering::Relaxed);
        Ok(())
    }
    async fn resolve_delivery_target(&self, _bot_id: &str) -> ServiceResult<BotDeliveryTarget> {
        unimplemented!()
    }
}

#[tokio::test]
async fn scan_once_disconnects_expired_bot() {
    let registry = Arc::new(BotConnectionRegistry::new());
    let (tx, mut rx) = mpsc::channel::<String>(1);
    registry.connect("bot-expired".to_string(), tx).await;
    let past = current_unix_secs() - 3600;
    registry.set_token_expires_at("bot-expired", past).await;

    let runtime: Arc<dyn BotRuntimeConnectionService> = Arc::new(MockBotRuntime::default());
    let n = scan_once_inner(&registry, &runtime, 0).await;

    assert_eq!(n, 1);
    assert!(!registry.is_connected("bot-expired").await);
    assert!(rx.recv().await.is_none());
}

#[tokio::test]
async fn scan_once_skips_bot_without_token_expires() {
    let registry = Arc::new(BotConnectionRegistry::new());
    let (tx, _rx) = mpsc::channel::<String>(1);
    registry.connect("bot-no-exp".to_string(), tx).await;

    let runtime: Arc<dyn BotRuntimeConnectionService> = Arc::new(MockBotRuntime::default());
    let n = scan_once_inner(&registry, &runtime, 0).await;

    assert_eq!(n, 0);
    assert!(registry.is_connected("bot-no-exp").await);
}

#[tokio::test]
async fn scan_once_skips_bot_not_expiring_soon() {
    let registry = Arc::new(BotConnectionRegistry::new());
    let (tx, _rx) = mpsc::channel::<String>(1);
    registry.connect("bot-fresh".to_string(), tx).await;
    // Token expires in 1 hour — well beyond the 5-minute early disconnect window
    let future = current_unix_secs() + 3600;
    registry.set_token_expires_at("bot-fresh", future).await;

    let runtime: Arc<dyn BotRuntimeConnectionService> = Arc::new(MockBotRuntime::default());
    let n = scan_once_inner(&registry, &runtime, 0).await;

    assert_eq!(n, 0);
    assert!(registry.is_connected("bot-fresh").await);
}

#[tokio::test]
async fn scan_once_disconnects_multiple_expired_bots() {
    let registry = Arc::new(BotConnectionRegistry::new());
    let past = current_unix_secs() - 3600;

    let (tx1, _rx1) = mpsc::channel::<String>(1);
    let (tx2, _rx2) = mpsc::channel::<String>(1);
    let (tx3, _rx3) = mpsc::channel::<String>(1);
    registry.connect("expired-a".to_string(), tx1).await;
    registry.connect("expired-b".to_string(), tx2).await;
    registry.connect("still-valid".to_string(), tx3).await;

    registry.set_token_expires_at("expired-a", past).await;
    registry.set_token_expires_at("expired-b", past - 100).await;
    registry.set_token_expires_at("still-valid", current_unix_secs() + 3600).await;

    let runtime = Arc::new(MockBotRuntime::default());
    let rt: Arc<dyn BotRuntimeConnectionService> = runtime.clone();
    let n = scan_once_inner(&registry, &rt, 0).await;

    assert_eq!(n, 2);
    assert!(!registry.is_connected("expired-a").await);
    assert!(!registry.is_connected("expired-b").await);
    assert!(registry.is_connected("still-valid").await);
    assert_eq!(runtime.disconnect_count.load(Ordering::Relaxed), 2);
}

#[test]
fn make_jwt_with_exp_is_decodable() {
    let token = make_jwt_with_exp(1780742441);
    let exp = bcs_ws::bot::decode_jwt_exp(&token);
    assert_eq!(exp, Some(1780742441));
}
