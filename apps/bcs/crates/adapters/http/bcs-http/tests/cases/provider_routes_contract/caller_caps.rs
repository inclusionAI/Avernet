use super::*;

// ---------------------------------------------------------------------------
// caller_capabilities on POST/PATCH /providers (§10.7.3 management plane)
// ---------------------------------------------------------------------------

fn caps_subject_routed() -> Value {
    json!({
        "supports_caller": true,
        "supports_isolation_binding": false,
        "supports_observe_inject": false,
        "token_visibility": "runtime_only",
        "container_routing": "caller_subject"
    })
}

async fn register_provider_with_body(app: &Router, body: Value) -> (StatusCode, Value) {
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/providers")
                .header("content-type", "application/json")
                .body(Body::from(body.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = response.status();
    let bytes = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    // json on success; the raw rejection text on 4xx (axum answers JSON
    // rejections with a text/plain body).
    let parsed = serde_json::from_slice(&bytes)
        .unwrap_or_else(|_| json!({ "raw": String::from_utf8_lossy(&bytes) }));
    (status, parsed)
}

async fn get_provider_info(app: &Router, provider_id: &str, admin_token: &str) -> (StatusCode, Value) {
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("GET")
                .uri(format!("/providers/{provider_id}"))
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let status = response.status();
    (status, response_json(response).await)
}

#[tokio::test]
async fn legacy_provider_registration_stays_wire_compatible_without_caller_caps() {
    let TestApp { app, .. } = test_app();
    // Pre-caller registration shape: no caller_capabilities key at all.
    let (status, outcome) = register_provider_with_body(&app, json!({
        "name": "Legacy Provider",
        "webhook_url": "https://provider.example.com/bcs/webhook",
        "auth": { "mode": "static_bearer" }
    })).await;
    assert_eq!(status, StatusCode::OK, "{outcome:?}");
    // The additive readback stays ABSENT for undeclared providers, so old
    // consumers and old snapshots see byte-identical provider info.
    let (_, info) = get_provider_info(&app, outcome["provider_id"].as_str().unwrap(), outcome["provider_admin_token"].as_str().unwrap()).await;
    assert!(info.get("caller_capabilities").is_none(), "undeclared provider must not carry a caller_capabilities field: {info}");
}

#[tokio::test]
async fn register_and_patch_provider_manage_caller_capabilities() {
    let TestApp { app, .. } = test_app();

    // Register WITH a declaration: the subject-routed profile for a
    // provider that has not implemented caller.container.ensure.
    let (status, outcome) = register_provider_with_body(&app, json!({
        "name": "Subject Routed Provider",
        "webhook_url": "https://provider.example.com/bcs/webhook",
        "auth": { "mode": "static_bearer" },
        "caller_capabilities": caps_subject_routed()
    })).await;
    assert_eq!(status, StatusCode::OK, "{outcome:?}");
    let provider_id = outcome["provider_id"].as_str().unwrap().to_string();
    let admin_token = outcome["provider_admin_token"].as_str().unwrap().to_string();

    let (_, info) = get_provider_info(&app, &provider_id, &admin_token).await;
    assert_eq!(info["caller_capabilities"], caps_subject_routed(), "the registered declaration reads back verbatim: {info}");

    // A capability-shaped patch replaces the declaration; old-shape fields
    // keep behaving exactly as before.
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!("/providers/{provider_id}"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(json!({
                    "caller_capabilities": {
                        "supports_caller": true,
                        "supports_isolation_binding": false,
                        "supports_observe_inject": true,
                        "token_visibility": "runtime_only"
                    }
                }).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK, "caps patch accepted");
    let info = response_json(response).await;
    assert_eq!(info["caller_capabilities"]["supports_observe_inject"], true, "{info}");
    assert_eq!(info["caller_capabilities"]["container_routing"], "ensure", "routing defaults wire-back as the legacy ensure path: {info}");

    // Legacy PATCH body without the new key PRESERVES the declaration —
    // old operators/CLI keep working and never wipe state implicitly.
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!("/providers/{provider_id}"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(json!({ "name": "Renamed Provider" }).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let info = response_json(response).await;
    assert_eq!(info["name"], "Renamed Provider");
    assert_eq!(info["caller_capabilities"]["supports_caller"], true, "omitted caps must preserve: {info}");

    // Malformed declarations are rejected up front by the DTO's strict
    // closed shape — no partial or defaulted capability sneaks in.
    let (status, body) = register_provider_with_body(&app, json!({
        "name": "Broken Caps Provider",
        "webhook_url": "https://provider.example.com/bcs/webhook",
        "auth": { "mode": "static_bearer" },
        "caller_capabilities": { "supports_caller": true }
    })).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, "strict shape must reject: {body:?}");
    assert!(
        body["raw"].as_str().unwrap_or_default().contains("supports_isolation_binding"),
        "the rejection names the missing mandatory field: {body:?}"
    );
}
