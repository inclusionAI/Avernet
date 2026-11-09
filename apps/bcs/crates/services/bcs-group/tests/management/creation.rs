use super::*;

#[tokio::test]
async fn create_group_authorizes_human_caller_with_any_driver() {
    // Human callers can designate any bot as driver (no owner check).
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_bot("other-bot", "Other", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);

    // Owner human can still create
    let created = service
        .create_group(create_cmd(
            Some("human_alice"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .expect("human owner should be authorized");
    assert_eq!(created.driver_bot_id, "driver");

    // Non-owner human can also designate any bot as driver
    let created2 = service
        .create_group(create_cmd(
            Some("human_bob"),
            "other-bot",
            vec![participant("other-bot", Some("driver"))],
        ))
        .await
        .expect("any human should be authorized to designate any bot as driver");
    assert_eq!(created2.driver_bot_id, "other-bot");

    // Bot caller that is not the driver is still rejected
    let forbidden = service
        .create_group(create_cmd(
            Some("other-bot"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .expect_err("bot caller must be the driver itself");
    assert!(matches!(forbidden, GroupUseCaseError::Forbidden(_)));
}

#[tokio::test]
async fn create_group_without_explicit_id_uses_native_namespace() {
    let fixture = Fixture::new().with_bot("driver", "Driver", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);
    let mut cmd = create_cmd(
        Some("driver"),
        "driver",
        vec![participant("driver", Some("driver"))],
    );
    cmd.group_id = None;

    let created = service
        .create_group(cmd)
        .await
        .expect("generated group should be created");

    assert!(created.group_id.starts_with("bcs_grp_"));
    assert!(!created.group_id.starts_with("bcs_grp_dm_"));
    assert_eq!(created.group_id.chars().count(), 40);
}

#[tokio::test]
async fn create_group_human_caller_with_ownerless_driver_ok() {
    // Human can designate ownerless bot as driver (no owner restriction).
    let fixture = Fixture::new().with_bot("driver", "Driver", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);

    let created = service
        .create_group(create_cmd(
            Some("human_alice"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .expect("human should be authorized even for ownerless driver");
    assert_eq!(created.driver_bot_id, "driver");

    // Bot as itself still works
    let created2 = service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .expect("driver bot should still be authorized as itself");
    assert_eq!(created2.driver_bot_id, "driver");
}

#[tokio::test]
async fn create_manager_worker_group_requires_exactly_one_manager() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_bot("worker-a", "Worker A", "public", None)
        .with_bot("worker-b", "Worker B", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);

    let mut no_manager = create_cmd(
        Some("driver"),
        "driver",
        vec![
            participant("driver", Some("worker")),
            participant("worker-a", Some("worker")),
        ],
    );
    no_manager.group_strategy = Some(bcs_service_api::GroupStrategy::ManagerWorker);
    let err = service
        .create_group(no_manager)
        .await
        .expect_err("manager_worker without manager must be rejected");
    assert!(matches!(err, GroupUseCaseError::InvalidProposal(_)));

    let mut two_managers = create_cmd(
        Some("driver"),
        "driver",
        vec![
            participant("driver", Some("manager")),
            participant("worker-a", Some("manager")),
            participant("worker-b", Some("worker")),
        ],
    );
    two_managers.group_strategy = Some(bcs_service_api::GroupStrategy::ManagerWorker);
    let err = service
        .create_group(two_managers)
        .await
        .expect_err("manager_worker with two managers must be rejected");
    assert!(matches!(err, GroupUseCaseError::InvalidProposal(_)));
}

#[tokio::test]
async fn create_manager_worker_group_allows_provider_downlink_bot() {
    let fixture = Fixture::new()
        .with_bot("manager", "Manager", "public", Some("alice"))
        .with_provider_downlink_bot("provider-worker", "Provider Worker", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);

    let mut cmd = create_cmd(
        Some("manager"),
        "manager",
        vec![
            participant("manager", Some("manager")),
            participant("provider-worker", Some("worker")),
        ],
    );
    cmd.group_strategy = Some(bcs_service_api::GroupStrategy::ManagerWorker);

    service
        .create_group(cmd)
        .await
        .expect("provider downlink bot can join manager_worker group");
}

#[tokio::test]
async fn create_group_persists_typed_routing_policy_and_validates_sender_routes() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", None)
        .with_bot("helper", "Helper", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);

    let mut sender_routes = HashMap::new();
    sender_routes.insert("helper".to_string(), vec!["driver".to_string()]);
    let mut cmd = create_cmd(
        Some("driver"),
        "driver",
        vec![
            participant("driver", Some("driver")),
            participant("helper", Some("observer")),
        ],
    );
    cmd.routing_policy = Some(RoutingPolicy {
        mode: RoutingMode::Structured,
        default_bot_final_delivery: DefaultDelivery::InjectObservers,
        sender_routes,
    });

    service.create_group(cmd).await.unwrap();
    let stored = fixture.group.get("group-under-test").await.unwrap();
    let policy = stored
        .routing_policy
        .expect("routing policy should persist");
    assert_eq!(policy.mode, RoutingMode::Structured);
    assert_eq!(
        policy.default_bot_final_delivery,
        DefaultDelivery::InjectObservers
    );
    assert_eq!(policy.sender_routes["helper"], ["driver"]);

    let mut invalid_routes = HashMap::new();
    invalid_routes.insert("helper".to_string(), vec!["missing".to_string()]);
    let mut invalid_cmd = create_cmd(
        Some("driver"),
        "driver",
        vec![
            participant("driver", Some("driver")),
            participant("helper", None),
        ],
    );
    invalid_cmd.routing_policy = Some(RoutingPolicy {
        sender_routes: invalid_routes,
        ..Default::default()
    });

    let err = service
        .create_group(invalid_cmd)
        .await
        .expect_err("sender_routes must reference only participants");
    assert!(matches!(err, GroupUseCaseError::InvalidProposal(_)));
}

#[tokio::test]
async fn create_group_maps_participant_roles_and_reachability() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", None)
        .with_bot("friend", "Friend", "protected", None)
        .with_bot("stranger", "Stranger", "protected", None)
        .with_friendship("driver", "friend");
    let service = fixture.service_with_limits(5, 10, 10);

    let detail = service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![
                participant("driver", None),
                participant("friend", Some("observer")),
            ],
        ))
        .await
        .unwrap();

    let driver = detail
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == "driver")
        .unwrap();
    let observer = detail
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == "friend")
        .unwrap();
    assert_eq!(driver.role, "driver");
    assert_eq!(observer.role, "observer");

    let forbidden = service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![participant("driver", None), participant("stranger", None)],
        ))
        .await
        .expect_err("protected non-friend target should not be reachable");
    assert!(matches!(forbidden, GroupUseCaseError::Forbidden(_)));
}

#[tokio::test]
async fn create_group_allows_private_friend_participant() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", None)
        .with_bot("private-friend", "Private Friend", "private", None)
        .with_friendship("driver", "private-friend");
    let service = fixture.service_with_limits(5, 10, 10);

    let detail = service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![
                participant("driver", None),
                participant("private-friend", None),
            ],
        ))
        .await
        .expect("private friends are reachable for collaboration");

    assert!(
        detail
            .participants
            .iter()
            .any(|participant| participant.bot_uuid == "private-friend")
    );
}

#[tokio::test]
async fn create_group_writes_subscription_edge_for_public_participants_only() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", None)
        .with_bot("public-helper", "Public Helper", "public", None)
        .with_bot("protected-helper", "Protected Helper", "protected", None)
        .with_friendship("driver", "protected-helper");
    let service = fixture.service_with_limits(5, 10, 10);

    service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![
                participant("driver", Some("driver")),
                participant("public-helper", Some("consultant")),
                participant("protected-helper", Some("consultant")),
            ],
        ))
        .await
        .unwrap();

    let public_edge = fixture
        .relation
        .get_edge("driver", "public-helper", "dev")
        .await
        .unwrap();
    assert!(
        public_edge.is_some(),
        "creating a group should subscribe the driver to public participants"
    );

    let protected_edge = fixture
        .relation
        .get_edge("driver", "protected-helper", "dev")
        .await
        .unwrap();
    assert!(
        protected_edge.is_none(),
        "friend/private visibility should not create subscription edges"
    );
}

#[tokio::test]
async fn create_group_hides_private_non_friend_participant() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", None)
        .with_bot("private-stranger", "Private Stranger", "private", None);
    let service = fixture.service_with_limits(5, 10, 10);

    let err = service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![
                participant("driver", None),
                participant("private-stranger", None),
            ],
        ))
        .await
        .expect_err("private non-friends should be hidden");

    assert!(matches!(
        err,
        GroupUseCaseError::Service(ServiceError::BotNotFound(bot_id))
            if bot_id == "private-stranger"
    ));
}

#[tokio::test]
async fn create_group_enforces_member_limits() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", None)
        .with_bot("a", "A", "public", None)
        .with_bot("b", "B", "public", None);
    let service = fixture.service_with_limits(2, 10, 10);

    let err = service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![
                participant("driver", None),
                participant("a", None),
                participant("b", None),
            ],
        ))
        .await
        .expect_err("group is over max member limit");
    assert!(matches!(err, GroupUseCaseError::InvalidProposal(_)));
}
