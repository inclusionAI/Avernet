use super::*;

#[tokio::test]
async fn get_provider_returns_metadata_without_tokens() {
    let TestApp { app, _temp_dir, .. } = test_app();
    let provider = register_provider(
        &app,
        json!({
            "mode": "static_bearer"
        }),
    )
    .await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();
    let bcs_to_provider_token = provider["bcs_to_provider_token"].as_str().unwrap();

    let response = app
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

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["provider_id"], provider_id);
    assert_eq!(body["name"], "Provider");
    assert_eq!(body["webhook_url"], "https://provider.example.com/bcs/webhook");
    assert_eq!(body["auth_mode"], "static_bearer");
    assert!(body.get("provider_admin_token").is_none());
    assert!(body.get("bcs_to_provider_token").is_none());
    let body_text = body.to_string();
    assert!(!body_text.contains(admin_token));
    assert!(!body_text.contains(bcs_to_provider_token));
}
#[tokio::test]
async fn patch_provider_updates_name_and_webhook_url() {
    let TestApp { app, _temp_dir, .. } = test_app();
    let provider = register_provider(
        &app,
        json!({
            "mode": "static_bearer"
        }),
    )
    .await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!("/providers/{provider_id}"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(
                    json!({
                        "name": "Updated Provider",
                        "webhook_url": "https://provider.example.com/updated/webhook"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["provider_id"], provider_id);
    assert_eq!(body["name"], "Updated Provider");
    assert_eq!(
        body["webhook_url"],
        "https://provider.example.com/updated/webhook"
    );
    assert_eq!(body["auth_mode"], "static_bearer");

    let response = app
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

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["name"], "Updated Provider");
    assert_eq!(
        body["webhook_url"],
        "https://provider.example.com/updated/webhook"
    );
}

#[tokio::test]
async fn patch_provider_updates_and_returns_admin_callback_url() {
    let TestApp { app, _temp_dir, .. } = test_app();
    let provider = register_provider(
        &app,
        json!({
            "mode": "static_bearer"
        }),
    )
    .await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();
    let callback_url = "https://provider.example.com/bcs/admin-callback";

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!("/providers/{provider_id}"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(
                    json!({
                        "admin_callback_url": callback_url
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["admin_callback_url"], callback_url);

    let response = app
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

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["admin_callback_url"], callback_url);
}

#[tokio::test]
async fn organization_admin_run_requires_provider_admin_token_in_unified_envelope() {
    let app = test_app();
    let response = app
        .app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/organizations/engineering/admin-runs")
                .header("content-type", "application/json")
                .body(Body::from(
                    json!({
                        "target_bot_uuid": "bot_target",
                        "message": {
                            "role": "user",
                            "content": [{ "type": "text", "text": "please review" }]
                        }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    let body = response_json(response).await;
    assert_eq!(body["code"], 40101);
    assert_eq!(body["message"], "valid provider admin token is required");
    assert!(body["request_id"].as_str().is_some());
}

#[tokio::test]
async fn patch_and_get_provider_use_typed_organization_management_config() {
    let TestApp { app, _temp_dir, .. } = test_app();
    let provider_a = register_provider_as(&app, "11111111").await;
    let provider_b = register_provider_as(&app, "11111111").await;
    let provider_a_id = provider_a["provider_id"].as_str().unwrap();
    let provider_b_id = provider_b["provider_id"].as_str().unwrap();
    let admin_token_b = provider_b["provider_admin_token"].as_str().unwrap();

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!("/providers/{provider_b_id}"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token_b}"))
                .header("x-test-staff-no", "11111111")
                .body(Body::from(
                    json!({
                        "organization_management": {
                            "authorized_manager_provider_ids": [provider_a_id, provider_a_id]
                        }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(
        body["organization_management"]["authorized_manager_provider_ids"],
        json!([provider_a_id])
    );
    assert!(body.get("config").is_none());

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri(format!("/providers/{provider_b_id}"))
                .header("authorization", format!("Bearer {admin_token_b}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(
        body["organization_management"]["authorized_manager_provider_ids"],
        json!([provider_a_id])
    );
    assert!(body.get("config").is_none());
}

#[tokio::test]
async fn patch_provider_preserves_absent_and_clears_empty_organization_management_config() {
    let TestApp { app, _temp_dir, .. } = test_app();
    let provider_a = register_provider_as(&app, "11111111").await;
    let provider_b = register_provider_as(&app, "11111111").await;
    let provider_a_id = provider_a["provider_id"].as_str().unwrap();
    let provider_b_id = provider_b["provider_id"].as_str().unwrap();
    let admin_token_b = provider_b["provider_admin_token"].as_str().unwrap();

    for body in [
        json!({
            "organization_management": {
                "authorized_manager_provider_ids": [provider_a_id]
            }
        }),
        json!({"name": "Renamed Provider"}),
    ] {
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .method("PATCH")
                    .uri(format!("/providers/{provider_b_id}"))
                    .header("content-type", "application/json")
                    .header("authorization", format!("Bearer {admin_token_b}"))
                    .header("x-test-staff-no", "11111111")
                    .body(Body::from(body.to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body = response_json(response).await;
        assert_eq!(
            body["organization_management"]["authorized_manager_provider_ids"],
            json!([provider_a_id])
        );
    }

    let response = app
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!("/providers/{provider_b_id}"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token_b}"))
                .header("x-test-staff-no", "11111111")
                .body(Body::from(
                    json!({
                        "organization_management": {
                            "authorized_manager_provider_ids": []
                        }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(
        body["organization_management"]["authorized_manager_provider_ids"],
        json!([])
    );
}

#[tokio::test]
async fn patch_provider_requires_owner_identity() {
    let TestApp { app, _temp_dir, .. } =
        test_app_with_user_identity(Arc::new(HeaderUserIdentity));
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/providers")
                .header("content-type", "application/json")
                .header("x-test-staff-no", "11111111")
                .body(Body::from(
                    json!({
                        "name": "Provider",
                        "webhook_url": "https://provider.example.com/bcs/webhook",
                        "auth": {
                            "mode": "static_bearer"
                        }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let provider = response_json(response).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    let response = app
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!("/providers/{provider_id}"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .header("x-test-staff-no", "12345678")
                .body(Body::from(
                    json!({
                        "name": "Updated Provider"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let body = response_json(response).await;
    assert_eq!(body["error"], "provider_owner_required");
}

#[tokio::test]
async fn patch_provider_requires_human_identity() {
    let TestApp { app, _temp_dir, .. } =
        test_app_with_user_identity(Arc::new(HeaderUserIdentity));
    let provider = register_provider_as(&app, "11111111").await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    let response = app
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!("/providers/{provider_id}"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(
                    json!({
                        "name": "Updated Provider"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    let body = response_json(response).await;
    assert_eq!(body["error"], "valid human identity is required");
}

#[tokio::test]
async fn disable_provider_requires_owner_identity() {
    let TestApp { app, _temp_dir, .. } =
        test_app_with_user_identity(Arc::new(HeaderUserIdentity));
    let provider = register_provider_as(&app, "11111111").await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/providers/{provider_id}/disable"))
                .header("authorization", format!("Bearer {admin_token}"))
                .header("x-test-staff-no", "12345678")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let body = response_json(response).await;
    assert_eq!(body["error"], "provider_owner_required");
}

#[tokio::test]
async fn enable_provider_requires_owner_identity() {
    let TestApp { app, _temp_dir, .. } =
        test_app_with_user_identity(Arc::new(HeaderUserIdentity));
    let provider = register_provider_as(&app, "11111111").await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/providers/{provider_id}/disable"))
                .header("authorization", format!("Bearer {admin_token}"))
                .header("x-test-staff-no", "11111111")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/providers/{provider_id}/enable"))
                .header("authorization", format!("Bearer {admin_token}"))
                .header("x-test-staff-no", "12345678")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let body = response_json(response).await;
    assert_eq!(body["error"], "provider_owner_required");
}
#[tokio::test]
async fn provider_admin_token_cannot_manage_another_provider() {
    let TestApp { app, _temp_dir, .. } = test_app();
    let provider_a = register_provider(
        &app,
        json!({
            "mode": "static_bearer"
        }),
    )
    .await;
    let provider_b = register_provider(
        &app,
        json!({
            "mode": "static_bearer"
        }),
    )
    .await;
    let provider_b_id = provider_b["provider_id"].as_str().unwrap();
    let admin_token_a = provider_a["provider_admin_token"].as_str().unwrap();

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/providers/{provider_b_id}/bots"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token_a}"))
                .body(Body::from(
                    json!({
                        "name": "Code Reviewer",
                        "owners": ["11111111"],
                        "provider_bot_ref": "reviewer-v2"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let body = response_json(response).await;
    assert_eq!(body["error"], "provider_id_mismatch");
}
