use super::*;

#[tokio::test]
async fn candidate_bots_include_manager_and_granted_bots_without_leaking_ungranted_provider_ids() {
    let ctx = test_context().await;
    let provider_a = register_provider(&ctx, "Provider A").await;
    let provider_b = register_provider(&ctx, "Provider B").await;
    let provider_c = register_provider(&ctx, "Provider C").await;
    grant_manager(&ctx, &provider_b, &provider_a).await;
    register_bot(&ctx, &provider_a, "bot-a").await;
    register_bot(&ctx, &provider_b, "bot-b").await;
    register_bot(&ctx, &provider_c, "bot-c").await;
    create_org(&ctx, &provider_a).await;

    let candidates = ctx
        .service
        .candidate_bots(provider_auth(&provider_a), candidate_query())
        .await
        .expect("candidate bots");
    let mut bot_ids = candidates
        .iter()
        .map(|candidate| candidate.bot_uuid.as_str())
        .collect::<Vec<_>>();
    bot_ids.sort();
    assert_eq!(bot_ids, vec!["bot-a", "bot-b"]);
    assert!(candidates.iter().all(|candidate| {
        candidate.provider_id == provider_a.provider_id || candidate.provider_id == provider_b.provider_id
    }));
    assert!(!candidates
        .iter()
        .any(|candidate| candidate.provider_id == provider_c.provider_id));

    let filtered = ctx
        .service
        .candidate_bots(
            provider_auth(&provider_a),
            OrganizationCandidateQuery {
                q: Some("bot-b name".to_string()),
                ..candidate_query()
            },
        )
        .await
        .expect("filtered candidate bots");
    assert_eq!(filtered.len(), 1);
    assert_eq!(filtered[0].bot_uuid, "bot-b");

    // An explicit provider_id narrows to that one authorized resource provider.
    let narrowed = ctx
        .service
        .candidate_bots(
            provider_auth(&provider_a),
            OrganizationCandidateQuery {
                provider_id: Some(provider_b.provider_id.clone()),
                ..candidate_query()
            },
        )
        .await
        .expect("narrowed candidate bots");
    assert_eq!(narrowed.len(), 1);
    assert_eq!(narrowed[0].bot_uuid, "bot-b");
    assert_eq!(narrowed[0].provider_id, provider_b.provider_id);

    // A provider_id outside the manager's authorized set is rejected (403),
    // not silently returned as an empty list.
    let unauthorized = ctx
        .service
        .candidate_bots(
            provider_auth(&provider_a),
            OrganizationCandidateQuery {
                provider_id: Some(provider_c.provider_id.clone()),
                ..candidate_query()
            },
        )
        .await;
    match unauthorized {
        Err(ServiceError::Forbidden(reason)) => {
            assert_eq!(reason, "organization_manager_not_authorized");
        }
        other => panic!(
            "unauthorized provider_id should be rejected with 403, got {other:?}"
        ),
    }
}

#[tokio::test]
async fn candidate_bot_page_is_sorted_and_counts_before_paging() {
    let ctx = test_context().await;
    let provider_a = register_provider(&ctx, "Provider A").await;
    let provider_b = register_provider(&ctx, "Provider B").await;
    grant_manager(&ctx, &provider_b, &provider_a).await;
    register_bot(&ctx, &provider_b, "bot-z").await;
    register_bot(&ctx, &provider_a, "bot-a").await;
    create_org(&ctx, &provider_a).await;

    let page = ctx
        .service
        .candidate_bots_page(
            provider_auth(&provider_a),
            OrganizationCandidatePageQuery {
                candidate: candidate_query(),
                offset: 1,
                limit: 1,
            },
        )
        .await
        .expect("candidate bot page");

    assert_eq!(page.total, 2);
    assert_eq!(page.bots.len(), 1);
    assert_eq!(page.bots[0].bot_uuid, "bot-z");
}

#[tokio::test]
async fn candidate_bot_page_excludes_active_members_before_paging() {
    let ctx = test_context().await;
    let provider_a = register_provider(&ctx, "Provider A").await;
    register_bot(&ctx, &provider_a, "bot-a").await;
    register_bot(&ctx, &provider_a, "bot-z").await;
    create_org(&ctx, &provider_a).await;
    ctx.service
        .put_member(PutOrganizationMemberCommand {
            auth: member_auth(&provider_a),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-a".to_string(),
            role: None,
        })
        .await
        .expect("add active member");

    let page = ctx
        .service
        .candidate_bots_page(
            provider_auth(&provider_a),
            OrganizationCandidatePageQuery {
                candidate: OrganizationCandidateQuery {
                    organization_code: "promo-2026".to_string(),
                    ..OrganizationCandidateQuery::default()
                },
                offset: 0,
                limit: 1,
            },
        )
        .await
        .expect("candidate bot page");

    assert_eq!(page.total, 1);
    assert_eq!(page.bots.len(), 1);
    assert_eq!(page.bots[0].bot_uuid, "bot-z");
}

#[tokio::test]
async fn candidate_bot_detail_reports_active_and_disabled_membership() {
    let ctx = test_context().await;
    let provider_a = register_provider(&ctx, "Provider A").await;
    register_bot(&ctx, &provider_a, "bot-a").await;
    create_org(&ctx, &provider_a).await;

    let candidate = ctx
        .service
        .candidate_bot_detail(provider_auth(&provider_a), "promo-2026", "bot-a")
        .await
        .expect("candidate detail")
        .expect("candidate exists");
    assert!(!candidate.is_member);
    assert_eq!(candidate.organization_code, "promo-2026");
    assert_eq!(candidate.bot_uuid, "bot-a");
    assert_eq!(candidate.bot.provider_id, provider_a.provider_id);
    assert_eq!(candidate.bot.capabilities.name.as_deref(), Some("bot-a name"));

    ctx.service
        .put_member(PutOrganizationMemberCommand {
            auth: member_auth(&provider_a),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-a".to_string(),
            role: None,
        })
        .await
        .expect("add member");
    let member = ctx
        .service
        .candidate_bot_detail(provider_auth(&provider_a), "promo-2026", "bot-a")
        .await
        .expect("candidate detail")
        .expect("candidate exists");
    assert!(member.is_member);

    ctx.service
        .delete_member(member_auth(&provider_a), "promo-2026", "bot-a")
        .await
        .expect("disable member");
    let removed = ctx
        .service
        .candidate_bot_detail(provider_auth(&provider_a), "promo-2026", "bot-a")
        .await
        .expect("candidate detail")
        .expect("candidate exists");
    assert!(!removed.is_member);
}

#[tokio::test]
async fn member_page_for_manager_forwards_query_to_repository_page() {
    let ctx = test_context().await;
    let provider_a = register_provider(&ctx, "Provider A").await;
    for bot_uuid in ["bot-c", "bot-a", "bot-b"] {
        register_bot(&ctx, &provider_a, bot_uuid).await;
    }
    create_org(&ctx, &provider_a).await;
    for bot_uuid in ["bot-c", "bot-a", "bot-b"] {
        ctx.service
            .put_member(PutOrganizationMemberCommand {
                auth: member_auth(&provider_a),
                organization_code: "promo-2026".to_string(),
                bot_uuid: bot_uuid.to_string(),
                role: Some("traffic_analyst".to_string()),
            })
            .await
            .expect("add member");
    }

    let page = ctx
        .service
        .list_members_page(
            member_auth(&provider_a),
            "promo-2026",
            OrganizationMemberPageQuery {
                include_disabled: false,
                role: Some("traffic_analyst".to_string()),
                offset: 1,
                limit: 1,
            },
        )
        .await
        .expect("list member page");

    assert_eq!(page.total, 3);
    assert_eq!(page.offset, 1);
    assert_eq!(page.limit, 1);
    assert_eq!(
        page.members.iter().map(|member| member.bot_uuid.as_str()).collect::<Vec<_>>(),
        vec!["bot-b"]
    );
}
