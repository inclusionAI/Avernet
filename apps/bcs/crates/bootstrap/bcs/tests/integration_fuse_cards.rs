use std::sync::{Arc, Mutex};
use std::time::Duration;

use axum::{Json, Router, body::Bytes, extract::State, http::{Method, StatusCode, Uri}};
use bcs::{BcsConfig, BcsServer};
use bcs_fuse_client::BcsFuseApiVersion;
use bcs_service_api::application::ActorSearchCommand;
use bcs_service_api::{BotCapabilities, Group, GroupMutableFieldsPatch, PatchBotInternalAttributes, Skill, UserVisibility};
use serde_json::{Value, json};

#[derive(Clone, Default)]
struct Catalog {
    calls: Arc<Mutex<Vec<(Method, String, Value)>>>,
    fail: Arc<std::sync::atomic::AtomicBool>,
    search_response: Arc<Mutex<Option<Value>>>,
}

async fn catalog(State(state): State<Catalog>, method: Method, uri: Uri, body: Bytes) -> (StatusCode, Json<Value>) {
    let body = if body.is_empty() { Value::Null } else { serde_json::from_slice(&body).unwrap() };
    state.calls.lock().unwrap().push((method.clone(), uri.path().into(), body));
    if state.fail.load(std::sync::atomic::Ordering::SeqCst) {
        return (StatusCode::SERVICE_UNAVAILABLE, Json(json!({"detail":"unavailable"})));
    }
    if uri.path() == "/api/v2/search" {
        return (StatusCode::OK, Json(state.search_response.lock().unwrap().clone().unwrap_or_else(|| json!({
            "items":[{"id":"card-agent","name":"Fuse name","summary":"Invoices","score":0.9,"score_source":"reranker","extensions":{}}],
            "status":"ok","reasons":[]
        }))));
    }
    match method {
        Method::PUT => (StatusCode::ACCEPTED, Json(json!({"saved":true,"card_version":1}))),
        Method::DELETE => (StatusCode::ACCEPTED, Json(json!({"deleted":true}))),
        _ => (StatusCode::NOT_FOUND, Json(json!({"detail":"Not Found"}))),
    }
}

async fn wait_call(state: &Catalog, from: usize, method: Method, id: &str) -> Value {
    tokio::time::timeout(Duration::from_secs(10), async {
        loop {
            if let Some((_, _, body)) = state.calls.lock().unwrap().iter().skip(from)
                .find(|(m, path, _)| m == method && path.ends_with(id)).cloned() {
                return body;
            }
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    }).await.expect("catalog request")
}

fn count(state: &Catalog) -> usize { state.calls.lock().unwrap().len() }

#[tokio::test]
async fn v2_wiring_syncs_agents_and_public_groups_and_preserves_search_response() {
    let catalog_state = Catalog::default();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let router = Router::new().fallback(catalog).with_state(catalog_state.clone());
    let mock_task = tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
    let directory = tempfile::tempdir().unwrap();
    let mut config = BcsConfig::default();
    config.bots_base_dir = directory.path().into();
    config.bcsfuse.enabled = true;
    config.bcsfuse.api_version = BcsFuseApiVersion::V2;
    config.bcsfuse.url = url;
    config.bcsfuse.sync_timeout_ms = 500;
    let (_, server_task, state) = BcsServer::new_allowing_private_outbound_for_tests(config)
        .run_on_random_port_with_state().await.unwrap();
    let registry = &state.services.registry;
    registry.register("card-agent".into(), BotCapabilities {
        name: Some("Billing".into()), summary: Some("Old summary".into()),
        visibility: "protected".into(), skills: vec![Skill::new("invoices")],
        agent_token: Some("test-token".into()), ..Default::default()
    }).await.unwrap();
    let card = wait_call(&catalog_state, 0, Method::PUT, "card-agent").await;
    assert_eq!(card["resource_type"], "agent");
    assert_eq!(card["labels"], json!({"visibility":["protected"],"user_visibility":["protected"]}));
    assert!(card["card"].get("visibility").is_none());
    assert_eq!(card["card"]["skills"][0]["name"], "invoices");
    assert!(!card.to_string().contains("test-token"));

    let search = state.services.actor_directory.search_actors(ActorSearchCommand {
        query: "billing".into(), current_bot_uuid: "caller".into(),
        limit: 5, cooperatable_only: false,
    }).await;
    let search = serde_json::to_value(search).unwrap();
    assert_eq!(search["bots"][0]["bot_uuid"], "card-agent");
    assert_eq!(search["bots"][0]["capabilities"]["name"], "Billing");
    assert_eq!(search["bots"][0]["score"], 0.9);
    assert_eq!(search["bots"][0]["short_profile"], "Invoices");
    assert!(search["bots"][0]["tags"].is_object());
    assert_eq!(search["context"]["recommend_response"]["recommendations"][0]["worker_id"], "card-agent");
    assert!(search.get("items").is_none());
    let request = wait_call(&catalog_state, 0, Method::POST, "/api/v2/search").await;
    assert_eq!(request["filters"], json!({"resource_type":["agent"]}));
    assert_eq!(request["limit"], 5);

    // User visibility is independent of bot visibility and online state.
    // Organization scope controls friend applications, not catalog discovery.
    for user_visibility in [UserVisibility::Private, UserVisibility::Public, UserVisibility::Protected] {
        let from = count(&catalog_state);
        state.internal_bot_attributes_service.patch(PatchBotInternalAttributes {
            bot_id: "card-agent".into(), user_visibility: Some(user_visibility),
            friend_ext: Some(serde_json::from_value(json!({"view_scope_user_friend_deps":["department"]})).unwrap()),
            ..Default::default()
        }).await.unwrap();
        let card = wait_call(&catalog_state, from, Method::PUT, "card-agent").await;
        assert_eq!(card["labels"], json!({"visibility":["protected"],"user_visibility":[user_visibility]}));
        assert!(!card.to_string().contains("department"));
    }

    // Onboarding publishes through the existing-only profile write.
    let from = count(&catalog_state);
    let result = registry.update_profile_existing("card-agent",
        bcs_service_api::types::bot_profile::ExistingProfileUpdate {
            capabilities: BotCapabilities {
                name: Some("Billing".into()), summary: Some("Updated profile".into()),
                visibility: "protected".into(), ..Default::default()
            },
            avatar: bcs_service_api::types::bot_profile::AvatarChange::Keep,
        },
    ).await.unwrap();
    assert_eq!(result, bcs_service_api::types::bot_profile::ProfileWrite::Updated);
    let card = wait_call(&catalog_state, from, Method::PUT, "card-agent").await;
    assert_eq!(card["card"]["description"], "Updated profile");

    let from = count(&catalog_state);
    registry.update_capabilities("card-agent", BotCapabilities {
        name: Some("Billing".into()), visibility: "protected".into(), ..Default::default()
    }).await.unwrap();
    let card = wait_call(&catalog_state, from, Method::PUT, "card-agent").await;
    assert_eq!(card["card"]["skills"], json!([]));
    assert_eq!(card["card"]["description"], "");

    let groups = &state.services.group;
    let mut group = Group::new("card-group", "card-agent", vec![]);
    group.label = Some("Billing group".into());
    groups.upsert(group).await.unwrap();
    wait_call(&catalog_state, 0, Method::DELETE, "card-group").await;
    assert!(!catalog_state.calls.lock().unwrap().iter().any(|(m, path, _)| *m == Method::PUT && path.ends_with("card-group")));
    let from = count(&catalog_state);
    groups.patch_mutable_fields("card-group", GroupMutableFieldsPatch {
        visibility: Some("public".into()), context: Some("Invoice collaboration".into()),
        ..Default::default()
    }).await.unwrap();
    let card = wait_call(&catalog_state, from, Method::PUT, "card-group").await;
    assert_eq!(card["resource_type"], "group");
    assert_eq!(card["labels"], json!({"visibility":["public"]}));
    assert_eq!(card["card"]["groupId"], "card-group");
    assert_eq!(card["card"]["description"], "Invoice collaboration");
    let from = count(&catalog_state);
    groups.update_visibility("card-group", "private").await.unwrap();
    wait_call(&catalog_state, from, Method::DELETE, "card-group").await;

    // A failed write is retried from current state, including a later delete.
    catalog_state.fail.store(true, std::sync::atomic::Ordering::SeqCst);
    let fallback = state.services.actor_directory.search_actors(ActorSearchCommand {
        query: "Billing".into(), current_bot_uuid: "caller".into(),
        limit: 5, cooperatable_only: false,
    }).await;
    assert_eq!(fallback.bots[0].bot_uuid, "card-agent");
    assert!(fallback.context.recommend_response.is_none());
    let from = count(&catalog_state);
    groups.update_visibility("card-group", "public").await.unwrap();
    wait_call(&catalog_state, from, Method::PUT, "card-group").await;
    let from = count(&catalog_state);
    groups.delete("card-group").await.unwrap();
    catalog_state.fail.store(false, std::sync::atomic::Ordering::SeqCst);
    wait_call(&catalog_state, from, Method::DELETE, "card-group").await;
    assert!(!catalog_state.calls.lock().unwrap().iter().skip(from).any(|(m, path, _)| *m == Method::PUT && path.ends_with("card-group")));

    let from = count(&catalog_state);
    registry.update_visibility("card-agent", "private").await.unwrap();
    let card = wait_call(&catalog_state, from, Method::PUT, "card-agent").await;
    assert_eq!(card["labels"], json!({"visibility":["private"],"user_visibility":["protected"]}));

    let from = count(&catalog_state);
    state.internal_bot_attributes_service.patch(PatchBotInternalAttributes {
        bot_id: "card-agent".into(), user_visibility: Some(UserVisibility::Private),
        ..Default::default()
    }).await.unwrap();
    let card = wait_call(&catalog_state, from, Method::PUT, "card-agent").await;
    assert_eq!(card["labels"], json!({"visibility":["private"],"user_visibility":["private"]}));

    // A private friend can collaborate. A non-friend and the caller itself are
    // filtered locally; the legacy context keeps the original candidates.
    state.services.friend.add_friendship("caller", "card-agent").await.unwrap();
    *catalog_state.search_response.lock().unwrap() = Some(json!({
        "items":[{"id":"card-agent","name":"Billing","summary":"RRF result","score":0.02,"score_source":"rrf","extensions":{}}],
        "status":"degraded","reasons":["reranker_unavailable"]
    }));
    for (caller, expected) in [("caller", 1), ("stranger", 0), ("card-agent", 0)] {
        let from = count(&catalog_state);
        let result = state.services.actor_directory.search_actors(ActorSearchCommand {
            query: "semantic invoice query".into(), current_bot_uuid: caller.into(),
            limit: 5, cooperatable_only: true,
        }).await;
        assert_eq!(result.bots.len(), expected);
        let context = serde_json::to_value(&result.context).unwrap();
        assert_eq!(context["recommend_response"]["recommendations"][0]["worker_id"], "card-agent");
        if expected == 1 { assert_eq!(result.bots[0].score, Some(0.02)); }
        assert_eq!(count(&catalog_state) - from, 1, "no extra retrieval or refill");
    }

    // The configured relevance threshold still applies to reranker scores.
    catalog_state.search_response.lock().unwrap().as_mut().unwrap()["items"][0]["score_source"] = json!("reranker");
    let result = state.services.actor_directory.search_actors(ActorSearchCommand {
        query: "semantic invoice query".into(), current_bot_uuid: "caller".into(),
        limit: 5, cooperatable_only: true,
    }).await;
    assert!(result.bots.is_empty());

    // Atomic trusted registration and retirement also publish after commit.
    use bcs_service_api::core::registry::TrustedBotRegistration;
    use bcs_service_api::types::bot_authority::{AuthorityActor, AuthorityMutation, OwnershipInitialization};
    let owned = registry.register_with_ownership(TrustedBotRegistration {
        capabilities: BotCapabilities { name: Some("Owned agent".into()), visibility: "private".into(), ..Default::default() },
        initialization: OwnershipInitialization {
            owner_user_id: "owner".into(),
            initial_manager_user_ids: Vec::new(),
            mutation: AuthorityMutation { mutation_id: "card-owned-create".into(), actor: AuthorityActor::Human { user_id: "owner".into() } },
        },
    }).await.unwrap();
    let card = wait_call(&catalog_state, 0, Method::PUT, &owned.bot_uuid).await;
    assert_eq!(card["labels"]["visibility"], json!(["private"]));
    let from = count(&catalog_state);
    assert!(registry.retire_with_authority(&owned.bot_uuid, AuthorityMutation {
        mutation_id: "card-owned-retire".into(), actor: AuthorityActor::Human { user_id: "owner".into() },
    }).await.unwrap());
    wait_call(&catalog_state, from, Method::DELETE, &owned.bot_uuid).await;
    let from = count(&catalog_state);
    assert!(registry.soft_delete("card-agent").await);
    wait_call(&catalog_state, from, Method::DELETE, "card-agent").await;
    assert!(catalog_state.calls.lock().unwrap().iter().all(|(_, path, _)| path.starts_with("/api/v2/")));
    state.lifecycle.lock().await.shutdown_all().await.unwrap();
    server_task.abort();
    mock_task.abort();
}

#[tokio::test]
async fn v2_sync_has_bounded_retries_and_no_startup_scan() {
    let catalog_state = Catalog::default();
    catalog_state.fail.store(true, std::sync::atomic::Ordering::SeqCst);
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let router = Router::new().fallback(catalog).with_state(catalog_state.clone());
    let mock_task = tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
    let directory = tempfile::tempdir().unwrap();
    let mut config = BcsConfig::default();
    config.bots_base_dir = directory.path().into();
    config.bcsfuse.enabled = true;
    config.bcsfuse.api_version = BcsFuseApiVersion::V2;
    config.bcsfuse.url = url;
    config.bcsfuse.sync_retry_base_ms = 0;
    config.bcsfuse.sync_timeout_ms = 2_000;
    let (_, server_task, state) = BcsServer::new_allowing_private_outbound_for_tests(config)
        .run_on_random_port_with_state().await.unwrap();
    state.services.registry.register("retry-agent".into(), BotCapabilities {
        name: Some("Retry agent".into()), visibility: "private".into(), ..Default::default()
    }).await.unwrap();
    tokio::time::timeout(Duration::from_secs(20), async {
        while count(&catalog_state) < 10 { tokio::time::sleep(Duration::from_millis(10)).await; }
    }).await.unwrap_or_else(|_| panic!("expected ten attempts, received {}", count(&catalog_state)));
    tokio::time::sleep(Duration::from_millis(100)).await;
    assert_eq!(count(&catalog_state), 10, "stop after ten attempts until a new change");

    catalog_state.fail.store(false, std::sync::atomic::Ordering::SeqCst);
    let from = count(&catalog_state);
    state.internal_bot_attributes_service.patch(PatchBotInternalAttributes {
        bot_id: "retry-agent".into(), user_visibility: Some(UserVisibility::Private),
        ..Default::default()
    }).await.unwrap();
    let card = wait_call(&catalog_state, from, Method::PUT, "retry-agent").await;
    assert_eq!(card["labels"], json!({"visibility":["private"],"user_visibility":["private"]}));

    // An unchanged existing Agent is not republished on lifecycle restart.
    state.lifecycle.lock().await.shutdown_all().await.unwrap();
    let from = count(&catalog_state);
    state.lifecycle.lock().await.initialize_all().await.unwrap();
    tokio::time::sleep(Duration::from_millis(100)).await;
    assert_eq!(count(&catalog_state), from);
    state.lifecycle.lock().await.shutdown_all().await.unwrap();
    server_task.abort();
    mock_task.abort();
}
