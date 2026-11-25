//! Signed V1 HTTP -> identity SQL -> explicit application -> actual A2A send.
//! Public-router integration, not Singlebox/host inventory acceptance.
mod agent_invoke_direct_support;
use agent_invoke_direct_support::*;
use axum::http::StatusCode;
use bcs_message_flow::agent_invoke_ingress::AgentInvokeDirectApplicationService;
use bcs_service_api::application::agent_invoke::*;
use serde_json::json;
use std::sync::atomic::Ordering::SeqCst;

#[tokio::test]
async fn conformance_agent_invoke_direct_service() {
    let f = Fixture::new().await;
    f.grant().await;
    let service: &AgentInvokeDirectApplicationService = f.service.as_ref();
    bcs_test_support::contract::agent_invoke::agent_invoke_direct_service_contract_tests(
        service,
        AgentInvokeDirectCommand {
            identity: AgentInvokeVerifiedBot {
                bot_id: "http-a".into(),
                agent_code: "code".into(),
            },
            authenticated_scope: f.managed.d.scope.clone(),
            to_bot_id: "http-b".into(),
            message: "test".into(),
            session_id: None,
            timeout_ms: None,
        },
    )
    .await;
    assert_eq!(f.support.bot_delivery.frames().await.len(), 1);
}
#[tokio::test]
async fn signed_bot_invocation_uses_exact_uuid_binding_and_reaches_actual_transport() {
    for path in [PATH, MIRROR] {
        let f = Fixture::new().await;
        f.grant().await;
        let (status, data) = f.call(path, Some("bot"), body()).await;
        assert_eq!(status, StatusCode::ACCEPTED, "{data}");
        assert_eq!(data["code"], 20200);
        assert_eq!(data["data"]["bot_uuid"], "http-b");
        assert!(
            data["data"]["run_id"]
                .as_str()
                .is_some_and(|id| uuid::Uuid::parse_str(id).is_ok())
        );
        assert_eq!(f.identity.calls.load(SeqCst), 1);
        let frames = f.support.bot_delivery.frames().await;
        assert_eq!(frames.len(), 1);
        let frame = serde_json::to_value(&frames[0]).unwrap();
        assert_eq!(frame["params"]["session_context"]["originator"], "http-a");
        assert_eq!(
            frame["params"]["message"]["content"][0]["text"],
            " original direct text 世界 "
        );
    }
}
#[tokio::test]
async fn missing_unsigned_app_human_foreign_or_wrong_binding_cannot_start_a_run() {
    for (kind, expected) in [
        (None, 401),
        (Some("unsigned"), 401),
        (Some("app"), 403),
        (Some("user-and-bot"), 403),
        (Some("user"), 404),
        (Some("foreign"), 404),
        (Some("wrong-code"), 403),
        (Some("missing"), 403),
        (Some("other-bot"), 403),
    ] {
        let f = Fixture::new().await;
        f.grant().await;
        let (status, data) = f.call(PATH, kind, body()).await;
        assert_eq!(status.as_u16(), expected, "{kind:?}: {data}");
        f.assert_no_effects().await;
    }
}
#[tokio::test]
async fn self_asserted_source_scope_lineage_and_invalid_payloads_are_rejected_before_resolution() {
    let f = Fixture::new().await;
    f.grant().await;
    for (name, value) in [
        ("from_bot_id", json!("http-c")),
        ("originator", json!("human_target")),
        ("scope", json!("other")),
        ("eligible", json!(true)),
        ("operation", json!("tool_invocation")),
        ("provider_bypass_headers", json!([])),
        ("authentication", json!("trusted_in_process")),
    ] {
        let mut request = body();
        request[name] = value;
        let (status, _) = f.call(PATH, Some("bot"), request).await;
        assert_eq!(status, StatusCode::BAD_REQUEST, "{name}");
    }
    for request in [
        json!({"to_bot_id":"http-b","message":""}),
        json!({"to_bot_id":"http-a","message":"self"}),
        json!({"to_bot_id":"http-b","message":"text","timeout_ms":0}),
        json!({"to_bot_id":"http-b","message":"text","timeout_ms":120001}),
        json!({"to_bot_id":"http-b","message":"text","session_id":"a2a-test:forged"}),
    ] {
        assert_eq!(
            f.call(PATH, Some("bot"), request).await.0,
            StatusCode::BAD_REQUEST
        );
    }
    assert_eq!(f.identity.calls.load(SeqCst), 0);
    f.assert_no_effects().await;
}
#[tokio::test]
async fn public_target_without_grant_or_store_failure_stays_closed() {
    for scenario in ["no-grant", "store-error", "source-ineligible"] {
        let f = Fixture::new().await;
        f.support.registry.set_visibility("http-b", "public").await;
        if scenario != "no-grant" {
            f.grant().await;
        }
        if scenario == "store-error" {
            f.managed
                .d
                .sql("DROP TABLE agent_invoke_facts", vec![])
                .await;
        }
        if scenario == "source-ineligible" {
            f.managed.d.qualify("http-a", false, false).await;
        }
        let (status, _) = f.call(PATH, Some("bot"), body()).await;
        assert_eq!(
            status,
            if scenario == "store-error" {
                StatusCode::SERVICE_UNAVAILABLE
            } else {
                StatusCode::FORBIDDEN
            },
            "{scenario}"
        );
        f.assert_no_effects().await;
    }
}
#[tokio::test]
async fn identity_change_after_resolution_is_rejected_before_channel_or_run_effects() {
    let f = Fixture::new().await;
    f.grant().await;
    f.identity.pause.store(true, SeqCst);
    let invoke = f.call(PATH, Some("bot"), body());
    let change = async {
        tokio::time::timeout(
            std::time::Duration::from_secs(5),
            f.identity.entered.notified(),
        )
        .await
        .unwrap();
        // Real trusted publication changes generation and invalidates old approval.
        f.managed.d.qualify("http-a", true, true).await;
        // A new explicit approval exists; the OLD authenticated handoff must
        // still refuse it, rather than silently rebinding to current epochs.
        let row = f
            .managed
            .d
            .db
            .query(bcs_db_api::DbStatement::new(
                "SELECT version FROM agent_invoke_grants LIMIT 1",
            ))
            .await
            .unwrap();
        let version = bcs_db_api::db_get_column::<u64>(&row[0], "version").unwrap();
        let (status,receipt)=f.managed.request("POST","/grants",Some("target"),Some("regrant-after-binding"),json!({"from_bot_id":"http-a","to_bot_id":"http-b","expected_version":version,"reason":"new binding explicitly reapproved"})).await;
        assert_eq!(status, StatusCode::CREATED, "{receipt}");
        assert_eq!(receipt["data"]["current"]["grant"]["status"], "approved");
        assert!(
            f.managed
                .d
                .service
                .authorize_invocation(AgentInvocationInput {
                    authentication: AgentInvocationAuthentication::TrustedInProcess,
                    principal: AgentInvokePrincipal::Agent("http-a".into()),
                    authenticated_scope: f.managed.d.scope.clone(),
                    pair: AgentInvokePair {
                        from_bot_id: "http-a".into(),
                        to_bot_id: "http-b".into()
                    },
                    originator: "http-a".into(),
                    operation: AgentInvokeOperation::DirectInvocation,
                })
                .await
                .is_ok()
        );
        f.identity.release.notify_one();
    };
    let ((status, data), _) = tokio::join!(invoke, change);
    assert_eq!(status, StatusCode::FORBIDDEN, "{data}");
    f.assert_no_effects().await;
}

#[tokio::test]
async fn strict_http_body_method_query_and_default_disabled_capability_are_closed() {
    use axum::{body::Body, http::Request};
    use tower::ServiceExt;
    let f = Fixture::new().await;
    assert_eq!(
        f.managed
            .request("POST", "/invocations", Some("bot"), None, body())
            .await
            .0,
        StatusCode::SERVICE_UNAVAILABLE
    );
    assert_eq!(
        f.call(&format!("{PATH}?originator=http-c"), Some("bot"), body())
            .await
            .0,
        StatusCode::BAD_REQUEST
    );
    for (method, content, raw, expected) in [
        (
            "GET",
            "application/json",
            "{}".to_string(),
            StatusCode::METHOD_NOT_ALLOWED,
        ),
        (
            "POST",
            "text/plain",
            body().to_string(),
            StatusCode::BAD_REQUEST,
        ),
        (
            "POST",
            "application/json",
            r#"{"to_bot_id":"http-b","message":"x","message":"y"}"#.to_string(),
            StatusCode::BAD_REQUEST,
        ),
        (
            "POST",
            "application/json",
            json!({"to_bot_id":"http-b","message":"x".repeat(16*1024)}).to_string(),
            StatusCode::BAD_REQUEST,
        ),
    ] {
        let request = Request::builder()
            .method(method)
            .uri(PATH)
            .header("content-type", content)
            .header("x-avernet-principal", token("bot"))
            .body(Body::from(raw))
            .unwrap();
        let response = f.app.clone().oneshot(request).await.unwrap();
        assert_eq!(response.status(), expected);
    }
    assert_eq!(f.identity.calls.load(SeqCst), 0);
    f.assert_no_effects().await;
}


#[tokio::test]
async fn server_generated_request_id_is_identical_in_header_and_envelope() {
    use axum::{body::{Body,to_bytes},http::Request};
    use tower::ServiceExt;
    for path in [PATH,MIRROR] {
        let f=Fixture::new().await;f.grant().await;
        for authenticated in [false,true] {
            let mut request=Request::builder().method("POST").uri(path).header("content-type","application/json");
            if authenticated {request=request.header("x-avernet-principal",token("bot"));}
            let response=f.app.clone().oneshot(request.body(Body::from(body().to_string())).unwrap()).await.unwrap();
            assert_eq!(response.status(),if authenticated {StatusCode::ACCEPTED}else{StatusCode::UNAUTHORIZED});
            let id=response.headers()["x-request-id"].to_str().unwrap().to_owned();
            assert!(!id.is_empty());
            let data:serde_json::Value=serde_json::from_slice(&to_bytes(response.into_body(),1024*1024).await.unwrap()).unwrap();
            assert_eq!(data["request_id"],id);
        }
    }
}

#[tokio::test]
async fn shared_agent_code_never_shares_approval_between_distinct_physical_bots() {
    let f=Fixture::new().await;f.grant().await;
    assert_eq!(f.call(PATH,Some("other-bot"),body()).await.0,StatusCode::FORBIDDEN);
    f.assert_no_effects().await;
    let(status,receipt)=f.managed.request("POST","/grants",Some("target"),Some("own-physical-c-grant"),
        json!({"from_bot_id":"http-c","to_bot_id":"http-b","expected_version":0,"reason":"approve C independently of same-code A"})).await;
    assert_eq!(status,StatusCode::CREATED,"{receipt}");
    assert_eq!(f.call(PATH,Some("other-bot"),body()).await.0,StatusCode::ACCEPTED);
    let frames=f.support.bot_delivery.frames().await;assert_eq!(frames.len(),1);
    let frame=serde_json::to_value(&frames[0]).unwrap();assert_eq!(frame["params"]["session_context"]["originator"],"http-c");
}
