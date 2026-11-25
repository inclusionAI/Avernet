mod agent_invoke_support;
use agent_invoke_support::*;
use axum::http::StatusCode;
use serde_json::{Value, json};
#[tokio::test]
async fn grant_get_list_degree_revoke_and_replay_use_real_sql_without_reviving_history() {
    let f = Fixture::new().await;
    let receipt = f.grant("http-b", "grant-1").await;
    let id = receipt["current"]["grant"]["edge_id"].as_u64().unwrap();
    for prefix in [PREFIX, MIRROR] {
        for suffix in [
            format!("/grants/{id}?bot_id=http-b&direction=inbound"),
            "/grants?bot_id=http-b&direction=inbound".into(),
            "/degrees?bot_id=http-b".into(),
        ] {
            let (status, data) = call(
                &f.app,
                "GET",
                &format!("{prefix}{suffix}"),
                Some("target"),
                None,
                Value::Null,
            )
            .await;
            assert_eq!(status, StatusCode::OK, "{data}");
        }
    }
    let path = format!("/grants/{id}/revoke?bot_id=http-a&direction=outbound");
    let (status, revoke) = f
        .request(
            "POST",
            &path,
            Some("source"),
            Some("revoke-1"),
            json!({"expected_version":1,"reason":"stop"}),
        )
        .await;
    assert_eq!(status, StatusCode::OK, "{revoke}");
    let replay = f.grant("http-b", "grant-1").await;
    assert_eq!(replay["replayed"], true);
    assert_eq!(replay["original"]["grant"]["status"], "approved");
    assert_eq!(replay["current"]["grant"]["status"], "revoked");
    f.gates_off().await;
    let (status, data) = f
        .request(
            "GET",
            &format!("/grants/{id}?bot_id=http-a&direction=outbound"),
            Some("source"),
            None,
            Value::Null,
        )
        .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(data["data"]["result"]["status"], "revoked");
}
#[tokio::test]
async fn request_get_and_approve_are_independently_authorized_and_versioned() {
    let f = Fixture::new().await;
    let pending = f.pending("request-1").await;
    let id = pending["current"]["request"]["request_id"]
        .as_str()
        .unwrap();
    let (status, data) = f
        .request(
            "GET",
            &format!("/requests/{id}?bot_id=http-b&direction=inbound"),
            Some("target"),
            None,
            Value::Null,
        )
        .await;
    assert_eq!(status, StatusCode::OK, "{data}");
    assert!(data["data"]["result"]["edge_id"].is_null());
    let (status, data) = f
        .request(
            "GET",
            "/requests?bot_id=http-a&direction=outbound",
            Some("manager"),
            None,
            Value::Null,
        )
        .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(data["data"]["result"]["items"].as_array().unwrap().len(), 1);
    let path = format!("/requests/{id}/approve?bot_id=http-b&direction=inbound");
    let (status, _) = f
        .request(
            "POST",
            &path,
            Some("source"),
            Some("forbidden-approve"),
            json!({"expected_version":1,"reason":"not target"}),
        )
        .await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    let (status, data) = f
        .request(
            "POST",
            &path,
            Some("target"),
            Some("approve-1"),
            json!({"expected_version":1,"reason":"approved"}),
        )
        .await;
    assert_eq!(status, StatusCode::OK, "{data}");
    assert_eq!(data["data"]["current"]["request"]["status"], "approved");
    let (status, data) = f
        .request(
            "POST",
            &path,
            Some("target"),
            Some("approve-1"),
            json!({"expected_version":1,"reason":"approved"}),
        )
        .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(data["data"]["replayed"], true);
}
#[tokio::test]
async fn reject_has_no_grant_and_rejects_a_terminal_change() {
    let f = Fixture::new().await;
    let pending = f.pending("reject-request").await;
    let id = pending["current"]["request"]["request_id"]
        .as_str()
        .unwrap();
    let path = format!("/requests/{id}/reject?bot_id=http-b&direction=inbound");
    let (status, data) = f
        .request(
            "POST",
            &path,
            Some("target"),
            Some("reject"),
            json!({"expected_version":1,"reason":"no"}),
        )
        .await;
    assert_eq!(status, StatusCode::OK, "{data}");
    assert_eq!(data["data"]["current"]["request"]["status"], "rejected");
    assert!(data["data"]["current"]["grant"].is_null());
    let (status, _) = f
        .request(
            "POST",
            &path,
            Some("target"),
            Some("new-reject"),
            json!({"expected_version":1,"reason":"new decision"}),
        )
        .await;
    assert_eq!(status, StatusCode::CONFLICT);
}
#[tokio::test]
async fn cancel_requires_original_requester_and_preserves_history() {
    let f = Fixture::new().await;
    let pending = f.pending("cancel-request").await;
    let id = pending["current"]["request"]["request_id"]
        .as_str()
        .unwrap();
    let path = format!("/requests/{id}/cancel?bot_id=http-a&direction=outbound");
    let (status, _) = f
        .request(
            "POST",
            &path,
            Some("manager"),
            Some("other-cancel"),
            json!({"expected_version":1,"reason":"not original"}),
        )
        .await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    let (status, data) = f
        .request(
            "POST",
            &path,
            Some("source"),
            Some("cancel"),
            json!({"expected_version":1,"reason":"cancelled"}),
        )
        .await;
    assert_eq!(status, StatusCode::OK, "{data}");
    assert_eq!(data["data"]["current"]["request"]["status"], "cancelled");
    let (status, data) = f
        .request(
            "GET",
            &format!("/requests/{id}?bot_id=http-b&direction=inbound"),
            Some("target"),
            None,
            Value::Null,
        )
        .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(data["data"]["result"]["status"], "cancelled");
}
#[tokio::test]
async fn cursor_is_bound_to_identity_scope_filters_and_direction_and_audit_is_pair_only() {
    let f = Fixture::new().await;
    f.grant("http-b", "page-b").await;
    f.grant("http-c", "page-c").await;
    let q = "/grants?bot_id=http-a&direction=outbound&limit=1";
    let (_, one) = f.request("GET", q, Some("source"), None, Value::Null).await;
    let cursor = one["data"]["result"]["next_cursor"].as_str().unwrap();
    let encoded = urlencoding::encode(cursor);
    let (status, two) = f
        .request(
            "GET",
            &format!("{q}&cursor={encoded}"),
            Some("source"),
            None,
            Value::Null,
        )
        .await;
    assert_eq!(status, StatusCode::OK, "{two}");
    assert_ne!(
        one["data"]["result"]["items"][0]["edge_id"],
        two["data"]["result"]["items"][0]["edge_id"]
    );
    for query in [
        "/grants?bot_id=http-a&direction=inbound",
        "/grants?bot_id=http-a&direction=outbound&status=revoked",
        "/requests?bot_id=http-a&direction=outbound",
    ] {
        let (status, _) = f
            .request(
                "GET",
                &format!("{query}&cursor={encoded}"),
                Some("source"),
                None,
                Value::Null,
            )
            .await;
        assert_eq!(status, StatusCode::BAD_REQUEST);
    }
    let (status, _) = f
        .request(
            "GET",
            &format!("{q}&cursor={encoded}"),
            Some("manager"),
            None,
            Value::Null,
        )
        .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    let (status, _) = f
        .request(
            "GET",
            "/audit?bot_id=http-a&direction=outbound",
            Some("source"),
            None,
            Value::Null,
        )
        .await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    let (status, data) = f
        .request(
            "GET",
            "/audit?bot_id=http-a&peer_bot_id=http-b&direction=outbound&action=grant",
            Some("source"),
            None,
            Value::Null,
        )
        .await;
    assert_eq!(status, StatusCode::OK, "{data}");
    assert!(!data.to_string().contains("before_json"));
    assert!(!data.to_string().contains("after_json"));
}
#[tokio::test]
async fn governance_mapping_is_not_implicit_app_or_agent_authority() {
    let f = Fixture::new().await;
    f.grant("http-b", "governance-grant").await;
    let q = "/grants?bot_id=http-a&peer_bot_id=http-b&direction=outbound";
    for actor in ["governor", "unmapped-app", "bot"] {
        let (status, _) = f.request("GET", q, Some(actor), None, Value::Null).await;
        assert_eq!(status, StatusCode::FORBIDDEN);
    }
    f.delegate_query().await;
    let (status, data) = f
        .request("GET", q, Some("governor"), None, Value::Null)
        .await;
    assert_eq!(status, StatusCode::OK, "{data}");
    let (status, _) = f
        .request(
            "GET",
            "/degrees?bot_id=http-a&peer_bot_id=http-b",
            Some("governor"),
            None,
            Value::Null,
        )
        .await;
    assert_eq!(
        status,
        StatusCode::FORBIDDEN,
        "degree reveals both directions"
    );
    let (status, _) = f
        .request("GET", q, Some("foreign"), None, Value::Null)
        .await;
    assert_eq!(status, StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn governance_decision_requires_action_not_unrelated_query_delegation() {
    use bcs_service_api::{
        port::repo::agent_invoke::AgentInvokeGovernanceRepoPort, types::agent_invoke::*,
    };
    let f = Fixture::new().await;
    let pending = f.pending("governance-pending").await;
    let id = pending["current"]["request"]["request_id"]
        .as_str()
        .unwrap();
    f.d.store
        .publish_delegation(
            &f.d.scope,
            AgentInvokeDelegationPublication {
                principal: AgentInvokePrincipal::Governance("http-governor".into()),
                pair: AgentInvokePair {
                    from_bot_id: "http-a".into(),
                    to_bot_id: "http-b".into(),
                },
                expected_revision: 0,
                actions: vec![AgentInvokeAction::Approve],
                reason: "approve only".into(),
            },
        )
        .await
        .unwrap();
    let before = f
        .read_faults
        .calls
        .load(std::sync::atomic::Ordering::SeqCst);
    let path = format!("/requests/{id}/approve?bot_id=http-b&peer_bot_id=http-a&direction=inbound");
    let (status, data) = f
        .request(
            "POST",
            &path,
            Some("governor"),
            Some("governance-approve"),
            json!({"expected_version":1,"reason":"delegated approve"}),
        )
        .await;
    assert_eq!(status, StatusCode::OK, "{data}");
    assert_eq!(
        f.read_faults
            .calls
            .load(std::sync::atomic::Ordering::SeqCst),
        before,
        "write action must not require unrelated Query delegation"
    );
    let (status, _) = f
        .request(
            "GET",
            &format!("/requests/{id}?bot_id=http-b&peer_bot_id=http-a&direction=inbound"),
            Some("governor"),
            None,
            Value::Null,
        )
        .await;
    assert_eq!(status, StatusCode::FORBIDDEN);
}
