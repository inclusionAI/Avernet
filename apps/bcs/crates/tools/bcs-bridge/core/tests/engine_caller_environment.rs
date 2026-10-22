#![cfg(unix)]
use std::{path::PathBuf, sync::Arc};
use bcs_bridge_core::engine::{claude_code::ClaudeCodeFactory, Engine, EngineFactory, SessionObserver, TurnRequest};

struct Observer;
#[async_trait::async_trait]
impl SessionObserver for Observer {
    async fn established(&self,_:&str)->Result<(),String>{Ok(())}
}

#[tokio::test]
async fn concurrent_engine_processes_receive_their_own_actor() {
    use std::os::unix::fs::PermissionsExt;
    let dir=tempfile::tempdir().unwrap();let bin=dir.path().join("engine");
    std::fs::write(&bin,r#"#!/bin/sh
# Consume the initial request before replying so the driver cannot write to a closed pipe.
IFS= read -r request || exit 1
printf '{"type":"result","subtype":"success","result":"%s","is_error":false,"session_id":"session"}\n' "$BCN_ACTOR_BOT_ID"
"#).unwrap();
    std::fs::set_permissions(&bin,std::fs::Permissions::from_mode(0o755)).unwrap();
    // The shell peer only implements legacy result frames; isolate caller env here.
    let options = toml::toml! { completion_mode = "legacy" };
    let engine = ClaudeCodeFactory::default().build(bin, &options).unwrap();
    async fn run(engine: Arc<dyn Engine>,cwd:PathBuf,actor:&str)->String {
        let mut req=TurnRequest::new(actor,"hello",cwd,Arc::new(Observer));
        req.env=vec![("BCN_ACTOR_BOT_ID".into(),actor.into()),("BCN_AUTH_MODE".into(),"agentpass-subbots".into())];
        let(tx,mut rx)=tokio::sync::mpsc::channel(64);
        let consumer=tokio::spawn(async move{while rx.recv().await.is_some(){}});
        let outcome=engine.run_turn(req,tx,tokio_util::sync::CancellationToken::new()).await.unwrap();
        consumer.await.unwrap();outcome.final_text.unwrap()
    }
    let (a,b)=tokio::join!(run(engine.clone(),dir.path().into(),"bot_A"),run(engine,dir.path().into(),"bot_B"));
    assert_eq!(a,"bot_A");assert_eq!(b,"bot_B");
}
