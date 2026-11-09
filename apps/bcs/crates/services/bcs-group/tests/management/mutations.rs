use super::*;

#[tokio::test]
async fn update_status_parses_status_and_rejects_unknown_status() {
    let fixture = Fixture::new().with_bot("driver", "Driver", "public", Some("alice"));
    let service = fixture.service_with_limits(5, 10, 10);
    service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .unwrap();

    let updated = service
        .update_status(GroupStatusCommand {
            caller_actor_id: Some("driver".to_string()),
            group_id: "group-under-test".to_string(),
            status: "closed".to_string(),
        })
        .await
        .unwrap();
    assert_eq!(updated.status, GroupStatus::Closed);

    let invalid = service
        .update_status(GroupStatusCommand {
            caller_actor_id: Some("driver".to_string()),
            group_id: "group-under-test".to_string(),
            status: "paused".to_string(),
        })
        .await
        .expect_err("paused is not a supported group status");
    assert!(matches!(
        invalid,
        GroupUseCaseError::InvalidGroupStatus(status) if status == "paused"
    ));
}

#[tokio::test]
async fn group_secondary_mutations_run_through_management_service() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", None)
        .with_bot("helper", "Helper", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);
    service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![
                participant("driver", Some("driver")),
                participant("helper", None),
            ],
        ))
        .await
        .unwrap();

    let label = service
        .update_label(GroupUpdateLabelCommand {
            caller_actor_id: "driver".to_string(),
            group_id: "group-under-test".to_string(),
            label: Some("Renamed".to_string()),
        })
        .await
        .unwrap();
    assert_eq!(label.label.as_deref(), Some("Renamed"));


    let mut routes = HashMap::new();
    routes.insert("driver".to_string(), vec!["helper".to_string()]);
    let routing = service
        .update_routing_policy(GroupRoutingPolicyCommand {
            caller_actor_id: Some("driver".to_string()),
            group_id: "group-under-test".to_string(),
            mode: Some(RoutingMode::Structured),
            default_bot_final_delivery: Some(DefaultDelivery::InjectObservers),
            sender_routes: Some(routes),
        })
        .await
        .unwrap();
    assert_eq!(routing.routing_policy.mode, RoutingMode::Structured);
    assert_eq!(
        routing.routing_policy.default_bot_final_delivery,
        DefaultDelivery::InjectObservers
    );

    let mut invalid_routes = HashMap::new();
    invalid_routes.insert("driver".to_string(), vec!["missing".to_string()]);
    let invalid = service
        .update_routing_policy(GroupRoutingPolicyCommand {
            caller_actor_id: Some("driver".to_string()),
            group_id: "group-under-test".to_string(),
            mode: None,
            default_bot_final_delivery: None,
            sender_routes: Some(invalid_routes),
        })
        .await
        .expect_err("routing policy must reference participants");
    assert!(matches!(invalid, GroupUseCaseError::InvalidProposal(_)));

    let terminated = service
        .terminate_group(GroupTerminateCommand {
            caller_actor_id: "driver".to_string(),
            group_id: "group-under-test".to_string(),
        })
        .await
        .unwrap();
    assert_eq!(terminated.status, GroupStatus::Completed);
}
