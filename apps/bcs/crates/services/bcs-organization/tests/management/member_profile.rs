use super::*;

#[tokio::test]
async fn update_member_profile_merges_allowed_fields_and_preserves_omitted_fields() {
    let ctx = test_context().await;
    let provider = register_provider(&ctx, "Provider A").await;
    register_bot(&ctx, &provider, "bot-a").await;
    create_org(&ctx, &provider).await;
    ctx.service
        .put_member(PutOrganizationMemberCommand {
            auth: member_auth(&provider),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-a".to_string(),
            role: None,
        })
        .await
        .expect("add member");

    let profile = ctx
        .service
        .update_member_profile(UpdateOrganizationMemberProfileCommand {
            auth: member_auth(&provider),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-a".to_string(),
            patch: OrganizationMemberProfilePatch {
                name: Some("Updated Bot".to_string()),
                domains: Some(vec!["engineering".to_string()]),
                skills: Some(vec![Skill::with_description(
                    "code_review",
                    "Reviews code",
                )]),
                ..OrganizationMemberProfilePatch::default()
            },
        })
        .await
        .expect("update member profile");

    assert_eq!(profile.organization_code, "promo-2026");
    assert_eq!(profile.bot_uuid, "bot-a");
    assert_eq!(profile.provider_id, provider.provider_id);
    assert_eq!(profile.capabilities.name.as_deref(), Some("Updated Bot"));
    assert_eq!(profile.capabilities.summary.as_deref(), Some("bot-a summary"));
    assert_eq!(profile.capabilities.domains, vec!["engineering"]);
    assert_eq!(profile.capabilities.skills[0].name, "code_review");
    assert_eq!(
        profile.capabilities.skills[0].description.as_deref(),
        Some("Reviews code")
    );
    assert_eq!(profile.capabilities.scopes, vec!["campaign"]);
    assert_eq!(profile.capabilities.visibility, "protected");

    let persisted = ctx.registry.get("bot-a").await.expect("persisted bot");
    assert_eq!(persisted.capabilities.name.as_deref(), Some("Updated Bot"));
    assert_eq!(persisted.capabilities.summary.as_deref(), Some("bot-a summary"));
    assert_eq!(persisted.capabilities.domains, vec!["engineering"]);
    assert_eq!(persisted.capabilities.scopes, vec!["campaign"]);
    assert_eq!(persisted.capabilities.visibility, "protected");
}

#[tokio::test]
async fn update_member_profile_allows_an_authorized_cross_provider_member() {
    let ctx = test_context().await;
    let manager = register_provider(&ctx, "Manager").await;
    let resource = register_provider(&ctx, "Resource").await;
    grant_manager(&ctx, &resource, &manager).await;
    register_bot(&ctx, &resource, "bot-b").await;
    create_org(&ctx, &manager).await;
    ctx.service
        .put_member(PutOrganizationMemberCommand {
            auth: member_auth(&manager),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-b".to_string(),
            role: None,
        })
        .await
        .expect("add granted member");

    let profile = ctx
        .service
        .update_member_profile(UpdateOrganizationMemberProfileCommand {
            auth: member_auth(&manager),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-b".to_string(),
            patch: OrganizationMemberProfilePatch {
                summary: Some("Managed summary".to_string()),
                ..OrganizationMemberProfilePatch::default()
            },
        })
        .await
        .expect("update granted member");

    assert_eq!(profile.provider_id, resource.provider_id);
    assert_eq!(profile.capabilities.summary.as_deref(), Some("Managed summary"));
}

#[tokio::test]
async fn update_member_profile_rejects_disabled_member_without_mutating_bot() {
    let ctx = test_context().await;
    let provider = register_provider(&ctx, "Provider A").await;
    register_bot(&ctx, &provider, "bot-a").await;
    create_org(&ctx, &provider).await;
    ctx.service
        .put_member(PutOrganizationMemberCommand {
            auth: member_auth(&provider),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-a".to_string(),
            role: None,
        })
        .await
        .expect("add member");
    ctx.service
        .delete_member(member_auth(&provider), "promo-2026", "bot-a")
        .await
        .expect("disable member");

    let error = ctx
        .service
        .update_member_profile(UpdateOrganizationMemberProfileCommand {
            auth: member_auth(&provider),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-a".to_string(),
            patch: OrganizationMemberProfilePatch {
                name: Some("Must Not Persist".to_string()),
                ..OrganizationMemberProfilePatch::default()
            },
        })
        .await
        .expect_err("disabled member must be rejected");

    assert!(matches!(error, ServiceError::Forbidden(reason) if reason == "organization_member_disabled"));
    let bot = ctx.registry.get("bot-a").await.expect("bot unchanged");
    assert_eq!(bot.capabilities.name.as_deref(), Some("bot-a name"));
}

#[tokio::test]
async fn update_member_profile_rejects_revoked_cross_provider_grant() {
    let ctx = test_context().await;
    let manager = register_provider(&ctx, "Manager").await;
    let resource = register_provider(&ctx, "Resource").await;
    grant_manager(&ctx, &resource, &manager).await;
    register_bot(&ctx, &resource, "bot-b").await;
    create_org(&ctx, &manager).await;
    ctx.service
        .put_member(PutOrganizationMemberCommand {
            auth: member_auth(&manager),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-b".to_string(),
            role: None,
        })
        .await
        .expect("add granted member");
    ctx.provider_core
        .update_provider(
            &resource.provider_id,
            &resource.admin_token,
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
        .expect("revoke manager");

    let error = ctx
        .service
        .update_member_profile(UpdateOrganizationMemberProfileCommand {
            auth: member_auth(&manager),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-b".to_string(),
            patch: OrganizationMemberProfilePatch {
                scopes: Some(vec!["forbidden".to_string()]),
                ..OrganizationMemberProfilePatch::default()
            },
        })
        .await
        .expect_err("revoked manager must be rejected");

    assert!(matches!(error, ServiceError::Forbidden(reason) if reason == "organization_provider_grant_required"));
}

#[tokio::test]
async fn update_member_profile_rejects_wrong_manager() {
    let ctx = test_context().await;
    let manager = register_provider(&ctx, "Manager").await;
    let other = register_provider(&ctx, "Other").await;
    register_bot(&ctx, &manager, "bot-a").await;
    create_org(&ctx, &manager).await;
    ctx.service
        .put_member(PutOrganizationMemberCommand {
            auth: member_auth(&manager),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-a".to_string(),
            role: None,
        })
        .await
        .expect("add member");

    let error = ctx
        .service
        .update_member_profile(UpdateOrganizationMemberProfileCommand {
            auth: member_auth(&other),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-a".to_string(),
            patch: OrganizationMemberProfilePatch {
                name: Some("Forbidden".to_string()),
                ..OrganizationMemberProfilePatch::default()
            },
        })
        .await
        .expect_err("wrong manager must be rejected");

    assert!(matches!(error, ServiceError::Forbidden(reason) if reason == "organization_manager_required"));
}

#[tokio::test]
async fn update_member_profile_rejects_disabled_binding_and_deleted_bot() {
    let ctx = test_context().await;
    let provider = register_provider(&ctx, "Provider A").await;
    register_bot(&ctx, &provider, "bot-a").await;
    create_org(&ctx, &provider).await;
    ctx.service
        .put_member(PutOrganizationMemberCommand {
            auth: member_auth(&provider),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-a".to_string(),
            role: None,
        })
        .await
        .expect("add member");
    ctx.provider_core
        .set_provider_bot_disabled(
            &provider.provider_id,
            "bot-a",
            &provider.admin_token,
            true,
        )
        .await
        .expect("disable binding");

    let disabled = ctx
        .service
        .update_member_profile(UpdateOrganizationMemberProfileCommand {
            auth: member_auth(&provider),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-a".to_string(),
            patch: OrganizationMemberProfilePatch {
                name: Some("Forbidden".to_string()),
                ..OrganizationMemberProfilePatch::default()
            },
        })
        .await
        .expect_err("disabled binding must be rejected");
    assert!(matches!(disabled, ServiceError::Forbidden(reason) if reason == "provider_bot_disabled"));

    ctx.provider_core
        .set_provider_bot_disabled(
            &provider.provider_id,
            "bot-a",
            &provider.admin_token,
            false,
        )
        .await
        .expect("enable binding");
    assert!(ctx.registry.soft_delete("bot-a").await);
    let deleted = ctx
        .service
        .update_member_profile(UpdateOrganizationMemberProfileCommand {
            auth: member_auth(&provider),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-a".to_string(),
            patch: OrganizationMemberProfilePatch {
                name: Some("Forbidden".to_string()),
                ..OrganizationMemberProfilePatch::default()
            },
        })
        .await
        .expect_err("deleted bot must be rejected");
    assert!(matches!(deleted, ServiceError::BotNotFound(bot_uuid) if bot_uuid == "bot-a"));
}
