//! Shared MCP-producer cache fixture, linked only into the test executable.
use std::sync::{Arc, OnceLock};
use std::time::Duration;
use axum::{Json, Router, routing::post};
use bcs_cache_api::{CachePlugin, CacheSetMode};
use bcs_cache_local::InMemoryCachePlugin;
use serde::Deserialize;

static CACHE: OnceLock<Arc<InMemoryCachePlugin>> = OnceLock::new();

inventory::submit! {
    bcs::plugins::CachePluginFactory {
        name: "task-cache-e2e",
        build: |_| Box::pin(async {
            Ok(bcs::plugins::CachePluginRegistration {
                kind: bcs::plugins::CachePluginKind::LocalMemory,
                plugin: CACHE.get().expect("cache fixture initialized before BCS").clone(),
            })
        }),
    }
}

#[derive(Deserialize)]
struct Record { intent_id: String, suffix: String, value: serde_json::Value }

pub async fn start(port: u16) -> anyhow::Result<()> {
    CACHE.set(Arc::new(InMemoryCachePlugin::new())).ok().expect("one cache fixture");
    let listener = tokio::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, port)).await?;
    let router = Router::new().route("/record", post(|Json(record): Json<Record>| async move {
        assert!(record.intent_id.starts_with("bcs_intent_"));
        assert!(["payload", "result", "delivery"].contains(&record.suffix.as_str()));
        let key = format!("bcs:coordination:v2:{}:{}", record.intent_id, record.suffix);
        CACHE.get().unwrap().set_value(&key, serde_json::to_vec(&record.value).unwrap(),
            Some(Duration::from_secs(604800)), CacheSetMode::Upsert).await.unwrap();
        Json(serde_json::json!({"ok": true}))
    }));
    tokio::spawn(async move { axum::serve(listener, router).await.unwrap(); });
    Ok(())
}
