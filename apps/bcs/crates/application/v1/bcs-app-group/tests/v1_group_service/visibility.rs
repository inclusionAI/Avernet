use super::*;

#[tokio::test]
async fn list_filters_deduplicates_before_pagination_and_direct_wins() {
    let fixture = Fixture::new().await;
    for bot in ["target", "driver-a", "driver-b", "driver-c"] {
        fixture.add_public_bot(bot).await;
    }

    fixture
        .groups
        .upsert(normal_group(
            "direct-chat",
            "driver-a",
            vec![
                Participant::bot("driver-a", ParticipantRole::Driver),
                Participant::bot("target", ParticipantRole::Consultant),
            ],
            GroupStrategy::Chat,
            30,
        ))
        .await
        .expect("store direct");
    fixture
        .groups
        .upsert(normal_group(
            "both-state-machine",
            "driver-b",
            vec![
                Participant::bot("driver-b", ParticipantRole::Driver),
                Participant::bot("target", ParticipantRole::Consultant),
            ],
            GroupStrategy::StateMachine,
            20,
        ))
        .await
        .expect("store both");
    fixture
        .groups
        .upsert(normal_group(
            "session-only-state-machine",
            "driver-c",
            vec![Participant::bot("driver-c", ParticipantRole::Driver)],
            GroupStrategy::StateMachine,
            10,
        ))
        .await
        .expect("store session-only");

    for group_id in ["both-state-machine", "session-only-state-machine"] {
        fixture
            .sessions
            .create_or_reactivate(CreateOrReactivateCommand {
                group_id: group_id.into(),
                session_id: None,
                params: NewSessionParams {
                    participants: vec![Participant::bot("target", ParticipantRole::Consultant)],
                    ..Default::default()
                },
            })
            .await
            .expect("create session");
    }

    let page = fixture
        .service
        .list_groups(ListGroups {
            caller: bot_principal("target"),
            view_bot_id: Some("target".into()),
            offset: 0,
            limit: 10,
            q: None,
            visibility: None,
            membership: MembershipFilter::All,
            kind: GroupKindFilter::All,
            strategy: Some(V1GroupStrategy::StateMachine),
        })
        .await
        .expect("list groups");

    assert_eq!(page.total, 2);
    assert_eq!(page.items.len(), 2);
    match &page.items[0] {
        GroupSummary::Normal(summary) => {
            assert_eq!(summary.group_id, "both-state-machine");
            assert_eq!(summary.membership, Membership::Direct);
        }
        other => panic!("expected normal summary, got {other:?}"),
    }
    match &page.items[1] {
        GroupSummary::Normal(summary) => {
            assert_eq!(summary.group_id, "session-only-state-machine");
            assert_eq!(summary.membership, Membership::SessionOnly);
        }
        other => panic!("expected normal summary, got {other:?}"),
    }

    let session_only = fixture
        .service
        .list_groups(ListGroups {
            caller: bot_principal("target"),
            view_bot_id: Some("target".into()),
            offset: 0,
            limit: 1,
            q: None,
            visibility: None,
            membership: MembershipFilter::SessionOnly,
            kind: GroupKindFilter::All,
            strategy: None,
        })
        .await
        .expect("list session-only");
    assert_eq!(session_only.total, 1);
    assert_eq!(session_only.items.len(), 1);
}
#[tokio::test]
async fn list_groups_filters_by_visibility() {
    let fixture = Fixture::new().await;
    for bot in ["target", "driver"] {
        fixture.add_public_bot(bot).await;
    }

    for (group_id, visibility, created_at) in [
        ("public-newest", "public", 30),
        ("private-middle", "private", 20),
        ("public-oldest", "public", 10),
    ] {
        let mut group = normal_group(
            group_id,
            "driver",
            vec![
                Participant::bot("driver", ParticipantRole::Driver),
                Participant::bot("target", ParticipantRole::Consultant),
            ],
            GroupStrategy::Chat,
            created_at,
        );
        group.visibility = visibility.into();
        fixture.groups.upsert(group).await.expect("store Group");
    }

    let page = fixture
        .service
        .list_groups(ListGroups {
            caller: bot_principal("target"),
            view_bot_id: Some("target".into()),
            offset: 1,
            limit: 1,
            q: None,
            visibility: Some(GroupVisibility::Public),
            membership: MembershipFilter::Direct,
            kind: GroupKindFilter::All,
            strategy: None,
        })
        .await
        .expect("list public Groups");

    assert_eq!(page.total, 2);
    assert_eq!(page.items.len(), 1);
    match &page.items[0] {
        GroupSummary::Normal(summary) => assert_eq!(summary.group_id, "public-oldest"),
        other => panic!("expected normal summary, got {other:?}"),
    }
}
#[tokio::test]
async fn list_session_only_groups_filters_by_visibility() {
    let fixture = Fixture::new().await;
    for bot in ["target", "public-driver", "private-driver"] {
        fixture.add_public_bot(bot).await;
    }

    for (group_id, driver, visibility, created_at) in [
        ("public-session", "public-driver", "public", 20),
        ("private-session", "private-driver", "private", 10),
    ] {
        let mut group = normal_group(
            group_id,
            driver,
            vec![Participant::bot(driver, ParticipantRole::Driver)],
            GroupStrategy::Chat,
            created_at,
        );
        group.visibility = visibility.into();
        fixture.groups.upsert(group).await.expect("store Group");
        fixture
            .sessions
            .create_or_reactivate(CreateOrReactivateCommand {
                group_id: group_id.into(),
                session_id: None,
                params: NewSessionParams {
                    participants: vec![Participant::bot("target", ParticipantRole::Consultant)],
                    ..Default::default()
                },
            })
            .await
            .expect("create Session");
    }

    let page = fixture
        .service
        .list_groups(ListGroups {
            caller: bot_principal("target"),
            view_bot_id: Some("target".into()),
            offset: 0,
            limit: 20,
            q: None,
            visibility: Some(GroupVisibility::Private),
            membership: MembershipFilter::SessionOnly,
            kind: GroupKindFilter::All,
            strategy: None,
        })
        .await
        .expect("list private session-only Groups");

    assert_eq!(page.total, 1);
    assert_eq!(page.items.len(), 1);
    match &page.items[0] {
        GroupSummary::Normal(summary) => assert_eq!(summary.group_id, "private-session"),
        other => panic!("expected normal summary, got {other:?}"),
    }
}
#[tokio::test]
async fn list_defaults_to_the_authenticated_human_and_accepts_only_authorized_views() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("owned-by-someone-else").await;
    fixture
        .bots
        .register(
            "owned-by-alice".into(),
            BotCapabilities {
                visibility: "public".into(),
                ..Default::default()
            },
        )
        .await
        .expect("register Alice's Bot");
    fixture
        .bots
        .save_created_by("owned-by-alice", "alice", true)
        .await
        .expect("assign Alice's Bot ownership");
    // CURRENT owner edge for the alice-owned Bot.
    bcs_test_support::grant_owner_edge(&fixture.authority_repo, "owned-by-alice", "alice")
        .await;
    fixture
        .groups
        .upsert(normal_group(
            "human-group",
            "owned-by-someone-else",
            vec![Participant::human("human_alice", ParticipantRole::Observer)],
            GroupStrategy::Chat,
            1,
        ))
        .await
        .expect("store Human Group");
    fixture
        .groups
        .upsert(normal_group(
            "owned-bot-group",
            "owned-by-someone-else",
            vec![Participant::bot(
                "owned-by-alice",
                ParticipantRole::Consultant,
            )],
            GroupStrategy::Chat,
            1,
        ))
        .await
        .expect("store owned Bot Group");

    let list = |view_bot_id: Option<&str>| ListGroups {
        caller: bot_principal("alice"),
        view_bot_id: view_bot_id.map(str::to_string),
        offset: 0,
        limit: 20,
        q: None,
        visibility: None,
        membership: MembershipFilter::All,
        kind: GroupKindFilter::All,
        strategy: None,
    };

    let default_view = fixture
        .service
        .list_groups(list(None))
        .await
        .expect("omission selects the Human Actor");
    assert_eq!(default_view.total, 1);
    let explicit_human = fixture
        .service
        .list_groups(list(Some("human_alice")))
        .await
        .expect("the caller's Human Actor is an explicit valid view");
    assert_eq!(explicit_human.total, 1);
    let owned_bot = fixture
        .service
        .list_groups(list(Some("owned-by-alice")))
        .await
        .expect("an exact-created_by Bot is an explicit valid view");
    assert_eq!(owned_bot.total, 1);

    for invalid_view in ["human_bob", "owned-by-someone-else", "unknown-bot"] {
        let error = fixture
            .service
            .list_groups(list(Some(invalid_view)))
            .await
            .expect_err("unauthorized View Actor must not fall back");
        assert!(matches!(error, ApplicationError::Forbidden(_)));
    }
}
#[tokio::test]
async fn group_detail_accepts_human_or_exact_owned_bot_participation_only() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "relation-only"] {
        fixture.add_public_bot(bot).await;
    }
    fixture
        .bots
        .register(
            "owned".into(),
            BotCapabilities {
                visibility: "public".into(),
                ..Default::default()
            },
        )
        .await
        .expect("register Alice's Bot");
    fixture
        .bots
        .save_created_by("owned", "alice", true)
        .await
        .expect("assign Alice's Bot ownership");
    bcs_test_support::grant_owner_edge(&fixture.authority_repo, "owned", "alice")
        .await;
    fixture
        .relation
        .upsert_edge(bcs_service_api::RelationEdge {
            from_id: "human_alice".into(),
            to_id: "relation-only".into(),
            env: "dev".into(),
            kinds: 0,
            allow: 0,
            deny: 0,
            is_creator: true,
        })
        .await
        .expect("seed legacy creator relation");

    for (group_id, participant) in [
        (
            "human-detail",
            Participant::human("human_alice", ParticipantRole::Observer),
        ),
        (
            "owned-detail",
            Participant::bot("owned", ParticipantRole::Consultant),
        ),
        (
            "relation-detail",
            Participant::bot("relation-only", ParticipantRole::Consultant),
        ),
    ] {
        fixture
            .groups
            .upsert(normal_group(
                group_id,
                "driver",
                vec![participant],
                GroupStrategy::Chat,
                1,
            ))
            .await
            .expect("store Group");
    }

    for group_id in ["human-detail", "owned-detail"] {
        fixture
            .service
            .get(GetGroup {
                caller: bot_principal("alice"),
                group_id: group_id.into(),
            })
            .await
            .expect("direct Human or exact owned Bot participation grants detail read");
    }
    let error = fixture
        .service
        .get(GetGroup {
            caller: bot_principal("alice"),
            group_id: "relation-detail".into(),
        })
        .await
        .expect_err("creator relation alone is not Bot ownership");
    assert!(matches!(error, ApplicationError::Forbidden(_)));
}
#[tokio::test]
async fn get_public_group_readable_without_participation() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("driver").await;
    let mut group = normal_group(
        "public-plaza-detail",
        "driver",
        vec![Participant::bot("driver", ParticipantRole::Driver)],
        GroupStrategy::Chat,
        1,
    );
    group.visibility = "public".to_string();
    fixture
        .groups
        .upsert(group)
        .await
        .expect("store public Group");

    let detail = fixture
        .service
        .get(GetGroup {
            caller: bot_principal("alice"),
            group_id: "public-plaza-detail".into(),
        })
        .await
        .expect("public Group is readable without participation or owned Bot");
    let GroupDetail::Collaboration(detail) = detail else {
        panic!("expected collaboration detail");
    };
    assert_eq!(detail.group_id, "public-plaza-detail");
    assert!(matches!(detail.visibility, GroupVisibility::Public));
}
#[tokio::test]
async fn group_detail_propagates_authority_read_failure_as_internal() {
    // Bot-authority plan Task 6.3: the Human participant
    // view now consults the authority hook; an authority
    // READ failure must surface as an internal error —
    // never as a forbidden (deny-as-false masquerade).
    // The integrity failure is staged with a duplicated
    // approved-owner edge in the authority store.
    let bots = Arc::new(BotCore::with_repo(Arc::new(
        PersistentBotRepo::new(Arc::new(FailingDb)),
    )));
    let fixture = Fixture::new_with_bots(bots).await;
    fixture
        .groups
        .upsert(normal_group(
            "owned-bot-lookup-failure",
            "driver",
            vec![Participant::bot("owned", ParticipantRole::Consultant)],
            GroupStrategy::Chat,
            1,
        ))
        .await
        .expect("store Group");
    fixture.grant_owner("owned", "alice").await;
    // The duplicate corrupting owner edge is
    // inserted DIRECTLY (grant_owner deduplicates
    // by design; the integrity failure is the
    // staging for the read error).
    {
        use bcs_bot_store::memory_authority::{
            MemoryAuthorityEdgeKind, MemoryAuthorityEdgeStatus,
        };
        let authority = fixture.authority_repo.authority_state();
        let existing = authority.try_read().expect("authority lock");
        let env = existing
            .edges
            .keys()
            .find_map(|key| {
                (key.1 == "owned").then(|| key.0.clone())
            })
            .expect("first owner edge");
        drop(existing);
        ensure_human_actor(&fixture, "bob").await;
        let mut write = authority.try_write().expect("authority write lock");
        write.edges.insert(
            (
                env,
                "owned".to_string(),
                "human_bob".to_string(),
                MemoryAuthorityEdgeKind::Owner,
            ),
            (MemoryAuthorityEdgeStatus::Approved, 0),
        );
    }
    fixture
        .groups
        .upsert(normal_group(
            "owned-bot-lookup-failure",
            "driver",
            vec![Participant::bot("owned", ParticipantRole::Consultant)],
            GroupStrategy::Chat,
            2,
        ))
        .await
        .expect("store Group");

    let error = fixture
        .service
        .get(GetGroup {
            caller: human_principal_with_profile("alice", "alice", None, None),
            group_id: "owned-bot-lookup-failure".into(),
        })
        .await
        .expect_err("authority failure must not be reported as forbidden");

    assert!(matches!(
        error,
        ApplicationError::Internal(message) if message.contains("bot authority lookup failed")
    ));
}
#[tokio::test]
async fn list_groups_sorts_by_created_at_desc_not_updated_at() {
    // V1 contract declares `created_at DESC, group_id ASC`. The legacy
    // `updated_at DESC` ordering would put a recently-edited but older group
    // first, violating the contract. Seed three groups where the sort keys
    // diverge to prove the V1 facade uses `created_at` (with `group_id` as
    // the deterministic tie-breaker).
    let fixture = Fixture::new().await;
    for bot in ["target", "driver-a", "driver-b", "driver-c"] {
        fixture.add_public_bot(bot).await;
    }

    // Group A: oldest by created_at, but most recently edited.
    let mut group_a = normal_group(
        "group-a",
        "driver-a",
        vec![
            Participant::bot("driver-a", ParticipantRole::Driver),
            Participant::bot("target", ParticipantRole::Consultant),
        ],
        GroupStrategy::Chat,
        500,
    );
    group_a.created_at = 100;
    fixture.groups.upsert(group_a).await.expect("store group-a");

    // Group B: newer by created_at, older by updated_at.
    let mut group_b = normal_group(
        "group-b",
        "driver-b",
        vec![
            Participant::bot("driver-b", ParticipantRole::Driver),
            Participant::bot("target", ParticipantRole::Consultant),
        ],
        GroupStrategy::Chat,
        200,
    );
    group_b.created_at = 300;
    fixture.groups.upsert(group_b).await.expect("store group-b");

    // Group C: ties Group B on created_at, lower group_id; proves the
    // `group_id ASC` tie-breaker keeps B before C.
    let mut group_c = normal_group(
        "group-c",
        "driver-c",
        vec![
            Participant::bot("driver-c", ParticipantRole::Driver),
            Participant::bot("target", ParticipantRole::Consultant),
        ],
        GroupStrategy::Chat,
        50,
    );
    group_c.created_at = 300;
    fixture.groups.upsert(group_c).await.expect("store group-c");

    let page = fixture
        .service
        .list_groups(ListGroups {
            caller: bot_principal("target"),
            view_bot_id: Some("target".into()),
            offset: 0,
            limit: 10,
            q: None,
            visibility: None,
            membership: MembershipFilter::All,
            kind: GroupKindFilter::All,
            strategy: None,
        })
        .await
        .expect("list groups");

    assert_eq!(page.total, 3);
    assert_eq!(page.items.len(), 3);
    // created_at DESC (group-b & group-c at 300 before group-a at 100), then
    // group_id ASC tie-breaker (group-b before group-c).
    let ids = page
        .items
        .iter()
        .map(|summary| match summary {
            GroupSummary::Normal(it) => it.group_id.as_str(),
            other => panic!("expected normal summary, got {other:?}"),
        })
        .collect::<Vec<_>>();
    assert_eq!(ids, vec!["group-b", "group-c", "group-a"]);
}
