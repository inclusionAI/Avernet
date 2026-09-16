use super::*;

fn group_only_command(strategy: GroupStrategy) -> GroupCreateCommand {
    let (lead_role, member_role) = if strategy == GroupStrategy::ManagerWorker {
        ("manager", "worker")
    } else {
        ("driver", "consultant")
    };
    let mut command = create_cmd(
        Some("lead"),
        "lead",
        vec![
            participant("lead", Some(lead_role)),
            participant("member", Some(member_role)),
        ],
    );
    command.group_strategy = Some(strategy);
    command.create_initial_session = false;
    command
}

#[tokio::test]
async fn group_only_creation_does_not_call_session_service_even_when_it_would_fail() {
    for strategy in [GroupStrategy::Chat, GroupStrategy::ManagerWorker] {
        let fixture = Fixture::new()
            .with_bot("lead", "Lead", "public", None)
            .with_bot("member", "Member", "public", None);
        let sessions = Arc::new(StaticSessionManagement::failing_create(test_session(
            "group-under-test:unused",
            "group-under-test",
            Vec::new(),
        )));
        let service = fixture.service_with_limits_and_session(5, 10, 10, sessions.clone());
        let result = service
            .create_group(group_only_command(strategy))
            .await
            .unwrap();

        assert!(sessions.commands.lock().await.is_empty());
        assert!(result.latest_running_session_id.is_none());
        assert!(result.initial_run.is_none());
        assert_eq!(result.context_injected, 0);
        assert!(fixture.group.get("group-under-test").await.is_some());
    }
}

#[tokio::test]
async fn group_only_creation_rejects_state_machine_and_provisioning_before_persistence() {
    for (strategy, provisioning) in [
        (GroupStrategy::StateMachine, false),
        (GroupStrategy::Chat, true),
        (GroupStrategy::ManagerWorker, true),
    ] {
        let fixture = Fixture::new()
            .with_bot("lead", "Lead", "public", None)
            .with_bot("member", "Member", "public", None);
        let service = fixture.service_with_limits(5, 10, 10);
        let mut command = group_only_command(strategy);
        command.provisioning = provisioning;
        let error = service.create_group(command).await.unwrap_err();

        assert!(matches!(error, GroupUseCaseError::InvalidProposal(message)
            if message.contains("create_initial_session")));
        assert!(fixture.group.get("group-under-test").await.is_none());
    }
}

#[tokio::test]
async fn group_only_creation_still_consumes_driver_group_quota() {
    let fixture = Fixture::new()
        .with_bot("lead", "Lead", "public", None)
        .with_bot("member", "Member", "public", None);
    let service = fixture.service_with_limits(5, 1, 10);
    service
        .create_group(group_only_command(GroupStrategy::Chat))
        .await
        .unwrap();
    let mut next = group_only_command(GroupStrategy::Chat);
    next.group_id = Some("second-group".to_string());
    let error = service.create_group(next).await.unwrap_err();

    assert!(matches!(error, GroupUseCaseError::InvalidProposal(message)
        if message.contains("already drives")));
    assert!(fixture.group.get("second-group").await.is_none());
}
