use super::*;

#[tokio::test]
async fn resolve_delivery_target_returns_http_provider_for_enabled_binding() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::StaticBearer).await;
    let (binding, _) = ctx
        .core
        .register_provider_bot_with_bot_uuid(
            &provider.provider_id,
            &provider.admin_token,
            RegisterProviderBotParams {
                bot_name: "Code Reviewer".to_string(),
                summary: None,
                owners: vec!["11111111".to_string()],
                provider_bot_ref: "reviewer-v2".to_string(),
                ..Default::default()
            },
        )
        .await
        .expect("register provider bot");

    let target = ctx
        .registry
        .resolve_delivery_target(&binding.bot_uuid)
        .await
        .expect("resolve delivery target");

    match target {
        BotDeliveryTarget::HttpProvider {
            provider_id,
            provider_bot_ref,
            webhook_url,
            ..
        } => {
            assert_eq!(provider_id, provider.provider_id);
            assert_eq!(provider_bot_ref, "reviewer-v2");
            assert_eq!(webhook_url, "https://provider.example.com/bcs/webhook");
        }
        BotDeliveryTarget::WebSocket { .. } | BotDeliveryTarget::A2a { .. } => panic!("expected http provider target"),
    }
}

#[tokio::test]
async fn provider_http_bot_is_effectively_online_without_ws_connection() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::StaticBearer).await;
    let (binding, _) = ctx
        .core
        .register_provider_bot_with_bot_uuid(
            &provider.provider_id,
            &provider.admin_token,
            RegisterProviderBotParams {
                bot_name: "Code Reviewer".to_string(),
                summary: None,
                owners: vec!["11111111".to_string()],
                provider_bot_ref: "reviewer-v2".to_string(),
                ..Default::default()
            },
        )
        .await
        .expect("register provider bot");

    assert!(!ctx.registry.is_connected(&binding.bot_uuid).await);
    assert!(ctx.registry.is_effectively_online(&binding.bot_uuid).await);

    ctx.registry
        .update_actor_status(&binding.bot_uuid, ActorStatus::Hidden)
        .await
        .expect("hide provider bot");
    assert!(!ctx.registry.is_effectively_online(&binding.bot_uuid).await);
}

#[tokio::test]
async fn disabled_provider_is_not_routable() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::StaticBearer).await;
    let (binding, _) = ctx
        .core
        .register_provider_bot_with_bot_uuid(
            &provider.provider_id,
            &provider.admin_token,
            RegisterProviderBotParams {
                bot_name: "Code Reviewer".to_string(),
                summary: None,
                owners: vec!["11111111".to_string()],
                provider_bot_ref: "reviewer-v2".to_string(),
                ..Default::default()
            },
        )
        .await
        .expect("register provider bot");
    ctx.core
        .set_provider_disabled(&provider.provider_id, &provider.admin_token, "11111111", true)
        .await
        .expect("disable provider");

    let err = ctx
        .registry
        .resolve_delivery_target(&binding.bot_uuid)
        .await
        .expect_err("disabled provider should not be routable");
    assert!(matches!(err, ServiceError::InvalidOperation { .. }));
}

#[tokio::test]
async fn set_provider_disabled_requires_matching_admin_token() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::StaticBearer).await;
    let other_registered = ctx
        .core
        .register_provider(
            "Provider 2".to_string(),
            Some("https://provider2.example.com/bcs/webhook".to_string()),
            ProviderAuthMode::StaticBearer,
            "11111111".to_string(),
            None,
            None,
        )
        .await
        .expect("register provider 2");

    let err = ctx
        .core
        .set_provider_disabled(
            &provider.provider_id,
            &other_registered.provider_admin_token,
            "11111111",
            true,
        )
        .await
        .expect_err("admin token for another provider must fail");

    assert!(matches!(err, ServiceError::Forbidden(message) if message == "provider_id_mismatch"));
}

#[tokio::test]
async fn provider_admin_provider_bot_registration_returns_runtime_token() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::ProviderAdmin).await;

    let (binding, bot_runtime_token) = ctx
        .core
        .register_provider_bot_with_bot_uuid(
            &provider.provider_id,
            &provider.admin_token,
            RegisterProviderBotParams {
                bot_name: "Code Reviewer".to_string(),
                summary: None,
                owners: vec!["11111111".to_string()],
                provider_bot_ref: "reviewer-v2".to_string(),
                ..Default::default()
            },
        )
        .await
        .expect("register provider bot");

    assert!(
        bot_runtime_token.is_some(),
        "provider_admin mode still issues a bot_runtime_token"
    );
    assert_eq!(binding.provider_bot_ref, "reviewer-v2");
}

#[tokio::test]
async fn authenticate_provider_admin_event_returns_identity() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::ProviderAdmin).await;
    let (binding, _) = ctx
        .core
        .register_provider_bot_with_bot_uuid(
            &provider.provider_id,
            &provider.admin_token,
            RegisterProviderBotParams {
                bot_name: "Code Reviewer".to_string(),
                summary: None,
                owners: vec!["11111111".to_string()],
                provider_bot_ref: "reviewer-v2".to_string(),
                ..Default::default()
            },
        )
        .await
        .expect("register provider bot");

    let identity = ctx
        .core
        .authenticate_provider_admin_event(
            &provider.provider_id,
            &provider.admin_token,
            "reviewer-v2",
        )
        .await
        .expect("authenticate provider_admin event");

    assert_eq!(identity.bot_uuid, binding.bot_uuid);
    assert_eq!(identity.provider_id, provider.provider_id);
}

#[tokio::test]
async fn authenticate_provider_admin_event_rejects_static_bearer_provider() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::StaticBearer).await;
    ctx.core
        .register_provider_bot_with_bot_uuid(
            &provider.provider_id,
            &provider.admin_token,
            RegisterProviderBotParams {
                bot_name: "Code Reviewer".to_string(),
                summary: None,
                owners: vec!["11111111".to_string()],
                provider_bot_ref: "reviewer-v2".to_string(),
                ..Default::default()
            },
        )
        .await
        .expect("register provider bot");

    let err = ctx
        .core
        .authenticate_provider_admin_event(
            &provider.provider_id,
            &provider.admin_token,
            "reviewer-v2",
        )
        .await
        .expect_err("auth_mode_mismatch should fail");
    assert!(matches!(err, ServiceError::Unauthorized(msg) if msg == "auth_mode_mismatch"));
}

#[tokio::test]
async fn authenticate_provider_admin_event_rejects_other_provider_id() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::ProviderAdmin).await;
    let other = register_provider(&ctx, ProviderAuthMode::ProviderAdmin).await;
    ctx.core
        .register_provider_bot_with_bot_uuid(
            &provider.provider_id,
            &provider.admin_token,
            RegisterProviderBotParams {
                bot_name: "Code Reviewer".to_string(),
                summary: None,
                owners: vec!["11111111".to_string()],
                provider_bot_ref: "reviewer-v2".to_string(),
                ..Default::default()
            },
        )
        .await
        .expect("register provider bot");

    let err = ctx
        .core
        .authenticate_provider_admin_event(
            &other.provider_id,
            &provider.admin_token,
            "reviewer-v2",
        )
        .await
        .expect_err("admin token from another provider must fail");
    assert!(matches!(err, ServiceError::Forbidden(msg) if msg == "provider_id_mismatch"));
}

#[tokio::test]
async fn authenticate_provider_admin_event_rejects_unknown_bot_ref() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::ProviderAdmin).await;

    let err = ctx
        .core
        .authenticate_provider_admin_event(
            &provider.provider_id,
            &provider.admin_token,
            "no-such-ref",
        )
        .await
        .expect_err("unknown bot ref should fail");
    assert!(matches!(err, ServiceError::BotNotFound(_)));
}

#[tokio::test]
async fn authenticate_provider_admin_event_rejects_disabled_provider() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::ProviderAdmin).await;
    ctx.core
        .register_provider_bot_with_bot_uuid(
            &provider.provider_id,
            &provider.admin_token,
            RegisterProviderBotParams {
                bot_name: "Code Reviewer".to_string(),
                summary: None,
                owners: vec!["11111111".to_string()],
                provider_bot_ref: "reviewer-v2".to_string(),
                ..Default::default()
            },
        )
        .await
        .expect("register provider bot");
    ctx.core
        .set_provider_disabled(&provider.provider_id, &provider.admin_token, "11111111", true)
        .await
        .expect("disable provider");

    let err = ctx
        .core
        .authenticate_provider_admin_event(
            &provider.provider_id,
            &provider.admin_token,
            "reviewer-v2",
        )
        .await
        .expect_err("disabled provider should fail");
    assert!(matches!(err, ServiceError::InvalidOperation { message, .. } if message.contains("is disabled")));
}

#[tokio::test]
async fn authenticate_provider_admin_event_rejects_disabled_binding() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::ProviderAdmin).await;
    let (binding, _) = ctx
        .core
        .register_provider_bot_with_bot_uuid(
            &provider.provider_id,
            &provider.admin_token,
            RegisterProviderBotParams {
                bot_name: "Code Reviewer".to_string(),
                summary: None,
                owners: vec!["11111111".to_string()],
                provider_bot_ref: "reviewer-v2".to_string(),
                ..Default::default()
            },
        )
        .await
        .expect("register provider bot");
    ctx.core
        .set_provider_bot_disabled(
            &provider.provider_id,
            &binding.bot_uuid,
            &provider.admin_token,
            true,
        )
        .await
        .expect("disable provider bot");

    let err = ctx
        .core
        .authenticate_provider_admin_event(
            &provider.provider_id,
            &provider.admin_token,
            "reviewer-v2",
        )
        .await
        .expect_err("disabled binding should fail");
    assert!(matches!(err, ServiceError::InvalidOperation { message, .. } if message.contains("is disabled")));
}

#[tokio::test]
async fn plugin_register_then_connect_promotes_mock_to_real_and_routes_websocket() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::StaticBearer).await;
    let bot_ref = "plugin-bot:alice";

    // provider pre-registers a plugin bot (bot_uuid == provider_bot_ref),
    // skipping the binding and writing a MOCK placeholder token
    let (binding, bot_runtime_token) = ctx
        .core
        .register_provider_bot_with_bot_uuid(
            &provider.provider_id,
            &provider.admin_token,
            RegisterProviderBotParams {
                bot_name: "Plugin Bot".to_string(),
                summary: Some("Plugin-connected".to_string()),
                owners: vec!["11111111".to_string()],
                provider_bot_ref: bot_ref.to_string(),
                bot_uuid: Some(bot_ref.to_string()),
                connection_mode: ProviderBotConnectionMode::Plugin,
                ..Default::default()
            },
        )
        .await
        .expect("register plugin bot");

    // plugin mode never returns a runtime token (the real token comes from WS)
    assert!(bot_runtime_token.is_none(), "plugin response must not expose a token");
    // no provider_binding row → routing is WebSocket, not HttpProvider
    assert!(ctx
        .core
        .get_provider_bot_binding_by_ref(&provider.provider_id, bot_ref)
        .await
        .expect("query binding")
        .is_none());
    // the stored token is the MOCK sentinel
    let stored = ctx.registry.load_token(&binding.bot_uuid).await.expect("stored token");
    assert!(is_mock_token(&stored), "plugin pre-registration stores a MOCK: {stored}");
    // routing target: WebSocket (no binding)
    let target = ctx
        .registry
        .resolve_delivery_target(&binding.bot_uuid)
        .await
        .expect("resolve delivery target");
    assert!(matches!(target, BotDeliveryTarget::WebSocket { .. }));

    // plugin's first WS connect (empty token, bot_id == provider_bot_ref):
    // connect_or_promote_streaming finds the bot by id and promotes MOCK → real
    let promoted = ctx
        .registry
        .connect_or_promote_streaming(binding.bot_uuid.clone())
        .await
        .expect("promote");
    assert!(!is_mock_token(&promoted), "promoted token is real");
    assert_eq!(
        ctx.registry.load_token(&binding.bot_uuid).await.as_deref(),
        Some(promoted.as_str()),
        "stored token was promoted to real"
    );
}

#[tokio::test]
async fn plugin_register_over_existing_real_bot_preserves_token_no_binding() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::StaticBearer).await;
    let bot_ref = "plugin-bot:alice";

    // plugin connects FIRST → connect_or_promote_streaming creates the bot with
    // a real runtime token (create branch).
    let real_token = ctx
        .registry
        .connect_or_promote_streaming(bot_ref.to_string())
        .await
        .expect("first connect creates real token");
    assert!(!is_mock_token(&real_token));

    // provider then registers in plugin mode over the SAME bot_uuid == ref.
    // §3.3 token rule: load_token returns Some(real) → preserve (no overwrite).
    let (binding, bot_runtime_token) = ctx
        .core
        .register_provider_bot_with_bot_uuid(
            &provider.provider_id,
            &provider.admin_token,
            RegisterProviderBotParams {
                bot_name: "Plugin Bot".to_string(),
                summary: Some("Plugin-connected".to_string()),
                owners: vec!["11111111".to_string()],
                provider_bot_ref: bot_ref.to_string(),
                bot_uuid: Some(bot_ref.to_string()),
                connection_mode: ProviderBotConnectionMode::Plugin,
                ..Default::default()
            },
        )
        .await
        .expect("plugin register over existing bot");

    assert!(bot_runtime_token.is_none(), "plugin response must not expose a token");
    assert_eq!(binding.bot_uuid, bot_ref, "same bot_uuid");
    // real token preserved — registration did not overwrite it
    assert_eq!(
        ctx.registry.load_token(bot_ref).await.as_deref(),
        Some(real_token.as_str()),
        "registration must preserve the existing real token"
    );
    // still no binding → routing WebSocket
    assert!(ctx
        .core
        .get_provider_bot_binding_by_ref(&provider.provider_id, bot_ref)
        .await
        .expect("query binding")
        .is_none());
    let target = ctx
        .registry
        .resolve_delivery_target(bot_ref)
        .await
        .expect("resolve");
    assert!(matches!(target, BotDeliveryTarget::WebSocket { .. }));
}

#[tokio::test]
async fn plugin_register_idempotent_keeps_same_bot_uuid_and_token() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::StaticBearer).await;
    let bot_ref = "plugin-bot:alice";

    let params = RegisterProviderBotParams {
        bot_name: "Plugin Bot".to_string(),
        summary: Some("Plugin-connected".to_string()),
        owners: vec!["11111111".to_string()],
        provider_bot_ref: bot_ref.to_string(),
        bot_uuid: Some(bot_ref.to_string()),
        connection_mode: ProviderBotConnectionMode::Plugin,
        ..Default::default()
    };
    let (first, _) = ctx
        .core
        .register_provider_bot_with_bot_uuid(&provider.provider_id, &provider.admin_token, params.clone())
        .await
        .expect("first register");
    let (second, _) = ctx
        .core
        .register_provider_bot_with_bot_uuid(&provider.provider_id, &provider.admin_token, params)
        .await
        .expect("second register");

    assert_eq!(first.bot_uuid, second.bot_uuid, "idempotent ⇒ same bot_uuid");
    // both writes left a single MOCK (idempotent rule: keep existing MOCK);
    // token unchanged across the two registrations
    let after_first = ctx.registry.load_token(&first.bot_uuid).await.expect("token");
    let after_second = ctx.registry.load_token(&second.bot_uuid).await.expect("token");
    assert_eq!(after_first, after_second, "token unchanged by idempotent re-register");
    assert!(is_mock_token(&after_first), "still MOCK (no plugin connected yet)");
}
