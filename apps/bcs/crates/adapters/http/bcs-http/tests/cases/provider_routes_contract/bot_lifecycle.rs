use super::*;

async fn patch_provider_bot(
    app: &Router,
    provider_id: &str,
    admin_token: &str,
    provider_bot_ref: &str,
    patch: Value,
) -> (StatusCode, Value) {
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!("/providers/{provider_id}/bots/{provider_bot_ref}"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(patch.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = response.status();
    (status, response_json(response).await)
}

#[tokio::test]
async fn patch_provider_bot_merges_and_preserves_unmodified_fields() {
    let TestApp {
        app, registry, _temp_dir, ..
    } = test_app();
    let provider = register_provider(&app, json!({"mode": "static_bearer"})).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    // Seed a bot with full capabilities in one POST (the default helper omits
    // domains/skills/scopes; a second POST with the same ref is idempotent and
    // would NOT update them).
    let seeded = response_json(
        app.clone()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri(format!("/providers/{provider_id}/bots"))
                    .header("content-type", "application/json")
                    .header("authorization", format!("Bearer {admin_token}"))
                    .body(
                        Body::from(
                            json!({
                                "name": "Code Reviewer",
                                "summary": "Reviews code",
                                "owners": ["12345678"],
                                "provider_bot_ref": "reviewer-v2",
                                "domains": ["development", "security"],
                                "skills": ["code_review"],
                                "scopes": ["production"]
                            })
                            .to_string(),
                        ),
                    )
                    .unwrap(),
            )
            .await
            .unwrap(),
    )
    .await;
    let bot_uuid = seeded["bot_uuid"].as_str().unwrap();

    let (status, body) = patch_provider_bot(
        &app,
        provider_id,
        admin_token,
        "reviewer-v2",
        json!({
            "name": "Senior Reviewer",
            "domains": ["database"]
        }),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["bot_uuid"], bot_uuid);
    assert_eq!(body["name"], "Senior Reviewer");
    assert_eq!(body["domains"], json!(["database"]));
    // Unmodified fields preserved.
    assert_eq!(body["summary"], "Reviews code");
    assert_eq!(body["scopes"], json!(["production"]));
    let skills: Vec<&str> = body["skills"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v["name"].as_str().unwrap())
        .collect();
    assert_eq!(skills, vec!["code_review"]);
    // Persisted to the registry.
    let bot = registry.get(bot_uuid).await.expect("bot registered");
    assert_eq!(bot.capabilities.name.as_deref(), Some("Senior Reviewer"));
    assert_eq!(bot.capabilities.domains, vec!["database"]);
    assert_eq!(bot.capabilities.summary.as_deref(), Some("Reviews code"));
}

#[tokio::test]
async fn patch_provider_bot_clears_array_with_empty_vec_and_keeps_when_field_absent() {
    let TestApp {
        app, registry, _temp_dir, ..
    } = test_app();
    let provider = register_provider(&app, json!({"mode": "static_bearer"})).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    let bot =
        register_provider_bot(&app, provider_id, admin_token, "reviewer-v2").await;
    let bot_uuid = bot["bot_uuid"].as_str().unwrap();

    // Absent fields leave the (default) empty arrays unchanged.
    let (status, body) =
        patch_provider_bot(&app, provider_id, admin_token, "reviewer-v2", json!({})).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["bot_uuid"], bot_uuid);

    // Now seed domains/scopes and clear them with empty vecs.
    patch_provider_bot(
        &app,
        provider_id,
        admin_token,
        "reviewer-v2",
        json!({"domains": ["development"], "scopes": ["production"]}),
    )
    .await;
    // Confirm they landed before clearing.
    let seeded = registry.get(bot_uuid).await.expect("bot registered");
    assert_eq!(seeded.capabilities.domains, vec!["development"]);
    assert_eq!(seeded.capabilities.scopes, vec!["production"]);

    let (status, body) = patch_provider_bot(
        &app,
        provider_id,
        admin_token,
        "reviewer-v2",
        json!({"domains": [], "scopes": []}),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["domains"], json!([]));
    assert_eq!(body["scopes"], json!([]));
    // Regression guard: the cleared arrays must persist in the *live* registry
    // (not just the response / DB). Previously `register`'s empty-array skip
    // left the in-memory bot with the old, non-empty arrays until restart.
    let cleared = registry.get(bot_uuid).await.expect("bot registered");
    assert!(cleared.capabilities.domains.is_empty(), "domains must be cleared in registry");
    assert!(cleared.capabilities.scopes.is_empty(), "scopes must be cleared in registry");
    // An unmodified field (e.g. visibility default) must be preserved.
    assert!(
        matches!(cleared.capabilities.visibility.as_str(), "public" | "protected" | "private"),
        "visibility must remain valid, got {}",
        cleared.capabilities.visibility
    );
}

#[tokio::test]
async fn patch_provider_bot_accepts_structured_skills_and_round_trips() {
    // PATCH skills use structured {name, description} objects only (the legacy
    // bare-string form is rejected). The request shape matches the response so
    // a client can round-trip it, and skill descriptions are mutable.
    let TestApp { app, registry, _temp_dir, .. } = test_app();
    let provider = register_provider(&app, json!({"mode": "static_bearer"})).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();
    let bot = register_provider_bot(&app, provider_id, admin_token, "reviewer-v2").await;
    let bot_uuid = bot["bot_uuid"].as_str().unwrap();

    // Structured skills with a description are accepted and round-tripped in
    // the response (objects, not strings).
    let (status, body) = patch_provider_bot(
        &app,
        provider_id,
        admin_token,
        "reviewer-v2",
        json!({
            "skills": [
                {"name": "code_review", "description": "Reviews submitted code"}
            ]
        }),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        body["skills"],
        json!([{"name": "code_review", "description": "Reviews submitted code"}])
    );

    // Persisted to the registry with the description intact.
    let stored = registry.get(bot_uuid).await.expect("bot registered");
    let skills = &stored.capabilities.skills;
    assert_eq!(skills.len(), 1);
    assert_eq!(skills[0].name, "code_review");
    assert_eq!(skills[0].description.as_deref(), Some("Reviews submitted code"));

    // A subsequent PATCH can update only the description.
    let (status, body) = patch_provider_bot(
        &app,
        provider_id,
        admin_token,
        "reviewer-v2",
        json!({
            "skills": [{"name": "code_review", "description": "Senior code reviews"}]
        }),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        body["skills"],
        json!([{"name": "code_review", "description": "Senior code reviews"}])
    );

    // Skills can be cleared entirely (empty array).
    let (status, body) = patch_provider_bot(
        &app,
        provider_id,
        admin_token,
        "reviewer-v2",
        json!({"skills": []}),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["skills"], json!([]));
    let cleared = registry.get(bot_uuid).await.expect("bot registered");
    assert!(cleared.capabilities.skills.is_empty());
}

#[tokio::test]
async fn patch_provider_bot_rejects_legacy_string_skills() {
    // The legacy bare-string skill form (accepted by registration) is rejected
    // on PATCH: skills must be structured {name, description} objects.
    let TestApp { app, _temp_dir, .. } = test_app();
    let provider = register_provider(&app, json!({"mode": "static_bearer"})).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();
    register_provider_bot(&app, provider_id, admin_token, "reviewer-v2").await;

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!("/providers/{provider_id}/bots/reviewer-v2"))
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::from(json!({"skills": ["code_review"]}).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::UNPROCESSABLE_ENTITY);
}

#[tokio::test]
async fn patch_provider_bot_rejects_invalid_visibility() {
    let TestApp { app, _temp_dir, .. } = test_app();
    let provider = register_provider(&app, json!({"mode": "static_bearer"})).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();
    register_provider_bot(&app, provider_id, admin_token, "reviewer-v2").await;

    let (status, body) = patch_provider_bot(
        &app,
        provider_id,
        admin_token,
        "reviewer-v2",
        json!({"visibility": "friends"}),
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert!(
        body["error"]
            .as_str()
            .unwrap()
            .contains("visibility must be 'public', 'protected', or 'private'")
    );
}

#[tokio::test]
async fn patch_provider_bot_accepts_admin_token_without_human_identity() {
    let TestApp { app, _temp_dir, .. } = test_app();
    let provider = register_provider(&app, json!({"mode": "static_bearer"})).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();
    register_provider_bot(&app, provider_id, admin_token, "reviewer-v2").await;

    let (status, _) = patch_provider_bot(
        &app,
        provider_id,
        admin_token,
        "reviewer-v2",
        json!({"name": "Renamed"}),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
}

#[tokio::test]
async fn patch_provider_bot_rejects_without_admin_token() {
    let TestApp { app, _temp_dir, .. } = test_app();
    let provider = register_provider(&app, json!({"mode": "static_bearer"})).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();
    register_provider_bot(&app, provider_id, admin_token, "reviewer-v2").await;

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!("/providers/{provider_id}/bots/reviewer-v2"))
                .header("content-type", "application/json")
                .body(Body::from(json!({"name": "Renamed"}).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);

    let response = app
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!("/providers/{provider_id}/bots/reviewer-v2"))
                .header("content-type", "application/json")
                .header("authorization", "Bearer not-the-admin-token")
                .body(Body::from(json!({"name": "Renamed"}).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn patch_provider_bot_preserves_agent_code_for_agentpass_bot() {
    // The whole point of the core-layer agent_code reconstruction: an AgentPass
    // bot's routing identifier (== its provider_bot_ref) must survive an update.
    // `registry.get()` strips agent_code, so assert via `find_bot_by_agent_code`.
    let TestApp { app, registry, _temp_dir, .. } = test_app();
    let provider = register_provider(&app, json!({"mode": "agentpass"})).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    let bot = register_provider_bot(&app, provider_id, admin_token, "reviewer-v2").await;
    let bot_uuid = bot["bot_uuid"].as_str().unwrap();
    assert_eq!(
        registry.find_bot_by_agent_code("reviewer-v2").await,
        Some(bot_uuid.to_string())
    );

    let (status, _) = patch_provider_bot(
        &app,
        provider_id,
        admin_token,
        "reviewer-v2",
        json!({"name": "Renamed Agent"}),
    )
    .await;
    assert_eq!(status, StatusCode::OK);

    // agent_code must still resolve to the same bot after the update.
    assert_eq!(
        registry.find_bot_by_agent_code("reviewer-v2").await,
        Some(bot_uuid.to_string())
    );
}

#[tokio::test]
async fn patch_provider_bot_rejects_unreachable_bot() {
    // When the bound bot has been soft-deleted (unreachable from the registry),
    // the update surfaces BotNotFound (404), never silently no-ops.
    let TestApp { app, registry, _temp_dir, .. } = test_app();
    let provider = register_provider(&app, json!({"mode": "static_bearer"})).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();
    let bot = register_provider_bot(&app, provider_id, admin_token, "reviewer-v2").await;
    let bot_uuid = bot["bot_uuid"].as_str().unwrap();

    let _ = registry.soft_delete(bot_uuid).await;

    let (status, _) = patch_provider_bot(
        &app,
        provider_id,
        admin_token,
        "reviewer-v2",
        json!({"name": "Renamed"}),
    )
    .await;
    assert_eq!(status, StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn patch_provider_bot_returns_not_found_for_unknown_ref() {
    let TestApp { app, _temp_dir, .. } = test_app();
    let provider = register_provider(&app, json!({"mode": "static_bearer"})).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();

    let (status, _) = patch_provider_bot(
        &app,
        provider_id,
        admin_token,
        "nonexistent-bot",
        json!({"name": "Renamed"}),
    )
    .await;
    assert_eq!(status, StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn delete_provider_bot_soft_deletes_bound_bot_and_runtime_token() {
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
    let bot = register_provider_bot(&app, provider_id, admin_token, "reviewer-v2").await;
    let bot_uuid = bot["bot_uuid"].as_str().unwrap();
    let runtime_token = bot["bot_runtime_token"].as_str().unwrap();

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("DELETE")
                .uri(format!("/providers/{provider_id}/bots/reviewer-v2"))
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["deleted"], true);
    assert_eq!(body["provider_id"], provider_id);
    assert_eq!(body["provider_bot_ref"], "reviewer-v2");
    assert_eq!(body["bot_uuid"], bot_uuid);
    assert!(registry.get(bot_uuid).await.is_none());
    assert_eq!(registry.find_bot_by_token(runtime_token).await, None);

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri(format!("/providers/{provider_id}/bots"))
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["items"].as_array().unwrap().len(), 0);
}

#[tokio::test]
async fn delete_provider_bot_returns_ok_when_bot_is_not_registered_in_bcs() {
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
    let missing_provider_bot_ref = "missing-bot";

    let response = app
        .oneshot(
            Request::builder()
                .method("DELETE")
                .uri(format!("/providers/{provider_id}/bots/{missing_provider_bot_ref}"))
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["deleted"], false);
    assert_eq!(body["provider_id"], provider_id);
    assert_eq!(body["provider_bot_ref"], missing_provider_bot_ref);
    assert_eq!(body["message"], "bot is not registered in BCS");
}

#[tokio::test]
async fn delete_allowed_switch_provider_legacy_bot_without_binding() {
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
                summary: Some("Existing TC-style bot".to_string()),
                ..Default::default()
            },
            "alice",
            "legacy-token",
        )
        .await
        .expect("seed existing bot row");

    let response = app
        .oneshot(
            Request::builder()
                .method("DELETE")
                .uri(format!("/providers/{provider_id}/bots/teamclaw-bot:alice"))
                .header("authorization", format!("Bearer {admin_token}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["deleted"], true);
    assert_eq!(body["bot_uuid"], "teamclaw-bot:alice");
    assert!(registry.get("teamclaw-bot:alice").await.is_none());
    assert_eq!(registry.find_bot_by_token("legacy-token").await, None);
}
async fn set_task_modes(
    control_plane: &Arc<BotControlPlaneCore>,
    bot_uuid: &str,
    env: &str,
    task_claim_mode: Option<bool>,
    task_dream_mode: Option<bool>,
) {
    control_plane
        .patch(
            bot_uuid,
            env,
            BotControlPlanePatch {
                name: None,
                visibility: None,
                status: None,
                descriptor: None,
                task_claim_mode,
                task_dream_mode,
                ..Default::default()
            },
        )
        .await
        .expect("patch control-plane toggles")
        .expect("bot control-plane record should exist after onboarding");
}

async fn task_mode_roster(
    app: &Router,
    provider_id: &str,
    token: Option<&str>,
    query: &str,
) -> (StatusCode, Value) {
    let mut builder = Request::builder()
        .method("GET")
        .uri(format!("/providers/{provider_id}/bots/by-task-modes{query}"));
    if let Some(token) = token {
        builder = builder.header("authorization", format!("Bearer {token}"));
    }
    let response = app
        .clone()
        .oneshot(builder.body(Body::empty()).unwrap())
        .await
        .unwrap();
    let status = response.status();
    (status, response_json(response).await)
}

fn roster_bot_ids(body: &Value) -> Vec<String> {
    body["items"]
        .as_array()
        .expect("roster response has items array")
        .iter()
        .map(|item| item["bot_id"].as_str().expect("item has bot_id").to_string())
        .collect()
}

fn sorted_ids(mut ids: Vec<String>) -> Vec<String> {
    ids.sort();
    ids
}
#[tokio::test]
async fn list_provider_bots_by_task_modes_is_env_scoped_for_allow_listed_provider() {
    // The roster is admission-gated by `allowed_switch_provider_ids` and
    // env-scoped: it returns every current-env bot whose task-mode toggles
    // satisfy the filter, regardless of which provider a bot is bound to.
    let provider_id = "prv_task_modes".to_string();
    let admin_token = "admin-token";
    let TestApp {
        app,
        control_plane,
        provider_repo,
        provider_credentials,
        ..
    } = test_app_with_allowed_switch_provider_ids(vec![provider_id.clone()]);
    seed_provider_admin(
        &provider_repo,
        &provider_credentials,
        &provider_id,
        admin_token,
        "11111111",
    )
    .await;

    // A second provider (NOT in the allow-list) hosts bot-d. Under the new
    // env-scoped semantics d must still appear in provider_id's roster.
    let other = register_provider(&app, json!({ "mode": "static_bearer" })).await;
    let other_provider_id = other["provider_id"].as_str().unwrap().to_string();
    let other_token = other["provider_admin_token"].as_str().unwrap().to_string();

    let bot_a = register_provider_bot(&app, &provider_id, admin_token, "bot-a").await;
    let bot_b = register_provider_bot(&app, &provider_id, admin_token, "bot-b").await;
    let bot_c = register_provider_bot(&app, &provider_id, admin_token, "bot-c").await;
    let bot_d = register_provider_bot(&app, &other_provider_id, &other_token, "bot-d").await;
    let uuid_a = bot_a["bot_uuid"].as_str().unwrap().to_string();
    let uuid_b = bot_b["bot_uuid"].as_str().unwrap().to_string();
    let uuid_c = bot_c["bot_uuid"].as_str().unwrap().to_string();
    let uuid_d = bot_d["bot_uuid"].as_str().unwrap().to_string();

    let env = resolve_env_str();
    // a = claim, b = dream, c = claim+dream, d = claim+dream (other provider).
    set_task_modes(&control_plane, &uuid_a, &env, Some(true), Some(false)).await;
    set_task_modes(&control_plane, &uuid_b, &env, Some(false), Some(true)).await;
    set_task_modes(&control_plane, &uuid_c, &env, Some(true), Some(true)).await;
    set_task_modes(&control_plane, &uuid_d, &env, Some(true), Some(true)).await;

    // No toggles => all current-env bots (d included; binding scoping removed).
    let (status, body) = task_mode_roster(&app, &provider_id, Some(admin_token), "").await;
    assert_eq!(status, StatusCode::OK, "no-filter roster failed: {body}");
    assert_eq!(
        sorted_ids(roster_bot_ids(&body)),
        sorted_ids(vec![
            uuid_a.clone(),
            uuid_b.clone(),
            uuid_c.clone(),
            uuid_d.clone()
        ])
    );

    // claim_mode=true, match=any => a, c, d (every env bot with claim on).
    let (status, body) = task_mode_roster(
        &app,
        &provider_id,
        Some(admin_token),
        "?task_claim_mode=true&match=any",
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        sorted_ids(roster_bot_ids(&body)),
        sorted_ids(vec![uuid_a.clone(), uuid_c.clone(), uuid_d.clone()])
    );

    // claim=true AND dream=true, match=all => c, d.
    let (status, body) = task_mode_roster(
        &app,
        &provider_id,
        Some(admin_token),
        "?task_claim_mode=true&task_dream_mode=true&match=all",
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        sorted_ids(roster_bot_ids(&body)),
        sorted_ids(vec![uuid_c.clone(), uuid_d.clone()])
    );

    // claim=true OR dream=true, match=any => a, b, c, d.
    let (status, body) = task_mode_roster(
        &app,
        &provider_id,
        Some(admin_token),
        "?task_claim_mode=true&task_dream_mode=true&match=any",
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        sorted_ids(roster_bot_ids(&body)),
        sorted_ids(vec![
            uuid_a.clone(),
            uuid_b.clone(),
            uuid_c.clone(),
            uuid_d.clone()
        ])
    );

    // dream=true, match=any => b, c, d.
    let (status, body) = task_mode_roster(
        &app,
        &provider_id,
        Some(admin_token),
        "?task_dream_mode=true&match=any",
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        sorted_ids(roster_bot_ids(&body)),
        sorted_ids(vec![uuid_b.clone(), uuid_c.clone(), uuid_d.clone()])
    );

    // Missing token => 401.
    let (status, body) =
        task_mode_roster(&app, &provider_id, None, "?task_claim_mode=true").await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert_eq!(body["status"], 401);

    // Wrong token => 401.
    let (status, body) = task_mode_roster(
        &app,
        &provider_id,
        Some("not-the-admin-token"),
        "?task_claim_mode=true",
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert_eq!(body["status"], 401);

    // Token that authenticates a different provider than the path => 403.
    let (status, body) = task_mode_roster(
        &app,
        &provider_id,
        Some(other_token.as_str()),
        "?task_claim_mode=true",
    )
    .await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    assert_eq!(body["error"], "provider_id_mismatch");

    // Provider admin token valid, but the path provider is not allow-listed.
    let (status, body) = task_mode_roster(
        &app,
        &other_provider_id,
        Some(other_token.as_str()),
        "?task_claim_mode=true",
    )
    .await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    assert_eq!(
        body["error"],
        format!(
            "provider '{}' is not allowed to access the task-mode roster",
            other_provider_id
        )
    );
}
#[tokio::test]
async fn list_provider_bots_by_task_modes_rejects_invalid_toggle_and_accepts_false() {
    let provider_id = "prv_task_modes_toggle".to_string();
    let admin_token = "admin-token";
    let TestApp {
        app,
        provider_repo,
        provider_credentials,
        ..
    } = test_app_with_allowed_switch_provider_ids(vec![provider_id.clone()]);
    seed_provider_admin(
        &provider_repo,
        &provider_credentials,
        &provider_id,
        admin_token,
        "11111111",
    )
    .await;

    // `false` parses to Some(false) — exercises the false/0 arm of
    // parse_task_mode_toggle. With no current-env bots the roster is empty
    // but 200.
    let (status, body) =
        task_mode_roster(&app, &provider_id, Some(admin_token), "?task_claim_mode=false").await;
    assert_eq!(status, StatusCode::OK, "false toggle failed: {body}");
    assert!(body["items"].is_array(), "false toggle response missing items: {body}");

    // `0` is the other accepted false spelling (same parse arm).
    let (status, _body) =
        task_mode_roster(&app, &provider_id, Some(admin_token), "?task_dream_mode=0").await;
    assert_eq!(status, StatusCode::OK, "0 toggle failed");

    // An unrecognized toggle value surfaces as 400 bad_request from the handler
    // (parse_task_mode_toggle error arm), before the service is consulted.
    let (status, body) =
        task_mode_roster(&app, &provider_id, Some(admin_token), "?task_claim_mode=maybe").await;
    assert_eq!(status, StatusCode::BAD_REQUEST, "invalid toggle not rejected: {body}");
    assert_eq!(body["status"], 400);
}
#[tokio::test]
async fn list_provider_bots_by_task_modes_parses_visibility_status_user_visibility_filters() {
    // Exercises the parser arms added for the visibility / status /
    // user_visibility roster filters. Every accepted spelling returns 200;
    // unrecognized status / user_visibility values surface as 400 bad_request
    // from the handler before the service is consulted. No bots are seeded —
    // the parser branches are covered regardless of roster cardinality (the
    // None/empty arms are already covered by the toggle tests that omit them).
    let provider_id = "prv_task_modes_filters".to_string();
    let admin_token = "admin-token";
    let TestApp {
        app,
        provider_repo,
        provider_credentials,
        ..
    } = test_app_with_allowed_switch_provider_ids(vec![provider_id.clone()]);
    seed_provider_admin(
        &provider_repo,
        &provider_credentials,
        &provider_id,
        admin_token,
        "11111111",
    )
    .await;

    // Accepted status spellings => 200 (online + hidden arms of parse_actor_status).
    let (status, _body) =
        task_mode_roster(&app, &provider_id, Some(admin_token), "?status=online").await;
    assert_eq!(status, StatusCode::OK, "status=online rejected");
    let (status, _body) =
        task_mode_roster(&app, &provider_id, Some(admin_token), "?status=hidden").await;
    assert_eq!(status, StatusCode::OK, "status=hidden rejected");

    // Accepted user_visibility spellings => 200 (public/protected/private arms).
    for value in ["public", "protected", "private"] {
        let (status, _body) = task_mode_roster(
            &app,
            &provider_id,
            Some(admin_token),
            &format!("?user_visibility={value}"),
        )
        .await;
        assert_eq!(status, StatusCode::OK, "user_visibility={value} rejected");
    }

    // Non-empty visibility => 200 (Some arm of parse_non_empty_query).
    let (status, _body) =
        task_mode_roster(&app, &provider_id, Some(admin_token), "?visibility=public").await;
    assert_eq!(status, StatusCode::OK, "visibility=public rejected");

    // Empty spellings are treated as "no filter" => 200 (empty -> None arms).
    let (status, _body) = task_mode_roster(
        &app,
        &provider_id,
        Some(admin_token),
        "?status=&user_visibility=&visibility=",
    )
    .await;
    assert_eq!(status, StatusCode::OK, "empty filter values rejected");

    // Unrecognized status => 400 (error arm of parse_actor_status).
    let (status, body) =
        task_mode_roster(&app, &provider_id, Some(admin_token), "?status=maybe").await;
    assert_eq!(
        status,
        StatusCode::BAD_REQUEST,
        "invalid status not rejected: {body}"
    );
    assert_eq!(body["status"], 400);

    // Unrecognized user_visibility => 400 (error arm of parse_user_visibility).
    let (status, body) = task_mode_roster(
        &app,
        &provider_id,
        Some(admin_token),
        "?user_visibility=secret",
    )
    .await;
    assert_eq!(
        status,
        StatusCode::BAD_REQUEST,
        "invalid user_visibility not rejected: {body}"
    );
    assert_eq!(body["status"], 400);
}
