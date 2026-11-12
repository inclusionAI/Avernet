use super::*;

#[tokio::test]
async fn organization_services_pass_conformance_contracts() {
    let ctx = test_context().await;
    let provider = register_provider(&ctx, "Contract Provider").await;

    bcs_test_support::contract::core::organization_core_service_contract_tests(
        ctx.core.as_ref(),
        &provider.provider_id,
        "core-contract-org",
    )
    .await;
    bcs_test_support::contract::application::organization_management_service_contract_tests(
        &ctx.service,
        provider_auth(&provider),
        bad_provider_auth(&provider),
        "application-contract-org",
    )
    .await;
}

#[tokio::test]
async fn organization_management_authorizes_invocable_member_for_owning_manager() {
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
        .expect("put member");

    let member = ctx
        .service
        .require_invocable_member(provider_auth(&provider), "promo-2026", "bot-a")
        .await
        .expect("authorize invocable member");

    assert_eq!(member.bot_uuid, "bot-a");
    assert!(matches!(
        ctx.service
            .require_invocable_member(bad_provider_auth(&provider), "promo-2026", "bot-a")
            .await,
        Err(ServiceError::Unauthorized(_))
    ));
}
