use super::*;

#[tokio::test]
async fn manager_worker_manager_view_reads_public_rows_after_cutoff() {
    let (service, repo, _sessions, fallback, session_id) =
        service_fixture(GroupStrategy::ManagerWorker, 0, 0, Vec::new()).await;
    append_history(
        &repo,
        "group-1",
        &session_id,
        "human_1",
        "public-human",
        None,
    )
    .await;
    append_history(&repo, "group-1", &session_id, "mgr", "public-manager", None).await;
    append_history(
        &repo,
        "group-1",
        &session_id,
        "worker-a",
        "a-only",
        Some("worker-a"),
    )
    .await;
    append_history(
        &repo,
        "group-1",
        &session_id,
        "worker-b",
        "b-only",
        Some("worker-b"),
    )
    .await;

    let result = service
        .get_session_history(session_cmd("group-1", &session_id, Some("mgr")))
        .await
        .expect("manager worker manager view history");

    assert_eq!(fallback.session_calls().await, 0);
    assert_eq!(result.messages.len(), 2);
    let contents: Vec<_> = result.messages.iter().map(|m| m.content.as_str()).collect();
    assert_eq!(contents, vec!["public-manager", "public-human"]);
}

#[tokio::test]
async fn manager_worker_human_view_reads_public_rows_after_cutoff() {
    let (service, repo, sessions, fallback, session_id) =
        service_fixture(GroupStrategy::ManagerWorker, 0, 0, Vec::new()).await;
    sessions
        .add_participant(
            &session_id,
            Participant::human("human_1", ParticipantRole::Observer),
        )
        .await
        .expect("add full Human participant");
    append_history(
        &repo,
        "group-1",
        &session_id,
        "human_1",
        "public-human",
        None,
    )
    .await;
    append_history(&repo, "group-1", &session_id, "mgr", "public-manager", None).await;
    append_history(
        &repo,
        "group-1",
        &session_id,
        "worker-a",
        "a-only",
        Some("worker-a"),
    )
    .await;

    let result = service
        .get_session_history(session_cmd("group-1", &session_id, Some("human_1")))
        .await
        .expect("manager worker human view history");

    assert_eq!(fallback.session_calls().await, 0);
    assert_eq!(result.messages.len(), 2);
    let contents: Vec<_> = result.messages.iter().map(|m| m.content.as_str()).collect();
    assert_eq!(contents, vec!["public-manager", "public-human"]);
}

#[tokio::test]
async fn manager_worker_unknown_view_bot_is_rejected_after_cutoff() {
    let (service, repo, _sessions, _fallback, session_id) =
        service_fixture(GroupStrategy::ManagerWorker, 0, 0, Vec::new()).await;
    append_history(
        &repo,
        "group-1",
        &session_id,
        "human_1",
        "public-human",
        None,
    )
    .await;

    let err = service
        .get_session_history(session_cmd(
            "group-1",
            &session_id,
            Some("not-a-participant"),
        ))
        .await
        .expect_err("unknown view bot should not read public history");

    assert!(
        matches!(
            err,
            GroupUseCaseError::Service(ServiceError::InvalidOperation { .. })
        ),
        "expected InvalidOperation, got {err:?}"
    );
}

#[tokio::test]
async fn manager_worker_history_without_view_owner_reads_public_rows_after_cutoff() {
    let (service, repo, _sessions, fallback, session_id) =
        service_fixture(GroupStrategy::ManagerWorker, 0, 0, Vec::new()).await;
    append_history(
        &repo,
        "group-1",
        &session_id,
        "human_1",
        "public-human",
        None,
    )
    .await;
    append_history(
        &repo,
        "group-1",
        &session_id,
        "worker-a",
        "a-only",
        Some("worker-a"),
    )
    .await;

    let result = service
        .get_session_history(session_cmd("group-1", &session_id, None))
        .await
        .expect("manager worker default public history");

    assert_eq!(fallback.session_calls().await, 0);
    assert_eq!(result.messages.len(), 1);
    assert_eq!(result.messages[0].content, "public-human");
}

#[tokio::test]
async fn chat_bot_viewer_sees_public_and_own_system_copies_not_others() {
    let (service, repo, _sessions, fallback, session_id) =
        service_fixture(GroupStrategy::Chat, 0, u64::MAX, Vec::new()).await;
    append_history(
        &repo,
        "group-1",
        &session_id,
        "human_1",
        "public-human",
        None,
    )
    .await;
    append_history(
        &repo,
        "group-1",
        &session_id,
        "system",
        "sys-to-worker-a",
        Some("worker-a"),
    )
    .await;
    append_history(
        &repo,
        "group-1",
        &session_id,
        "system",
        "sys-to-worker-b",
        Some("worker-b"),
    )
    .await;

    // worker-a view: public + own system copy; NOT worker-b's copy.
    let res_a = service
        .get_session_history(session_cmd("group-1", &session_id, Some("worker-a")))
        .await
        .expect("worker-a chat history");
    let contents_a: Vec<&str> = res_a.messages.iter().map(|m| m.content.as_str()).collect();
    assert!(contents_a.contains(&"public-human"));
    assert!(contents_a.contains(&"sys-to-worker-a"));
    assert!(
        !contents_a.contains(&"sys-to-worker-b"),
        "other bot's system copy must be hidden under PublicOrOwner"
    );

    // no view_bot_id: only public (IsNull).
    let res_none = service
        .get_session_history(session_cmd("group-1", &session_id, None))
        .await
        .expect("public chat history");
    let contents_none: Vec<&str> = res_none
        .messages
        .iter()
        .map(|m| m.content.as_str())
        .collect();
    assert!(contents_none.contains(&"public-human"));
    assert!(!contents_none.contains(&"sys-to-worker-a"));
    assert!(!contents_none.contains(&"sys-to-worker-b"));
    let _ = fallback;
}

#[tokio::test]
async fn mw_manager_viewer_sees_public_and_own_system_copies() {
    let (service, repo, _sessions, _fallback, session_id) =
        service_fixture(GroupStrategy::ManagerWorker, 0, 0, Vec::new()).await;
    append_history(
        &repo,
        "group-1",
        &session_id,
        "human_1",
        "public-human",
        None,
    )
    .await;
    append_history(
        &repo,
        "group-1",
        &session_id,
        "system",
        "sys-to-manager",
        Some("mgr"),
    )
    .await;
    append_history(
        &repo,
        "group-1",
        &session_id,
        "system",
        "sys-to-worker-a",
        Some("worker-a"),
    )
    .await;

    let res = service
        .get_session_history(session_cmd("group-1", &session_id, Some("mgr")))
        .await
        .expect("manager history");
    let contents: Vec<&str> = res.messages.iter().map(|m| m.content.as_str()).collect();
    assert!(contents.contains(&"public-human"));
    assert!(
        contents.contains(&"sys-to-manager"),
        "manager now sees own system copy under PublicOrOwner(mgr)"
    );
    assert!(!contents.contains(&"sys-to-worker-a"));
}

#[tokio::test]
async fn get_history_chat_view_bot_id_now_filters_by_public_or_owner() {
    let (service, repo, _sessions, _fallback, _session_id) =
        service_fixture(GroupStrategy::Chat, 0, u64::MAX, Vec::new()).await;
    // get_history new-path hardcodes session_id "" (String::new()),
    // so seed with session_id "" to match the query.
    let gid = "group-1";
    append_history(&repo, gid, "", "human_1", "public-human", None).await;
    append_history(&repo, gid, "", "system", "sys-to-a", Some("worker-a")).await;
    append_history(&repo, gid, "", "system", "sys-to-b", Some("worker-b")).await;

    // Regression: view_bot_id was previously ignored (hardcoded Any).
    let res_a = service
        .get_history(group_cmd(gid, Some("worker-a")))
        .await
        .expect("worker-a group history");
    let contents_a: Vec<&str> = res_a.messages.iter().map(|m| m.content.as_str()).collect();
    assert!(contents_a.contains(&"public-human"));
    assert!(contents_a.contains(&"sys-to-a"));
    assert!(
        !contents_a.contains(&"sys-to-b"),
        "get_history must now honor view_bot_id (was hardcoded Any)"
    );

    let res_none = service
        .get_history(group_cmd(gid, None))
        .await
        .expect("public group history");
    let contents_none: Vec<&str> = res_none
        .messages
        .iter()
        .map(|m| m.content.as_str())
        .collect();
    assert!(contents_none.contains(&"public-human"));
    assert!(!contents_none.contains(&"sys-to-a"));
    assert!(!contents_none.contains(&"sys-to-b"));
}

/// End-to-end round-trip: `SystemMessageDispatcherImpl` persists system
/// messages by `PersistMode` into a REAL `MemoryMessageRepo` — personalized
/// copies with `owner_bot_id = Some(recipient)` and shared notices as a
/// single public (`owner = None`) record — and
/// `MessageService::get_session_history` with `view_bot_id=recipient`
/// returns that recipient's own copy plus public records, hides other
/// recipients' copies, and lets human viewers (no bot view) read the
/// public notices. This locks the §数据流 bridge between the write side
/// (PersistMode-driven ownership) and the query side (`PublicOrOwner`
/// scoping).
#[tokio::test]
async fn system_message_dispatch_round_trips_through_message_service_view_scoping() {
    use bcs_service_api::SystemMessageDispatcherService;
    use bcs_system_message::SystemMessageDispatcherImpl;
    use bcs_system_message::producers::bot_joined::BotJoinedMessageProducer;
    use bcs_test_support::{
        NoopBotDeliveryPort, NoopBotRegistryCoreService, NoopFrontendDeliveryPort,
        NoopGroupMessageHistoryService,
    };

    let (service, repo, _sessions, _fallback, session_id) =
        service_fixture(GroupStrategy::Chat, 0, u64::MAX, Vec::new()).await;
    let group_id = "group-1";

    // A public (owner=None) anchor that must remain visible to every viewer.
    append_history(
        &repo,
        group_id,
        &session_id,
        "bot-anchor",
        "public-anchor",
        None,
    )
    .await;

    // Build a REAL dispatcher wired to the SAME MemoryMessageRepo. Delivery
    // ports are noops — persistence happens before delivery, so the
    // per-recipient records land in the repo regardless of delivery outcome.
    let dispatcher = SystemMessageDispatcherImpl::builder()
        .with_registry(Arc::new(NoopBotRegistryCoreService))
        .with_delivery(Arc::new(NoopBotDeliveryPort))
        .with_frontend_delivery(Arc::new(NoopFrontendDeliveryPort))
        .with_message_repo(repo.clone())
        .register(BotJoinedMessageProducer::new(Arc::new(
            NoopGroupMessageHistoryService,
        )))
        .build()
        .expect("build dispatcher");

    // BotJoined: new-bot joins a group that already has `mgr` (driver) and
    // two workers. The producer emits one context-injection message for
    // new-bot and one notification for each existing participant.
    let new_bot_id = "bot-new".to_string();
    let existing_id = "mgr".to_string();
    let participants = vec![
        Participant::bot(&existing_id, ParticipantRole::Manager),
        Participant::bot("worker-a", ParticipantRole::Worker),
        Participant::bot("worker-b", ParticipantRole::Worker),
        Participant::bot(&new_bot_id, ParticipantRole::Consultant),
    ];
    let event = SystemMessageEvent::BotJoined {
        group_id: group_id.to_string(),
        actor: Participant::bot(&new_bot_id, ParticipantRole::Consultant),
        session_id: session_id.clone(),
        session_input: None,
    };
    dispatcher
        .dispatch(
            event,
            &group_fixture(group_id, &existing_id),
            &session_id,
            &participants,
        )
        .await
        .expect("dispatch succeeded");

    // Viewer = existing mgr: sees the public join notice (owner=None) +
    // the public anchor; must NOT see new-bot's context injection
    // (owner=new-bot).
    let res_existing = service
        .get_session_history(session_cmd(group_id, &session_id, Some(&existing_id)))
        .await
        .expect("existing view session history");
    let existing_contents: Vec<&str> = res_existing
        .messages
        .iter()
        .map(|m| m.content.as_str())
        .collect();
    assert!(
        existing_contents.contains(&"public-anchor"),
        "public owner=None records still visible to mgr"
    );
    assert!(
        existing_contents.iter().any(|c| c.contains("已加入协作群")),
        "public join notice (owner=None) is returned to mgr"
    );
    assert_eq!(
        existing_contents
            .iter()
            .filter(|c| c.contains("已加入协作群"))
            .count(),
        1,
        "the shared notice is a single public record, not per-bot copies"
    );
    assert!(
        existing_contents
            .iter()
            .all(|c| !c.contains("<GroupContext>")),
        "new-bot's context injection (owner=new-bot) is hidden from mgr"
    );

    // Viewer = new-bot: sees its own context injection (owner=new-bot) +
    // the public anchor + the public join notice.
    let res_new = service
        .get_session_history(session_cmd(group_id, &session_id, Some(&new_bot_id)))
        .await
        .expect("new-bot view session history");
    let new_contents: Vec<&str> = res_new
        .messages
        .iter()
        .map(|m| m.content.as_str())
        .collect();
    assert!(
        new_contents.contains(&"public-anchor"),
        "public owner=None records still visible to new-bot"
    );
    assert!(
        new_contents.iter().any(|c| c.contains("<GroupContext>")),
        "new-bot's own context injection (owner=new-bot) is returned"
    );
    assert!(
        new_contents.iter().any(|c| c.contains("已加入协作群")),
        "public join notice (owner=None) is returned to new-bot"
    );

    // Viewer = human (no bot view): sees the public anchor + the public
    // join notice; must NOT see any per-bot owned copy. This is the
    // regression guard for system messages vanishing from human history.
    let res_human = service
        .get_session_history(session_cmd(group_id, &session_id, None))
        .await
        .expect("human view session history");
    let human_contents: Vec<&str> = res_human
        .messages
        .iter()
        .map(|m| m.content.as_str())
        .collect();
    assert!(
        human_contents.contains(&"public-anchor"),
        "public owner=None records visible to human viewers"
    );
    assert!(
        human_contents.iter().any(|c| c.contains("已加入协作群")),
        "public join notice is visible to human viewers"
    );
    assert!(
        human_contents.iter().all(|c| !c.contains("<GroupContext>")),
        "per-bot owned copies stay hidden from human viewers"
    );
}
