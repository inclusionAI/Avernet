use super::*;

#[tokio::test]
async fn scoped_a2a_authorizes_pair_after_cross_provider_grant_revocation() {
    let ctx = test_context().await;
    let provider_a = register_provider(&ctx, "Provider A").await;
    let provider_b = register_provider(&ctx, "Provider B").await;
    grant_manager(&ctx, &provider_b, &provider_a).await;
    register_bot(&ctx, &provider_a, "bot-a").await;
    register_bot(&ctx, &provider_b, "bot-b").await;
    create_org(&ctx, &provider_a).await;
    ctx.service
        .put_member(PutOrganizationMemberCommand {
            auth: member_auth(&provider_a),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-a".to_string(),
            role: Some("planner".to_string()),
        })
        .await
        .expect("add sender");
    ctx.service
        .put_member(PutOrganizationMemberCommand {
            auth: member_auth(&provider_a),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-b".to_string(),
            role: Some("traffic_analyst".to_string()),
        })
        .await
        .expect("add target");

    let pair = ctx
        .core
        .authorize_pair("promo-2026", "bot-a", "bot-b")
        .await
        .expect("authorized pair");
    assert_eq!(pair.organization.code, "promo-2026");
    assert_eq!(pair.sender.bot_uuid, "bot-a");
    assert_eq!(pair.target.bot_uuid, "bot-b");

    ctx.provider_core
        .update_provider(
            &provider_b.provider_id,
            &provider_b.admin_token,
            "11111111",
            None,
            None,
            None,
            None,
            Some(ProviderOrganizationManagementConfig {
                authorized_manager_provider_ids: Vec::new(),
            }),
        )
        .await
        .expect("revoke organization manager");

    ctx.core
        .authorize_pair("promo-2026", "bot-a", "bot-b")
        .await
        .expect("grant revocation must not remove active scoped A2A members");
}

#[tokio::test]
async fn effective_membership_rejects_disabled_org_disabled_member_and_nonmember_sender() {
    let ctx = test_context().await;
    let provider_a = register_provider(&ctx, "Provider A").await;
    register_bot(&ctx, &provider_a, "bot-a").await;
    register_bot(&ctx, &provider_a, "bot-b").await;
    create_org(&ctx, &provider_a).await;
    for bot_uuid in ["bot-a", "bot-b"] {
        ctx.service
            .put_member(PutOrganizationMemberCommand {
                auth: member_auth(&provider_a),
                organization_code: "promo-2026".to_string(),
                bot_uuid: bot_uuid.to_string(),
                role: None,
            })
            .await
            .expect("add member");
    }

    ctx.service
        .delete_member(member_auth(&provider_a), "promo-2026", "bot-b")
        .await
        .expect("disable member");
    let disabled_member = ctx
        .core
        .authorize_pair("promo-2026", "bot-a", "bot-b")
        .await
        .expect_err("disabled target member must be rejected");
    assert!(matches!(disabled_member, ServiceError::Forbidden(reason) if reason == "organization_member_disabled"));

    let nonmember = ctx
        .core
        .authorize_pair("promo-2026", "missing-member", "bot-a")
        .await
        .expect_err("nonmember sender must be rejected");
    assert!(matches!(nonmember, ServiceError::Forbidden(reason) if reason == "organization_member_required"));

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
    let disabled_org = ctx
        .core
        .authorize_pair("promo-2026", "bot-a", "bot-b")
        .await
        .expect_err("disabled organization must be rejected");
    assert!(matches!(disabled_org, ServiceError::Forbidden(reason) if reason == "organization_disabled"));
}

#[tokio::test]
async fn runtime_member_list_keeps_active_members_after_provider_grant_revocation() {
    let ctx = test_context().await;
    let provider_a = register_provider(&ctx, "Provider A").await;
    let provider_b = register_provider(&ctx, "Provider B").await;
    let provider_c = register_provider(&ctx, "Provider C").await;
    grant_manager(&ctx, &provider_b, &provider_a).await;
    register_bot(&ctx, &provider_a, "bot-a").await;
    register_bot(&ctx, &provider_b, "bot-b").await;
    register_bot(&ctx, &provider_c, "bot-c").await;
    create_org(&ctx, &provider_a).await;
    for (bot_uuid, role) in [
        ("bot-a", "planner"),
        ("bot-b", "traffic_analyst"),
        ("bot-c", "traffic_analyst"),
    ] {
        if bot_uuid == "bot-c" {
            grant_manager(&ctx, &provider_c, &provider_a).await;
        }
        ctx.service
            .put_member(PutOrganizationMemberCommand {
                auth: member_auth(&provider_a),
                organization_code: "promo-2026".to_string(),
                bot_uuid: bot_uuid.to_string(),
                role: Some(role.to_string()),
            })
            .await
            .expect("add member");
    }
    ctx.provider_core
        .update_provider(
            &provider_c.provider_id,
            &provider_c.admin_token,
            "11111111",
            None,
            None,
            None,
            None,
            Some(ProviderOrganizationManagementConfig {
                authorized_manager_provider_ids: Vec::new(),
            }),
        )
        .await
        .expect("revoke provider c");
    ctx.provider_core
        .set_provider_bot_disabled(
            &provider_b.provider_id,
            "bot-b",
            &provider_b.admin_token,
            true,
        )
        .await
        .expect("disable provider b binding");

    ctx.core
        .require_runtime_member("promo-2026", "bot-b")
        .await
        .expect("disabled provider binding must not remove an active runtime member");

    let members = ctx
        .core
        .list_runtime_members("promo-2026", Some("traffic_analyst"))
        .await
        .expect("list effective members");
    let bot_ids = members
        .iter()
        .map(|member| member.bot_uuid.as_str())
        .collect::<Vec<_>>();
    assert_eq!(bot_ids, vec!["bot-b", "bot-c"]);
}

#[tokio::test]
async fn scoped_a2a_ignores_disabled_binding_and_rejects_missing_org() {
    let ctx = test_context().await;
    let provider_a = register_provider(&ctx, "Provider A").await;
    let provider_b = register_provider(&ctx, "Provider B").await;
    grant_manager(&ctx, &provider_b, &provider_a).await;
    register_bot(&ctx, &provider_a, "bot-a").await;
    register_bot(&ctx, &provider_b, "bot-b").await;
    create_org(&ctx, &provider_a).await;
    for bot_uuid in ["bot-a", "bot-b"] {
        ctx.service
            .put_member(PutOrganizationMemberCommand {
                auth: member_auth(&provider_a),
                organization_code: "promo-2026".to_string(),
                bot_uuid: bot_uuid.to_string(),
                role: None,
            })
            .await
            .expect("add member");
    }

    let missing_org = ctx
        .core
        .require_effective_member("missing-org", "bot-a")
        .await
        .expect_err("missing org must be rejected");
    assert!(matches!(missing_org, ServiceError::InvalidOperation { message, .. } if message.contains("organization 'missing-org' not found")));

    ctx.provider_core
        .set_provider_bot_disabled(
            &provider_b.provider_id,
            "bot-b",
            &provider_b.admin_token,
            true,
        )
        .await
        .expect("disable provider b binding");
    ctx.core
        .authorize_pair("promo-2026", "bot-a", "bot-b")
        .await
        .expect("disabled provider binding must not remove active scoped A2A members");
}

#[tokio::test]
async fn scoped_a2a_allows_sender_to_target_itself_with_one_member_record() {
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

    ctx.core
        .authorize_pair("promo-2026", "bot-a", "bot-a")
        .await
        .expect("sender and target may be the same active member");
}
