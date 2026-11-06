use super::*;

#[tokio::test]
async fn provider_bot_attributes_allow_an_allowlisted_provider_admin_to_manage_unbound_plugin_bots() {
    let provider_id = "prv_internal_attributes".to_string();
    let admin_token = "admin-token";
    let TestApp {
        app,
        provider_repo,
        provider_credentials,
        internal_bot_attributes,
        _temp_dir,
        ..
    } = test_app_with_allowed_switch_provider_ids(vec![provider_id.clone()]);
    provider_repo
        .insert_provider(ProviderRecord {
            provider_id: provider_id.clone(),
            slug: None,
            name: "Provider".to_string(),
            config: json!({
                "downlink": {
                    "enabled": true,
                    "webhook_url": "https://provider.example.com/bcs/webhook",
                    "auth_mode": "static_bearer",
                    "protocol_version": "1.0"
                }
            })
            .to_string(),
            created_by: "11111111".to_string(),
            owners: r#"["11111111"]"#.to_string(),
            disabled: false,
            created_at: 1,
            updated_at: 1,
        })
        .await
        .expect("seed provider");
    provider_credentials
        .insert_credential(ProviderCredential {
            provider_id: provider_id.clone(),
            credential_kind: "provider_admin".to_string(),
            secret_value: admin_token.to_string(),
            disabled: false,
            created_at: 1,
            updated_at: 1,
        })
        .await
        .expect("seed provider admin credential");

    let register = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/providers/{provider_id}/bots"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(
                    json!({
                        "name": "Teamclaw Bot",
                        "owners": ["alice"],
                        "provider_bot_ref": "teamclaw-bot:alice",
                        "connection_mode": "plugin"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(register.status(), StatusCode::OK);

    let get = app
        .clone()
        .oneshot(
            Request::builder()
                .method("GET")
                .uri(format!(
                    "/providers/{provider_id}/bots/teamclaw-bot:alice/attributes"
                ))
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(get.status(), StatusCode::OK);
    let get_body = response_json(get).await;
    assert_eq!(get_body["visibility"], "protected");
    assert_eq!(get_body["user_visibility"], "protected");
    assert_eq!(get_body["friend_ext"], json!({}));
    assert_eq!(get_body["friend_check_in_strategy"], "APPROVAL");

    let patch = app
        .clone()
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!(
                    "/providers/{provider_id}/bots/teamclaw-bot:alice/attributes"
                ))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(
                    json!({
                        "visibility": "private",
                        "user_visibility": "public",
                        "friend_ext": {"department_code": "TECH"},
                        "friend_check_in_strategy": "DEPT_FREE"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(patch.status(), StatusCode::OK);
    let patch_body = response_json(patch).await;
    assert_eq!(patch_body["visibility"], "private");
    assert_eq!(patch_body["user_visibility"], "public");
    assert_eq!(patch_body["friend_ext"]["department_code"], "TECH");
    assert_eq!(patch_body["friend_check_in_strategy"], "DEPT_FREE");

    for body in [
        json!({}),
        json!({"user_visibility": "public", "forged": true}),
        json!({"user_visibility": null}),
        json!({"friend_ext": []}),
    ] {
        let invalid = app
            .clone()
            .oneshot(
                Request::builder()
                    .method("PATCH")
                    .uri(format!(
                        "/providers/{provider_id}/bots/teamclaw-bot:alice/attributes"
                    ))
                    .header("content-type", "application/json")
                    .header("authorization", format!("Bearer {admin_token}"))
                    .body(Body::from(body.to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(invalid.status(), StatusCode::BAD_REQUEST);
    }

    let patches = internal_bot_attributes.patches.lock().await;
    assert_eq!(patches.len(), 1);
    assert_eq!(patches[0].bot_id, "teamclaw-bot:alice");
    assert_eq!(patches[0].visibility.as_deref(), Some("private"));
    assert_eq!(patches[0].user_visibility, Some(UserVisibility::Public));
    assert_eq!(
        patches[0].friend_check_in_strategy,
        Some(FriendCheckInStrategy::DeptFree)
    );
}

#[tokio::test]
async fn provider_bot_attributes_reject_a_disabled_provider_for_get_and_patch() {
    let provider_id = "prv_disabled_attributes".to_string();
    let admin_token = "admin-token";
    let TestApp {
        app,
        provider_repo,
        provider_credentials,
        _temp_dir,
        ..
    } = test_app_with_allowed_switch_provider_ids(vec![provider_id.clone()]);
    provider_repo
        .insert_provider(ProviderRecord {
            provider_id: provider_id.clone(),
            slug: None,
            name: "Provider".to_string(),
            config: json!({
                "downlink": {
                    "enabled": true,
                    "webhook_url": "https://provider.example.com/bcs/webhook",
                    "auth_mode": "static_bearer",
                    "protocol_version": "1.0"
                }
            })
            .to_string(),
            created_by: "11111111".to_string(),
            owners: r#"["11111111"]"#.to_string(),
            disabled: false,
            created_at: 1,
            updated_at: 1,
        })
        .await
        .expect("seed provider");
    provider_credentials
        .insert_credential(ProviderCredential {
            provider_id: provider_id.clone(),
            credential_kind: "provider_admin".to_string(),
            secret_value: admin_token.to_string(),
            disabled: false,
            created_at: 1,
            updated_at: 1,
        })
        .await
        .expect("seed provider admin credential");

    let register = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/providers/{provider_id}/bots"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(
                    json!({
                        "name": "Teamclaw Bot",
                        "owners": ["alice"],
                        "provider_bot_ref": "teamclaw-bot:alice"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(register.status(), StatusCode::OK);

    provider_repo
        .update_provider_disabled(&provider_id, true, 2)
        .await
        .expect("disable provider");

    let get = app
        .clone()
        .oneshot(
            Request::builder()
                .method("GET")
                .uri(format!(
                    "/providers/{provider_id}/bots/teamclaw-bot:alice/attributes"
                ))
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(get.status(), StatusCode::FORBIDDEN);

    let patch = app
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!(
                    "/providers/{provider_id}/bots/teamclaw-bot:alice/attributes"
                ))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(json!({ "user_visibility": "public" }).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(patch.status(), StatusCode::FORBIDDEN);
}

#[tokio::test]
async fn provider_bot_attributes_fail_closed_when_provider_is_not_allowlisted() {
    let TestApp { app, _temp_dir, .. } = test_app();
    let provider = register_provider_as(&app, "11111111").await;
    let provider_id = provider["provider_id"].as_str().expect("provider id");
    let admin_token = provider["provider_admin_token"]
        .as_str()
        .expect("provider admin token");
    let registered_bot = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/providers/{provider_id}/bots"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(
                    json!({
                        "name": "Code Reviewer",
                        "owners": ["11111111"],
                        "provider_bot_ref": "reviewer"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    let bot_uuid = response_json(registered_bot).await["bot_uuid"]
        .as_str()
        .expect("bot uuid")
        .to_string();

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri(format!("/providers/{provider_id}/bots/{bot_uuid}/attributes"))
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
}
#[tokio::test]
async fn provider_attributes_sync_agent_visibility_but_not_user_or_approval_metadata() {
    let provider_id = "prv_publication_sync".to_string();
    let admin_token = "admin-token";
    let sync = Arc::new(RecordingVisibilitySyncPort::default());
    let TestApp { app, provider_repo, provider_credentials, .. } =
        test_app_with_allowed_switch_provider_ids_and_visibility_sync(
            vec![provider_id.clone()], sync.clone(),
        );
    seed_allowed_provider(&provider_repo, &provider_credentials, &provider_id, admin_token).await;
    let bot_uuid = "teamclaw-bot:alice";
    register_provider_bot(&app, &provider_id, admin_token, bot_uuid).await;
    sync.wait_for(1).await;

    for (index, visibility) in ["public", "protected", "private"].iter().enumerate() {
        let response = app.clone().oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!("/providers/{provider_id}/bots/{bot_uuid}/attributes"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(json!({"visibility": visibility}).to_string()))
                .unwrap(),
        ).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        sync.wait_for(index + 2).await;
        let requests = sync.requests.lock().await;
        assert_eq!(requests[index + 1].bot_uuid, bot_uuid);
        assert_eq!(requests[index + 1].visibility, *visibility);
    }

    for (body, expected) in [
        (json!({"user_visibility": "private"}), StatusCode::OK),
        (json!({"friend_ext": {"public_user_approval": {"status": "PROCESSING"}}}), StatusCode::OK),
        (json!({"visibility": "public", "unknown_field": true}), StatusCode::BAD_REQUEST),
    ] {
        let response = app.clone().oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!("/providers/{provider_id}/bots/{bot_uuid}/attributes"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(body.to_string()))
                .unwrap(),
        ).await.unwrap();
        assert_eq!(response.status(), expected);
    }
    tokio::task::yield_now().await;
    assert_eq!(sync.requests.lock().await.len(), 4);
}
