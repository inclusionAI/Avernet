use super::*;

#[tokio::test]
async fn mine_materializes_the_current_human_before_listing() {
    let fixture = Fixture::new();
    assert!(fixture.repo.get("human_staff-1").await.is_none());

    let mut caller = human_caller("staff-1");
    let user = caller.user.as_mut().expect("human caller");
    user.display_name = Some(" Display Name ".to_string());
    user.full_name = Some("Full Name".to_string());

    let page = fixture
        .service
        .list_mine(ListMyBots {
            caller,
            kind: Some(BotKind::Human),
            name: None,
            status: None,
            reachability: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("list mine");

    assert_eq!(page.total, 1);
    let Bot::Human(human) = &page.items[0] else {
        panic!("expected human actor");
    };
    assert_eq!(human.bot_id, "human_staff-1");
    assert_eq!(human.name, "Display Name");
}
#[tokio::test]
async fn mine_uses_non_empty_identity_name_fallbacks_for_materialized_humans() {
    let fixture = Fixture::new();
    let cases = [
        (
            "staff-display",
            Some(" Display "),
            Some("Full"),
            "username",
            "Display",
        ),
        (
            "staff-full",
            Some("   "),
            Some(" Full "),
            "username",
            "Full",
        ),
        (
            "staff-username",
            None,
            Some("   "),
            " username ",
            "username",
        ),
        ("staff-id", None, None, "   ", "staff-id"),
    ];

    for (staff_no, display_name, full_name, username, expected_name) in cases {
        let mut caller = human_caller(staff_no);
        let user = caller.user.as_mut().expect("human caller");
        user.display_name = display_name.map(str::to_string);
        user.full_name = full_name.map(str::to_string);
        user.username = username.to_string();

        fixture
            .service
            .list_mine(ListMyBots {
                caller,
                kind: Some(BotKind::Human),
                name: None,
                status: None,
                reachability: None,
                offset: 0,
                limit: 20,
            })
            .await
            .expect("list mine");

        let stored = fixture
            .repo
            .get(&format!("human_{staff_no}"))
            .await
            .expect("materialized human actor");
        assert_eq!(stored.capabilities.name.as_deref(), Some(expected_name));
    }
}
#[tokio::test]
async fn mine_validates_pagination_before_materializing_the_human() {
    let fixture = Fixture::new();

    let error = fixture
        .service
        .list_mine(ListMyBots {
            caller: human_caller("invalid"),
            kind: None,
            name: None,
            status: None,
            reachability: None,
            offset: 0,
            limit: 0,
        })
        .await
        .expect_err("invalid pagination must fail");

    assert_eq!(error.code(), "invalid_request");
    assert!(fixture.repo.get("human_invalid").await.is_none());
}
#[tokio::test]
async fn mine_accepts_tenantless_users_and_callers_without_user_are_forbidden() {
    let fixture = Fixture::new();
    fixture
        .add_controlled_bot("reachable", "staff-1", "public", ActorStatus::Online)
        .await;
    fixture
        .add_controlled_bot("unreachable", "staff-1", "public", ActorStatus::Online)
        .await;
    fixture
        .repo
        .register_streaming_connection("reachable".to_string())
        .await
        .expect("connect bot");
    fixture
        .repo
        .ensure_human_actor("staff-1", "Human")
        .await
        .expect("ensure human");

    let mut tenantless_user = human_caller("staff-1");
    tenantless_user.tenant = None;
    let page = fixture
        .service
        .list_mine(ListMyBots {
            caller: tenantless_user,
            kind: None,
            name: None,
            status: None,
            reachability: Some(BotReachability::Reachable),
            offset: 0,
            limit: 1,
        })
        .await
        .expect("list mine");
    assert_eq!(page.total, 1);
    assert_eq!(page.items.len(), 1);
    assert_eq!(page.items[0].bot_id(), "reachable");

    let error = fixture
        .service
        .get(GetBot {
            caller: bot_only_caller("reachable"),
            bot_id: "reachable".to_string(),
        })
        .await
        .expect_err("caller without User must be rejected");
    assert_eq!(error.code(), "forbidden");
}
#[tokio::test]
async fn invalid_application_inputs_use_stable_codes() {
    let fixture = Fixture::new();
    let error = fixture
        .service
        .query(QueryBots {
            caller: human_caller("staff-1"),
            bot_ids: (0..101).map(|index| format!("bot-{index}")).collect(),
        })
        .await
        .expect_err("oversized query must fail");
    assert!(matches!(error, ApplicationError::InvalidInput { .. }));
    assert_eq!(error.code(), "invalid_request");
}
#[tokio::test]
async fn mine_lazily_initializes_ownership_for_legacy_bot() {
    use bcs_service_api::port::repo::BotAuthorityRepoPort;
    use bcs_service_api::types::bot_authority::BotAccessRelation;

    let fixture = Fixture::new();
    // Pre-migration legacy bot: created_by = the caller, version 0,
    // no authority edge — exactly the pre-cutover production shape
    // (plain registration, NOT the harness's owner-edge helper).
    fixture
        .repo
        .register_with_owner_and_token(
            "legacy-bot".to_string(),
            BotCapabilities {
                name: Some("legacy-bot".to_string()),
                visibility: "protected".to_string(),
                ..Default::default()
            },
            "staff-1",
            "legacy-token",
        )
        .await
        .expect("legacy registration");

    // Before the mine call the strict authority read denies (the
    // store flag is off in this harness: no legacy fallback).
    assert_eq!(
        BotAuthorityRepoPort::role(fixture.repo.as_ref(), "human_staff-1", "legacy-bot")
            .await
            .expect("role before"),
        None,
    );

    let page = fixture
        .service
        .list_mine(ListMyBots {
            caller: human_caller("staff-1"),
            kind: None,
            name: None,
            status: None,
            reachability: None,
            offset: 0,
            limit: 50,
        })
        .await
        .expect("mine succeeds");

    // The mine view itself was the migration: the legacy Bot is now
    // initialized with the caller as its owner — so the strict read
    // (no fallback at play) resolves Owner, and the page surfaces the
    // Bot with the owner relation.
    assert_eq!(
        BotAuthorityRepoPort::role(fixture.repo.as_ref(), "human_staff-1", "legacy-bot")
            .await
            .expect("role after"),
        Some(BotAccessRelation::Owner),
    );
    assert!(
        page.items.iter().any(|bot| matches!(bot,
            Bot::Physical(item) if item.bot_id == "legacy-bot")),
        "the migrated bot must show up in mine: {:?}",
        page.items
    );

    // The migration audit records the caller themself as the real
    // initiator (no system/service impersonation).
    let state = fixture.repo.authority_state().read().await;
    let audits: Vec<_> = state.audit
        .iter()
        .filter(|row| row.bot_id == "legacy-bot" && row.action == "initialize")
        .collect();
    assert_eq!(audits.len(), 1, "exactly one initialize audit: {:?}", state.audit);
    assert_eq!(audits[0].actor_kind, "human");
    assert_eq!(audits[0].actor_id, "human_staff-1");
    assert_eq!(audits[0].subject_id, "human_staff-1");
}
#[tokio::test]
async fn mine_never_initializes_the_callers_own_human_actor_row() {
    use bcs_service_api::port::repo::{BotAuthorityRepoPort, BotRepoPort};

    let fixture = Fixture::new();
    assert!(fixture.repo.get("human_staff-1").await.is_none());
    fixture
        .service
        .list_mine(ListMyBots {
            caller: human_caller("staff-1"),
            kind: None,
            name: None,
            status: None,
            reachability: None,
            offset: 0,
            limit: 50,
        })
        .await
        .expect("mine succeeds");

    // The mine call materialized the Human actor row (created_by =
    // the same staff), but the migration sweep must skip it: a Human
    // ACTOR row is never an ownership target — no version and no
    // human→human owner edge.
    assert!(fixture.repo.get("human_staff-1").await.is_some());
    let governance = BotRepoPort::ownership_governance_state(
        fixture.repo.as_ref(),
        "human_staff-1",
    )
    .await
    .expect("governance read")
    .expect("human actor row exists");
    assert_eq!(governance.ownership_version, 0);
    assert_eq!(
        BotAuthorityRepoPort::role(fixture.repo.as_ref(), "human_staff-1", "human_staff-1")
            .await
            .expect("role"),
        None,
        "no human→human owner edge after the first mine call"
    );
}
#[tokio::test]
async fn mine_migration_requires_byte_exact_creator_match() {
    use bcs_service_api::port::repo::{BotAuthorityRepoPort, BotRepoPort};
    use bcs_service_api::types::bot_authority::BotAccessRelation;

    let fixture = Fixture::new();
    // The stored creator's spelling differs from the caller only by
    // case: a case-insensitive candidate read is never authority for
    // minting ownership — the victim must stay unmigrated.
    fixture
        .repo
        .register_with_owner_and_token(
            "victim-bot".to_string(),
            BotCapabilities::default(),
            "Staff-1",
            "victim-token",
        )
        .await
        .expect("victim bot");
    // The control Bot's creator matches byte-for-byte → it migrates.
    fixture
        .repo
        .register_with_owner_and_token(
            "own-bot".to_string(),
            BotCapabilities::default(),
            "staff-1",
            "own-token",
        )
        .await
        .expect("control bot");

    fixture
        .service
        .list_mine(ListMyBots {
            caller: human_caller("staff-1"),
            kind: None,
            name: None,
            status: None,
            reachability: None,
            offset: 0,
            limit: 50,
        })
        .await
        .expect("mine succeeds");

    assert_eq!(
        BotAuthorityRepoPort::role(fixture.repo.as_ref(), "human_staff-1", "own-bot")
            .await
            .expect("control role"),
        Some(BotAccessRelation::Owner),
        "the byte-exact bot migrates"
    );
    assert_eq!(
        BotAuthorityRepoPort::role(fixture.repo.as_ref(), "human_staff-1", "victim-bot")
            .await
            .expect("victim role"),
        None,
        "the case-off bot is never minted to the caller"
    );
    let governance = BotRepoPort::ownership_governance_state(
        fixture.repo.as_ref(),
        "victim-bot",
    )
    .await
    .expect("governance read")
    .expect("victim exists");
    assert_eq!(governance.ownership_version, 0, "victim stays at version 0");
}
#[tokio::test]
async fn mine_migration_progresses_beyond_the_first_hundred() {
    let fixture = Fixture::new();
    for i in 0..105 {
        fixture
            .repo
            .register_with_owner_and_token(
                format!("legacy-{i}"),
                BotCapabilities::default(),
                "staff-9",
                &format!("legacy-token-{i}"),
            )
            .await
            .expect("legacy bot");
    }
    // First call attempts up to the first hundred.
    fixture
        .service
        .list_mine(ListMyBots {
            caller: human_caller("staff-9"),
            kind: None,
            name: None,
            status: None,
            reachability: None,
            offset: 0,
            limit: 1,
        })
        .await
        .expect("first mine");
    assert_eq!(initialized_count(&fixture.repo).await, 100);

    // A later call advances the remaining backlog (never rescans the
    // already-migrated prefix against the cap).
    fixture
        .service
        .list_mine(ListMyBots {
            caller: human_caller("staff-9"),
            kind: None,
            name: None,
            status: None,
            reachability: None,
            offset: 0,
            limit: 1,
        })
        .await
        .expect("second mine");
    assert_eq!(initialized_count(&fixture.repo).await, 105);
}
