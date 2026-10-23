use std::{path::Path, sync::Arc, time::Duration};
use bcs_bridge_core::{config::{BridgeConfig, ProviderConfig}, engine::EngineRegistry, status::BridgeStatus, AppState};
use futures::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tokio::net::TcpListener;
use tokio_tungstenite::{accept_async, tungstenite::Message};
use tokio_util::sync::CancellationToken;

fn config(root: &Path, id: &str, upstream: Option<&str>) -> ProviderConfig {
    let mode=match upstream {
        Some(url)=>format!("mode='plugin'\n[plugin]\nurl='{url}'\nreconnect_interval_ms=10"),
        None=>format!("listen='127.0.0.1:0'\nbcs_to_provider_token='secret-{id}'"),
    };
    ProviderConfig::from_toml(&format!("provider_id='{id}'\nstate_path='{id}.db'\n{mode}\n[[bot]]\nprovider_bot_ref='same'\nbot_id='bot-{id}'\ntoken='initial-{id}'\nengine='claude-code'\ncwd='{}'",root.display()), &root.join("bridge.toml")).unwrap()
}
async fn wait_state(status: &BridgeStatus, provider: &str, expected: &str) {
    tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            if status.snapshot().iter().any(|bot|bot.provider_id==provider && bot.state==expected) { break; }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    }).await.unwrap();
}

#[tokio::test]
async fn shared_gateway_routes_authentication_and_durable_sessions_by_provider() {
    let dir=tempfile::tempdir().unwrap();
    let a=Arc::new(AppState::new(config(dir.path(),"a",None), &EngineRegistry::builtin()).unwrap());
    let b=Arc::new(AppState::new(config(dir.path(),"b",None), &EngineRegistry::builtin()).unwrap());
    let listener=TcpListener::bind("127.0.0.1:0").await.unwrap(); let url=format!("http://{}/webhook",listener.local_addr().unwrap());
    let router=bcs_bridge_core::webhook::multi_router(vec![a.clone(),b.clone()]);
    let task=tokio::spawn(async move{axum::serve(listener,router).await.unwrap()});
    let client=reqwest::Client::builder().no_proxy().timeout(Duration::from_secs(5)).build().unwrap();
    for (provider,token,code) in [("a","secret-a",200),("b","secret-b",200),("b","secret-a",401),("a","secret-b",401),("missing","secret-a",401)] {
        let response=client.post(&url).bearer_auth(token).json(&json!({"type":"req","id":"same-request","method":"bot.ping","to_bot":{"provider_id":provider,"provider_bot_ref":"same"}})).send().await.unwrap();
        assert_eq!(response.status().as_u16(),code);
    }
    for state in [&a,&b] {
        let provider=&state.config.provider_id;
        let response=client.post(&url).bearer_auth(format!("secret-{provider}")).json(&json!({"type":"req","id":"same-inject","method":"chat.inject","session_id":"same-session","to_bot":{"provider_id":provider,"provider_bot_ref":"same"},"message":{"role":"user","content":[{"type":"text","text":provider}]}})).send().await.unwrap();
        assert_eq!(response.status().as_u16(),200,"{}",response.text().await.unwrap());
        state.sessions.set_engine_session_id("same","same-session",&format!("engine-{provider}")).await.unwrap();
        state.sessions.try_start_run("same","same-session","same-run").await.unwrap();
        let injects=state.sessions.claim_injects("same","same-session","same-run").await.unwrap();
        assert_eq!(injects.len(),1);
        assert_eq!(state.sessions.mapping("same","same-session").await.unwrap().engine_session_id,Some(format!("engine-{provider}")));
    }
    a.sessions.complete_injects("same","same-session","same-run").await.unwrap();
    b.sessions.release_injects("same","same-session","same-run").await.unwrap();
    assert!(a.sessions.claim_injects("same","same-session","next").await.unwrap().is_empty());
    assert_eq!(b.sessions.claim_injects("same","same-session","next").await.unwrap().len(),1);
    task.abort();
}

#[tokio::test]
async fn mixed_transports_survive_a_fatal_provider_and_persist_independent_rotated_tokens() {
    let dir=tempfile::tempdir().unwrap();
    let listener=TcpListener::bind("127.0.0.1:0").await.unwrap(); let url=format!("ws://{}/ws/bot",listener.local_addr().unwrap());
    let configs=vec![config(dir.path(),"a",Some(&url)),config(dir.path(),"b",Some(&url)),config(dir.path(),"gateway",None)];
    let saved=configs.clone(); let status=BridgeStatus::default(); let observations=status.clone();
    let shutdown=CancellationToken::new(); let stop=shutdown.clone();
    let task=tokio::spawn(async move {
        bcs_bridge_core::service::serve_bridge_managed(BridgeConfig::new(configs).unwrap(),&EngineRegistry::builtin(),stop,None,observations).await
    });
    let mut sockets=Vec::new();
    for _ in 0..2 {
        let (stream,_)=tokio::time::timeout(Duration::from_secs(5),listener.accept()).await.unwrap().unwrap();
        let mut socket=accept_async(stream).await.unwrap();
        let request:Value=serde_json::from_str(socket.next().await.unwrap().unwrap().to_text().unwrap()).unwrap();
        let id=request["params"]["bot_id"].as_str().unwrap();
        assert_eq!(request["params"]["token"],format!("initial-{}",id.strip_prefix("bot-").unwrap()));
        if id=="bot-a" {
            socket.send(Message::Text(json!({"type":"res","id":request["id"],"ok":false,"error":{"code":"invalid_token","message":"rejected"}}).to_string().into())).await.unwrap();
        } else {
            socket.send(Message::Text(json!({"type":"res","id":request["id"],"ok":true,"payload":{"bot_uuid":id,"token":"rotated-b","is_new":false,"protocol_version":3,"min_supported_version":1}}).to_string().into())).await.unwrap();
        }
        sockets.push(socket);
    }
    wait_state(&status,"a","failed").await; wait_state(&status,"b","online").await; wait_state(&status,"gateway","listening").await;
    assert!(!task.is_finished());
    let serialized=serde_json::to_string(&status.snapshot()).unwrap();
    assert!(!serialized.contains("initial-") && !serialized.contains("rotated-") && !serialized.contains("secret-"));
    shutdown.cancel();
    assert!(tokio::time::timeout(Duration::from_secs(5),task).await.unwrap().unwrap().is_err());
    for cfg in &saved[..2] {
        let state=AppState::new(cfg.clone(),&EngineRegistry::builtin()).unwrap();
        let identity=state.sessions.load_identity(&url,"same").await.unwrap();
        if cfg.provider_id=="a" { assert!(identity.is_none()); } else { assert_eq!(identity.unwrap().token,"rotated-b"); }
    }
}

#[test]
fn explicit_socket_cannot_alias_the_default_desktop_connection() {
    let dir=tempfile::tempdir().unwrap();
    let mut a=config(dir.path(),"a",None).bots.remove(0);
    a.engine="codex-desktop".into();
    let mut b=a.clone();b.bot_id=Some("bot-b".into());
    let socket=dir.path().join("app-server-control/app-server-control.sock");
    b.engine_options.insert("socket".into(),socket.to_str().unwrap().into());
    let mut engines=EngineRegistry::new();
    engines.register(bcs_bridge_core::engine::codex_desktop::CodexDesktopFactory::with_codex_home(dir.path().into()));
    assert!(engines.validate_resources([&a,&b]).is_err());
    b.engine_options.insert("socket".into(),dir.path().join("different.sock").to_str().unwrap().into());
    assert!(engines.validate_resources([&a,&b]).is_ok());
}
