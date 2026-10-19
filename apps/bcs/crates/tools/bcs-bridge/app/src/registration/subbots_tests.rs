use super::*;

#[test]
fn generated_slug_survives_retry_and_changes_only_after_completion() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("bridge.toml");
    let first = pending::prepare(&path, "same-request", None).unwrap();
    assert_eq!(first, pending::prepare(&path, "same-request", None).unwrap());
    pending::submitted(&path).unwrap();
    assert!(pending::prepare(&path, "different-request", None).is_err());
    pending::complete(&path).unwrap();
    assert_ne!(first, pending::prepare(&path, "same-request", None).unwrap());
}

#[test]
fn only_subbot_configs_can_append() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("bridge.toml");
    std::fs::write(&path, "provider_id='p'\nprovider_auth='agentpass-subbots'\nmode='plugin'\nbot=[]\n").unwrap();
    assert!(config::read_for_registration(&path, "agentpass-subbots", "p", "plugin").is_ok());
    assert!(config::read_for_registration(&path, "agentpass", "p", "plugin").is_err());
    assert!(config::read_for_registration(&path, "agentpass-subbots", "other", "plugin").is_err());
    assert!(config::read_for_registration(&path, "agentpass-subbots", "p", "gateway").is_err());
}

#[test]
fn pre_post_attempt_can_be_replaced_but_submitted_attempt_cannot() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("bridge.toml");
    pending::prepare(&path, "request-a", Some("a")).unwrap();
    assert_eq!(pending::prepare(&path, "request-b", Some("b")).unwrap(), "b");
    pending::submitted(&path).unwrap();
    assert!(pending::prepare(&path, "request-c", Some("c")).is_err());
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

#[test]
fn basename_relative_config_can_persist_pending_registration() {
    let path = PathBuf::from(format!("relative-{}.toml", uuid::Uuid::new_v4()));
    let result = (|| -> anyhow::Result<()> {
        assert_eq!(pending::prepare(&path, "relative-request", Some("reviewer"))?, "reviewer");
        pending::submitted(&path)?;
        assert_eq!(pending::prepare(&path, "relative-request", None)?, "reviewer");
        Ok(())
    })();
    let _ = pending::complete(&path);
    result.unwrap();
}
