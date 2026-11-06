use super::*;

#[derive(Default)]
struct RecordingUserDirectoryPlugin {
    nick_name: String,
    lookups: tokio::sync::Mutex<Vec<String>>,
}

#[async_trait::async_trait]
impl UserDirectoryPlugin for RecordingUserDirectoryPlugin {
    async fn lookup_by_staff_no(
        &self,
        staff_no: &str,
    ) -> Result<Option<UserDirectoryProfile>, bcs_user_directory_api::UserDirectoryError> {
        self.lookups.lock().await.push(staff_no.to_string());
        Ok(Some(UserDirectoryProfile {
            staff_no: staff_no.to_string(),
            nick_name: Some(self.nick_name.clone()),
        }))
    }

    async fn lookup_department_by_staff_no(
        &self,
        _staff_no: &str,
    ) -> Result<Option<String>, bcs_user_directory_api::UserDirectoryError> {
        Ok(None)
    }
}
#[tokio::test]
async fn register_provider_requires_human_identity() {
    let TestApp {
        app, _temp_dir, ..
    } = test_app_with_user_identity(Arc::new(NoUserIdentity));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/providers")
                .header("content-type", "application/json")
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

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    let body = response_json(response).await;
    assert_eq!(body["error"], "valid human identity is required");
}

#[tokio::test]
async fn register_provider_sets_created_by_and_owners_from_human_identity() {
    let TestApp {
        app,
        provider_repo,
        _temp_dir,
        ..
    } = test_app_with_user_identity(Arc::new(ChainUserIdentityPort::new(
        static_auth_chain("11111111", "Admin"),
    )));

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/providers")
                .header("content-type", "application/json")
                .body(Body::from(
                    json!({
                        "name": "Provider",
                        "webhook_url": "https://provider.example.com/bcs/webhook",
                        "auth": {
                            "mode": "static_bearer"
                        },
                        "owners": ["mallory"]
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

    let stored = provider_repo
        .get_provider(provider_id)
        .await
        .expect("get provider")
        .expect("provider exists");

    assert_eq!(stored.created_by, "11111111");
    assert_eq!(stored.owners, r#"["11111111"]"#);
}

#[tokio::test]
async fn register_provider_ignores_client_supplied_provider_id() {
    let TestApp { app, _temp_dir, .. } = test_app();

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/providers")
                .header("content-type", "application/json")
                .body(Body::from(
                    json!({
                        "provider_id": "client-supplied-provider",
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
    let body = response_json(response).await;
    let provider_id = body["provider_id"].as_str().unwrap();
    assert_ne!(provider_id, "client-supplied-provider");
    assert!(provider_id.starts_with("prv_"));
}

#[tokio::test]
async fn register_provider_rejects_private_webhook_url() {
    let TestApp { app, _temp_dir, .. } = test_app();

    for webhook_url in [
        "http://127.0.0.1:8080/webhook",
        "http://169.254.169.254/latest/meta-data/",
    ] {
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/providers")
                    .header("content-type", "application/json")
                    .body(Body::from(
                        json!({
                            "name": "Provider",
                            "webhook_url": webhook_url,
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

        assert_eq!(response.status(), StatusCode::BAD_REQUEST, "{webhook_url}");
        let body = response_json(response).await;
        assert!(
            body["error"].as_str().unwrap_or_default().contains("webhook_url is not allowed"),
            "{body}"
        );
    }
}

#[tokio::test]
async fn register_provider_persists_protocol_version() {
    let TestApp {
        app,
        provider_repo,
        _temp_dir,
        ..
    } = test_app();

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/providers")
                .header("content-type", "application/json")
                .body(Body::from(
                    json!({
                        "name": "Provider",
                        "webhook_url": "https://provider.example.com/bcs/webhook",
                        "protocol_version": "2.0",
                        "auth": {
                            "mode": "provider_admin"
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
    let provider_id = body["provider_id"].as_str().unwrap();
    let stored = provider_repo
        .get_provider(provider_id)
        .await
        .expect("get provider")
        .expect("provider exists");
    let config: Value = serde_json::from_str(&stored.config).unwrap();
    assert_eq!(config["downlink"]["protocol_version"], "2.0");
}

#[tokio::test]
async fn register_static_bearer_provider_bot_returns_runtime_token() {
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

    let bot = register_provider_bot(&app, provider_id, admin_token, "reviewer-v2").await;

    assert_eq!(bot["provider_id"], provider_id);
    assert_eq!(bot["provider_bot_ref"], "reviewer-v2");
    assert!(bot["bot_runtime_token"]
        .as_str()
        .is_some_and(|token| uuid::Uuid::parse_str(token).is_ok()));
}

#[tokio::test]
async fn register_provider_bot_is_idempotent_for_existing_provider_ref() {
    let TestApp {
        app,
        registry,
        _temp_dir,
        ..
    } = test_app();
    let provider = register_provider(
        &app,
        json!({
            "mode": "static_bearer"
        }),
    )
    .await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    let first = register_provider_bot(&app, provider_id, admin_token, "reviewer-v2").await;
    let first_bot_uuid = first["bot_uuid"].as_str().unwrap().to_string();
    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/providers/{provider_id}/bots"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(
                    json!({
                        "name": "Updated Reviewer",
                        "summary": "Should not update existing bot",
                        "owners": ["197262"],
                        "provider_bot_ref": "reviewer-v2"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["bot_uuid"], first_bot_uuid);
    assert_eq!(body["provider_id"], provider_id);
    assert_eq!(body["provider_bot_ref"], "reviewer-v2");
    assert_eq!(body["message"], "provider bot ref already registered; returning existing bot");
    assert!(body["bot_runtime_token"].is_null());
    let bot = registry
        .get(&first_bot_uuid)
        .await
        .expect("existing bot should still be registered");
    assert_eq!(bot.capabilities.name.as_deref(), Some("Code Reviewer"));
}

#[tokio::test]
async fn register_provider_bot_ensures_human_actor_and_owner_edges() {
    let user_directory = Arc::new(RecordingUserDirectoryPlugin {
        nick_name: "Alice Hua".to_string(),
        ..Default::default()
    });
    let TestApp {
        app,
        registry,
        relation,
        _temp_dir,
        ..
    } = test_app_with_user_identity_and_user_directory(
        Arc::new(HeaderUserIdentity),
        Some(user_directory.clone()),
    );
    let provider = register_provider_as(&app, "11111111").await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/providers/{provider_id}/bots"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(
                    json!({
                        "name": "Code Reviewer",
                        "summary": "Reviews code",
                        "owners": ["11111111"],
                        "provider_bot_ref": "reviewer-v2"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    let bot_uuid = body["bot_uuid"].as_str().unwrap();
    let bot = registry.get(bot_uuid).await.expect("bot should be registered");
    assert_eq!(bot.created_by.as_deref(), Some("11111111"));
    let human = registry
        .get("human_11111111")
        .await
        .expect("human actor should be ensured");
    assert_eq!(human.actor_kind, ActorKind::Human);
    assert_eq!(human.capabilities.name.as_deref(), Some("Alice Hua"));
    assert_eq!(user_directory.lookups.lock().await.as_slice(), ["11111111"]);
    let owner_edges = relation.owner_edges.lock().await;
    assert!(
        owner_edges.iter().any(|(human_id, edge_bot_id, _)| {
            human_id == "human_11111111" && edge_bot_id == bot_uuid
        }),
        "expected owner edge for human_11111111 -> {bot_uuid}, got {owner_edges:?}",
    );
}

#[tokio::test]
async fn register_provider_bot_persists_skills_domains_scopes() {
    let TestApp {
        app,
        registry,
        _temp_dir,
        ..
    } = test_app();
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
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/providers/{provider_id}/bots"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(
                    json!({
                        "name": "Code Reviewer",
                        "summary": "Reviews code",
                        "owners": ["12345678"],
                        "provider_bot_ref": "reviewer-v2",
                        "domains": ["development", "security"],
                        "skills": [
                            "code_review",
                            {"name": "sql_analysis", "description": "Analyzes SQL"}
                        ],
                        "scopes": ["production"]
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    let bot_uuid = body["bot_uuid"].as_str().unwrap();
    let bot = registry.get(bot_uuid).await.expect("bot should be registered");
    assert_eq!(bot.capabilities.domains, vec!["development", "security"]);
    assert_eq!(bot.capabilities.scopes, vec!["production"]);
    let skills: Vec<(&str, Option<&str>)> = bot
        .capabilities
        .skills
        .iter()
        .map(|skill| (skill.name.as_str(), skill.description.as_deref()))
        .collect();
    assert_eq!(
        skills,
        vec![
            ("code_review", None),
            ("sql_analysis", Some("Analyzes SQL")),
        ]
    );
}

#[tokio::test]
async fn register_agentpass_provider_bot_omits_runtime_token() {
    let TestApp { app, _temp_dir, .. } = test_app();
    let provider = register_provider(
        &app,
        json!({
            "mode": "agentpass"
        }),
    )
    .await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    let bot = register_provider_bot(&app, provider_id, admin_token, "reviewer-v2").await;

    assert_eq!(bot["provider_id"], provider_id);
    assert!(bot.get("bot_runtime_token").is_none());
}
#[tokio::test]
async fn register_provider_returns_coordination_metadata() {
    let TestApp { app, _temp_dir, .. } = test_app();

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/providers")
                .header("content-type", "application/json")
                .body(Body::from(
                    json!({
                        "name": "Provider",
                        "webhook_url": "https://provider.example.com/bcs/webhook",
                        "auth": {
                            "mode": "static_bearer"
                        },
                        "coordination": {
                            "mode": "mcporter_mcp",
                            "mcp_server": "bcs",
                            "mcporter_command": "mcporter"
                        }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let registered = response_json(response).await;
    let provider_id = registered["provider_id"].as_str().unwrap();
    let admin_token = registered["provider_admin_token"].as_str().unwrap();

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
    assert_eq!(body["coordination"]["mode"], "mcporter_mcp");
    assert_eq!(body["coordination"]["mcp_server"], "bcs");
    assert_eq!(body["coordination"]["mcporter_command"], "mcporter");
    assert!(body["coordination"]
        .get("worker_send_task_message_enabled")
        .is_none());
}

#[tokio::test]
async fn register_provider_round_trips_native_mcp_tool_name_mapping() {
    let TestApp { app, _temp_dir, .. } = test_app();
    let assign_tool = "mcp_mcp.ant.agentclawscs.bcs_mcp_bcs_assign_task";
    let send_message_tool = "mcp_mcp.ant.agentclawscs.bcs_mcp_bcs_send_task_message";

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/providers")
                .header("content-type", "application/json")
                .body(Body::from(
                    json!({
                        "name": "Provider",
                        "webhook_url": "https://provider.example.com/bcs/webhook",
                        "auth": {
                            "mode": "static_bearer"
                        },
                        "coordination": {
                            "mode": "native_mcp",
                            "worker_send_task_message_enabled": false,
                            "mcp_server": "mcp.ant.agentclawscs.bcs",
                            "tool_name_mapping": {
                                (assign_tool): "bcs_assign_task",
                                (send_message_tool): "bcs_send_task_message"
                            }
                        }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let registered = response_json(response).await;
    let provider_id = registered["provider_id"].as_str().unwrap();
    let admin_token = registered["provider_admin_token"].as_str().unwrap();

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
    assert_eq!(body["coordination"]["mode"], "native_mcp");
    assert_eq!(
        body["coordination"]["tool_name_mapping"][assign_tool],
        "bcs_assign_task"
    );
    assert_eq!(
        body["coordination"]["tool_name_mapping"][send_message_tool],
        "bcs_send_task_message"
    );
    assert_eq!(
        body["coordination"]["worker_send_task_message_enabled"],
        false
    );
}
#[tokio::test]
async fn register_provider_bot_accepts_admin_token_without_human_identity() {
    let TestApp { app, _temp_dir, .. } =
        test_app_with_user_identity(Arc::new(HeaderUserIdentity));
    let provider = register_provider_as(&app, "11111111").await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/providers/{provider_id}/bots"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(
                    json!({
                        "name": "Code Reviewer",
                        "summary": "Reviews code",
                        "owners": ["11111111"],
                        "provider_bot_ref": "reviewer-v2"
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
    assert_eq!(body["provider_bot_ref"], "reviewer-v2");
}

#[tokio::test]
async fn register_provider_bot_reuses_provider_ref_as_bot_uuid_for_allowed_switch_provider() {
    let provider_id = "prv_allowed_switch".to_string();
    let admin_token = "admin-token";
    let TestApp {
        app,
        provider_repo,
        provider_credentials,
        registry,
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

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/providers/{provider_id}/bots"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {}", admin_token))
                .body(Body::from(
                    json!({
                        "name": "Teamclaw Bot",
                        "summary": "Handles Teamclaw tasks",
                        "owners": ["alice"],
                        "provider_bot_ref": "teamclaw-bot:alice"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["bot_uuid"], "teamclaw-bot:alice");
    assert_eq!(body["provider_bot_ref"], "teamclaw-bot:alice");
    let bot = registry
        .get("teamclaw-bot:alice")
        .await
        .expect("bot should use provider ref as uuid");
    assert_eq!(bot.created_by.as_deref(), Some("alice"));
}
#[tokio::test]
async fn register_provider_bot_rejects_allowed_switch_provider_ref_that_is_existing_bot_uuid() {
    let provider_id = "prv_allowed_switch".to_string();
    let admin_token = "admin-token";
    let TestApp {
        app,
        provider_repo,
        provider_credentials,
        registry,
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
    registry
        .register_with_owner_and_token(
            "teamclaw-bot:alice".to_string(),
            bcs_service_api::BotCapabilities {
                name: Some("Teamclaw Bot".to_string()),
                summary: Some("Already registered".to_string()),
                ..Default::default()
            },
            "alice",
            "existing-token",
        )
        .await
        .expect("seed existing bot row");

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/providers/{provider_id}/bots"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {}", admin_token))
                .body(Body::from(
                    json!({
                        "name": "Teamclaw Bot",
                        "summary": "Handles Teamclaw tasks",
                        "owners": ["alice"],
                        "provider_bot_ref": "teamclaw-bot:alice"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::CONFLICT);
    let body = response_json(response).await;
    assert!(body["error"]
        .as_str()
        .unwrap()
        .contains("already registered"));
}
#[tokio::test]
async fn register_provider_bot_plugin_mode_rejected_for_non_allow_listed_provider() {
    // The default test_app is NOT allow-listed, so plugin mode must be
    // refused with 400 before reaching the service layer.
    let app = test_app().app;
    let provider = register_provider(&app, json!({ "mode": "static_bearer" })).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    let (status, body) =
        register_provider_bot_mode(&app, provider_id, admin_token, "plugin-bot-1", "plugin").await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    let msg = body["error"].as_str().unwrap_or_default();
    assert!(
        msg.contains("connection_mode plugin requires an allow-listed provider"),
        "unexpected rejection message: {msg}"
    );
}

#[tokio::test]
async fn register_provider_bot_plugin_mode_accepted_for_allow_listed_provider() {
    let provider_id = "prv_plugin_allowed".to_string();
    let admin_token = "admin-token";
    let TestApp {
        app,
        provider_repo,
        provider_credentials,
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
        .expect("seed plugin provider record");
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
        .expect("seed plugin provider admin credential");

    let bot_ref = "plugin-bot:alice";
    let (status, body) =
        register_provider_bot_mode(&app, &provider_id, admin_token, bot_ref, "plugin").await;
    assert_eq!(
        status,
        StatusCode::OK,
        "plugin mode should be accepted for allow-listed provider: {body}"
    );
    // deterministic id == provider_bot_ref (allow-listed + plugin)
    assert_eq!(body["bot_uuid"].as_str(), Some(bot_ref));
    // plugin mode never writes a binding → response carries no runtime token
    // (the real token comes from the WS handshake); the MOCK sentinel is not
    // exposed.
    assert!(
        body["bot_runtime_token"].is_null(),
        "plugin mode response must not expose a runtime token, got: {body}"
    );
}
#[tokio::test]
async fn register_provider_bot_dispatches_visibility_sync_for_allowlisted_provider() {
    let provider_id = "prv_visibility_sync_allowed".to_string();
    let admin_token = "admin-token";
    let sync = Arc::new(RecordingVisibilitySyncPort::default());
    let TestApp {
        app,
        provider_repo,
        provider_credentials,
        visibility_sync,
        ..
    } = test_app_with_allowed_switch_provider_ids_and_visibility_sync(
        vec![provider_id.clone()],
        sync.clone(),
    );
    seed_allowed_provider(&provider_repo, &provider_credentials, &provider_id, &admin_token).await;

    let bot = register_provider_bot(&app, &provider_id, &admin_token, "teamclaw-bot:alice").await;
    let bot_uuid = bot["bot_uuid"].as_str().unwrap().to_string();
    let sync = visibility_sync.expect("visibility sync port wired");
    sync.wait_for(1).await;
    let requests = sync.requests.lock().await;
    assert_eq!(requests.len(), 1, "expected exactly one visibility sync dispatch");
    assert_eq!(requests[0].bot_uuid, bot_uuid);
    assert_eq!(requests[0].actor_kind, ActorKind::Bot);
    assert_eq!(requests[0].capabilities.visibility, "protected");
    assert_eq!(requests[0].visibility, "protected");
}

#[tokio::test]
async fn register_provider_bot_skips_visibility_sync_for_non_allowlisted_provider() {
    let sync = Arc::new(RecordingVisibilitySyncPort::default());
    let TestApp {
        app,
        visibility_sync,
        ..
    } = test_app_with_allowed_switch_provider_ids_and_visibility_sync(Vec::new(), sync);
    let provider = register_provider(&app, json!({ "mode": "static_bearer" })).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    let _ = register_provider_bot(&app, provider_id, admin_token, "reviewer-v2").await;
    // No notify is emitted for 0 requests, so poll briefly instead of wait_for.
    tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    let sync = visibility_sync.expect("visibility sync port wired");
    let requests = sync.requests.lock().await;
    assert!(
        requests.is_empty(),
        "non-allowlisted provider must not dispatch visibility sync, got {requests:?}"
    );
}

#[tokio::test]
async fn register_provider_bot_skips_visibility_sync_for_idempotent_gateway_replay() {
    let provider_id = "prv_visibility_sync_replay".to_string();
    let admin_token = "admin-token";
    let sync = Arc::new(RecordingVisibilitySyncPort::default());
    let TestApp {
        app,
        provider_repo,
        provider_credentials,
        visibility_sync,
        ..
    } = test_app_with_allowed_switch_provider_ids_and_visibility_sync(
        vec![provider_id.clone()],
        sync,
    );
    seed_allowed_provider(&provider_repo, &provider_credentials, &provider_id, &admin_token).await;

    let first = register_provider_bot(&app, &provider_id, &admin_token, "teamclaw-bot:alice").await;
    let first_bot_uuid = first["bot_uuid"].as_str().unwrap().to_string();
    let sync = visibility_sync.expect("visibility sync port wired");
    sync.wait_for(1).await;

    // Idempotent replay with the same provider_bot_ref: Gateway short-circuits in
    // core (`duplicate_registration=true`) and the message indicates the existing
    // bot was returned.
    let _ = register_provider_bot(&app, &provider_id, &admin_token, "teamclaw-bot:alice").await;
    tokio::time::sleep(std::time::Duration::from_millis(50)).await;

    let requests = sync.requests.lock().await;
    assert_eq!(
        requests.len(),
        1,
        "idempotent replay must not dispatch a second visibility sync, got {requests:?}"
    );
    assert_eq!(requests[0].bot_uuid, first_bot_uuid);
}

#[tokio::test]
async fn register_provider_bot_dispatches_visibility_sync_for_plugin_mode_allowlisted() {
    let provider_id = "prv_visibility_sync_plugin".to_string();
    let admin_token = "admin-token";
    let sync = Arc::new(RecordingVisibilitySyncPort::default());
    let TestApp {
        app,
        provider_repo,
        provider_credentials,
        visibility_sync,
        ..
    } = test_app_with_allowed_switch_provider_ids_and_visibility_sync(
        vec![provider_id.clone()],
        sync,
    );
    seed_allowed_provider(&provider_repo, &provider_credentials, &provider_id, &admin_token).await;

    let bot_ref = "plugin-bot:alice";
    let (status, body) =
        register_provider_bot_mode(&app, &provider_id, &admin_token, bot_ref, "plugin").await;
    assert_eq!(status, StatusCode::OK, "plugin mode rejected: {body}");
    assert_eq!(body["bot_uuid"].as_str(), Some(bot_ref));

    let sync = visibility_sync.expect("visibility sync port wired");
    sync.wait_for(1).await;
    let requests = sync.requests.lock().await;
    assert_eq!(
        requests.len(),
        1,
        "plugin mode over allowlisted provider should dispatch visibility sync once, got {requests:?}"
    );
    assert_eq!(requests[0].bot_uuid, bot_ref);
    assert_eq!(requests[0].actor_kind, ActorKind::Bot);
}
