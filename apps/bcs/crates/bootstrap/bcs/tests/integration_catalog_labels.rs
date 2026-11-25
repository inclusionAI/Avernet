//! Real HTTP -> application -> SQLite -> queued full Fuse payload evidence.
use std::sync::{Arc,Mutex,atomic::{AtomicBool,Ordering}};
use std::time::Duration;
use axum::{Router,Json,extract::State,http::{StatusCode,Uri},body::Bytes};
use bcs::{BcsConfig,BcsServer};
use bcs_fuse_client::BcsFuseApiVersion;
use bcs_service_api::types::catalog_labels::{CatalogLabelPolicy,CatalogLabelRule};
use serde_json::{Value,json};
#[derive(Clone,Default)]struct Catalog {remote_card:Arc<Mutex<Value>>,calls:Arc<Mutex<Vec<(String,Value)>>>,fail:Arc<AtomicBool>}
async fn catalog(State(state):State<Catalog>,uri:Uri,headers:axum::http::HeaderMap,body:Bytes)->(StatusCode,Json<Value>){
    if uri.path()=="/remote-card" {
        assert!(headers.get("authorization").is_none(),"Provider credentials must not reach the Card host");
        return (StatusCode::OK,Json(state.remote_card.lock().unwrap().clone()));
    }
    let body=serde_json::from_slice(&body).unwrap_or(Value::Null);state.calls.lock().unwrap().push((uri.path().into(),body));
    (if state.fail.load(Ordering::SeqCst){StatusCode::SERVICE_UNAVAILABLE}else{StatusCode::ACCEPTED},Json(json!({"saved":true,"card_version":1,"deleted":true})))
}
async fn payload(state:&Catalog,from:usize,id:&str,stage:Option<&str>)->Value {
    tokio::time::timeout(Duration::from_secs(15),async {loop {
        if let Some((_,value))=state.calls.lock().unwrap().iter().skip(from).find(|(path,value)|path.ends_with(id)
            && value.get("labels").is_some() && value["labels"]["stage"].as_array().and_then(|v|v.first()).and_then(Value::as_str)==stage).cloned(){return value;}
        tokio::time::sleep(Duration::from_millis(20)).await;
    }}).await.expect("full current catalog payload")
}
fn count(state:&Catalog)->usize{state.calls.lock().unwrap().len()}
fn raw_card()->Value {json!({"name":"LabelsAgent","description":"Catalog metadata regression","version":"1.0",
    "supportedInterfaces":[{"url":"http://127.0.0.1:9/agent","protocolBinding":"HTTP+JSON","protocolVersion":"1.0"}],
    "capabilities":{"streaming":true},"defaultInputModes":["text/plain"],"defaultOutputModes":["text/plain"],
    "skills":[{"id":"lookup","name":"Lookup","description":"Lookup","tags":[]}]})}
fn owner_principal()->String {
    use jsonwebtoken::{Algorithm,EncodingKey,Header,encode};
    let now=std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs();
    let mut header=Header::new(Algorithm::HS256);header.typ=Some("JWT".into());header.kid=Some("bare".into());
    encode(&header,&json!({"iss":"gateway","aud":"bcs","iat":now-1,"exp":now+120,
        "principals":[{"type":"user","tenant":"tenant-a","subject":{"id":"owner","username":"owner","tenant_id":"tenant-a"}}]}),
        &EncodingKey::from_secret(b"test-only-catalog-labels-signing-key")).unwrap()
}
#[tokio::test]
async fn agent_http_persistence_retry_restart_and_full_payload(){
    // One test per executable; initialize a test-only secret before any server tasks.
    unsafe {std::env::set_var("BCS_SECRET_CATALOG_LABELS_TEST","test-only-catalog-labels-signing-key");}
    let mock=Catalog::default();*mock.remote_card.lock().unwrap()=raw_card();let listener=tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url=format!("http://{}",listener.local_addr().unwrap());let router=Router::new().fallback(catalog).with_state(mock.clone());
    let catalog_task=tokio::spawn(async move {axum::serve(listener,router).await.unwrap()});
    let dir=tempfile::tempdir().unwrap();let mut config=BcsConfig::default();config.bots_base_dir=dir.path().join("bots");
    config.security.outbound_url.block_private_networks=false;config.security.outbound_url.allow_loopback=true;
    config.secret.provider="env".into();config.gateway_principal.signing_key_secret=Some("catalog-labels-test".into());
    config.group_session_ws.signing_key_secret="catalog-labels-test".into();
    config.session_files.backend.insert("data_dir".into(),toml::Value::String(dir.path().join("files").display().to_string()));
    config.database.sqlite.path=dir.path().join("state.sqlite").display().to_string();config.a2a_allow_anonymous=true;
    config.catalog_label_policy=Arc::new(CatalogLabelPolicy::new([("stage".into(),CatalogLabelRule {allowed_values:vec!["trial".into(),"running".into()],allow_multiple:false})].into()).unwrap());
    config.bcsfuse.enabled=true;config.bcsfuse.api_version=BcsFuseApiVersion::V2;config.bcsfuse.url=url.clone();config.bcsfuse.sync_timeout_ms=200;
    let (addr,server,state)=BcsServer::new_with_storage(config.clone()).await.unwrap().run_on_random_port_with_state().await.unwrap();
    let provider=state.services.provider_management.register_provider(bcs_service_api::RegisterProviderCommand {
        name:"Labels provider".into(),slug:Some("labels-provider".into()),webhook_url:Some("https://example.test/provider".into()),
        admin_callback_url:None,auth_mode:bcs_service_api::ProviderAuthMode::StaticBearer,created_by:"owner".into(),protocol_version:None,
        coordination:None,collaboration_scope:None,caller_capabilities:None,a2a:None,
    }).await.unwrap();
    let token=provider.provider_admin_token;
    let base=format!("http://{addr}/api/v1/collaboration/a2a/agents");
    let http=reqwest::Client::new();
    let response=http.post(&base).bearer_auth(&token).json(&json!({"provider_bot_ref":"labels-source","owners":["owner"],"agent_card_url":format!("{url}/remote-card"),"catalog_labels":{"stage":["trial","trial"]}})).send().await.unwrap();
    let status=response.status();let body:Value=response.json().await.unwrap();assert_eq!(status,StatusCode::CREATED,"{body}");
    let id=body["data"]["bot_id"].as_str().unwrap();assert_eq!(body["data"]["catalog_labels"],json!({"stage":["trial"]}));
    let agent=payload(&mock,0,id,Some("trial")).await;assert_eq!(agent["labels"]["visibility"],json!(["protected"]));assert!(agent["labels"].get("user_visibility").is_some());assert!(agent["card"].get("catalog_labels").is_none());
    let mut group=bcs_service_api::Group::new("labels-group",id,vec![]);group.group_strategy=bcs_service_api::GroupStrategy::ManagerWorker;
    let group_labels=serde_json::from_value(json!({"stage":["trial"]})).unwrap();
    state.services.group.upsert_with_catalog_labels(group,Some(group_labels)).await.unwrap();
    let from=count(&mock);
    state.services.group.patch_mutable_fields("labels-group",bcs_service_api::GroupMutableFieldsPatch {visibility:Some("public".into()),..Default::default()}).await.unwrap();
    let group_payload=payload(&mock,from,"labels-group",Some("trial")).await;
    assert_eq!(group_payload["labels"],json!({"stage":["trial"],"visibility":["public"]}));
    let from=count(&mock);
    state.services.group.patch_mutable_fields("labels-group",bcs_service_api::GroupMutableFieldsPatch {catalog_labels:Some(Default::default()),..Default::default()}).await.unwrap();
    assert_eq!(payload(&mock,from,"labels-group",None).await["labels"],json!({"visibility":["public"]}));
    // The mounted Group REST path uses real ownership admission, application and SQLite transactions.
    let groups_url=format!("http://{addr}/openapi/v1/collaboration/groups");let principal=owner_principal();
    let response=http.post(&groups_url).header("x-avernet-principal",&principal).json(&json!({
        "group_kind":"normal","name":"HTTP labels group","driver_bot_uuid":id,"participants":[],
        "collaboration":{"strategy":"manager_worker"},"catalog_labels":{"stage":["trial"]}
    })).send().await.unwrap();let status=response.status();let created:Value=response.json().await.unwrap();
    assert_eq!(status,StatusCode::CREATED,"{created}");
    let group_id=created["data"]["group_id"].as_str().unwrap();let group_url=format!("{groups_url}/{group_id}");
    assert_eq!(created["data"]["catalog_labels"],json!({"stage":["trial"]}));
    // A legacy create must not be a second, unauthorized update surface for an existing ID.
    let unrelated=state.services.a2a_bot_management.as_ref().unwrap().register_provider_bot(bcs_service_api::RegisterProviderBotCommand {
        provider_id:provider.provider_id.clone(),provider_admin_token:token.clone(),provider_bot_ref:"other-owner-ref".into(),name:String::new(),
        owners:vec!["other-owner".into()],summary:None,webhook_url:None,domains:vec![],skills:vec![],scopes:vec![],bot_uuid:None,
        reject_existing_bot_uuid:true,connection_mode:bcs_service_api::ProviderBotConnectionMode::Gateway,
        avatar:bcs_service_api::types::bot_profile::AvatarChange::Keep,
    },bcs_service_api::types::a2a_bot::A2aCardSource::Inline(raw_card()),1000).await.unwrap();
    let unrelated_token=unrelated.bot_runtime_token.unwrap();
    let legacy_group_url=format!("http://{addr}/groups/{group_id}");
    // Compare the same detail projection: create also returns transient bootstrap fields.
    let before:Value=http.get(&group_url).header("x-avernet-principal",&principal).send().await.unwrap().json().await.unwrap();
    let denied=http.patch(&legacy_group_url).bearer_auth(&unrelated_token).json(&json!({"name":"must not overwrite"})).send().await.unwrap();
    assert_eq!(denied.status(),StatusCode::FORBIDDEN);
    let denied=http.post(format!("http://{addr}/groups")).bearer_auth(&unrelated_token).json(&json!({
        "id":group_id,"driver_bot":unrelated.bot_uuid,"originator":unrelated.bot_uuid,
        "participants":[{"bot_uuid":unrelated.bot_uuid,"role":"manager"}],
        "group_strategy":"manager_worker","visibility":"private","create_initial_session":false,
        "catalog_labels":{"stage":["running"]}
    })).send().await.unwrap();
    let status=denied.status();let denial=denied.text().await.unwrap();assert_eq!(status,StatusCode::CONFLICT,"{denial}");
    let preserved:Value=http.get(&group_url).header("x-avernet-principal",&principal).send().await.unwrap().json().await.unwrap();
    assert_eq!(preserved["data"],before["data"]);

    for patch in [json!({"name":"HTTP renamed"}),json!({"catalog_labels":{"stage":["running"]}})] {
        let response=http.patch(&group_url).header("x-avernet-principal",&principal).json(&patch).send().await.unwrap();
        let status=response.status();let value:Value=response.json().await.unwrap();assert_eq!(status,StatusCode::OK,"{value}");
        assert_eq!(value["data"]["catalog_labels"],if patch.get("catalog_labels").is_some(){json!({"stage":["running"]})}else{json!({"stage":["trial"]})});
    }
    for labels in [json!(null),json!({"unknown":null}),json!({"stage":["trial","running"]})] {
        assert!(http.patch(&group_url).header("x-avernet-principal",&principal).json(&json!({"name":"must not save","catalog_labels":labels})).send().await.unwrap().status().is_client_error());
    }
    let saved:Value=http.get(&group_url).header("x-avernet-principal",&principal).send().await.unwrap().json().await.unwrap();
    assert_eq!(saved["data"]["name"],"HTTP renamed");assert_eq!(saved["data"]["catalog_labels"],json!({"stage":["running"]}));
    let cleared:Value=http.patch(&group_url).header("x-avernet-principal",&principal).json(&json!({"catalog_labels":{}})).send().await.unwrap().json().await.unwrap();
    assert_eq!(cleared["data"]["catalog_labels"],json!({}));
    let endpoint=format!("{base}/{id}/agent-card");
    for input in [json!(null),json!(1),json!([]),json!({"unknown":null}),json!({"stage":["trial","running"]}),json!({"stage":[null]}),json!({"stage":["TRIAL"]})] {
        let response=http.put(&endpoint).bearer_auth(&token).json(&json!({"agent_card":raw_card(),"catalog_labels":input})).send().await.unwrap();
        assert!(response.status().is_client_error());
        let saved:Value=http.get(&endpoint).bearer_auth(&token).send().await.unwrap().json().await.unwrap();assert_eq!(saved["data"]["catalog_labels"],json!({"stage":["trial"]}));
    }
    // Real URL refresh: unchanged and changed declarations preserve metadata and republish full labels.
    for description in ["Catalog metadata regression","Changed remote declaration"] {
        mock.remote_card.lock().unwrap()["description"]=json!(description);
        let from=count(&mock);
        let response=http.post(format!("{endpoint}:refresh")).bearer_auth(&token).json(&json!({})).send().await.unwrap();
        let status=response.status();let refreshed:Value=response.json().await.unwrap();assert_eq!(status,StatusCode::OK,"{refreshed}");
        assert_eq!(refreshed["data"]["catalog_labels"],json!({"stage":["trial"]}));
        assert_eq!(refreshed["data"]["agent_card"]["description"],description);
        let published=payload(&mock,from,id,Some("trial")).await;assert_eq!(published["card"]["description"],description);
    }
    // Failed catalog delivery is retried from persisted latest state, not a queued trial payload.
    mock.fail.store(true,Ordering::SeqCst);let from=count(&mock);
    let response=http.put(&endpoint).bearer_auth(&token).json(&json!({"catalog_labels":{"stage":["running"]}})).send().await.unwrap();assert!(response.status().is_success());
    payload(&mock,from,id,Some("running")).await;
    let response=http.put(&endpoint).bearer_auth(&token).json(&json!({"catalog_labels":{}})).send().await.unwrap();assert!(response.status().is_success());
    mock.fail.store(false,Ordering::SeqCst);let cleared=payload(&mock,from,id,None).await;
    assert_eq!(cleared["labels"].as_object().unwrap().len(),3);assert!(!cleared.to_string().contains("tombstone"));
    let response=http.put(&endpoint).bearer_auth(&token).json(&json!({"catalog_labels":{"stage":["running"]}})).send().await.unwrap();assert!(response.status().is_success());
    let find:Value=http.get(format!("{base}?provider_bot_ref=labels-source")).bearer_auth(&token).send().await.unwrap().json().await.unwrap();assert_eq!(find["data"]["items"][0]["catalog_labels"],json!({"stage":["running"]}));
    // Close the serving task/lifecycles, rebuild against the same persisted schema/data.
    state.lifecycle.lock().await.shutdown_all().await.unwrap();server.abort();let _=server.await;drop(state);
    let (addr,server,state)=BcsServer::new_with_storage(config).await.unwrap().run_on_random_port_with_state().await.unwrap();
    let endpoint=format!("http://{addr}/api/v1/collaboration/a2a/agents/{id}/agent-card");
    let saved:Value=http.get(&endpoint).bearer_auth(&token).send().await.unwrap().json().await.unwrap();assert_eq!(saved["data"]["catalog_labels"],json!({"stage":["running"]}));
    let from=count(&mock);let response=http.put(&endpoint).bearer_auth(&token).json(&json!({"visibility":"private"})).send().await.unwrap();assert!(response.status().is_success());
    let preserved=payload(&mock,from,id,Some("running")).await;assert_eq!(preserved["labels"]["visibility"],json!(["private"]));
    // Both canonical URL aliases and Provider-ID legacy Card updates keep omission distinct from {}.
    let provider_id=provider.provider_id;
    for (endpoint,enveloped) in [
        (endpoint.clone(),true),
        (format!("http://{addr}/a2a/agents/{id}/agent-card"),false),
        (format!("http://{addr}/providers/{provider_id}/bots/{id}/a2a"),false),
    ] {
        for expected in [None,Some(0)] {
            let legacy_read:Value=http.get(format!("http://{addr}/providers/{provider_id}/bots/{id}/a2a")).bearer_auth(&token).send().await.unwrap().json().await.unwrap();
            let mut request=json!({"agent_card":raw_card()});
            if expected.is_some() && endpoint.contains("/providers/") {request["expected_revision"]=legacy_read["revision"].clone();}
            let from=count(&mock);
            let response=http.put(&endpoint).bearer_auth(&token).json(&request).send().await.unwrap();
            let status=response.status();let text=response.text().await.unwrap();assert_eq!(status,StatusCode::OK,"endpoint={endpoint}, response={text}");let saved:Value=serde_json::from_str(&text).unwrap();
            let saved=if enveloped {&saved["data"]}else{&saved};assert_eq!(saved["catalog_labels"],json!({"stage":["running"]}));
            assert_eq!(payload(&mock,from,id,Some("running")).await["labels"]["visibility"],json!(["private"]));
        }
        let from=count(&mock);
        let response=http.put(&endpoint).bearer_auth(&token).json(&json!({"agent_card":raw_card(),"catalog_labels":{}})).send().await.unwrap();
        assert!(response.status().is_success());assert!(payload(&mock,from,id,None).await["labels"].get("stage").is_none());
        let response=http.put(&endpoint).bearer_auth(&token).json(&json!({"agent_card":raw_card(),"catalog_labels":{"stage":["running"]}})).send().await.unwrap();
        assert!(response.status().is_success());
        let saved:Value=http.get(&endpoint).bearer_auth(&token).send().await.unwrap().json().await.unwrap();
        assert_eq!(if enveloped {&saved["data"]["catalog_labels"]}else{&saved["catalog_labels"]},&json!({"stage":["running"]}));
    }
    state.lifecycle.lock().await.shutdown_all().await.unwrap();server.abort();catalog_task.abort();
}
