#![cfg(unix)]
use std::sync::{Arc, Mutex};
use bcs_bridge_core::engine::{EngineFactory, SessionObserver, TurnError, TurnRequest};
use bcs_bridge_core::engine::codex_desktop::CodexDesktopFactory;
use serde_json::{Value, json};
use futures::{SinkExt, StreamExt};
use tokio_util::sync::CancellationToken;

struct Observer;
#[async_trait::async_trait]
impl SessionObserver for Observer {
    async fn established(&self, id: &str) -> Result<(), String> {
        assert_eq!(id, "desktop-thread"); Ok(())
    }
}

#[tokio::test]
async fn desktop_reuses_shared_server_streams_resumes_and_interrupts_only_its_turn() {
    let temp = tempfile::tempdir().unwrap();
    let socket = temp.path().join("desktop.sock");
    let listener = tokio::net::UnixListener::bind(&socket).unwrap();
    let requests = Arc::new(Mutex::new(Vec::<Value>::new()));
    let log = requests.clone();
    let server = tokio::spawn(async move {
        for _ in 0..5 {
            let (stream, _) = listener.accept().await.unwrap();
            let mut socket = tokio_tungstenite::accept_async(stream).await.unwrap();
            while let Some(frame) = socket.next().await {
                let frame = frame.unwrap();
                if frame.is_close() { break; }
                let request: Value = serde_json::from_str(frame.to_text().unwrap()).unwrap();
                log.lock().unwrap().push(request.clone());
                let method = request["method"].as_str().unwrap();
                let params = &request["params"];
                let response = match method {
                    "initialize" => json!({}),
                    "initialized" => continue,
                    "thread/start" => json!({"thread":{"id":"desktop-thread"}}),
                    "thread/resume" => {
                        assert_eq!(params["threadId"], "desktop-thread");
                        json!({"thread":{"id":"desktop-thread"}})
                    },
                    "turn/start" => json!({"turn":{"id":"owned-turn"}}),
                    "turn/interrupt" => {
                        assert_eq!(params, &json!({"threadId":"desktop-thread","turnId":"owned-turn"}));
                        json!({})
                    },
                    other => panic!("unexpected desktop RPC: {other}"),
                };
                let response = json!({"id":request["id"],"result":response}).to_string();
                socket.send(response.into()).await.unwrap();
                if method == "turn/start" {
                    let prompt = params["input"][0]["text"].as_str().unwrap();
                    let delta = json!({"method":"item/agentMessage/delta","params":{
                        "threadId":"desktop-thread","turnId":"owned-turn","delta":prompt}});
                    let unrelated = json!({"method":"item/agentMessage/delta","params":{
                        "threadId":"other-thread","turnId":"other-turn","delta":"unrelated desktop work"}});
                    socket.send(unrelated.to_string().into()).await.unwrap();
                    socket.send(delta.to_string().into()).await.unwrap();
                    if !matches!(prompt, "cancel" | "drop") {
                        let completed = json!({"method":"turn/completed","params":{
                            "threadId":"desktop-thread","turn":{"id":"owned-turn","status":"completed"}}});
                        socket.send(completed.to_string().into()).await.unwrap();
                    }
                }
            }
        }
    });
    let options = toml::Table::from_iter([("socket".into(), socket.to_str().unwrap().into())]);
    let engine = CodexDesktopFactory.build(std::path::PathBuf::new(), &options).unwrap();
    for prompt in ["first", "second", "cancel", "drop", "after-cancel"] {
        let mut request = TurnRequest::new(prompt, prompt, temp.path(), Arc::new(Observer));
        if prompt != "first" { request.engine_session_id = Some("desktop-thread".into()); }
        let (sender, mut receiver) = tokio::sync::mpsc::channel(32);
        let abort = CancellationToken::new();
        let engine = engine.clone();
        let cancellation = abort.clone();
        let task = tokio::spawn(async move { engine.run_turn(request, sender, cancellation).await });
        let event = tokio::time::timeout(std::time::Duration::from_secs(5), receiver.recv()).await.unwrap().unwrap();
        assert!(!format!("{event:?}").contains("unrelated desktop work"));
        if prompt == "cancel" { abort.cancel(); }
        if prompt == "drop" { drop(receiver); }
        let outcome = tokio::time::timeout(std::time::Duration::from_secs(5), task).await.unwrap().unwrap();
        if matches!(prompt, "cancel" | "drop") { assert!(matches!(outcome, Err(TurnError::Aborted))); }
        else { assert_eq!(outcome.unwrap().final_text.as_deref(), Some(prompt)); }
    }
    tokio::time::timeout(std::time::Duration::from_secs(5), server).await.unwrap().unwrap();
    let methods = requests.lock().unwrap().iter().map(|r| r["method"].as_str().unwrap().to_owned()).collect::<Vec<_>>();
    assert_eq!(methods.iter().filter(|m| *m == "thread/start").count(), 1);
    assert_eq!(methods.iter().filter(|m| *m == "thread/resume").count(), 4);
    assert_eq!(methods.iter().filter(|m| *m == "turn/interrupt").count(), 2);
}

#[tokio::test]
async fn missing_desktop_socket_has_no_cli_fallback() {
    let temp = tempfile::tempdir().unwrap();
    let socket = temp.path().join("missing.sock");
    let options = toml::Table::from_iter([("socket".into(), socket.to_str().unwrap().into())]);
    let engine = CodexDesktopFactory.build(std::path::PathBuf::new(), &options).unwrap();
    let (sender, _receiver) = tokio::sync::mpsc::channel(1);
    let result = engine.run_turn(TurnRequest::new("missing", "test", temp.path(), Arc::new(Observer)),
        sender, CancellationToken::new()).await;
    assert!(result.unwrap_err().to_string().contains("No standalone server was started"));
}
