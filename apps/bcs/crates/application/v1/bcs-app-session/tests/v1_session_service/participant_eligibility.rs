use super::*;

// ── VfhG3: derive session participant role from parent group ───────────────
//
// create_session / add_participant previously hardcoded
// ParticipantRole::Consultant for every participant, losing the Worker /
// Manager role carried by the parent group's roster. The V1 facade now derives
// the role from group.participants first, then falls back to the strategy
// default (ManagerWorker→Worker, else Consultant), mirroring the legacy
// bcs-http handler which cloned group.participants. add_participant also
// surfaces an explicit `participant_already_exists` 409 (the legacy memory repo
// silently skipped duplicates).

#[tokio::test]
async fn create_session_preserves_manager_worker_worker_role() {
    // VfhG3: when the parent group is ManagerWorker and a participant Bot is a
    // Worker in group.participants, create_session must derive the Worker role
    // for the session participant rather than hardcoding Consultant.
    let fixture = Fixture::new().await;
    for bot in ["driver", "worker"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["worker"],
            "human_driver",
            Some("the task"),
        )
        .await;

    let outcome = create_session(
        &fixture,
        bot_principal("driver"),
        "g1",
        "driver",
        vec![participant_input("worker", None)],
        None,
        None,
    )
    .await;

    let worker = outcome
        .session
        .participants
        .iter()
        .find(|p| p.actor_id == "worker")
        .expect("worker participant projected");
    assert_eq!(
        worker.role,
        ParticipantRole::Worker,
        "VfhG3: ManagerWorker Worker must keep Worker role in session, not Consultant"
    );
    assert_eq!(worker.mode, ParticipantMode::Auto);

    // The driver is still forced to the Driver role regardless of derivation.
    let driver = outcome
        .session
        .participants
        .iter()
        .find(|p| p.actor_id == "driver")
        .expect("driver participant projected");
    assert_eq!(driver.role, ParticipantRole::Driver);
}
#[tokio::test]
async fn create_session_defaults_consultant_for_chat_context() {
    // VfhG3: a Bot NOT present in the parent group.participants (Chat strategy)
    // falls back to the strategy default (Chat → Consultant), preserving the
    // pre-VfhG3 behaviour for the common Chat case.
    let fixture = Fixture::new().await;
    for bot in ["driver", "expert"] {
        fixture.add_bot(bot).await;
    }
    let mut group = Group::new(
        "g1",
        "driver",
        vec![
            Participant::bot("driver", ParticipantRole::Driver),
            Participant::bot("expert", ParticipantRole::Consultant),
        ],
    );
    group.originator = Some("human_driver".into());
    group.group_strategy = GroupStrategy::Chat;
    fixture.groups.upsert(group).await.expect("store group");

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

    let expert = outcome
        .session
        .participants
        .iter()
        .find(|p| p.actor_id == "expert")
        .expect("expert participant projected");
    assert_eq!(
        expert.role,
        ParticipantRole::Consultant,
        "VfhG3: Chat group + non-roster participant defaults to Consultant"
    );
}
#[tokio::test]
async fn add_participant_duplication_rejects_409() {
    // VfhG3: adding the same Bot to a session twice must reject the second call
    // with a 409 Conflict carrying the explicit `participant_already_exists`
    // code (the legacy memory repo silently skipped duplicates).
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

    // First add of newcomer succeeds.
    fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "newcomer".into(),
            message_view_scope: None,
        })
        .await
        .expect("first add of newcomer");

    // Second add of newcomer is rejected with participant_already_exists.
    let error = fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: bot_principal("driver"),
            session_id: session_id.clone(),
            bot_uuid: "newcomer".into(),
            message_view_scope: None,
        })
        .await
        .expect_err("duplicate add should reject with participant_already_exists");
    assert!(
        matches!(
            error,
            bcs_service_api::application::v1::ApplicationError::Conflict { ref code, .. }
                if code == "participant_already_exists"
        ),
        "expected Conflict(participant_already_exists), got {error:?}",
    );
    assert_eq!(error.code(), "participant_already_exists");
}
#[tokio::test]
async fn add_participant_derives_worker_role_for_manager_worker() {
    // VfhG3: when the parent group is ManagerWorker and the added Bot is a
    // Worker in group.participants, add_participant must derive the Worker role
    // rather than hardcoding Consultant.
    let fixture = Fixture::new().await;
    for bot in ["driver", "worker", "expert"] {
        fixture.add_bot(bot).await;
    }
    fixture
        .store_manager_worker_group_with_originator(
            "g1",
            "driver",
            &["worker"],
            "human_driver",
            None,
        )
        .await;
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
    let worker = outcome
        .session
        .participants
        .iter()
        .find(|p| p.actor_id == "worker")
        .expect("worker participant inherited");

    assert_eq!(
        worker.role,
        ParticipantRole::Worker,
        "VfhG3: ManagerWorker Worker gains Worker role on add_participant, not Consultant"
    );
    assert_eq!(worker.mode, ParticipantMode::Auto);
}
#[tokio::test]
async fn add_participant_admits_protected_bot_reachable_from_driver_friend() {
    // Protected Bot not owned by / friends with the caller, but friends with the
    // group driver → admitted via the driver anchor.
    let fixture = eligibility_fixture("g1", "driver-bot", "human_manager").await;
    fixture.add_bot_with("px", "protected", "other-owner").await;
    fixture.befriend("driver-bot", "px").await;

    let outcome = create_session(
        &fixture,
        human_principal("manager"),
        "g1",
        "driver-bot",
        vec![],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    let added = fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: human_principal("manager"),
            session_id: session_id.clone(),
            bot_uuid: "px".into(),
            message_view_scope: None,
        })
        .await
        .expect("driver-friend Bot should be admitted");
    assert_eq!(added.actor_id, "px");
}
#[tokio::test]
async fn add_participant_rejects_protected_bot_unreachable_from_all_anchors() {
    // Protected Bot reachable from none of caller/driver/originator → 403.
    let fixture = eligibility_fixture("g1", "driver-bot", "human_manager").await;
    fixture.add_bot_with("px", "protected", "other-owner").await;

    let outcome = create_session(
        &fixture,
        human_principal("manager"),
        "g1",
        "driver-bot",
        vec![],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    let error = fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: human_principal("manager"),
            session_id: session_id.clone(),
            bot_uuid: "px".into(),
            message_view_scope: None,
        })
        .await
        .expect_err("unreachable Bot should be rejected");
    assert!(
        matches!(
            error,
            bcs_service_api::application::v1::ApplicationError::Forbidden(_)
        ),
        "expected Forbidden, got {error:?}",
    );
    assert_eq!(error.code(), "forbidden");
}
#[tokio::test]
async fn add_participant_admits_protected_bot_owned_by_caller() {
    // Protected Bot whose `created_by` matches the Human caller → admitted via
    // the caller (Human) anchor's ownership rule.
    let fixture = eligibility_fixture("g1", "driver-bot", "human_manager").await;
    fixture.add_bot_with("px", "protected", "manager").await;

    let outcome = create_session(
        &fixture,
        human_principal("manager"),
        "g1",
        "driver-bot",
        vec![],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    let added = fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: human_principal("manager"),
            session_id: session_id.clone(),
            bot_uuid: "px".into(),
            message_view_scope: None,
        })
        .await
        .expect("caller-owned protected Bot should be admitted");
    assert_eq!(added.actor_id, "px");
}
#[tokio::test]
async fn add_participant_admits_protected_bot_reachable_from_originator_friend() {
    // Distinct Bot originator (not the driver) is the only anchor that reaches
    // the target → admitted via the originator anchor, proving the anchor set is
    // not collapsed to caller+driver only.
    let fixture = eligibility_fixture("g1", "driver-bot", "originator-bot").await;
    fixture.add_bot_with("px", "protected", "other-owner").await;
    fixture.befriend("originator-bot", "px").await;

    let outcome = create_session(
        &fixture,
        human_principal("manager"),
        "g1",
        "driver-bot",
        vec![],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    let added = fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: human_principal("manager"),
            session_id: session_id.clone(),
            bot_uuid: "px".into(),
            message_view_scope: None,
        })
        .await
        .expect("originator-friend Bot should be admitted");
    assert_eq!(added.actor_id, "px");
}
#[tokio::test]
async fn add_participant_rejects_hidden_bot_regardless_of_anchors() {
    // A Hidden Bot is rejected outright before any anchor is consulted, even when
    // the driver is its friend.
    let fixture = eligibility_fixture("g1", "driver-bot", "human_manager").await;
    fixture.add_bot_with("px", "protected", "other-owner").await;
    fixture.befriend("driver-bot", "px").await;
    fixture
        .bots
        .update_actor_status("px", ActorStatus::Hidden)
        .await
        .expect("hide bot");

    let outcome = create_session(
        &fixture,
        human_principal("manager"),
        "g1",
        "driver-bot",
        vec![],
        None,
        None,
    )
    .await;
    let session_id = outcome.session.session_id.clone();

    let error = fixture
        .service
        .add_participant(AddSessionParticipant {
            caller: human_principal("manager"),
            session_id: session_id.clone(),
            bot_uuid: "px".into(),
            message_view_scope: None,
        })
        .await
        .expect_err("hidden Bot should be rejected");
    assert!(
        matches!(
            error,
            bcs_service_api::application::v1::ApplicationError::Forbidden(ref message)
                if message.contains("hidden")
        ),
        "expected hidden-Bot Forbidden, got {error:?}",
    );
    assert_eq!(error.code(), "forbidden");
}
