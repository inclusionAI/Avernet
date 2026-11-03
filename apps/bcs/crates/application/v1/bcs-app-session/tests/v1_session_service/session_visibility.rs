use super::*;

#[tokio::test]
async fn complete_service_invocation_session_rejected() {
    // VaGQN: ServiceInvocation sessions have their own callback/output
    // lifecycle and must not be completed via this V1 endpoint (legacy
    // handler rejects "service sessions cannot be completed via this
    // endpoint"). Gate the CAS with a session_kind check.
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;

    // The V1 facade `create` hardcodes SessionKind::Chat, so seed a
    // ServiceInvocation session directly via the repo.
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                session_kind: SessionKind::ServiceInvocation,
                participants: vec![Participant::bot("driver", ParticipantRole::Driver)],
                group_version: Some(group.version),
                caller_id: Some("driver".to_string()),
                caller_principal: Some("driver".to_string()),
                created_by: Some("driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed service invocation session");
    assert_eq!(session.session_kind, SessionKind::ServiceInvocation);

    let error = fixture
        .service
        .complete(CompleteSession {
            caller: bot_principal("driver"),
            session_id: session.id,
        })
        .await
        .expect_err("service sessions cannot be completed via V1");

    assert!(
        matches!(
            error,
            bcs_service_api::application::v1::ApplicationError::Conflict { .. }
        ),
        "expected Conflict, got {error:?}",
    );
    assert_eq!(error.code(), "conflict");
}
#[tokio::test]
async fn session_only_participant_can_list_sessions() {
    // VaGQQ: a Bot that is only a session participant (added to a session
    // but NOT to group.participants) must still be able to list the group's
    // sessions. The sibling bcs-app-group facade already permits this via
    // `list_group_ids_by_session_participant`; the session-v1 facade's
    // `can_read_group` must mirror that check.
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert", "newcomer"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    // Add newcomer to the SESSION (not the group). newcomer is public so the
    // collaboration-eligibility check passes; the driver is the manager.
    fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "newcomer".into(),
            message_view_scope: None,
        })
        .await
        .expect("add newcomer to session");

    // Guard: newcomer must be session-only (not in group.participants).
    let group = fixture.groups.get("g1").await.expect("group exists");
    assert!(
        !group.participants.iter().any(|p| p.bot_uuid == "newcomer"),
        "newcomer must be session-only (not in group.participants)"
    );

    // Session-only participant may list the group's sessions.
    let page = SessionService::list(
        &fixture.service,
        ListSessions {
            caller: bot_principal("newcomer"),
            group_id: "g1".into(),
            view_bot_id: Some("newcomer".into()),
            offset: 0,
            limit: 10,
            status: None,
        },
    )
    .await
    .expect("session-only participant may list sessions");
    assert_eq!(page.total, 1);
    assert_eq!(page.items.len(), 1);
    assert_eq!(page.items[0].group_id, "g1");
}
#[tokio::test]
async fn session_only_participant_list_sessions_scoped() {
    // Vcj5: a session-only Bot (in `session.participants` but NOT in
    // `group.participants`) must see ONLY the sessions it participates in
    // when calling `list_sessions` — not the entire group session pool. The
    // prior VaGQQ fix let a session-only Bot pass `can_read_group`, but the
    // list + count calls still passed `participant_id=None`, surfacing ALL
    // group sessions. The V1 facade now scopes both `list_by_group` and
    // `count_by_group` to `Some(principal.actor_id())` when access derives
    // solely from session membership.
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert", "expert2", "expert3", "newcomer"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;

    // S1: driver + expert. Add newcomer to S1 only (session-only for
    // newcomer — newcomer is NOT a group.participants member).
    let s1 = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        None,
        Some("S1"),
    )
    .await;
    let s1_id = s1.session.session_id.clone();
    fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: bot_principal("driver"),
            session_id: s1_id.clone(),
            bot_uuid: "newcomer".into(),
            message_view_scope: None,
        })
        .await
        .expect("add newcomer to S1");

    // S2 + S3: driver + a different expert; newcomer is NOT a participant.
    let s2 = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert2", None)],
        None,
        Some("S2"),
    )
    .await;
    let s3 = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert3", None)],
        None,
        Some("S3"),
    )
    .await;

    // Guard: newcomer is session-only (in S1.participants, NOT in
    // group.participants).
    let group = fixture.groups.get("g1").await.expect("group exists");
    assert!(
        !group.participants.iter().any(|p| p.bot_uuid == "newcomer"),
        "newcomer must be session-only (not in group.participants)"
    );

    // Session-only Bot lists g1 sessions → must see ONLY S1, not S2 / S3.
    let page = SessionService::list(
        &fixture.service,
        ListSessions {
            caller: bot_principal("newcomer"),
            group_id: "g1".into(),
            view_bot_id: Some("newcomer".into()),
            offset: 0,
            limit: 10,
            status: None,
        },
    )
    .await
    .expect("session-only participant lists scoped sessions");
    assert_eq!(page.total, 1, "total must reflect scoping (only S1)");
    assert_eq!(page.items.len(), 1, "items must contain only S1");
    assert_eq!(page.items[0].session_id, s1_id);
    assert_ne!(page.items[0].session_id, s2.session.session_id);
    assert_ne!(page.items[0].session_id, s3.session.session_id);
}
#[tokio::test]
async fn create_session_inherits_parent_group_participants_without_request_roster() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["expert"],
            "human_driver",
            None,
        )
        .await;
    let mut group = fixture.groups.get("g1").await.expect("group exists");
    group
        .participants
        .iter_mut()
        .find(|participant| participant.bot_uuid == "expert")
        .expect("expert Group participant")
        .tags = vec!["tenant-a".to_string(), "scene-review".to_string()];
    group.participants.push(Participant::human(
        "human_driver",
        ParticipantRole::Observer,
    ));
    fixture
        .groups
        .upsert(group)
        .await
        .expect("store Human participant");

    let outcome = fixture
        .service
        .create(CreateSession {
            caller: bot_principal("driver"),
            group_id: "g1".into(),
            title: None,
            kind: None,
            acting_bot_id: None,
            creator_role: None,
            message_view_scope: None,
            input: None,
            meta: None,
            context_delivery: None,
        })
        .await
        .expect("session should inherit parent group roster");

    assert!(
        outcome
            .session
            .participants
            .iter()
            .any(|p| p.actor_id == "expert")
    );
    let inherited_human = outcome
        .session
        .participants
        .iter()
        .find(|p| p.actor_id == "human_driver")
        .expect("Human Group participant should be inherited into Session");
    assert_eq!(inherited_human.actor_kind, ActorKind::Human);
    assert!(
        outcome
            .session
            .participants
            .iter()
            .any(|p| p.actor_id == "driver" && p.role == ParticipantRole::Driver)
    );
    let inherited_expert = outcome
        .session
        .participants
        .iter()
        .find(|participant| participant.actor_id == "expert")
        .expect("expert Group participant should be inherited into Session");
    assert_eq!(
        inherited_expert.tags,
        vec!["tenant-a".to_string(), "scene-review".to_string()]
    );
}
