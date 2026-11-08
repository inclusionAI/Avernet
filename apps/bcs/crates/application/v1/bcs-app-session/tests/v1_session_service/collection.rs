use super::*;

#[tokio::test]
async fn human_collects_and_uncollects_for_owned_participant_bot_idempotently() {
    let fixture = Fixture::new().await;
    fixture.add_bot("bot-1").await;
    fixture
        .bots
        .save_created_by("bot-1", "owner-1", true)
        .await
        .expect("assign Bot ownership");
        bcs_test_support::grant_owner_edge(
            &fixture.authority_repo,
            "bot-1",
            "owner-1",
        )
        .await;
    fixture
        .store_group_with_originator("g1", "bot-1", "human_owner-1", None)
        .await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![Participant::bot("bot-1", ParticipantRole::Driver)],
                group_version: Some(group.version),
                ..Default::default()
            },
        )
        .await
        .expect("seed Session");

    for _ in 0..2 {
        let result = fixture
            .service
            .collect(CollectSession {
                caller: human_principal("owner-1"),
                session_id: session.id.clone(),
                participant: "bot-1".into(),
            })
            .await
            .expect("owned participant Bot can collect");
        assert_eq!(result.session_id, session.id);
        assert_eq!(result.participant, "bot-1");
        assert!(result.collected);
    }
    let collected = fixture
        .session_repo
        .collected_at_map(&[session.id.as_str()], "bot-1")
        .await;
    assert_eq!(collected.len(), 1);
    assert_eq!(collected[0].0, session.id);

    for _ in 0..2 {
        let result = fixture
            .service
            .uncollect(UncollectSession {
                caller: human_principal("owner-1"),
                session_id: session.id.clone(),
                participant: "bot-1".into(),
            })
            .await
            .expect("owned participant Bot can uncollect idempotently");
        assert_eq!(result.participant, "bot-1");
        assert!(!result.collected);
    }
    assert!(
        fixture
            .session_repo
            .collected_at_map(&[session.id.as_str()], "bot-1")
            .await
            .is_empty()
    );
}
#[tokio::test]
async fn human_collects_as_own_human_actor_idempotently() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    // Register the human's own actor entry (`human_owner-1`) the way
    // `ensure_human_actor` does: actor_kind = Human, created_by = the staff_no.
    fixture
        .bots
        .ensure_human_actor("owner-1", "Owner")
        .await
        .expect("provision human actor");
    fixture
        .store_group_with_originator("g1", "driver", "human_owner-1", None)
        .await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant::human("human_owner-1", ParticipantRole::Observer),
                ],
                group_version: Some(group.version),
                ..Default::default()
            },
        )
        .await
        .expect("seed Session");

    // Collect as the human's own actor id (participant = "human_owner-1").
    for _ in 0..2 {
        let result = fixture
            .service
            .collect(CollectSession {
                caller: human_principal("owner-1"),
                session_id: session.id.clone(),
                participant: "human_owner-1".into(),
            })
            .await
            .expect("human can collect as its own actor");
        assert_eq!(result.session_id, session.id);
        assert_eq!(result.participant, "human_owner-1");
        assert!(result.collected);
    }
    let collected = fixture
        .session_repo
        .collected_at_map(&[session.id.as_str()], "human_owner-1")
        .await;
    assert_eq!(collected.len(), 1);
    assert_eq!(collected[0].0, session.id);

    // Uncollecting as the human actor is idempotent.
    for _ in 0..2 {
        let result = fixture
            .service
            .uncollect(UncollectSession {
                caller: human_principal("owner-1"),
                session_id: session.id.clone(),
                participant: "human_owner-1".into(),
            })
            .await
            .expect("human can uncollect as its own actor");
        assert_eq!(result.participant, "human_owner-1");
        assert!(!result.collected);
    }
    assert!(
        fixture
            .session_repo
            .collected_at_map(&[session.id.as_str()], "human_owner-1")
            .await
            .is_empty()
    );
}
#[tokio::test]
async fn collect_rejects_participant_not_owned_by_authenticated_human() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture
        .bots
        .ensure_human_actor("owner-1", "Owner")
        .await
        .expect("provision authenticated human actor");
    fixture
        .bots
        .ensure_human_actor("other", "Other")
        .await
        .expect("provision a different human actor");
    fixture
        .store_group_with_originator("g1", "driver", "human_owner-1", None)
        .await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant::human("human_owner-1", ParticipantRole::Observer),
                    Participant::human("human_other", ParticipantRole::Observer),
                ],
                group_version: Some(group.version),
                ..Default::default()
            },
        )
        .await
        .expect("seed Session");

    let error = fixture
        .service
        .collect(CollectSession {
            caller: human_principal("owner-1"),
            session_id: session.id.clone(),
            // `human_other` is registered, but not owned by the authenticated
            // human (`owner-1`); collecting on its behalf must be forbidden.
            participant: "human_other".into(),
        })
        .await
        .expect_err("must not collect as an actor the human does not own");
    assert!(
        matches!(
            error,
            bcs_service_api::application::v1::ApplicationError::Forbidden(_)
        ),
        "expected forbidden, got {error:?}"
    );
}
#[tokio::test]
async fn list_surfaces_per_session_collected_for_explicit_view_actor() {
    let fixture = Fixture::new().await;
    fixture.add_bot("bot-1").await;
    fixture
        .bots
        .save_created_by("bot-1", "owner-1", true)
        .await
        .expect("assign Bot ownership");
        bcs_test_support::grant_owner_edge(
            &fixture.authority_repo,
            "bot-1",
            "owner-1",
        )
        .await;
    fixture
        .store_group_with_originator("g1", "bot-1", "human_owner-1", None)
        .await;
    let group = fixture.groups.get("g1").await.expect("group exists");

    // Seed two sessions both listing bot-1 as a participant.
    let mk = || Participant::bot("bot-1", ParticipantRole::Driver);
    let s_a = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![mk()],
                group_version: Some(group.version),
                ..Default::default()
            },
        )
        .await
        .expect("seed session A");
    let s_b = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![mk()],
                group_version: Some(group.version),
                ..Default::default()
            },
        )
        .await
        .expect("seed session B");

    // Collect only session A as the owned participant bot.
    fixture
        .service
        .collect(CollectSession {
            caller: human_principal("owner-1"),
            session_id: s_a.id.clone(),
            participant: "bot-1".into(),
        })
        .await
        .expect("collect session A");

    let page = SessionService::list(
        &fixture.service,
        ListSessions {
            caller: human_principal("owner-1"),
            group_id: "g1".into(),
            view_bot_id: Some("bot-1".into()),
            offset: 0,
            limit: 10,
            status: None,
        },
    )
    .await
    .expect("list sessions");

    let by_id = page
        .items
        .iter()
        .map(|s| (s.session_id.as_str(), s.collected))
        .collect::<std::collections::HashMap<&str, Option<bool>>>();
    // The explicitly named view actor sees its per-session collected state.
    assert_eq!(by_id.get(s_a.id.as_str()), Some(&Some(true)));
    assert_eq!(by_id.get(s_b.id.as_str()), Some(&Some(false)));

    // When no view actor is named the field is left absent (None). Here the
    // human views as themselves, so the bot-participant sessions are filtered
    // out and the page is empty — confirming collected is not synthesized.
    let none_page = SessionService::list(
        &fixture.service,
        ListSessions {
            caller: human_principal("owner-1"),
            group_id: "g1".into(),
            view_bot_id: None,
            offset: 0,
            limit: 10,
            status: None,
        },
    )
    .await
    .expect("list sessions (no view actor)");
    assert!(
        none_page.items.iter().all(|s| s.collected.is_none()),
        "collected must be absent when view_bot_id is not specified"
    );
}
#[tokio::test]
async fn session_collection_rejects_an_unowned_bot() {
    let fixture = Fixture::new().await;
    fixture.add_bot("bot-1").await;
    fixture
        .bots
        .save_created_by("bot-1", "owner-1", true)
        .await
        .expect("assign Bot ownership");
        bcs_test_support::grant_owner_edge(
            &fixture.authority_repo,
            "bot-1",
            "owner-1",
        )
        .await;

    let error = fixture
        .service
        .collect(CollectSession {
            caller: human_principal("owner-2"),
            session_id: "session-1".into(),
            participant: "bot-1".into(),
        })
        .await
        .expect_err("another Human cannot collect for this Bot");
    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));
}
#[tokio::test]
async fn session_collection_hides_an_owned_bot_membership_miss() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "bot-1"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .bots
        .save_created_by("bot-1", "owner-1", true)
        .await
        .expect("assign Bot ownership");
        bcs_test_support::grant_owner_edge(
            &fixture.authority_repo,
            "bot-1",
            "owner-1",
        )
        .await;
    fixture.store_group("g1", "driver", None).await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![Participant::bot("driver", ParticipantRole::Driver)],
                group_version: Some(group.version),
                ..Default::default()
            },
        )
        .await
        .expect("seed Session");

    let error = fixture
        .service
        .collect(CollectSession {
            caller: human_principal("owner-1"),
            session_id: session.id,
            participant: "bot-1".into(),
        })
        .await
        .expect_err("a non-participant is hidden as a missing Session");
    assert_eq!(error.code(), "session_not_found");
}
#[tokio::test]
async fn session_collection_returns_not_found_for_a_missing_session() {
    let fixture = Fixture::new().await;
    fixture.add_bot("bot-1").await;
    fixture
        .bots
        .save_created_by("bot-1", "owner-1", true)
        .await
        .expect("assign Bot ownership");
        bcs_test_support::grant_owner_edge(
            &fixture.authority_repo,
            "bot-1",
            "owner-1",
        )
        .await;

    let error = fixture
        .service
        .collect(CollectSession {
            caller: human_principal("owner-1"),
            session_id: "missing".into(),
            participant: "bot-1".into(),
        })
        .await
        .expect_err("missing Session is rejected");
    assert_eq!(error.code(), "session_not_found");
}
#[tokio::test]
async fn session_collection_accepts_only_a_human_identity() {
    let fixture = Fixture::new().await;
    fixture.add_bot("bot-1").await;
    fixture
        .bots
        .save_created_by("bot-1", "owner-1", true)
        .await
        .expect("assign Bot ownership");
        bcs_test_support::grant_owner_edge(
            &fixture.authority_repo,
            "bot-1",
            "owner-1",
        )
        .await;

    for caller in [bot_only_caller("bot-1"), app_only_caller()] {
        let error = fixture
            .service
            .collect(CollectSession {
                caller,
                session_id: "session-1".into(),
                participant: "bot-1".into(),
            })
            .await
            .expect_err("Bot-only and App-only callers are rejected");
        assert!(matches!(
            error,
            bcs_service_api::application::v1::ApplicationError::Forbidden(_)
        ));
    }
}
