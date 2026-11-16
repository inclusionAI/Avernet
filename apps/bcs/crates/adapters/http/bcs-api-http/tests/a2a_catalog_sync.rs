//! V1 adapter -> real application/Core/SQLite (assembled in bcs-test-support)
//! -> consumed queue -> Fuse HTTP. The adapter manifest stays free of the
//! concrete BCS crates; only the test-support fixture dep carries them.
use std::sync::Arc;
use async_trait::async_trait;
use axum::{body::{Body, to_bytes}, http::Request, routing::put, extract::Path, Json};
use bcs_bot_store::MemoryProviderStore;
use bcs_db_api::{DbPlugin, DbStatement};
use bcs_service_api::{ProviderCredential, ProviderCredentialRepoPort, ServiceResult, lifecycle::ServiceLifecycle,
    port::a2a_card::{A2aCardPort, ValidatedA2aCard}, types::a2a_bot::select_a2a_interface};
use bcs_test_support::contract::a2a_catalog_stack::{build_a2a_catalog_stack, fuse_card_sync_worker};
use bcs_test_support::contract::bot_registration_chain::registration_chain;
use serde_json::{json, Value};
use tower::ServiceExt;
#[allow(dead_code)]
mod register_support;

fn card() -> Value {
    json!({"name":"Catalog Agent","description":"Card description","version":"1.0.0",
        "capabilities":{"streaming":true},"defaultInputModes":["text/plain"],"defaultOutputModes":["text/plain"],
        "supportedInterfaces":[{"url":"https://example.org/agent","protocolBinding":"JSONRPC","protocolVersion":"1.0"}],
        "skills":[{"id":"query","name":"query","description":"Queries","tags":[]}]})
}
struct Fetcher;
#[async_trait]
impl A2aCardPort for Fetcher {
    async fn validate(&self, raw: Value, _: bool) -> ServiceResult<ValidatedA2aCard> {
        Ok(ValidatedA2aCard { interface:select_a2a_interface(&raw, |_,_|true).unwrap(), raw })
    }
    async fn fetch(&self, _: &str, ready: bool) -> ServiceResult<ValidatedA2aCard> {
        self.validate(card(), ready).await
    }
}
async fn call(app: &axum::Router, method: &str, path: &str, body: Value) -> (u16, Value) {
    let response = app.clone().oneshot(Request::builder().method(method).uri(path)
        .header("authorization", "Bearer test-admin").header("content-type", "application/json")
        .header("x-request-id", "catalog-v1-request").body(Body::from(body.to_string())).unwrap()).await.unwrap();
    let status=response.status().as_u16();
    (status, serde_json::from_slice(&to_bytes(response.into_body(), 1024*1024).await.unwrap()).unwrap())
}

#[tokio::test]
async fn v1_mutations_publish_to_fuse_and_rejected_writes_do_not() {
    let chain=registration_chain().await;
    // The registration fixture intentionally has a minimal schema; publication
    // additionally reads these existing control-plane columns.
    for column in ["task_claim_mode INTEGER DEFAULT 0", "task_dream_mode INTEGER DEFAULT 0",
        "user_visibility TEXT DEFAULT 'protected'", "friend_ext TEXT", "friend_check_in_strategy TEXT"] {
        chain.db.execute(DbStatement::new(format!("ALTER TABLE bcs_bots ADD COLUMN {column}"))).await.unwrap();
    }
    chain.db.execute(DbStatement::new("UPDATE bcs_providers SET slug='orders', config='{\"downlink\":{\"enabled\":true,\"auth_mode\":\"static_bearer\"}}' WHERE provider_id='provider-a'")).await.unwrap();
    let credentials=Arc::new(MemoryProviderStore::new());
    for (kind, secret) in [("provider_admin","test-admin"),("downlink_bcs_to_provider","test-downlink")] {
        credentials.insert_credential(ProviderCredential {provider_id:"provider-a".into(), credential_kind:kind.into(),
            secret_value:secret.into(), disabled:false, created_at:0, updated_at:0}).await.unwrap();
    }
    let stack=build_a2a_catalog_stack(&chain, credentials, Arc::new(Fetcher));
    let app=bcs_api_http::router(register_support::test_state(chain.register_service.clone())
        .with_a2a_service(Arc::new(stack.api)));

    let (tx, mut rx)=tokio::sync::mpsc::unbounded_channel();
    let fuse=axum::Router::new().route("/api/v2/agent-cards/{id}", put(move |Path(id): Path<String>, Json(body): Json<Value>| {
        let tx=tx.clone(); async move {tx.send((id,body)).unwrap(); (axum::http::StatusCode::ACCEPTED,Json(json!({"saved":true,"card_version":1})))}
    }));
    let listener=tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr=listener.local_addr().unwrap();
    let server=tokio::spawn(async move {axum::serve(listener,fuse).await.unwrap()});
    let worker=fuse_card_sync_worker(&chain, stack.queue, addr.to_string());
    worker.initialize().await.unwrap();
    let input=json!({"provider_bot_ref":"catalog-ref","owners":["delegated-owner"],"agent_card_url":"https://example.org/card.json"});
    let (status, saved)=call(&app,"POST","/api/v1/collaboration/a2a/agents",input.clone()).await;
    assert_eq!(status,201,"{saved}");
    let id=saved["data"]["bot_id"].as_str().unwrap().to_owned();
    let published=tokio::time::timeout(std::time::Duration::from_secs(3),rx.recv()).await.unwrap().unwrap();
    assert_eq!(published.0,id);
    assert_eq!(published.1["card"]["name"],"Catalog Agent");
    assert_eq!(published.1["resource_type"],"agent");
    let path=format!("/api/v1/collaboration/a2a/agents/{id}/agent-card");
    for (method,path,body) in [
        ("PUT",path.clone(),json!({"visibility":"public"})),
        ("POST",format!("{path}:refresh"),json!({})),
        ("PUT",path.clone(),json!({"agent_card":card()})),
    ] {
        let (status,response)=call(&app,method,&path,body).await;
        assert_eq!(status,200,"{response}");
        let (published_id,body)=tokio::time::timeout(std::time::Duration::from_secs(3),rx.recv()).await.unwrap().unwrap();
        assert_eq!(published_id,id);
        assert_eq!(body["labels"]["visibility"],json!(["public"]));
    }
    let (status,_) = call(&app,"POST","/api/v1/collaboration/a2a/agents",input).await;
    assert_eq!(status,409);
    let (status,_) = call(&app,"PUT",&path,json!({})).await;
    assert_eq!(status,400);
    assert!(tokio::time::timeout(std::time::Duration::from_millis(100),rx.recv()).await.is_err());
    worker.shutdown().await.unwrap();
    server.abort(); let _=server.await;
}
