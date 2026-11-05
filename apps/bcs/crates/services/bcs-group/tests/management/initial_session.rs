use super::*;

#[derive(Default)]
struct InitialRunSystemMessage { queued: bool }

#[async_trait]
impl SystemMessageService for InitialRunSystemMessage {
    async fn notify(
        &self,
        _group_id: &str,
        _event: SystemMessageEvent,
        _session_id: &str,
        _session_participants: &[Participant],
    ) -> ServiceResult<usize> {
        Ok(1)
    }

    async fn notify_with_outcome(
        &self,
        _group_id: &str,
        _event: SystemMessageEvent,
        _session_id: &str,
        _session_participants: &[Participant],
    ) -> ServiceResult<SystemMessageDispatchOutcome> {
        Ok(SystemMessageDispatchOutcome {
            total_recipients: 2,
            successful_deliveries: 2,
            failed_deliveries: 0,
            recipient_results: vec![
                SystemMessageRecipientResult {
                    recipient_id: "manager".to_string(),
                    run_id: "manager-bootstrap-run".to_string(),
                    delivery_type: bcs_service_api::DeliveryType::Send,
                    delivery_id: self.queued.then(|| "manager-delivery".into()),
                    delivered: !self.queued,
                    error: None,
                },
                SystemMessageRecipientResult {
                    recipient_id: "worker".to_string(),
                    run_id: "worker-inject-run".to_string(),
                    delivery_type: bcs_service_api::DeliveryType::Inject,
                    delivery_id: self.queued.then(|| "worker-delivery".into()),
                    delivered: !self.queued,
                    error: None,
                },
            ],
        })
    }
}

#[tokio::test]
async fn create_group_fails_and_rolls_back_when_initial_session_creation_fails() {
    let fixture = Fixture::new().with_bot("driver", "Driver", "public", Some("alice"));
    let session = test_session(
        "group-under-test:abcdef12",
        "group-under-test",
        vec![Participant::bot("driver", ParticipantRole::Driver)],
    );
    let service = fixture
        .service_with_limits_and_session(
            5,
            10,
            10,
            Arc::new(StaticSessionManagement::failing_create(session)),
        );

    let err = service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .expect_err("group creation must fail when initial session creation fails");

    assert!(
        matches!(
            err,
            GroupUseCaseError::Service(ServiceError::InternalError(ref message))
                if message.contains("failed to auto-create initial session")
        ),
        "got: {:?}",
        err
    );
    assert!(
        fixture.group.get("group-under-test").await.is_none(),
        "group row must be rolled back when initial session creation fails"
    );
}

#[tokio::test]
async fn create_state_machine_group_auto_creates_service_invocation_session() {
    let fixture = Fixture::new().with_bot("driver", "Driver", "public", Some("alice"));
    let session = test_session(
        "group-under-test:abcdef12",
        "group-under-test",
        vec![Participant::bot("driver", ParticipantRole::Driver)],
    );
    let session_management = Arc::new(StaticSessionManagement::new(session));
    let service = fixture
        .service_with_limits_and_session(5, 10, 10, session_management.clone());

    let mut cmd = create_cmd(
        Some("driver"),
        "driver",
        vec![participant("driver", Some("driver"))],
    );
    cmd.group_strategy = Some(GroupStrategy::StateMachine);
    cmd.label = Some("BCN 宣传".to_string());
    cmd.topic = Some("BCN 宣传会话".to_string());
    cmd.context = Some("写一篇宣传 BCN 的文章".to_string());
    cmd.opening_message = Some(OpeningMessage::Text(
        "开始 {{bcs.run_id}}".to_string(),
    ));

    let created = service.create_group(cmd).await.unwrap();

    assert_eq!(
        created.latest_running_session_id.as_deref(),
        Some("group-under-test:abcdef12")
    );
    assert_eq!(created.context_injected, 0);
    assert_eq!(
        fixture
            .group
            .get("group-under-test")
            .await
            .expect("created group")
            .opening_message,
        Some(OpeningMessage::Text(
            "开始 {{bcs.run_id}}".to_string()
        ))
    );
    let commands = session_management.commands.lock().await;
    assert_eq!(commands.len(), 1);
    let params = &commands[0].params;
    assert_eq!(params.session_kind, SessionKind::ServiceInvocation);
    assert_eq!(
        params.input.as_ref(),
        Some(&serde_json::json!({ "query": "写一篇宣传 BCN 的文章" }))
    );
    assert_eq!(params.session_title.as_deref(), Some("新会话"));
}

#[tokio::test]
async fn create_chat_group_accepts_session_scoped_opening_message() {
    let fixture = Fixture::new().with_bot("driver", "Driver", "public", Some("alice"));
    let service = fixture.service_with_limits(5, 10, 10);
    let mut cmd = create_cmd(
        Some("driver"),
        "driver",
        vec![participant("driver", Some("driver"))],
    );
    let opening_message = OpeningMessage::Text(
        "hello {{bcs.session_id}}".to_string(),
    );
    cmd.opening_message = Some(opening_message.clone());

    service.create_group(cmd).await.expect("create Chat Group");
    assert_eq!(
        fixture
            .group
            .get("group-under-test")
            .await
            .expect("created group")
            .opening_message,
        Some(opening_message)
    );
}

#[tokio::test]
async fn create_state_machine_group_with_human_caller_adds_present_human_to_initial_session() {
    let fixture = Fixture::new().with_bot("driver", "Driver", "public", Some("alice"));
    let session = test_session(
        "group-under-test:abcdef12",
        "group-under-test",
        vec![Participant::bot("driver", ParticipantRole::Driver)],
    );
    let session_management = Arc::new(StaticSessionManagement::new(session));
    let service = fixture
        .service_with_limits_and_session(5, 10, 10, session_management.clone());

    let mut cmd = create_cmd(
        Some("human_alice"),
        "driver",
        vec![participant("driver", Some("driver"))],
    );
    cmd.group_strategy = Some(GroupStrategy::StateMachine);

    service.create_group(cmd).await.unwrap();

    let commands = session_management.commands.lock().await;
    assert_eq!(commands.len(), 1);
    let params = &commands[0].params;
    assert_eq!(params.created_by.as_deref(), Some("driver"));
    assert_eq!(params.caller_principal.as_deref(), Some("human_alice"));
    let human = params
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == "human_alice")
        .expect("authenticated Human caller must join the initial state-machine session");
    assert!(human.is_human());
    assert_eq!(human.role, ParticipantRole::Observer);
    assert_eq!(human.mode, Some(ParticipantMode::Present));
}

#[tokio::test]
async fn create_manager_worker_group_returns_only_manager_send_as_initial_run() {
    let fixture = Fixture::new()
        .with_bot("manager", "Manager", "public", Some("alice"))
        .with_bot("worker", "Worker", "public", None);
    let service = fixture.service_with_system_message(Arc::new(InitialRunSystemMessage::default()));
    let mut cmd = create_cmd(
        Some("manager"),
        "manager",
        vec![
            participant("manager", Some("manager")),
            participant("worker", Some("worker")),
        ],
    );
    cmd.group_strategy = Some(GroupStrategy::ManagerWorker);

    let created = service.create_group(cmd).await.expect("create group");

    let initial_run = created.initial_run.expect("manager initial run");
    assert_eq!(initial_run.run_id, "manager-bootstrap-run");
    assert_eq!(initial_run.bot_uuid, "manager");
    assert_eq!(
        initial_run.activity_kind,
        bcs_service_api::InitialGroupRunActivityKind::GroupBootstrap
    );
    assert_eq!(
        initial_run.state,
        bcs_service_api::InitialGroupRunState::Running
    );
}

#[tokio::test]
async fn create_chat_group_returns_driver_send_as_initial_run() {
    let fixture = Fixture::new()
        .with_bot("manager", "Driver", "public", Some("alice"))
        .with_bot("worker", "Member", "public", None);
    let service = fixture.service_with_system_message(Arc::new(InitialRunSystemMessage::default()));
    let cmd = create_cmd(
        Some("manager"),
        "manager",
        vec![
            participant("manager", Some("driver")),
            participant("worker", Some("consultant")),
        ],
    );

    let created = service.create_group(cmd).await.expect("create group");

    let initial_run = created.initial_run.expect("driver initial run");
    assert_eq!(initial_run.run_id, "manager-bootstrap-run");
    assert_eq!(initial_run.bot_uuid, "manager");
    assert_eq!(
        initial_run.activity_kind,
        bcs_service_api::InitialGroupRunActivityKind::GroupBootstrap
    );
    assert_eq!(
        initial_run.state,
        bcs_service_api::InitialGroupRunState::Running
    );
}

#[tokio::test]
async fn queued_group_bootstrap_returns_queued_initial_run() {
    for manager_worker in [false, true] {
        let fixture = Fixture::new()
            .with_bot("manager", "Manager", "public", Some("alice"))
            .with_bot("worker", "Worker", "public", None);
        let service = fixture.service_with_system_message(Arc::new(InitialRunSystemMessage { queued: true }));
        let mut cmd = create_cmd(Some("manager"), "manager", vec![
            participant("manager", Some(if manager_worker { "manager" } else { "driver" })),
            participant("worker", Some(if manager_worker { "worker" } else { "consultant" })),
        ]);
        if manager_worker { cmd.group_strategy = Some(GroupStrategy::ManagerWorker); }

        let created = service.create_group(cmd).await.expect("queued bootstrap allows group creation");
        let run = created.initial_run.expect("queued Send, not worker Inject, is initial run");
        assert_eq!(run.run_id, "manager-bootstrap-run");
        assert_eq!(run.bot_uuid, "manager");
        assert_eq!(run.state, bcs_service_api::InitialGroupRunState::Queued);
        assert_eq!(run.activity_kind, bcs_service_api::InitialGroupRunActivityKind::GroupBootstrap);
    }
}
