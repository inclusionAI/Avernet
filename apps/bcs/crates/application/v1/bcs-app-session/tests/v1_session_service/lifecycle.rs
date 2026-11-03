use super::*;

#[tokio::test]
async fn create_as_manager_succeeds_and_projects_participants() {
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
            Some("the task"),
        )
        .await;

    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", Some(ParticipantMode::Muted))],
        None,
        Some("session title"),
    )
    .await;

    assert!(outcome.created);
    let detail = outcome.session;
    assert_eq!(detail.group_id, "g1");
    assert_eq!(detail.status, V1SessionStatus::Running);
    assert_eq!(detail.title.as_deref(), Some("session title"));
    // Omitted input stays omitted; shared launch logic does not synthesize it.
    assert_eq!(detail.input, None);
    // Driver (Driver role) + inherited expert from the parent group.
    assert_eq!(detail.participants.len(), 2);
    let expert = detail
        .participants
        .iter()
        .find(|p| p.actor_id == "expert")
        .expect("expert participant");
    assert_eq!(expert.role, ParticipantRole::Worker);
    assert_eq!(expert.mode, ParticipantMode::Auto);
    assert_eq!(expert.name.as_deref(), Some("expert"));
    let driver = detail
        .participants
        .iter()
        .find(|p| p.actor_id == "driver")
        .expect("driver participant");
    assert_eq!(driver.role, ParticipantRole::Driver);
    assert_eq!(driver.mode, ParticipantMode::Auto);
}
#[tokio::test]
async fn create_with_explicit_input_does_not_fall_back() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .store_group("g1", "driver", Some("ignored context"))
        .await;

    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        Some(serde_json::json!({"query": "explicit query"})),
        None,
    )
    .await;

    assert_eq!(
        outcome.session.input,
        Some(serde_json::json!({"query": "explicit query"}))
    );
}
#[tokio::test]
async fn state_machine_service_create_projects_raw_fields_and_run() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture.store_state_machine_group("g1", "driver").await;
    let input = serde_json::json!("run this task");
    let meta = serde_json::json!({
        "callback_target": {"baas_session_id": "baas-1"},
        "channel": {"source": "caller-value"}
    });

    let outcome = fixture
        .service
        .create(CreateSession {
            caller: bot_principal("driver"),
            group_id: "g1".into(),
            title: Some("Invocation".into()),
            kind: Some(SessionKind::ServiceInvocation),
            acting_bot_id: Some("driver".into()),
            creator_role: None,
            message_view_scope: None,
            input: Some(input.clone()),
            meta: Some(meta.clone()),
            context_delivery: None,
        })
        .await
        .expect("StateMachine service invocation");

    assert_eq!(outcome.session.kind, SessionKind::ServiceInvocation);
    assert_eq!(outcome.session.input, Some(input.clone()));
    assert_eq!(outcome.session.meta, Some(meta));
    assert_eq!(
        outcome.session.state_machine_run_id.as_deref(),
        Some("run-1")
    );
    assert_eq!(
        outcome
            .session
            .state_machine_run
            .as_ref()
            .map(|view| view.run.run_id.as_str()),
        Some("run-1")
    );
    let calls = fixture
        .runtime
        .start_calls
        .lock()
        .expect("runtime start lock");
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].input, input);
}
#[tokio::test]
async fn create_as_non_manager_is_forbidden() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture.store_group("g1", "driver", None).await;

    let error = fixture
        .service
        .create(CreateSession {
            caller: bot_principal("outsider"),
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
        .expect_err("non-manager should be forbidden");
    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));
}
#[tokio::test]
async fn create_with_unknown_group_is_not_found() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;

    let error = fixture
        .service
        .create(CreateSession {
            caller: bot_principal("driver"),
            group_id: "missing-group".into(),
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
        .expect_err("unknown group should 404");
    assert_eq!(error.code(), "group_not_found");
}
#[tokio::test]
async fn list_sorts_desc_and_reports_total() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;

    // Create three sessions; sleep briefly so created_at strictly differs
    // and the DESC primary sort (not just the id tie-breaker) is exercised.
    for _ in 0..3 {
        create_session(
            &fixture,
            bot_principal("driver"),
            "g1",
            "driver",
            vec![participant_input("expert", None)],
            None,
            None,
        )
        .await;
        tokio::time::sleep(Duration::from_millis(6)).await;
    }

    let page = SessionService::list(
        &fixture.service,
        ListSessions {
            caller: bot_principal("driver"),
            group_id: "g1".into(),
            view_bot_id: Some("driver".into()),
            offset: 0,
            limit: 10,
            status: None,
        },
    )
    .await
    .expect("list sessions");

    assert_eq!(page.total, 3);
    assert_eq!(page.items.len(), 3);
    // created_at must be non-increasing (DESC).
    for window in page.items.windows(2) {
        assert!(
            window[0].created_at >= window[1].created_at,
            "sessions must be sorted by created_at DESC"
        );
    }
}
#[tokio::test]
async fn list_authorized_human_with_no_participating_sessions_is_empty() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture.store_group("g1", "driver", None).await;

    let error = SessionService::list(
        &fixture.service,
        ListSessions {
            caller: bot_principal("outsider"),
            group_id: "g1".into(),
            view_bot_id: None,
            offset: 0,
            limit: 10,
            status: None,
        },
    )
    .await
    .expect("an authorized Human view with no matching sessions is empty");
    assert_eq!(error.total, 0);
    assert!(error.items.is_empty());
}
#[tokio::test]
async fn session_list_uses_only_the_selected_authorized_view_actor() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "owned", "unowned"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .bots
        .save_created_by("owned", "alice", true)
        .await
        .expect("assign Alice's Bot ownership");
        bcs_test_support::grant_owner_edge(
            &fixture.authority_repo,
            "owned",
            "alice",
        )
        .await;
    fixture.store_group("g1", "driver", None).await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    for participant in ["human_alice", "owned", "unowned"] {
        fixture
            .session_repo
            .create(
                "g1",
                NewSessionParams {
                    participants: vec![if participant.starts_with("human_") {
                        Participant::human(participant, ParticipantRole::Observer)
                    } else {
                        Participant::bot(participant, ParticipantRole::Consultant)
                    }],
                    group_version: Some(group.version),
                    ..Default::default()
                },
            )
            .await
            .expect("seed actor-scoped Session");
    }

    let list = |view_bot_id| ListSessions {
        caller: human_principal("alice"),
        group_id: "g1".into(),
        view_bot_id,
        offset: 0,
        limit: 20,
        status: None,
    };
    let default_human = SessionService::list(&fixture.service, list(None))
        .await
        .expect("omission selects the authenticated Human");
    assert_eq!(default_human.total, 1);
    let explicit_human = SessionService::list(&fixture.service, list(Some("human_alice".into())))
        .await
        .expect("the authenticated Human is a valid explicit view");
    assert_eq!(explicit_human.total, 1);
    let owned_bot = SessionService::list(&fixture.service, list(Some("owned".into())))
        .await
        .expect("an exact-created_by Bot is a valid explicit view");
    assert_eq!(owned_bot.total, 1);

    for invalid_view in ["human_bob", "unowned", "missing"] {
        let error = SessionService::list(&fixture.service, list(Some(invalid_view.into())))
            .await
            .expect_err("an unauthorized explicit view never falls back to Human");
        assert!(matches!(
            error,
            bcs_service_api::application::v1::ApplicationError::Forbidden(_)
        ));
    }
}
#[tokio::test]
async fn session_detail_accepts_human_or_exact_owned_bot_participation_only() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "owned", "unowned"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .bots
        .save_created_by("owned", "alice", true)
        .await
        .expect("assign Alice's Bot ownership");
        bcs_test_support::grant_owner_edge(
            &fixture.authority_repo,
            "owned",
            "alice",
        )
        .await;
    fixture.store_group("g1", "driver", None).await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let mut ids = Vec::new();
    for participant in ["human_alice", "owned", "unowned"] {
        let session = fixture
            .session_repo
            .create(
                "g1",
                NewSessionParams {
                    participants: vec![if participant.starts_with("human_") {
                        Participant::human(participant, ParticipantRole::Observer)
                    } else {
                        Participant::bot(participant, ParticipantRole::Consultant)
                    }],
                    group_version: Some(group.version),
                    ..Default::default()
                },
            )
            .await
            .expect("seed Session");
        ids.push(session.id);
    }

    for session_id in &ids[..2] {
        fixture
            .service
            .get(GetSession {
                caller: human_principal("alice"),
                session_id: session_id.clone(),
            })
            .await
            .expect("Human or exact owned Bot participation grants detail read");
    }
    let error = fixture
        .service
        .get(GetSession {
            caller: human_principal("alice"),
            session_id: ids[2].clone(),
        })
        .await
        .expect_err("an unowned Bot participant grants no detail read");
    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));
}
#[tokio::test]
async fn session_detail_preserves_legacy_json_input_and_metadata() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture.store_group("g1", "driver", None).await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![Participant::bot("driver", ParticipantRole::Driver)],
                group_version: Some(group.version),
                input: Some(serde_json::json!(["legacy", 1])),
                meta: Some(serde_json::json!("legacy-metadata")),
                ..Default::default()
            },
        )
        .await
        .expect("seed legacy-shaped Session");

    let detail = fixture
        .service
        .get(GetSession {
            caller: bot_principal("driver"),
            session_id: session.id,
        })
        .await
        .expect("read legacy-shaped Session through V1");

    assert_eq!(detail.input, Some(serde_json::json!(["legacy", 1])));
    assert_eq!(detail.meta, Some(serde_json::json!("legacy-metadata")));
}
#[tokio::test]
async fn session_detail_propagates_authority_read_failure_as_internal() {
    // Bot-authority plan Task 6.3: the Human
    // participant view consults the authority hook;
    // an authority READ failure surfaces as an
    // internal error — never as a forbidden
    // (deny-as-false masquerade). The read failure
    // is staged as a duplicated approved-owner edge
    // (an authority integrity error).
    let bots = Arc::new(BotCore::with_repo(Arc::new(
        PersistentBotRepo::new(Arc::new(FailingDb)),
    )));
    let fixture = Fixture::new_with_bots(bots).await;
    fixture.store_group("g1", "driver", None).await;
    fixture.grant_owner("owned", "alice").await;
    // A second approved owner on one Bot is an
    // authority integrity failure for every reader:
    // insert the duplicate corrupting edge DIRECTLY
    // (grant_owner_edge deduplicates owners by
    // design).
    {
        use bcs_bot_store::memory_authority::{
            MemoryAuthorityEdgeKind, MemoryAuthorityEdgeStatus,
        };
        // take the FIRST approved owner edge's env
        // prefix (the fixture env).
        let existing = fixture
            .authority_repo
            .authority_state()
            .try_read()
            .expect("authority state lock");
        let env = existing
            .edges
            .keys()
            .find_map(|key| {
                (key.1 == "owned").then(|| key.0.clone())
            })
            .expect("first owner edge");
        drop(existing);
        ensure_human_actor(&fixture, "bob").await;
        let authority = fixture.authority_repo.authority_state();
        let mut authority = authority
            .try_write()
            .expect("authority state lock");
        authority.edges.insert(
            (
                env,
                "owned".to_string(),
                "human_bob".to_string(),
                MemoryAuthorityEdgeKind::Owner,
            ),
            (MemoryAuthorityEdgeStatus::Approved, 0),
        );
    }
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                participants: vec![Participant::bot("owned", ParticipantRole::Consultant)],
                group_version: Some(group.version),
                ..Default::default()
            },
        )
        .await
        .expect("seed Session");

    let error = fixture
        .service
        .get(GetSession {
            caller: human_principal("alice"),
            session_id: session.id,
        })
        .await
        .expect_err("authority failure must not be reported as forbidden");

    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Internal(message)
            if message.contains("bot authority lookup failed")
    ));
}
#[tokio::test]
async fn session_list_still_rejects_a_caller_without_user_identity() {
    let fixture = Fixture::new().await;
    fixture.add_bot("driver").await;
    fixture.store_group("g1", "driver", None).await;
    let error = SessionService::list(
        &fixture.service,
        ListSessions {
            caller: bot_only_caller("driver"),
            group_id: "g1".into(),
            view_bot_id: Some("driver".into()),
            offset: 0,
            limit: 20,
            status: None,
        },
    )
    .await
    .expect_err("the actor-relative list still requires an authenticated User");
    assert!(matches!(
        error,
        bcs_service_api::application::v1::ApplicationError::Forbidden(_)
    ));
}
#[tokio::test]
async fn get_returns_detail_and_not_found_for_missing() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
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
        Some("title"),
    )
    .await;

    let detail = fixture
        .service
        .get(GetSession {
            caller: bot_principal("driver"),
            session_id: outcome.session.session_id.clone(),
        })
        .await
        .expect("get session");
    assert_eq!(detail.session_id, outcome.session.session_id);
    assert_eq!(detail.title.as_deref(), Some("title"));

    let error = fixture
        .service
        .get(GetSession {
            caller: bot_principal("driver"),
            session_id: "g1:deadbeef".into(),
        })
        .await
        .expect_err("missing session should 404");
    assert_eq!(error.code(), "session_not_found");
}
#[tokio::test]
async fn update_changes_title() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
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
        Some("old title"),
    )
    .await;

    let detail = fixture
        .service
        .update(UpdateSession {
            caller: bot_principal("driver"),
            session_id: outcome.session.session_id.clone(),
            title: Some("new title".into()),
        })
        .await
        .expect("update session");
    assert_eq!(detail.title.as_deref(), Some("new title"));
}
#[tokio::test]
async fn update_requires_a_field() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
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
        Some("title"),
    )
    .await;

    let error = fixture
        .service
        .update(UpdateSession {
            caller: bot_principal("driver"),
            session_id: outcome.session.session_id,
            title: None,
        })
        .await
        .expect_err("empty patch should 400");
    assert_eq!(error.code(), "invalid_request");
}
#[tokio::test]
async fn delete_is_idempotent() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
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

    let first = fixture
        .service
        .delete(DeleteSession {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            acting_bot_id: None,
        })
        .await
        .expect("first delete");
    assert!(first.deleted);

    let second = fixture
        .service
        .delete(DeleteSession {
            caller: bot_principal("driver"),
            session_id,
            acting_bot_id: None,
        })
        .await
        .expect("second delete");
    // Idempotent: a missing session yields deleted=false, not a 404.
    assert!(!second.deleted);
}
#[tokio::test]
async fn bot_caller_creates_reads_updates_and_deletes_session() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;
    let caller = bot_only_caller("driver");
    let outcome = create_session(
        &fixture,
        caller.clone(),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        None,
        Some("Bot session"),
    )
    .await;
    let session_id = outcome.session.session_id;

    let detail = fixture
        .service
        .get(GetSession {
            caller: caller.clone(),
            session_id: session_id.clone(),
        })
        .await
        .expect("Bot Principal reads its Session");
    assert_eq!(detail.session_id, session_id);

    let updated = fixture
        .service
        .update(UpdateSession {
            caller: caller.clone(),
            session_id: session_id.clone(),
            title: Some("Bot updated".into()),
        })
        .await
        .expect("Bot Principal updates its Session");
    assert_eq!(updated.title.as_deref(), Some("Bot updated"));

    let deleted = fixture
        .service
        .delete(DeleteSession {
            caller,
            session_id,
            acting_bot_id: Some("driver".into()),
        })
        .await
        .expect("Bot Principal deletes its Session");
    assert!(deleted.deleted);
}
#[tokio::test]
async fn unrelated_bot_cannot_read_update_or_delete_session() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture.store_group("g1", "driver", None).await;
    let outcome = create_session(
        &fixture,
        bot_only_caller("driver"),
        "g1",
        "driver",
        vec![participant_input("expert", None)],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id;

    let read = fixture
        .service
        .get(GetSession {
            caller: bot_only_caller("outsider"),
            session_id: session_id.clone(),
        })
        .await;
    assert!(matches!(read, Err(V1ApplicationError::Forbidden(_))));

    let update = fixture
        .service
        .update(UpdateSession {
            caller: bot_only_caller("outsider"),
            session_id: session_id.clone(),
            title: Some("forged".into()),
        })
        .await;
    assert!(matches!(update, Err(V1ApplicationError::Forbidden(_))));

    let delete = fixture
        .service
        .delete(DeleteSession {
            caller: bot_only_caller("outsider"),
            session_id,
            acting_bot_id: None,
        })
        .await;
    assert!(matches!(delete, Err(V1ApplicationError::Forbidden(_))));
}
#[tokio::test]
async fn complete_is_idempotent() {
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
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

    let first = fixture
        .service
        .complete(CompleteSession {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
        })
        .await
        .expect("first complete");
    assert_eq!(first.status, V1SessionStatus::Completed);
    assert!(first.completed_at > 0);

    let second = fixture
        .service
        .complete(CompleteSession {
            caller: bot_principal("driver"),
            session_id,
        })
        .await
        .expect("second complete (idempotent)");
    assert_eq!(second.status, V1SessionStatus::Completed);
    // Idempotent: same completed_at as the first completion.
    assert_eq!(second.completed_at, first.completed_at);
}
