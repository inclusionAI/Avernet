use super::*;


#[test]
fn subbots_append_within_their_group_and_other_providers_create_new_groups() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("bridge.toml");
    std::fs::write(&path, "provider_id='p'\nprovider_auth='agentpass-subbots'\nmode='plugin'\nbot=[]\n").unwrap();
    assert!(config::read_for_registration(&path, "agentpass-subbots", "p", "plugin").is_ok());
    assert!(config::read_for_registration(&path, "agentpass", "p", "plugin").is_err());
    assert!(config::read_for_registration(&path, "agentpass-subbots", "other", "plugin").is_ok());
    assert!(config::read_for_registration(&path, "agentpass-subbots", "p", "gateway").is_err());
}


#[tokio::test]
async fn registration_sends_complete_ref_on_both_http_routes() {
    use axum::{extract::Query, routing::post, Json, Router};
    use std::{collections::HashMap, sync::{Arc, Mutex}};
    for legacy in [false, true] {
        let received = Arc::new(Mutex::new(Vec::new()));
        let captured = received.clone();
        let route = if legacy { "/register" } else { "/openapi/v1/collaboration/register" };
        let router = Router::new().route(route, post(move |Query(query): Query<HashMap<String, String>>| {
            let captured = captured.clone();
            async move {
                captured.lock().unwrap().push(query);
                let data = serde_json::json!({"bot_uuid":"bot_a","bot_token":"test-token",
                    "registration":{"provider_id":"provider","mode":"plugin","auth_mode":"agentpass-subbots",
                    "agent_code":"poolab:region:P","bot_slug":"reviewer","provider_bot_ref":"poolab:region:P:reviewer"}});
                Json(if legacy { data } else { serde_json::json!({"code":20100,"data":data}) })
            }
        }));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move { axum::serve(listener, router).await.unwrap(); });
        let client = RegistrationClient::new(&format!("http://{address}")).unwrap();
        let client = if legacy { client.with_legacy_registration() } else { client };
        let result = client.register("registration-token", "Reviewer", "poolab:region:P", "plugin", None, "provider", Some("reviewer")).await;
        server.abort();
        result.unwrap();
        let requests = received.lock().unwrap();
        assert_eq!(requests.len(), 1);
        assert_eq!(requests[0].get("provider_bot_ref").map(String::as_str), Some("poolab:region:P:reviewer"));
        assert!(!requests[0].contains_key("bot_slug"));
        assert!(!requests[0].contains_key("expected_auth_mode"));
    }
}


#[test]
fn complete_ref_validation_preserves_ordinary_refs_and_checks_boundaries() {
    assert_eq!(client::composed_ref("parent:Opaque", None).unwrap(), "parent:Opaque");
    assert_eq!(client::composed_ref("poolab:region:P", Some("reviewer")).unwrap(), "poolab:region:P:reviewer");
    assert_eq!(client::composed_ref(&"p".repeat(95), Some(&"a".repeat(32))).unwrap().len(), 128);
    assert!(client::composed_ref(&"p".repeat(96), Some(&"a".repeat(32))).is_err());
    for slug in ["", "Uppercase", "has:colon", "-a", "a "] {
        assert!(client::composed_ref("parent", Some(slug)).is_err());
    }
    assert!(client::composed_ref("bad/parent", Some("a")).is_err());
}


#[tokio::test]
async fn only_structured_registration_conflicts_are_retryable_on_either_route() {
    use axum::{http::StatusCode, routing::post, Router};
    for legacy in [false, true] {
        for confirmed in [false, true] {
            let response = if legacy {
                serde_json::json!({"error": if confirmed { "registration_conflict" } else { "unrelated_conflict" }})
            } else {
                serde_json::json!({"code":40900,"data":{"error_code": if confirmed { "registration_conflict" } else { "unrelated_conflict" }}})
            };
            let route = if legacy { "/register" } else { "/openapi/v1/collaboration/register" };
            let router = Router::new().route(route, post(move || {
                let response = response.clone();
                async move { (StatusCode::CONFLICT, axum::Json(response)) }
            }));
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let address = listener.local_addr().unwrap();
            let server = tokio::spawn(async move { axum::serve(listener, router).await.unwrap(); });
            let client = RegistrationClient::new(&format!("http://{address}")).unwrap();
            let client = if legacy { client.with_legacy_registration() } else { client };
            for slug in [Some("abc12"), None] {
                let error = client.register("test-token", "Test Bot", "parent", "plugin", None, "provider", slug).await.err().unwrap();
                assert_eq!(error.is::<client::RegistrationConflict>(), confirmed && slug.is_some());
                assert!(error.to_string().contains("HTTP 409"));
            }
            server.abort();
        }
    }
}
