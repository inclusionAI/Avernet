use super::*;

#[tokio::test]
async fn member_detail_includes_flattening_source_data_and_agent_code() {
    let ctx = test_context().await;
    let provider_a = register_provider(&ctx, "Provider A").await;
    let provider_b = register_provider_with_auth(
        &ctx,
        "Provider B",
        ProviderAuthMode::AgentPass,
    )
    .await;
    grant_manager(&ctx, &provider_b, &provider_a).await;
    register_bot(&ctx, &provider_b, "bot-b").await;
    create_org(&ctx, &provider_a).await;
    ctx.service
        .put_member(PutOrganizationMemberCommand {
            auth: member_auth(&provider_a),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-b".to_string(),
            role: Some("reviewer".to_string()),
        })
        .await
        .expect("add member");

    let detail = ctx
        .service
        .get_member_detail(member_auth(&provider_a), "promo-2026", "bot-b")
        .await
        .expect("get member detail")
        .expect("member exists");

    assert_eq!(detail.member.bot_uuid, "bot-b");
    assert_eq!(detail.member.role.as_deref(), Some("reviewer"));
    let bot = detail.bot.expect("registered bot detail");
    assert_eq!(bot.provider_id, provider_b.provider_id);
    assert_eq!(bot.provider_bot_ref, "bot-b-ref");
    assert_eq!(bot.agent_code.as_deref(), Some("bot-b-ref"));
    assert_eq!(bot.capabilities.name.as_deref(), Some("bot-b name"));
    assert_eq!(bot.capabilities.summary.as_deref(), Some("bot-b summary"));
    assert_eq!(bot.capabilities.domains, ["marketing"]);
    assert_eq!(bot.capabilities.skills, [Skill::new("planning")]);
    assert_eq!(bot.capabilities.scopes, ["campaign"]);
    assert_eq!(bot.capabilities.visibility, "protected");
    assert_eq!(bot.created_by.as_deref(), Some("11111111"));
    assert_eq!(bot.actor_kind, bcs_service_api::ActorKind::Bot);
}

#[tokio::test]
async fn member_detail_keeps_member_when_registered_bot_is_missing() {
    let ctx = test_context().await;
    let provider_a = register_provider(&ctx, "Provider A").await;
    register_bot(&ctx, &provider_a, "bot-a").await;
    create_org(&ctx, &provider_a).await;
    ctx.service
        .put_member(PutOrganizationMemberCommand {
            auth: member_auth(&provider_a),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-a".to_string(),
            role: None,
        })
        .await
        .expect("add member");
    assert!(ctx.registry.soft_delete("bot-a").await);

    let detail = ctx
        .service
        .get_member_detail(member_auth(&provider_a), "promo-2026", "bot-a")
        .await
        .expect("get stale member detail")
        .expect("member remains");

    assert_eq!(detail.member.bot_uuid, "bot-a");
    assert!(detail.bot.is_none());
}

#[tokio::test]
async fn put_member_allows_own_and_granted_provider_bots_and_restores_disabled_member() {
    let ctx = test_context().await;
    let provider_a = register_provider(&ctx, "Provider A").await;
    let provider_b = register_provider(&ctx, "Provider B").await;
    grant_manager(&ctx, &provider_b, &provider_a).await;
    register_bot(&ctx, &provider_a, "bot-a").await;
    register_bot(&ctx, &provider_b, "bot-b").await;
    create_org(&ctx, &provider_a).await;

    let own = ctx
        .service
        .put_member(PutOrganizationMemberCommand {
            auth: member_auth(&provider_a),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-a".to_string(),
            role: Some("planner".to_string()),
        })
        .await
        .expect("own bot allowed");
    assert_eq!(own.role.as_deref(), Some("planner"));

    let granted = ctx
        .service
        .put_member(PutOrganizationMemberCommand {
            auth: member_auth(&provider_a),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-b".to_string(),
            role: Some("traffic_analyst".to_string()),
        })
        .await
        .expect("granted provider bot allowed");
    assert_eq!(granted.bot_uuid, "bot-b");

    ctx.service
        .delete_member(member_auth(&provider_a), "promo-2026", "bot-b")
        .await
        .expect("delete existing member");
    let disabled = ctx
        .service
        .get_member(member_auth(&provider_a), "promo-2026", "bot-b")
        .await
        .expect("read disabled member")
        .expect("disabled member is retained");
    assert!(disabled.disabled);

    let restored = ctx
        .service
        .put_member(PutOrganizationMemberCommand {
            auth: member_auth(&provider_a),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-b".to_string(),
            role: Some("traffic_analyst".to_string()),
        })
        .await
        .expect("put restores disabled member");
    assert!(!restored.disabled);

    ctx.service
        .delete_member(member_auth(&provider_a), "promo-2026", "missing-member")
        .await
        .expect("delete missing member is idempotent");
}

#[tokio::test]
async fn put_member_rejects_ungranted_provider_bot() {
    let ctx = test_context().await;
    let provider_a = register_provider(&ctx, "Provider A").await;
    let provider_c = register_provider(&ctx, "Provider C").await;
    register_bot(&ctx, &provider_c, "bot-c").await;
    create_org(&ctx, &provider_a).await;

    let err = ctx
        .service
        .put_member(PutOrganizationMemberCommand {
            auth: member_auth(&provider_a),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-c".to_string(),
            role: Some("planner".to_string()),
        })
        .await
        .expect_err("ungranted provider bot must be rejected");

    assert!(matches!(err, ServiceError::Forbidden(reason) if reason == "organization_manager_not_authorized"));
}

#[tokio::test]
async fn put_member_rejects_disabled_organization_before_bot_checks() {
    let ctx = test_context().await;
    let provider_a = register_provider(&ctx, "Provider A").await;
    create_org(&ctx, &provider_a).await;
    ctx.service
        .update(UpdateOrganizationCommand {
            auth: member_auth(&provider_a),
            organization_code: "promo-2026".to_string(),
            name: None,
            description: None,
            disabled: Some(true),
        })
        .await
        .expect("disable organization");

    let err = ctx
        .service
        .put_member(PutOrganizationMemberCommand {
            auth: member_auth(&provider_a),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "missing-bot".to_string(),
            role: Some("planner".to_string()),
        })
        .await
        .expect_err("disabled organization should be rejected first");

    assert!(matches!(err, ServiceError::Forbidden(reason) if reason == "organization_disabled"));
}

#[tokio::test]
async fn put_member_rejects_missing_and_deleted_bots() {
    let ctx = test_context().await;
    let provider_a = register_provider(&ctx, "Provider A").await;
    create_org(&ctx, &provider_a).await;

    let missing = ctx
        .service
        .put_member(PutOrganizationMemberCommand {
            auth: member_auth(&provider_a),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "missing-bot".to_string(),
            role: Some("planner".to_string()),
        })
        .await
        .expect_err("missing bot should be rejected");
    assert!(matches!(missing, ServiceError::BotNotFound(bot) if bot == "missing-bot"));

    register_bot(&ctx, &provider_a, "deleted-bot").await;
    assert!(ctx.registry.soft_delete("deleted-bot").await);
    let deleted = ctx
        .service
        .put_member(PutOrganizationMemberCommand {
            auth: member_auth(&provider_a),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "deleted-bot".to_string(),
            role: Some("planner".to_string()),
        })
        .await
        .expect_err("deleted bot should be rejected");
    assert!(matches!(deleted, ServiceError::BotNotFound(bot) if bot == "deleted-bot"));
}

#[tokio::test]
async fn organization_management_rejects_wrong_manager_and_bad_auth() {
    let ctx = test_context().await;
    let provider_a = register_provider(&ctx, "Provider A").await;
    let provider_b = register_provider(&ctx, "Provider B").await;
    create_org(&ctx, &provider_a).await;

    let wrong_manager = ctx
        .service
        .get(member_auth(&provider_b), "promo-2026")
        .await
        .expect_err("wrong manager should be rejected");
    assert!(matches!(wrong_manager, ServiceError::Forbidden(reason) if reason == "organization_manager_required"));

    let bad_auth = ctx
        .service
        .list(bad_provider_auth(&provider_a), false)
        .await
        .expect_err("application must authenticate before core access");
    assert!(matches!(bad_auth, ServiceError::Unauthorized(_)));
}

#[tokio::test]
async fn validation_rejects_invalid_code_invalid_role_and_patch_without_fields() {
    let ctx = test_context().await;
    let provider_a = register_provider(&ctx, "Provider A").await;
    register_bot(&ctx, &provider_a, "bot-a").await;
    create_org(&ctx, &provider_a).await;

    let invalid_code = ctx
        .service
        .create(CreateOrganizationCommand {
            auth: provider_auth(&provider_a),
            organization_code: "invalid code".to_string(),
            name: "Invalid".to_string(),
            description: None,
        })
        .await
        .expect_err("invalid code should fail");
    assert!(matches!(invalid_code, ServiceError::InvalidOperation { message, .. } if message.contains("invalid organization_code")));

    let invalid_role = ctx
        .service
        .put_member(PutOrganizationMemberCommand {
            auth: member_auth(&provider_a),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-a".to_string(),
            role: Some("bad role!".to_string()),
        })
        .await
        .expect_err("invalid role should fail");
    assert!(matches!(invalid_role, ServiceError::InvalidOperation { message, .. } if message.contains("invalid role")));

    let patch_without_fields = ctx
        .service
        .update(UpdateOrganizationCommand {
            auth: member_auth(&provider_a),
            organization_code: "promo-2026".to_string(),
            name: None,
            description: None,
            disabled: None,
        })
        .await
        .expect_err("PATCH with no fields should fail");
    assert!(matches!(patch_without_fields, ServiceError::InvalidOperation { message, .. } if message.contains("no organization fields")));
}
