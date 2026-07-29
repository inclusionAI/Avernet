use std::{
    collections::{HashMap, HashSet},
    sync::{Arc, RwLock},
};

use async_trait::async_trait;

use bcs_service_api::{
    ActorKind, AgentCredentials, BotCapabilities, BotDynamicStatus, BotRegistryCoreService,
    ChannelBindingCleanupPort,
    BotDeliveryTarget, BotRuntimeConnectCommand, BotRuntimeConnectOutcome,
    BotRuntimeConnectionService, BotRuntimeDisconnectCommand, BotRuntimeStatusCommand,
    BotRuntimeStatusOutcome, BotUseCaseError, DefaultDelivery, DmCreateCommand,
    EnsureOwnerEdgesResult, FriendCoreService, Group,
    GroupAddMemberCommand, GroupCreateCommand, GroupCreateParticipantCommand, GroupDeleteCommand, GroupRemoveMemberCommand,
    GroupDetailCommand, GroupListCommand, GroupManagementService, GroupParticipantModeCommand,
    GroupQueryService, GroupRoutingPolicyCommand, GroupCoreService, GroupKind, GroupStatus,
    GroupStatusCommand, GroupStrategy, GroupTerminateCommand, GroupUpdateLabelCommand,
    GroupUpdateVisibilityCommand, GroupUpdateWorkspaceCommand,
    GroupUseCaseError, GroupWorkspaceQueryCommand, RegisteredBot, RelationEdge, RelationCoreService,
    Participant, ParticipantMode, ParticipantRole, RoutingMode, RoutingPolicy, ServiceError, ServiceResult, Session, SessionKind,
    SessionManagementService, SessionStatus, SessionUseCaseError, WorkbenchChatAuthorizationCommand,
    WorkbenchConnectCommand, WorkbenchSessionService, WorkbenchUseCaseError, Workspace,
};

use bcs_group::{GroupConfig, GroupManagement, GroupStore};
use bcs_test_support::NoopSystemMessageService;
use tokio::sync::Mutex;

#[derive(Default)]
struct RecordingChannelBindingCleanup {
    deleted_group_ids: Mutex<Vec<String>>,
    fail: bool,
}

impl RecordingChannelBindingCleanup {
    fn failing() -> Self {
        Self {
            deleted_group_ids: Mutex::new(Vec::new()),
            fail: true,
        }
    }
}

#[async_trait]
impl ChannelBindingCleanupPort for RecordingChannelBindingCleanup {
    async fn delete_bindings_for_group(&self, group_id: &str) -> ServiceResult<u64> {
        self.deleted_group_ids.lock().await.push(group_id.to_string());
        if self.fail {
            return Err(ServiceError::InternalError(
                "channel binding cleanup failed".to_string(),
            ));
        }
        Ok(1)
    }
}

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

    let created = service.create_group(cmd).await.unwrap();

    assert_eq!(
        created.latest_running_session_id.as_deref(),
        Some("group-under-test:abcdef12")
    );
    assert_eq!(created.context_injected, 0);
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
async fn query_methods_list_detail_bot_groups_and_workspace() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_bot("helper", "Helper", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);
    let created = service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![
                participant("driver", Some("driver")),
                participant("helper", Some("consultant")),
            ],
        ))
        .await
        .unwrap();
    service
        .update_workspace(GroupUpdateWorkspaceCommand {
            caller_actor_id: None,
            group_id: created.group_id.clone(),
            workspace: Workspace {
                decisions: vec!["ship it".to_string()],
                ..Workspace::default()
            },
        })
        .await
        .unwrap();

    let listed = service
        .list_groups(GroupListCommand {
            group_kind: Some(Default::default()),
            offset: 0,
            limit: 10,
            visibility: None,
            label: None,
        })
        .await
        .unwrap();
    assert_eq!(listed.total, 1);
    assert_eq!(listed.items[0].group_id, created.group_id);
    assert_eq!(listed.items[0].participant_count, 2);

    let detail = service
        .get_group(GroupDetailCommand {
            group_id: created.group_id.clone(),
        })
        .await
        .unwrap();
    assert_eq!(detail.driver_bot_id, "driver");
    assert_eq!(detail.participants.len(), 2);

    let bot_groups = service
        .list_bot_groups(bcs_service_api::BotGroupListCommand {
            bot_id: "helper".to_string(),
            group_kind: Some(Default::default()),
            q: None,
            offset: 0,
            limit: 10,
        })
        .await
        .unwrap();
    assert_eq!(bot_groups.total, 1);
    assert_eq!(bot_groups.items[0].driver_bot_id, "driver");

    let workspace = service
        .get_workspace(GroupWorkspaceQueryCommand {
            group_id: created.group_id,
        })
        .await
        .unwrap();
    assert_eq!(workspace.workspace.decisions, vec!["ship it".to_string()]);
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
async fn add_member_allows_provider_downlink_bot_in_manager_worker_group() {
    let fixture = Fixture::new()
        .with_bot("manager", "Manager", "public", Some("alice"))
        .with_bot("worker", "Worker", "public", None)
        .with_provider_downlink_bot("provider-worker", "Provider Worker", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);

    let mut cmd = create_cmd(
        Some("manager"),
        "manager",
        vec![
            participant("manager", Some("manager")),
            participant("worker", Some("worker")),
        ],
    );
    cmd.group_strategy = Some(bcs_service_api::GroupStrategy::ManagerWorker);
    let group = service
        .create_group(cmd)
        .await
        .expect("regular manager_worker group should be created");

    let result = service
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some("manager".to_string()),
            human_actor_id: None,
            group_id: group.group_id,
            bot_id: "provider-worker".to_string(),
            role: Some("worker".to_string()),
        })
        .await
        .expect("provider downlink bot can be added to manager_worker group");
    assert_eq!(result.member.bot_uuid, "provider-worker");
}

#[tokio::test]
async fn list_groups_orders_by_updated_at_desc_before_pagination() {
    let fixture = Fixture::new();
    let service = fixture.service_with_limits(5, 10, 10);

    for (group_id, updated_at) in [
        ("oldest-group", 10),
        ("newest-group", 30),
        ("middle-group", 20),
    ] {
        let mut group = Group::new(
            group_id,
            "driver",
            vec![bcs_service_api::Participant::bot(
                "driver",
                bcs_service_api::ParticipantRole::Driver,
            )],
        );
        group.updated_at = updated_at;
        fixture.group.upsert(group).await.unwrap();
    }

    let first_page = service
        .list_groups(GroupListCommand {
            group_kind: None,
            offset: 0,
            limit: 2,
            visibility: None,
            label: None,
        })
        .await
        .unwrap();
    assert_eq!(first_page.total, 3);
    assert_eq!(first_page.items[0].group_id, "newest-group");
    assert_eq!(first_page.items[1].group_id, "middle-group");

    let second_page = service
        .list_groups(GroupListCommand {
            group_kind: None,
            offset: 2,
            limit: 1,
            visibility: None,
            label: None,
        })
        .await
        .unwrap();
    assert_eq!(second_page.items[0].group_id, "oldest-group");
}

#[tokio::test]
async fn list_bot_groups_filters_absent_participant_groups() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);

    let mut present = Group::new(
        "present-group",
        "driver",
        vec![
            bcs_service_api::Participant::bot(
                "driver",
                bcs_service_api::ParticipantRole::Driver,
            ),
            bcs_service_api::Participant::human(
                "human_alice",
                bcs_service_api::ParticipantRole::Observer,
            ),
        ],
    );
    present
        .participants
        .iter_mut()
        .find(|participant| participant.bot_uuid == "human_alice")
        .expect("human participant")
        .mode = Some(bcs_service_api::ParticipantMode::Present);
    fixture.group.upsert(present).await.unwrap();

    fixture
        .group
        .upsert(Group::new(
            "absent-group",
            "driver",
            vec![
                bcs_service_api::Participant::bot(
                    "driver",
                    bcs_service_api::ParticipantRole::Driver,
                ),
                bcs_service_api::Participant::human(
                    "human_alice",
                    bcs_service_api::ParticipantRole::Observer,
                ),
            ],
        ))
        .await
        .unwrap();

    let bot_groups = service
        .list_bot_groups(bcs_service_api::BotGroupListCommand {
            bot_id: "human_alice".to_string(),
            group_kind: None,
            q: None,
            offset: 0,
            limit: 10,
        })
        .await
        .unwrap();

    assert_eq!(bot_groups.total, 1);
    assert_eq!(bot_groups.items[0].group_id, "present-group");
}

#[tokio::test]
async fn list_bot_groups_orders_by_updated_at_desc_after_filtering() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);

    for (group_id, updated_at) in [("older-present", 10), ("newer-present", 30)] {
        let mut group = Group::new(
            group_id,
            "driver",
            vec![
                bcs_service_api::Participant::bot(
                    "driver",
                    bcs_service_api::ParticipantRole::Driver,
                ),
                bcs_service_api::Participant::human(
                    "human_alice",
                    bcs_service_api::ParticipantRole::Observer,
                ),
            ],
        );
        group.updated_at = updated_at;
        group
            .participants
            .iter_mut()
            .find(|participant| participant.bot_uuid == "human_alice")
            .expect("human participant")
            .mode = Some(bcs_service_api::ParticipantMode::Present);
        fixture.group.upsert(group).await.unwrap();
    }

    let mut absent = Group::new(
        "newest-absent",
        "driver",
        vec![
            bcs_service_api::Participant::bot(
                "driver",
                bcs_service_api::ParticipantRole::Driver,
            ),
            bcs_service_api::Participant::human(
                "human_alice",
                bcs_service_api::ParticipantRole::Observer,
            ),
        ],
    );
    absent.updated_at = 40;
    fixture.group.upsert(absent).await.unwrap();

    let bot_groups = service
        .list_bot_groups(bcs_service_api::BotGroupListCommand {
            bot_id: "human_alice".to_string(),
            group_kind: None,
            q: None,
            offset: 0,
            limit: 10,
        })
        .await
        .unwrap();

    assert_eq!(bot_groups.total, 2);
    assert_eq!(bot_groups.items[0].group_id, "newer-present");
    assert_eq!(bot_groups.items[1].group_id, "older-present");
}

#[tokio::test]
async fn list_bot_groups_filters_query_by_label_only() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);

    for (group_id, label, updated_at) in [
        ("label-match-newer", "05231022测试主子", 40),
        ("label-match-older", "Demo Worker测试05232205", 30),
        ("id-contains-05", "plain label", 50),
        ("miss", "1213123213", 20),
    ] {
        let mut group = Group::new(
            group_id,
            "driver",
            vec![
                bcs_service_api::Participant::bot(
                    "driver",
                    bcs_service_api::ParticipantRole::Driver,
                ),
                bcs_service_api::Participant::human(
                    "human_alice",
                    bcs_service_api::ParticipantRole::Observer,
                ),
            ],
        );
        group.label = Some(label.to_string());
        group.updated_at = updated_at;
        group
            .participants
            .iter_mut()
            .find(|participant| participant.bot_uuid == "human_alice")
            .expect("human participant")
            .mode = Some(bcs_service_api::ParticipantMode::Present);
        fixture.group.upsert(group).await.unwrap();
    }

    let bot_groups = service
        .list_bot_groups(bcs_service_api::BotGroupListCommand {
            bot_id: "human_alice".to_string(),
            group_kind: None,
            q: Some("05".to_string()),
            offset: 0,
            limit: 10,
        })
        .await
        .unwrap();

    assert_eq!(bot_groups.total, 2);
    assert_eq!(bot_groups.items[0].group_id, "label-match-newer");
    assert_eq!(bot_groups.items[1].group_id, "label-match-older");
}

#[tokio::test]
async fn list_bot_groups_returns_empty_when_actor_only_has_absent_groups() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);

    for group_id in ["absent-group-a", "absent-group-b"] {
        fixture
            .group
            .upsert(Group::new(
                group_id,
                "driver",
                vec![
                    bcs_service_api::Participant::bot(
                        "driver",
                        bcs_service_api::ParticipantRole::Driver,
                    ),
                    bcs_service_api::Participant::human(
                        "human_alice",
                        bcs_service_api::ParticipantRole::Observer,
                    ),
                ],
            ))
            .await
            .unwrap();
    }

    let bot_groups = service
        .list_bot_groups(bcs_service_api::BotGroupListCommand {
            bot_id: "human_alice".to_string(),
            group_kind: None,
            q: None,
            offset: 0,
            limit: 10,
        })
        .await
        .unwrap();

    assert_eq!(bot_groups.total, 0);
    assert!(bot_groups.items.is_empty());
}

#[tokio::test]
async fn list_bot_groups_keeps_bot_groups_with_absent_human_members() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_bot("helper", "Helper", "public", Some("alice"))
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);

    fixture
        .group
        .upsert(Group::new(
            "bot-auto-group",
            "driver",
            vec![
                bcs_service_api::Participant::bot(
                    "driver",
                    bcs_service_api::ParticipantRole::Driver,
                ),
                bcs_service_api::Participant::bot(
                    "helper",
                    bcs_service_api::ParticipantRole::Consultant,
                ),
                bcs_service_api::Participant::human(
                    "human_alice",
                    bcs_service_api::ParticipantRole::Observer,
                ),
            ],
        ))
        .await
        .unwrap();

    let mut muted = Group::new(
        "bot-muted-group",
        "driver",
        vec![
            bcs_service_api::Participant::bot(
                "driver",
                bcs_service_api::ParticipantRole::Driver,
            ),
            bcs_service_api::Participant::bot(
                "helper",
                bcs_service_api::ParticipantRole::Consultant,
            ),
            bcs_service_api::Participant::human(
                "human_alice",
                bcs_service_api::ParticipantRole::Observer,
            ),
        ],
    );
    muted
        .participants
        .iter_mut()
        .find(|participant| participant.bot_uuid == "helper")
        .expect("helper participant")
        .mode = Some(bcs_service_api::ParticipantMode::Muted);
    fixture.group.upsert(muted).await.unwrap();

    let bot_groups = service
        .list_bot_groups(bcs_service_api::BotGroupListCommand {
            bot_id: "helper".to_string(),
            group_kind: None,
            q: None,
            offset: 0,
            limit: 10,
        })
        .await
        .unwrap();
    let ids = bot_groups
        .items
        .iter()
        .map(|item| item.group_id.as_str())
        .collect::<HashSet<_>>();

    assert_eq!(bot_groups.total, 2);
    assert_eq!(ids, HashSet::from(["bot-auto-group", "bot-muted-group"]));
}

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
async fn add_member_authorizes_coordinator_and_checks_reachability() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_bot("other-bot", "Other", "public", None)
        .with_bot("friend", "Friend", "protected", None)
        .with_bot("private-friend", "Private Friend", "private", None)
        .with_bot("stranger", "Stranger", "protected", None)
        .with_friendship("driver", "friend")
        .with_friendship("driver", "private-friend");
    let service = fixture.service_with_limits(5, 10, 10);
    service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .expect("create group");

    let non_coordinator = service
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some("other-bot".to_string()),
            human_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            bot_id: "friend".to_string(),
            role: Some("consultant".to_string()),
        })
        .await;
    assert!(matches!(
        non_coordinator,
        Err(GroupUseCaseError::Forbidden(message))
            if message.contains("group coordinator")
    ));

    let protected_stranger = service
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some("driver".to_string()),
            human_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            bot_id: "stranger".to_string(),
            role: Some("consultant".to_string()),
        })
        .await;
    assert!(matches!(
        protected_stranger,
        Err(GroupUseCaseError::Forbidden(_))
    ));

    let private_friend = service
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some("driver".to_string()),
            human_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            bot_id: "private-friend".to_string(),
            role: Some("consultant".to_string()),
        })
        .await;
    assert!(matches!(
        private_friend,
        Err(GroupUseCaseError::Service(ServiceError::BotNotFound(bot_id)))
            if bot_id == "private-friend"
    ));

    let wrong_human_owner = service
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some("driver".to_string()),
            human_actor_id: Some("human_impostor".to_string()),
            group_id: "group-under-test".to_string(),
            bot_id: "friend".to_string(),
            role: Some("consultant".to_string()),
        })
        .await;
    assert!(matches!(
        wrong_human_owner,
        Err(GroupUseCaseError::Forbidden(message))
            if message.contains("Not authorized as bot")
    ));

    let added = service
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some("driver".to_string()),
            human_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            bot_id: "friend".to_string(),
            role: Some("driver".to_string()),
        })
        .await
        .expect("friend target is reachable");
    assert_eq!(added.group_id, "group-under-test");
    assert_eq!(added.member.bot_uuid, "friend");
    assert_eq!(added.member.role, "driver");

    let stored = fixture.group.get("group-under-test").await.unwrap();
    assert!(
        stored
            .participants
            .iter()
            .any(|participant| participant.bot_uuid == "friend")
    );
}

#[tokio::test]
async fn add_member_writes_subscription_edge_with_driver_identity_for_public_targets() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", None)
        .with_bot("originator", "Originator", "public", Some("alice"))
        .with_bot("public-helper", "Public Helper", "public", None)
        .with_bot("protected-helper", "Protected Helper", "protected", None)
        .with_friendship("driver", "protected-helper");
    let service = fixture.service_with_limits(5, 10, 10);
    service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .expect("create group");

    let mut group = fixture.group.get("group-under-test").await.unwrap();
    group.originator = Some("originator".to_string());
    fixture.group.upsert(group).await.unwrap();

    service
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some("originator".to_string()),
            human_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            bot_id: "public-helper".to_string(),
            role: Some("consultant".to_string()),
        })
        .await
        .expect("originator can add public helper");
    service
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some("originator".to_string()),
            human_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            bot_id: "protected-helper".to_string(),
            role: Some("consultant".to_string()),
        })
        .await
        .expect("originator can add protected friend of driver");

    let driver_edge = fixture
        .relation
        .get_edge("driver", "public-helper", "dev")
        .await
        .unwrap();
    assert!(
        driver_edge.is_some(),
        "add-member should subscribe the driver to public targets"
    );

    let originator_edge = fixture
        .relation
        .get_edge("originator", "public-helper", "dev")
        .await
        .unwrap();
    assert!(
        originator_edge.is_none(),
        "add-member should not attribute subscription edges to the caller"
    );

    let protected_edge = fixture
        .relation
        .get_edge("driver", "protected-helper", "dev")
        .await
        .unwrap();
    assert!(
        protected_edge.is_none(),
        "protected visibility should not create subscription edges"
    );
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

#[tokio::test]
async fn create_dm_creates_and_reuses_human_bot_pair() {
    let fixture = Fixture::new().with_human("human_alice", "Alice").with_bot(
        "assistant",
        "Assistant",
        "public",
        Some("alice"),
    );
    let service = fixture.service_with_limits(5, 10, 10);

    let created = service
        .create_dm(DmCreateCommand {
            group_id: Some("dm-under-test".to_string()),
            caller_actor_id: Some("human_alice".to_string()),
            driver_bot: None,
            target_actor_id: "assistant".to_string(),
            label: None,
            topic: Some("help".to_string()),
            context: Some("dm context".to_string()),
        })
        .await
        .expect("owner human should create Human-Bot DM");

    assert!(created.created);
    assert_eq!(created.group.group_id, "dm-under-test");
    assert_eq!(created.group.group_kind, GroupKind::Dm);
    assert_eq!(created.group.driver_bot_id, "assistant");
    assert_eq!(created.group.context.as_deref(), Some("dm context"));

    let stored = fixture.group.get("dm-under-test").await.unwrap();
    assert_eq!(stored.originator.as_deref(), Some("human_alice"));
    assert_eq!(
        stored.dm_pair_key.as_deref(),
        Some(Group::compute_dm_pair_key("human_alice", "assistant").as_str())
    );
    let human = stored
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == "human_alice")
        .expect("human participant");
    assert_eq!(human.actor_kind, ActorKind::Human);
    assert_eq!(human.mode, Some(bcs_service_api::ParticipantMode::Present));
    let bot = stored
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == "assistant")
        .expect("bot participant");
    assert_eq!(bot.actor_kind, ActorKind::Bot);
    assert_eq!(bot.role, bcs_service_api::ParticipantRole::Driver);
    assert_eq!(bot.mode, Some(bcs_service_api::ParticipantMode::Auto));

    let reused = service
        .create_dm(DmCreateCommand {
            group_id: Some("dm-ignored".to_string()),
            caller_actor_id: Some("human_alice".to_string()),
            driver_bot: None,
            target_actor_id: "assistant".to_string(),
            label: Some("new label should not overwrite".to_string()),
            topic: None,
            context: Some("new context should not overwrite".to_string()),
        })
        .await
        .expect("second create should reuse canonical pair");
    assert!(!reused.created);
    assert_eq!(reused.group.group_id, "dm-under-test");
    assert_eq!(reused.group.context.as_deref(), Some("dm context"));
}

#[tokio::test]
async fn create_dm_without_explicit_id_uses_dm_namespace() {
    let fixture = Fixture::new().with_human("human_alice", "Alice").with_bot(
        "assistant",
        "Assistant",
        "public",
        Some("alice"),
    );
    let service = fixture.service_with_limits(5, 10, 10);

    let created = service
        .create_dm(DmCreateCommand {
            group_id: None,
            caller_actor_id: Some("human_alice".to_string()),
            driver_bot: None,
            target_actor_id: "assistant".to_string(),
            label: None,
            topic: None,
            context: None,
        })
        .await
        .expect("owner human should create generated Human-Bot DM");

    assert!(created.group.group_id.starts_with("bcs_grp_dm_"));
    assert_eq!(created.group.group_id.chars().count(), 43);
}

#[tokio::test]
async fn create_dm_enforces_human_to_bot_reachability() {
    let protected = Fixture::new()
        .with_human("human_bob", "Bob")
        .with_bot("protected", "Protected", "protected", Some("owner"));
    let protected_service = protected.service_with_limits(5, 10, 10);
    let protected_err = protected_service
        .create_dm(DmCreateCommand {
            group_id: None,
            caller_actor_id: Some("human_bob".to_string()),
            driver_bot: None,
            target_actor_id: "protected".to_string(),
            label: None,
            topic: None,
            context: None,
        })
        .await
        .expect_err("unrelated human cannot reach protected bot");
    assert!(matches!(protected_err, GroupUseCaseError::Forbidden(_)));

    let private = Fixture::new()
        .with_human("human_bob", "Bob")
        .with_bot("private", "Private", "private", Some("owner"));
    let private_service = private.service_with_limits(5, 10, 10);
    let private_err = private_service
        .create_dm(DmCreateCommand {
            group_id: None,
            caller_actor_id: Some("human_bob".to_string()),
            driver_bot: None,
            target_actor_id: "private".to_string(),
            label: None,
            topic: None,
            context: None,
        })
        .await
        .expect_err("unrelated human sees private bot as unreachable");
    assert!(matches!(
        private_err,
        GroupUseCaseError::Service(ServiceError::BotNotFound(bot_id)) if bot_id == "private"
    ));

    let related = Fixture::new()
        .with_human("human_bob", "Bob")
        .with_bot("private", "Private", "private", Some("owner"))
        .with_friendship("human_bob", "private");
    let related_service = related.service_with_limits(5, 10, 10);
    let allowed = related_service
        .create_dm(DmCreateCommand {
            group_id: Some("related-dm".to_string()),
            caller_actor_id: Some("human_bob".to_string()),
            driver_bot: None,
            target_actor_id: "private".to_string(),
            label: None,
            topic: None,
            context: None,
        })
        .await
        .expect("friendship allows private DM");
    assert!(allowed.created);
}

#[tokio::test]
async fn create_dm_rejects_mismatched_driver_bot_for_human_caller() {
    let fixture = Fixture::new()
        .with_human("human_alice", "Alice")
        .with_bot("assistant", "Assistant", "public", Some("alice"))
        .with_bot("other", "Other", "public", Some("alice"));
    let service = fixture.service_with_limits(5, 10, 10);

    let err = service
        .create_dm(DmCreateCommand {
            group_id: Some("dm-under-test".to_string()),
            caller_actor_id: Some("human_alice".to_string()),
            driver_bot: Some("other".to_string()),
            target_actor_id: "assistant".to_string(),
            label: None,
            topic: None,
            context: None,
        })
        .await
        .expect_err("Human-Bot DM driver_bot is only legacy advisory for target bot");

    assert!(matches!(
        err,
        GroupUseCaseError::InvalidProposal(message) if message.contains("driver_bot must match")
    ));
}

#[tokio::test]
async fn workbench_human_bot_dm_rejects_owner_bot_proxy_sender_by_default() {
    let fixture = Fixture::new().with_human("human_alice", "Alice").with_bot(
        "assistant",
        "Assistant",
        "public",
        Some("alice"),
    );
    let service = fixture.service_with_limits(5, 10, 10);
    service
        .create_dm(DmCreateCommand {
            group_id: Some("dm-under-test".to_string()),
            caller_actor_id: Some("human_alice".to_string()),
            driver_bot: None,
            target_actor_id: "assistant".to_string(),
            label: None,
            topic: None,
            context: None,
        })
        .await
        .unwrap();

    service
        .authorize_chat_send(WorkbenchChatAuthorizationCommand {
            bound_actor_id: Some("human_alice".to_string()),
            group_id: "dm-under-test".to_string(),
            from_actor_id: "human_alice".to_string(),
            session_id: None,
        })
        .await
        .expect("bound human participant can speak in DM");

    let proxy = service
        .authorize_chat_send(WorkbenchChatAuthorizationCommand {
            bound_actor_id: Some("human_alice".to_string()),
            group_id: "dm-under-test".to_string(),
            from_actor_id: "assistant".to_string(),
            session_id: None,
        })
        .await;
    assert!(matches!(proxy, Err(WorkbenchUseCaseError::ForbiddenSender)));
}

#[tokio::test]
async fn delete_group_enforces_legacy_driver_and_dm_rules() {
    let fixture = Fixture::new().with_bot("driver", "Driver", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);
    service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .unwrap();

    let wrong_driver = service
        .delete_group(GroupDeleteCommand {
            caller_actor_id: "other".to_string(),
            group_id: "group-under-test".to_string(),
        })
        .await
        .expect_err("only driver can delete a normal group");
    assert!(matches!(
        wrong_driver,
        GroupUseCaseError::Forbidden(_)
    ));

    let deleted = service
        .delete_group(GroupDeleteCommand {
            caller_actor_id: "driver".to_string(),
            group_id: "group-under-test".to_string(),
        })
        .await
        .unwrap();
    assert!(deleted.deleted);
    assert!(fixture.group.get("group-under-test").await.is_none());

    let mut dm = Group::new(
        "dm-under-test",
        "driver",
        vec![bcs_service_api::Participant::bot(
            "driver",
            bcs_service_api::ParticipantRole::Driver,
        )],
    );
    dm.group_kind = bcs_service_api::GroupKind::Dm;
    fixture.group.upsert(dm).await.unwrap();
    let dm_error = service
        .delete_group(GroupDeleteCommand {
            caller_actor_id: "driver".to_string(),
            group_id: "dm-under-test".to_string(),
        })
        .await
        .expect_err("dm groups keep the legacy delete prohibition");
    assert!(matches!(
        dm_error,
        GroupUseCaseError::InvalidProposal(message)
            if message.contains("DM groups")
    ));
}

#[tokio::test]
async fn delete_group_is_idempotent_when_group_is_missing() {
    let fixture = Fixture::new();
    let result = fixture
        .service_with_limits(5, 10, 10)
        .delete_group(GroupDeleteCommand {
            caller_actor_id: "driver".to_string(),
            group_id: "missing-group".to_string(),
        })
        .await
        .expect("missing group deletion should be idempotent");

    assert_eq!(result.group_id, "missing-group");
    assert!(!result.deleted);
}

#[tokio::test]
async fn delete_group_cleans_up_channel_bindings() {
    let fixture = Fixture::new().with_bot("driver", "Driver", "public", None);
    let cleanup = Arc::new(RecordingChannelBindingCleanup::default());
    let service = fixture
        .service_with_limits(5, 10, 10)
        .with_channel_binding_cleanup(cleanup.clone());
    service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .unwrap();

    let deleted = service
        .delete_group(GroupDeleteCommand {
            caller_actor_id: "driver".to_string(),
            group_id: "group-under-test".to_string(),
        })
        .await
        .expect("group and channel bindings should be deleted together");

    assert!(deleted.deleted);
    assert!(fixture.group.get("group-under-test").await.is_none());
    assert_eq!(
        cleanup.deleted_group_ids.lock().await.as_slice(),
        ["group-under-test"]
    );
}

#[tokio::test]
async fn delete_group_restores_group_when_channel_binding_cleanup_fails() {
    let fixture = Fixture::new().with_bot("driver", "Driver", "public", None);
    let cleanup = Arc::new(RecordingChannelBindingCleanup::failing());
    let service = fixture
        .service_with_limits(5, 10, 10)
        .with_channel_binding_cleanup(cleanup);
    service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .unwrap();

    let error = service
        .delete_group(GroupDeleteCommand {
            caller_actor_id: "driver".to_string(),
            group_id: "group-under-test".to_string(),
        })
        .await
        .expect_err("binding cleanup failure must fail group deletion");

    assert!(matches!(
        error,
        GroupUseCaseError::Service(ServiceError::InternalError(ref message))
            if message == "channel binding cleanup failed"
    ));
    assert!(
        fixture.group.get("group-under-test").await.is_some(),
        "group must be restored when binding cleanup fails"
    );
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

    let workspace = service
        .update_workspace(GroupUpdateWorkspaceCommand {
            caller_actor_id: None,
            group_id: "group-under-test".to_string(),
            workspace: Workspace {
                decisions: vec!["ship".to_string()],
                ..Default::default()
            },
        })
        .await
        .unwrap();
    assert_eq!(workspace.workspace.decisions, vec!["ship".to_string()]);

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

#[tokio::test]
async fn participant_mode_update_authorizes_self_or_creator_and_inserts_human() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", None)
        .with_bot("member", "Member", "public", Some("alice"))
        .with_human("human_alice", "Alice");
    fixture
        .relation
        .insert_creator("human_alice", "member", "dev");
    let service = fixture.service_with_limits(5, 10, 10);
    service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![
                participant("driver", Some("driver")),
                participant("member", None),
            ],
        ))
        .await
        .unwrap();

    let member_update = service
        .update_participant_mode(GroupParticipantModeCommand {
            caller_actor_id: "member".to_string(),
            group_id: "group-under-test".to_string(),
            actor_id: "member".to_string(),
            mode: bcs_service_api::ParticipantMode::Muted,
        })
        .await
        .unwrap();
    assert_eq!(member_update.mode, bcs_service_api::ParticipantMode::Muted);

    let human_update = service
        .update_participant_mode(GroupParticipantModeCommand {
            caller_actor_id: "human_alice".to_string(),
            group_id: "group-under-test".to_string(),
            actor_id: "human_alice".to_string(),
            mode: bcs_service_api::ParticipantMode::Present,
        })
        .await
        .unwrap();
    assert_eq!(human_update.actor_id, "human_alice");

    let stored = fixture.group.get("group-under-test").await.unwrap();
    let human = stored
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == "human_alice")
        .expect("human participant should be inserted on first mode update");
    assert_eq!(human.actor_kind, ActorKind::Human);
    assert_eq!(human.mode, Some(bcs_service_api::ParticipantMode::Present));

    let forbidden = service
        .update_participant_mode(GroupParticipantModeCommand {
            caller_actor_id: "driver".to_string(),
            group_id: "group-under-test".to_string(),
            actor_id: "member".to_string(),
            mode: bcs_service_api::ParticipantMode::Auto,
        })
        .await
        .expect_err("non-self non-creator caller is forbidden");
    assert!(matches!(forbidden, GroupUseCaseError::Forbidden(_)));
}

#[tokio::test]
async fn workbench_session_service_connects_for_owner_and_authorizes_owned_sender() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_bot("helper", "Helper", "public", Some("alice"));
    let service = fixture.service_with_limits(5, 10, 10);
    service
        .create_group(create_cmd(
            Some("human_alice"),
            "driver",
            vec![
                participant("driver", Some("driver")),
                participant("helper", Some("consultant")),
            ],
        ))
        .await
        .unwrap();

    let connected = service
        .connect(WorkbenchConnectCommand {
            bound_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            session_id: None,
        })
        .await
        .expect("human owner can connect");

    assert_eq!(connected.group_id, "group-under-test");
    assert_eq!(connected.participants.len(), 2);
    assert!(
        connected
            .participants
            .iter()
            .any(|participant| participant.bot_uuid == "driver" && participant.role == "driver")
    );

    service
        .authorize_chat_send(WorkbenchChatAuthorizationCommand {
            bound_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            from_actor_id: "helper".to_string(),
            session_id: None,
        })
        .await
        .expect("human owner can send as owned bot");
}

#[tokio::test]
async fn workbench_normal_group_allows_bound_human_sender_when_participant() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);
    service
        .create_group(create_cmd(
            Some("human_alice"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .unwrap();
    service
        .update_participant_mode(GroupParticipantModeCommand {
            caller_actor_id: "human_alice".to_string(),
            group_id: "group-under-test".to_string(),
            actor_id: "human_alice".to_string(),
            mode: bcs_service_api::ParticipantMode::Present,
        })
        .await
        .unwrap();

    service
        .authorize_chat_send(WorkbenchChatAuthorizationCommand {
            bound_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            from_actor_id: "human_alice".to_string(),
            session_id: None,
        })
        .await
        .expect("normal group should still allow bound Human actor sender");
}

#[tokio::test]
async fn workbench_chat_send_allows_bound_human_sender_when_present_in_session() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("owner"))
        .with_human("human_alice", "Alice");
    let session_id = "group-under-test:abcdef12".to_string();
    let session = test_session(
        &session_id,
        "group-under-test",
        vec![
            Participant::bot("driver", ParticipantRole::Driver),
            {
                let mut human = Participant::human("human_alice", ParticipantRole::Observer);
                human.mode = Some(ParticipantMode::Present);
                human
            },
        ],
    );
    let service = fixture
        .service_with_limits_and_session(5, 10, 10, Arc::new(StaticSessionManagement::new(session)));
    service
        .create_group(create_cmd(
            Some("human_owner"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .unwrap();

    service
        .authorize_chat_send(WorkbenchChatAuthorizationCommand {
            bound_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            from_actor_id: "human_alice".to_string(),
            session_id: Some(session_id),
        })
        .await
        .expect("session participants should be allowed to send inside that session");
}

#[tokio::test]
async fn workbench_session_service_rejects_invalid_sender_states() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_bot("helper", "Helper", "public", Some("alice"))
        .with_bot("outside", "Outside", "public", Some("alice"))
        .with_bot("bob-bot", "Bob Bot", "public", Some("bob"));
    let service = fixture.service_with_limits(5, 10, 10);
    service
        .create_group(create_cmd(
            Some("human_alice"),
            "driver",
            vec![
                participant("driver", Some("driver")),
                participant("helper", Some("consultant")),
                participant("bob-bot", Some("consultant")),
            ],
        ))
        .await
        .unwrap();

    let no_cookie = service
        .connect(WorkbenchConnectCommand {
            bound_actor_id: None,
            group_id: "group-under-test".to_string(),
            session_id: None,
        })
        .await;
    assert!(matches!(no_cookie, Err(WorkbenchUseCaseError::Unauthorized)));

    let no_group_access = service
        .connect(WorkbenchConnectCommand {
            bound_actor_id: Some("human_charlie".to_string()),
            group_id: "group-under-test".to_string(),
            session_id: None,
        })
        .await;
    assert!(matches!(
        no_group_access,
        Err(WorkbenchUseCaseError::ForbiddenGroupAccess)
    ));

    let mut group = fixture.group.get("group-under-test").await.unwrap();
    let helper = group
        .participants
        .iter_mut()
        .find(|participant| participant.bot_uuid == "helper")
        .expect("helper participant");
    helper.mode = Some(bcs_service_api::ParticipantMode::Absent);
    fixture.group.upsert(group).await.unwrap();

    let absent_sender = service
        .authorize_chat_send(WorkbenchChatAuthorizationCommand {
            bound_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            from_actor_id: "helper".to_string(),
            session_id: None,
        })
        .await;
    assert!(matches!(
        absent_sender,
        Err(WorkbenchUseCaseError::ParticipantAbsent)
    ));

    let outside_sender = service
        .authorize_chat_send(WorkbenchChatAuthorizationCommand {
            bound_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            from_actor_id: "outside".to_string(),
            session_id: None,
        })
        .await;
    assert!(matches!(
        outside_sender,
        Err(WorkbenchUseCaseError::SenderNotInGroup)
    ));

    let not_owned_sender = service
        .authorize_chat_send(WorkbenchChatAuthorizationCommand {
            bound_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            from_actor_id: "bob-bot".to_string(),
            session_id: None,
        })
        .await;
    assert!(matches!(
        not_owned_sender,
        Err(WorkbenchUseCaseError::ForbiddenSender)
    ));
}

#[tokio::test]
async fn workbench_connect_allows_bound_human_when_present_in_session() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("owner"))
        .with_human("human_alice", "Alice");
    let session_id = "group-under-test:abcdef12".to_string();
    let session = test_session(
        &session_id,
        "group-under-test",
        vec![
            Participant::bot("driver", ParticipantRole::Driver),
            {
                let mut human = Participant::human("human_alice", ParticipantRole::Observer);
                human.mode = Some(ParticipantMode::Present);
                human
            },
        ],
    );
    let service = fixture
        .service_with_limits_and_session(5, 10, 10, Arc::new(StaticSessionManagement::new(session)));
    service
        .create_group(create_cmd(
            Some("human_owner"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .unwrap();

    let connected = service
        .connect(WorkbenchConnectCommand {
            bound_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            session_id: Some(session_id),
        })
        .await
        .expect("session participants should be allowed to connect to that session");

    assert_eq!(connected.group_id, "group-under-test");
    assert!(connected
        .participants
        .iter()
        .any(|participant| participant.bot_uuid == "human_alice"));
}

struct StaticSessionManagement {
    session: Session,
    fail_create: bool,
    commands: Mutex<Vec<bcs_service_api::CreateOrReactivateCommand>>,
}

impl StaticSessionManagement {
    fn new(session: Session) -> Self {
        Self {
            session,
            fail_create: false,
            commands: Mutex::new(Vec::new()),
        }
    }

    fn failing_create(session: Session) -> Self {
        Self {
            session,
            fail_create: true,
            commands: Mutex::new(Vec::new()),
        }
    }
}

#[async_trait]
impl SessionManagementService for StaticSessionManagement {
    async fn create_or_reactivate(
        &self,
        cmd: bcs_service_api::CreateOrReactivateCommand,
    ) -> Result<bcs_service_api::CreateOrReactivateOutcome, SessionUseCaseError> {
        self.commands.lock().await.push(cmd);
        if self.fail_create {
            return Err(SessionUseCaseError::Internal(ServiceError::InternalError(
                "session create failed".to_string(),
            )));
        }
        Ok(bcs_service_api::CreateOrReactivateOutcome {
            session: self.session.clone(),
            created: true,
        })
    }

    async fn get(&self, session_id: &str) -> Result<Option<Session>, SessionUseCaseError> {
        Ok((self.session.id == session_id).then(|| self.session.clone()))
    }

    async fn belongs_to_group(
        &self,
        session_id: &str,
        group_id: &str,
    ) -> Result<bool, SessionUseCaseError> {
        Ok(self.session.id == session_id && self.session.group_id == group_id)
    }

    async fn list_by_group(
        &self,
        _group_id: &str,
        _status: Option<SessionStatus>,
        _offset: u64,
        _limit: u64,
        _title_contains: Option<&str>,
        _participant_id: Option<&str>,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn count_running_service(&self, _group_id: &str) -> Result<u64, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn list_running_service(
        &self,
        _offset: u64,
        _limit: u64,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn update_callback_status(
        &self,
        _session_id: &str,
        _status: &str,
    ) -> Result<(), SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn complete_if_running(
        &self,
        _session_id: &str,
        _output: Option<serde_json::Value>,
        _error: Option<String>,
    ) -> Result<Option<Session>, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn add_participant(
        &self,
        _session_id: &str,
        _participant: Participant,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn remove_participant(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn update_participant_mode(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
        _mode: ParticipantMode,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn update_title(
        &self,
        _session_id: &str,
        _title: Option<String>,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn list_group_ids_by_session_participant(
        &self,
        _bot_uuid: &str,
    ) -> Result<Vec<String>, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn delete(&self, _session_id: &str) -> Result<bool, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }
}

fn test_session(session_id: &str, group_id: &str, participants: Vec<Participant>) -> Session {
    Session {
        id: session_id.to_string(),
        group_id: group_id.to_string(),
        session_title: None,
        env: None,
        status: SessionStatus::Running,
        session_kind: SessionKind::Chat,
        participants,
        group_version: Some(1),
        caller_id: None,
        input: None,
        output: None,
        error_message: None,
        callback_status: None,
        activation_count: 1,
        caller_principal: None,
        created_by: None,
        created_at: 1,
        updated_at: 1,
        completed_at: None,
        meta: None,
        current_msg_seq: 0,
        participant_join_seq: None,
        collected_at: None,
    }
}

struct Fixture {
    group: Arc<GroupStore>,
    registry: Arc<FakeRegistry>,
    friend: Arc<FakeFriendCoreService>,
    relation: Arc<FakeRelationCoreService>,
    provider_downlink_bots: Arc<RwLock<HashSet<String>>>,
}

impl Fixture {
    fn new() -> Self {
        Self {
            group: Arc::new(GroupStore::new()),
            registry: Arc::new(FakeRegistry::default()),
            friend: Arc::new(FakeFriendCoreService::default()),
            relation: Arc::new(FakeRelationCoreService::default()),
            provider_downlink_bots: Arc::new(RwLock::new(HashSet::new())),
        }
    }

    fn service_with_limits(
        &self,
        max_group_members: usize,
        max_groups_as_driver: usize,
        max_groups_as_member: usize,
    ) -> GroupManagement {
        self.service_with_limits_and_session(
            max_group_members,
            max_groups_as_driver,
            max_groups_as_member,
            Arc::new(StaticSessionManagement::new(test_session(
                "group-under-test:abcdef12",
                "group-under-test",
                Vec::new(),
            ))),
        )
    }

    fn service_with_limits_and_session(
        &self,
        max_group_members: usize,
        max_groups_as_driver: usize,
        max_groups_as_member: usize,
        session_management: Arc<dyn SessionManagementService>,
    ) -> GroupManagement {
        GroupManagement::new(
            self.group.clone(),
            self.registry.clone(),
            self.friend.clone(),
            self.relation.clone(),
            GroupConfig {
                max_group_members,
                max_groups_as_driver,
                max_groups_as_member,
                relation_env: "dev".to_string(),
            },
            session_management,
            Arc::new(NoopSystemMessageService),
        )
        .with_bot_runtime(Arc::new(FakeBotRuntimeConnectionService {
            provider_downlink_bots: self.provider_downlink_bots.clone(),
        }))
    }

    fn with_bot(
        self,
        bot_uuid: &str,
        name: &str,
        visibility: &str,
        created_by: Option<&str>,
    ) -> Self {
        self.registry
            .insert(bot(bot_uuid, name, visibility, created_by));
        self
    }

    fn with_human(self, actor_id: &str, name: &str) -> Self {
        self.registry.insert(human(actor_id, name));
        self
    }

    fn with_provider_downlink_bot(
        self,
        bot_uuid: &str,
        name: &str,
        visibility: &str,
        created_by: Option<&str>,
    ) -> Self {
        self.registry
            .insert(bot(bot_uuid, name, visibility, created_by));
        self.provider_downlink_bots
            .write()
            .unwrap()
            .insert(bot_uuid.to_string());
        self
    }

    fn with_friendship(self, a: &str, b: &str) -> Self {
        self.friend.insert(a, b);
        self
    }
}

struct FakeBotRuntimeConnectionService {
    provider_downlink_bots: Arc<RwLock<HashSet<String>>>,
}

#[async_trait]
impl BotRuntimeConnectionService for FakeBotRuntimeConnectionService {
    async fn connect_streaming(
        &self,
        _command: BotRuntimeConnectCommand,
    ) -> Result<BotRuntimeConnectOutcome, BotUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn update_runtime_status(
        &self,
        _command: BotRuntimeStatusCommand,
    ) -> Result<BotRuntimeStatusOutcome, BotUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn disconnect_streaming(
        &self,
        _command: BotRuntimeDisconnectCommand,
    ) -> Result<(), BotUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn is_provider_downlink_bot(&self, bot_id: &str) -> ServiceResult<bool> {
        Ok(self
            .provider_downlink_bots
            .read()
            .unwrap()
            .contains(bot_id))
    }

    async fn resolve_delivery_target(&self, bot_id: &str) -> ServiceResult<BotDeliveryTarget> {
        Ok(BotDeliveryTarget::WebSocket {
            bot_id: bot_id.to_string(),
        })
    }
}

fn create_cmd(
    caller_actor_id: Option<&str>,
    driver_bot_id: &str,
    participants: Vec<GroupCreateParticipantCommand>,
) -> GroupCreateCommand {
    GroupCreateCommand {
        group_id: Some("group-under-test".to_string()),
        caller_actor_id: caller_actor_id.map(str::to_string),
        driver_bot_id: driver_bot_id.to_string(),
        originator: None,
        label: None,
        topic: Some("debug incident".to_string()),
        context: Some("prod checkout outage".to_string()),
        routing_policy: None,
        member_bot_ids: Vec::new(),
        participants,
        group_kind: None,
        service_spec: None,
        group_strategy: None,
        visibility: None,
    }
}

fn participant(bot_id: &str, role: Option<&str>) -> GroupCreateParticipantCommand {
    GroupCreateParticipantCommand {
        bot_id: bot_id.to_string(),
        role: role.map(str::to_string),
    }
}

fn bot(bot_uuid: &str, name: &str, visibility: &str, created_by: Option<&str>) -> RegisteredBot {
    RegisteredBot {
        bot_uuid: bot_uuid.to_string(),
        capabilities: BotCapabilities {
            name: Some(name.to_string()),
            visibility: visibility.to_string(),
            ..Default::default()
        },
        dynamic_status: BotDynamicStatus::default(),
        env: None,
        created_by: created_by.map(str::to_string),
        actor_kind: ActorKind::Bot,
        status: Default::default(),
    }
}

fn human(actor_id: &str, name: &str) -> RegisteredBot {
    RegisteredBot {
        bot_uuid: actor_id.to_string(),
        capabilities: BotCapabilities {
            name: Some(name.to_string()),
            visibility: "private".to_string(),
            ..Default::default()
        },
        dynamic_status: BotDynamicStatus::default(),
        env: None,
        created_by: None,
        actor_kind: ActorKind::Human,
        status: Default::default(),
    }
}

#[derive(Default)]
struct FakeRegistry {
    bots: RwLock<HashMap<String, RegisteredBot>>,
}

impl FakeRegistry {
    fn insert(&self, bot: RegisteredBot) {
        self.bots.write().unwrap().insert(bot.bot_uuid.clone(), bot);
    }
}

#[async_trait]
impl BotRegistryCoreService for FakeRegistry {
    async fn register(&self, bot_id: String, capabilities: BotCapabilities) -> ServiceResult<()> {
        self.bots.write().unwrap().insert(
            bot_id.clone(),
            RegisteredBot {
                bot_uuid: bot_id,
                capabilities,
                dynamic_status: BotDynamicStatus::default(),
                env: None,
                created_by: None,
                actor_kind: ActorKind::Bot,
                status: Default::default(),
            },
        );
        Ok(())
    }

    async fn update_status(&self, _bot_id: &str, _status: BotDynamicStatus) -> bool {
        true
    }

    async fn get(&self, bot_id: &str) -> Option<RegisteredBot> {
        self.bots.read().unwrap().get(bot_id).cloned()
    }

    async fn get_agent_credentials(&self, _bot_id: &str) -> Option<AgentCredentials> {
        None
    }

    async fn list_active(&self) -> Vec<RegisteredBot> {
        self.bots.read().unwrap().values().cloned().collect()
    }

    async fn list_bots_by_creator(&self, created_by: &str) -> Vec<RegisteredBot> {
        self.bots
            .read()
            .unwrap()
            .values()
            .filter(|bot| bot.created_by.as_deref() == Some(created_by))
            .cloned()
            .collect()
    }

    async fn discover(&self, _query: &str) -> Vec<RegisteredBot> {
        self.list_active().await
    }

    async fn find_by_skills(&self, _skills: &[&str]) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn find_by_domains(&self, _domains: &[&str]) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn find_by_scopes(&self, _scopes: &[&str]) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn unregister(&self, bot_id: &str) -> bool {
        self.bots.write().unwrap().remove(bot_id).is_some()
    }

    async fn cleanup_expired(&self) {}

    async fn load_from_storage(&self, _bot_id: &str) -> Option<BotCapabilities> {
        None
    }

    async fn save_to_storage(&self, _bot_id: &str, _caps: &BotCapabilities) -> ServiceResult<()> {
        Ok(())
    }

    async fn update_visibility(&self, bot_id: &str, visibility: &str) -> ServiceResult<()> {
        if let Some(bot) = self.bots.write().unwrap().get_mut(bot_id) {
            bot.capabilities.visibility = visibility.to_string();
        }
        Ok(())
    }

    #[allow(deprecated)]
    async fn set_hidden(&self, _bot_id: &str, _hidden: bool) -> ServiceResult<()> {
        Ok(())
    }

    async fn has_been_onboarded(&self, bot_id: &str) -> bool {
        self.bots.read().unwrap().contains_key(bot_id)
    }

    async fn save_created_by(
        &self,
        bot_id: &str,
        created_by: &str,
        overwrite: bool,
    ) -> ServiceResult<()> {
        if let Some(bot) = self.bots.write().unwrap().get_mut(bot_id) {
            if overwrite || bot.created_by.is_none() {
                bot.created_by = Some(created_by.to_string());
            }
        }
        Ok(())
    }

    async fn save_token(&self, _bot_id: &str, _token: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn load_token(&self, _bot_id: &str) -> Option<String> {
        None
    }

    async fn find_bot_by_token(&self, _token: &str) -> Option<String> {
        None
    }

    async fn register_streaming_connection(&self, _bot_id: String) -> Result<String, ()> {
        Err(())
    }

    async fn reconnect_streaming(&self, _existing_token: String) -> Result<(String, String), ()> {
        Err(())
    }

    async fn disconnect_streaming(&self, _bot_id: &str) {}

    async fn is_connected(&self, _bot_id: &str) -> bool {
        false
    }

    async fn send_frame(&self, _bot_id: &str, _frame: String) -> Result<(), ()> {
        Err(())
    }

    async fn list_connected(&self) -> Vec<String> {
        Vec::new()
    }

    async fn store_token_mapping(&self, _token: String, _bot_id: String) {}

    async fn register_http_connection(&self, _bot_id: String, token: String) -> String {
        token
    }
}

#[derive(Default)]
struct FakeFriendCoreService {
    pairs: RwLock<HashSet<(String, String)>>,
}

#[derive(Default)]
struct FakeRelationCoreService {
    edges: RwLock<HashMap<(String, String, String), RelationEdge>>,
}

impl FakeRelationCoreService {
    fn insert_creator(&self, from_id: &str, to_id: &str, env: &str) {
        self.edges.write().unwrap().insert(
            (from_id.to_string(), to_id.to_string(), env.to_string()),
            RelationEdge {
                from_id: from_id.to_string(),
                to_id: to_id.to_string(),
                env: env.to_string(),
                kinds: 0,
                allow: 0,
                deny: 0,
                is_creator: true,
            },
        );
    }
}

#[async_trait]
impl RelationCoreService for FakeRelationCoreService {
    async fn upsert_edge(&self, edge: RelationEdge) -> ServiceResult<()> {
        self.edges.write().unwrap().insert(
            (edge.from_id.clone(), edge.to_id.clone(), edge.env.clone()),
            edge,
        );
        Ok(())
    }

    async fn delete_edge(&self, from_id: &str, to_id: &str, env: &str) -> ServiceResult<()> {
        self.edges.write().unwrap().remove(&(
            from_id.to_string(),
            to_id.to_string(),
            env.to_string(),
        ));
        Ok(())
    }

    async fn get_edge(
        &self,
        from_id: &str,
        to_id: &str,
        env: &str,
    ) -> ServiceResult<Option<RelationEdge>> {
        Ok(self
            .edges
            .read()
            .unwrap()
            .get(&(from_id.to_string(), to_id.to_string(), env.to_string()))
            .cloned())
    }

    async fn ensure_owner_edges(
        &self,
        human_id: &str,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<()> {
        self.insert_creator(human_id, bot_id, env);
        Ok(())
    }

    async fn ensure_owner_edges_counted(
        &self,
        human_id: &str,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<EnsureOwnerEdgesResult> {
        self.insert_creator(human_id, bot_id, env);
        Ok(EnsureOwnerEdgesResult {
            created: 1,
            upgraded: 0,
        })
    }

    async fn add_friend_edges(&self, _a: &str, _b: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn remove_friend_edges(&self, _a: &str, _b: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn remove_all_friend_edges(&self, _actor_id: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn add_relation_edge(&self, caller: &str, target: &str, env: &str) -> ServiceResult<()> {
        self.upsert_edge(RelationEdge {
            from_id: caller.to_string(),
            to_id: target.to_string(),
            env: env.to_string(),
            kinds: 0,
            allow: 0,
            deny: 0,
            is_creator: false,
        })
        .await
    }

    async fn list_friends_via_relation(
        &self,
        _actor_id: &str,
        _env: &str,
    ) -> ServiceResult<Vec<String>> {
        Ok(Vec::new())
    }
}

impl FakeFriendCoreService {
    fn insert(&self, a: &str, b: &str) {
        self.pairs.write().unwrap().insert(canonical_pair(a, b));
    }
}

#[async_trait]
impl FriendCoreService for FakeFriendCoreService {
    async fn list_friends(&self, bot_id: &str) -> Vec<String> {
        self.pairs
            .read()
            .unwrap()
            .iter()
            .filter_map(|(a, b)| {
                if a == bot_id {
                    Some(b.clone())
                } else if b == bot_id {
                    Some(a.clone())
                } else {
                    None
                }
            })
            .collect()
    }

    async fn are_friends(&self, bot_a: &str, bot_b: &str) -> bool {
        self.pairs
            .read()
            .unwrap()
            .contains(&canonical_pair(bot_a, bot_b))
    }

    async fn are_all_friends(&self, bot_id: &str, others: &[String]) -> ServiceResult<()> {
        let pairs = self.pairs.read().unwrap();
        let not_friends: Vec<String> = others
            .iter()
            .filter(|other| !pairs.contains(&canonical_pair(bot_id, other)))
            .cloned()
            .collect();
        if not_friends.is_empty() {
            Ok(())
        } else {
            Err(ServiceError::NotFriends(not_friends))
        }
    }

    async fn add_friendship(&self, bot_a: &str, bot_b: &str) -> ServiceResult<()> {
        self.insert(bot_a, bot_b);
        Ok(())
    }

    async fn remove_all_friendships(&self, bot_id: &str) -> ServiceResult<usize> {
        let mut pairs = self.pairs.write().unwrap();
        let before = pairs.len();
        pairs.retain(|(a, b)| a != bot_id && b != bot_id);
        Ok(before - pairs.len())
    }
}

#[tokio::test]
async fn create_group_persists_service_spec() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", None)
        .with_bot("helper", "Helper", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);

    let mut cmd = create_cmd(
        Some("driver"),
        "driver",
        vec![
            participant("driver", Some("driver")),
            participant("helper", Some("consultant")),
        ],
    );
    cmd.service_spec = Some(bcs_service_api::ServiceSpec {
        callback_config: None,
        timeout_seconds: Some(60),
        max_concurrency: Some(8),
    });

    service.create_group(cmd).await.unwrap();
    let stored = fixture.group.get("group-under-test").await
        .expect("group should be persisted");
    let spec = stored.service_spec
        .expect("service_spec should round-trip through create_group");
    assert_eq!(spec.timeout_seconds, Some(60));
    assert_eq!(spec.max_concurrency, Some(8));
    assert!(spec.callback_config.is_none());
}

#[tokio::test]
async fn create_group_rejects_private_baas_callback_base_url() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", None)
        .with_bot("helper", "Helper", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);

    let mut cmd = create_cmd(
        Some("driver"),
        "driver",
        vec![
            participant("driver", Some("driver")),
            participant("helper", Some("consultant")),
        ],
    );
    cmd.service_spec = Some(bcs_service_api::ServiceSpec {
        callback_config: Some(bcs_service_api::CallbackConfig {
            channels: vec![bcs_service_api::CallbackChannelConfig::Baas {
                base_url: "http://169.254.169.254/latest/meta-data".to_string(),
                api_key: "sk-test".to_string(),
                bot_id: "default:callback-test".to_string(),
                metadata: None,
            }],
        }),
        timeout_seconds: Some(60),
        max_concurrency: Some(8),
    });

    let err = service
        .create_group(cmd)
        .await
        .expect_err("private BaaS callback base_url should be rejected");

    assert!(matches!(err, GroupUseCaseError::InvalidProposal(message) if message.contains("base_url is not allowed")));
    assert!(fixture.group.get("group-under-test").await.is_none());
}

#[tokio::test]
async fn create_group_with_human_consultant_chat() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_bot("helper", "Helper", "public", None)
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);

    let result = service
        .create_group(create_cmd(
            Some("human_alice"),
            "driver",
            vec![
                participant("driver", Some("driver")),
                participant("helper", Some("consultant")),
                participant("human_alice", Some("consultant")),
            ],
        ))
        .await
        .expect("human consultant in chat group should succeed");
    assert_eq!(result.participants.len(), 3);

    let human = result
        .participants
        .iter()
        .find(|p| p.bot_uuid == "human_alice")
        .expect("human participant");
    assert_eq!(human.actor_kind, ActorKind::Human);
    assert_eq!(human.mode, Some(ParticipantMode::Present));
    assert_eq!(human.role, "consultant");
}

#[tokio::test]
async fn create_group_with_human_worker_manager_worker() {
    let fixture = Fixture::new()
        .with_bot("mgr", "Manager", "public", Some("alice"))
        .with_bot("worker-bot", "Worker", "public", None)
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);

    let mut cmd = create_cmd(
        Some("human_alice"),
        "mgr",
        vec![
            participant("mgr", Some("manager")),
            participant("worker-bot", Some("worker")),
            participant("human_alice", Some("worker")),
        ],
    );
    cmd.group_strategy = Some(bcs_service_api::GroupStrategy::ManagerWorker);

    let result = service
        .create_group(cmd)
        .await
        .expect("human worker in manager_worker group should succeed");
    assert_eq!(result.participants.len(), 3);

    let human = result
        .participants
        .iter()
        .find(|p| p.bot_uuid == "human_alice")
        .expect("human participant");
    assert_eq!(human.mode, Some(ParticipantMode::Present));
    assert_eq!(human.role, "worker");
}

#[tokio::test]
async fn create_group_rejects_human_as_driver() {
    let fixture = Fixture::new()
        .with_bot("bot-a", "Bot A", "public", None)
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);

    let result = service
        .create_group(create_cmd(
            Some("bot-a"),
            "bot-a",
            vec![
                participant("bot-a", Some("consultant")),
                participant("human_alice", Some("driver")),
            ],
        ))
        .await;
    let err = result.expect_err("human driver should be rejected");
    assert!(matches!(err, GroupUseCaseError::InvalidProposal(ref msg) if msg.contains("consultant or observer")), "got: {:?}", err);
}

#[tokio::test]
async fn create_group_rejects_human_as_manager() {
    let fixture = Fixture::new()
        .with_bot("bot-a", "Bot A", "public", None)
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);

    let mut cmd = create_cmd(
        Some("bot-a"),
        "bot-a",
        vec![
            participant("bot-a", Some("worker")),
            participant("human_alice", Some("manager")),
        ],
    );
    cmd.group_strategy = Some(bcs_service_api::GroupStrategy::ManagerWorker);

    let err = service
        .create_group(cmd)
        .await
        .expect_err("human manager should be rejected");
    assert!(matches!(err, GroupUseCaseError::InvalidProposal(ref msg) if msg.contains("worker or observer")), "got: {:?}", err);
}

#[tokio::test]
async fn create_group_rejects_all_human_group() {
    let fixture = Fixture::new()
        .with_human("human_alice", "Alice")
        .with_human("human_bob", "Bob");
    let service = fixture.service_with_limits(5, 10, 10);

    let err = service
        .create_group(create_cmd(
            Some("human_alice"),
            "human_alice",
            vec![
                participant("human_alice", Some("driver")),
                participant("human_bob", Some("consultant")),
            ],
        ))
        .await
        .expect_err("all-human group should be rejected");
    assert!(matches!(err, GroupUseCaseError::InvalidProposal(ref msg) if msg.contains("at least one bot")), "got: {:?}", err);
}

#[tokio::test]
async fn create_group_human_owner_not_participant_ok() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_bot("helper", "Helper", "public", None)
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);

    let result = service
        .create_group(create_cmd(
            Some("human_alice"),
            "driver",
            vec![
                participant("driver", Some("driver")),
                participant("helper", Some("consultant")),
            ],
        ))
        .await
        .expect("human owner creating group without being participant should succeed");
    assert_eq!(result.participants.len(), 2);
    assert!(result.participants.iter().all(|p| p.actor_kind == ActorKind::Bot));
}

#[tokio::test]
async fn create_group_rejects_human_driver_bot_id() {
    // When caller == driver_bot_id, authorize_driver passes (self-auth).
    // Then validate_human_constraints catches the human_ prefix.
    let fixture = Fixture::new()
        .with_bot("bot-a", "Bot A", "public", None)
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);

    let result = service
        .create_group(create_cmd(
            Some("human_alice"),
            "human_alice",
            vec![
                participant("human_alice", Some("driver")),
                participant("bot-a", Some("consultant")),
            ],
        ))
        .await;
    let err = result.expect_err("human as driver_bot_id should be rejected");
    assert!(matches!(err, GroupUseCaseError::InvalidProposal(ref msg) if msg.contains("must be a bot")), "got: {:?}", err);
}

#[tokio::test]
async fn add_member_human_consultant_ok() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_human("human_bob", "Bob");
    let service = fixture.service_with_limits(5, 10, 10);
    service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .expect("create group");

    let result = service
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some("driver".to_string()),
            human_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            bot_id: "human_bob".to_string(),
            role: Some("consultant".to_string()),
        })
        .await
        .expect("adding human consultant should succeed");
    assert_eq!(result.member.actor_kind, ActorKind::Human);
    assert_eq!(result.member.mode, Some(ParticipantMode::Present));
    assert_eq!(result.member.role, "consultant");
}

#[tokio::test]
async fn add_member_human_driver_rejected() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_human("human_bob", "Bob");
    let service = fixture.service_with_limits(5, 10, 10);
    service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .expect("create group");

    let err = service
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some("driver".to_string()),
            human_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            bot_id: "human_bob".to_string(),
            role: Some("driver".to_string()),
        })
        .await
        .expect_err("human driver via add_member should be rejected");
    assert!(matches!(err, GroupUseCaseError::InvalidProposal(_)));
}

#[tokio::test]
async fn add_member_human_worker_in_manager_worker_ok() {
    let fixture = Fixture::new()
        .with_bot("mgr", "Manager", "public", Some("alice"))
        .with_human("human_bob", "Bob");
    let service = fixture.service_with_limits(5, 10, 10);

    let mut cmd = create_cmd(
        Some("mgr"),
        "mgr",
        vec![participant("mgr", Some("manager"))],
    );
    cmd.group_strategy = Some(bcs_service_api::GroupStrategy::ManagerWorker);
    service.create_group(cmd).await.expect("create mw group");

    let result = service
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some("mgr".to_string()),
            human_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            bot_id: "human_bob".to_string(),
            role: Some("worker".to_string()),
        })
        .await
        .expect("adding human worker to mw group should succeed");
    assert_eq!(result.member.actor_kind, ActorKind::Human);
    assert_eq!(result.member.mode, Some(ParticipantMode::Present));
}

#[tokio::test]
async fn add_member_human_manager_in_manager_worker_rejected() {
    let fixture = Fixture::new()
        .with_bot("mgr", "Manager", "public", Some("alice"))
        .with_human("human_bob", "Bob");
    let service = fixture.service_with_limits(5, 10, 10);

    let mut cmd = create_cmd(
        Some("mgr"),
        "mgr",
        vec![participant("mgr", Some("manager"))],
    );
    cmd.group_strategy = Some(bcs_service_api::GroupStrategy::ManagerWorker);
    service.create_group(cmd).await.expect("create mw group");

    let err = service
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some("mgr".to_string()),
            human_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            bot_id: "human_bob".to_string(),
            role: Some("manager".to_string()),
        })
        .await
        .expect_err("human manager via add_member should be rejected");
    assert!(matches!(err, GroupUseCaseError::InvalidProposal(_)));
}

#[tokio::test]
async fn originator_human_can_delete_group() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);
    service
        .create_group(create_cmd(
            Some("human_alice"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .expect("create group");

    let result = service
        .delete_group(GroupDeleteCommand {
            caller_actor_id: "human_alice".to_string(),
            group_id: "group-under-test".to_string(),
        })
        .await
        .expect("human originator should be able to delete group");
    assert!(result.deleted);
    assert!(fixture.group.get("group-under-test").await.is_none());
}

#[tokio::test]
async fn originator_human_can_add_member() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_bot("helper", "Helper", "public", None)
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);
    service
        .create_group(create_cmd(
            Some("human_alice"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .expect("create group");

    let result = service
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some("human_alice".to_string()),
            human_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            bot_id: "helper".to_string(),
            role: Some("consultant".to_string()),
        })
        .await
        .expect("human originator should be able to add member");
    assert_eq!(result.member.bot_uuid, "helper");
}

#[tokio::test]
async fn originator_human_can_update_label() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);
    service
        .create_group(create_cmd(
            Some("human_alice"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .expect("create group");

    let result = service
        .update_label(GroupUpdateLabelCommand {
            caller_actor_id: "human_alice".to_string(),
            group_id: "group-under-test".to_string(),
            label: Some("updated by originator".to_string()),
        })
        .await
        .expect("human originator should be able to update label");
    assert_eq!(result.label, Some("updated by originator".to_string()));
}

#[tokio::test]
async fn non_coordinator_cannot_delete_group() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_bot("stranger", "Stranger", "public", None)
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);
    service
        .create_group(create_cmd(
            Some("human_alice"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .expect("create group");

    let err = service
        .delete_group(GroupDeleteCommand {
            caller_actor_id: "stranger".to_string(),
            group_id: "group-under-test".to_string(),
        })
        .await
        .expect_err("stranger should not be able to delete group");
    assert!(matches!(err, GroupUseCaseError::Forbidden(_)));
}

#[tokio::test]
async fn originator_bot_self_driver_can_manage() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_bot("helper", "Helper", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);
    service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .expect("create group");

    // driver can add member (originator == driver, both work)
    let result = service
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some("driver".to_string()),
            human_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            bot_id: "helper".to_string(),
            role: Some("consultant".to_string()),
        })
        .await
        .expect("driver should still be able to add member");
    assert_eq!(result.member.bot_uuid, "helper");

    // driver can delete group
    let deleted = service
        .delete_group(GroupDeleteCommand {
            caller_actor_id: "driver".to_string(),
            group_id: "group-under-test".to_string(),
        })
        .await
        .expect("driver should be able to delete group");
    assert!(deleted.deleted);
}

#[tokio::test]
async fn originator_and_driver_different_both_can_manage() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", Some("alice"))
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);
    service
        .create_group(create_cmd(
            Some("human_alice"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .expect("create group");

    // human originator (different from driver) can update label
    service
        .update_label(GroupUpdateLabelCommand {
            caller_actor_id: "human_alice".to_string(),
            group_id: "group-under-test".to_string(),
            label: Some("originator updated".to_string()),
        })
        .await
        .expect("human originator should be able to update label");

    // driver can also update label (both are coordinators)
    service
        .update_label(GroupUpdateLabelCommand {
            caller_actor_id: "driver".to_string(),
            group_id: "group-under-test".to_string(),
            label: Some("driver updated".to_string()),
        })
        .await
        .expect("driver should also be able to update label");

    // driver can delete
    let deleted = service
        .delete_group(GroupDeleteCommand {
            caller_actor_id: "driver".to_string(),
            group_id: "group-under-test".to_string(),
        })
        .await
        .expect("driver should be able to delete group");
    assert!(deleted.deleted);
}

fn canonical_pair(a: &str, b: &str) -> (String, String) {
    if a <= b {
        (a.to_string(), b.to_string())
    } else {
        (b.to_string(), a.to_string())
    }
}

// ── Public Group Visibility Tests ────────────────────────────────────────────

#[tokio::test]
async fn create_public_group_with_all_public_bots_succeeds() {
    let fixture = Fixture::new()
        .with_bot("bot_a", "Bot A", "public", None)
        .with_bot("bot_b", "Bot B", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);

    let mut cmd = create_cmd(Some("bot_a"), "bot_a", vec![
        participant("bot_a", Some("driver")),
        participant("bot_b", Some("consultant")),
    ]);
    cmd.visibility = Some("public".to_string());

    let result = service.create_group(cmd).await;
    assert!(result.is_ok(), "error: {:?}", result.unwrap_err());
    let detail = result.unwrap();
    assert_eq!(detail.visibility, "public");
}

#[tokio::test]
async fn create_public_group_rejects_non_public_bot() {
    let fixture = Fixture::new()
        .with_bot("bot_a", "Bot A", "public", None)
        .with_bot("bot_private", "Private Bot", "protected", None)
        .with_friendship("bot_a", "bot_private");
    let service = fixture.service_with_limits(5, 10, 10);

    let mut cmd = create_cmd(Some("bot_a"), "bot_a", vec![
        participant("bot_a", Some("driver")),
        participant("bot_private", Some("consultant")),
    ]);
    cmd.visibility = Some("public".to_string());

    let result = service.create_group(cmd).await;
    assert!(result.is_err());
    let err = result.unwrap_err().to_string();
    assert!(err.contains("Group contains non-public bots"), "error: {}", err);
}

#[tokio::test]
async fn create_group_rejects_invalid_visibility() {
    let fixture = Fixture::new()
        .with_bot("bot_a", "Bot A", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);

    let mut cmd = create_cmd(Some("bot_a"), "bot_a", vec![
        participant("bot_a", Some("driver")),
    ]);
    cmd.visibility = Some("invalid".to_string());

    let result = service.create_group(cmd).await;
    assert!(result.is_err());
    assert!(
        result.unwrap_err().to_string().contains("Invalid visibility value"),
        "expected error about invalid visibility"
    );
}

#[tokio::test]
async fn update_visibility_to_public_with_all_public_bots() {
    let fixture = Fixture::new()
        .with_bot("bot_a", "Bot A", "public", None)
        .with_bot("bot_b", "Bot B", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);

    // Create private group first
    let cmd = create_cmd(Some("bot_a"), "bot_a", vec![
        participant("bot_a", Some("driver")),
        participant("bot_b", Some("consultant")),
    ]);
    service.create_group(cmd).await.unwrap();

    // Update to public
    let result = service.update_visibility(GroupUpdateVisibilityCommand {
        caller_actor_id: "bot_a".to_string(),
        group_id: "group-under-test".to_string(),
        visibility: "public".to_string(),
    }).await;
    assert!(result.is_ok(), "error: {:?}", result.unwrap_err());
    assert_eq!(result.unwrap().visibility, "public");
}

#[tokio::test]
async fn update_visibility_to_public_rejects_non_public_bot() {
    let fixture = Fixture::new()
        .with_bot("bot_a", "Bot A", "public", None)
        .with_bot("bot_private", "Private Bot", "protected", None)
        .with_friendship("bot_a", "bot_private");
    let service = fixture.service_with_limits(5, 10, 10);

    let cmd = create_cmd(Some("bot_a"), "bot_a", vec![
        participant("bot_a", Some("driver")),
        participant("bot_private", Some("consultant")),
    ]);
    service.create_group(cmd).await.unwrap();

    let result = service.update_visibility(GroupUpdateVisibilityCommand {
        caller_actor_id: "bot_a".to_string(),
        group_id: "group-under-test".to_string(),
        visibility: "public".to_string(),
    }).await;
    assert!(result.is_err());
    match result.unwrap_err() {
        GroupUseCaseError::Service(ServiceError::ExistNonPublicBots { bots }) => {
            assert_eq!(bots.len(), 1);
            assert_eq!(bots[0].0, "bot_private");
            assert_eq!(bots[0].1.as_deref(), Some("Private Bot"));
        }
        other => panic!("expected ExistNonPublicBots, got {:?}", other),
    }
}

#[tokio::test]
async fn update_visibility_to_private_always_succeeds() {
    let fixture = Fixture::new()
        .with_bot("bot_a", "Bot A", "public", None)
        .with_bot("bot_b", "Bot B", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);

    let mut cmd = create_cmd(Some("bot_a"), "bot_a", vec![
        participant("bot_a", Some("driver")),
        participant("bot_b", Some("consultant")),
    ]);
    cmd.visibility = Some("public".to_string());
    service.create_group(cmd).await.unwrap();

    let result = service.update_visibility(GroupUpdateVisibilityCommand {
        caller_actor_id: "bot_a".to_string(),
        group_id: "group-under-test".to_string(),
        visibility: "private".to_string(),
    }).await;
    assert!(result.is_ok(), "error: {:?}", result.unwrap_err());
    assert_eq!(result.unwrap().visibility, "private");
}

#[tokio::test]
async fn update_visibility_rejects_non_coordinator() {
    let fixture = Fixture::new()
        .with_bot("bot_a", "Bot A", "public", None)
        .with_bot("bot_b", "Bot B", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);

    let cmd = create_cmd(Some("bot_a"), "bot_a", vec![
        participant("bot_a", Some("driver")),
        participant("bot_b", Some("consultant")),
    ]);
    service.create_group(cmd).await.unwrap();

    let result = service.update_visibility(GroupUpdateVisibilityCommand {
        caller_actor_id: "bot_b".to_string(),
        group_id: "group-under-test".to_string(),
        visibility: "public".to_string(),
    }).await;
    assert!(result.is_err());
    assert!(result.unwrap_err().to_string().contains("coordinator"));
}

#[tokio::test]
async fn add_non_public_bot_to_public_group_rejected() {
    let fixture = Fixture::new()
        .with_bot("bot_a", "Bot A", "public", None)
        .with_bot("bot_b", "Bot B", "public", None)
        .with_bot("bot_private", "Private Bot", "protected", None)
        .with_friendship("bot_a", "bot_private");
    let service = fixture.service_with_limits(5, 10, 10);

    let mut cmd = create_cmd(Some("bot_a"), "bot_a", vec![
        participant("bot_a", Some("driver")),
        participant("bot_b", Some("consultant")),
    ]);
    cmd.visibility = Some("public".to_string());
    service.create_group(cmd).await.unwrap();

    let result = service.add_member(GroupAddMemberCommand {
        caller_actor_id: Some("bot_a".to_string()),
        human_actor_id: None,
        group_id: "group-under-test".to_string(),
        bot_id: "bot_private".to_string(),
        role: Some("consultant".to_string()),
    }).await;
    assert!(result.is_err());
    assert!(result.unwrap_err().to_string().contains("Cannot add non-public bot"));
}

#[tokio::test]
async fn add_human_to_public_group_succeeds() {
    let fixture = Fixture::new()
        .with_bot("bot_a", "Bot A", "public", None)
        .with_human("human_123", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);

    let mut cmd = create_cmd(Some("bot_a"), "bot_a", vec![
        participant("bot_a", Some("driver")),
    ]);
    cmd.visibility = Some("public".to_string());
    service.create_group(cmd).await.unwrap();

    let result = service.add_member(GroupAddMemberCommand {
        caller_actor_id: Some("bot_a".to_string()),
        human_actor_id: None,
        group_id: "group-under-test".to_string(),
        bot_id: "human_123".to_string(),
        role: Some("consultant".to_string()),
    }).await;
    assert!(result.is_ok(), "error: {:?}", result.unwrap_err());
}

#[tokio::test]
async fn list_groups_filters_by_visibility() {
    let fixture = Fixture::new()
        .with_bot("bot_a", "Bot A", "public", None)
        .with_bot("bot_b", "Bot B", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);

    // Create a private group
    let cmd = create_cmd(Some("bot_a"), "bot_a", vec![
        participant("bot_a", Some("driver")),
    ]);
    service.create_group(cmd).await.unwrap();

    // Create a public group
    let cmd2 = GroupCreateCommand {
        group_id: Some("public-group".to_string()),
        caller_actor_id: Some("bot_b".to_string()),
        driver_bot_id: "bot_b".to_string(),
        originator: None,
        label: Some("Translation".to_string()),
        topic: None,
        context: None,
        routing_policy: None,
        member_bot_ids: Vec::new(),
        participants: vec![participant("bot_b", Some("driver"))],
        group_kind: None,
        service_spec: None,
        group_strategy: None,
        visibility: Some("public".to_string()),
    };
    service.create_group(cmd2).await.unwrap();

    // Filter by visibility=public
    let result = service.list_groups(GroupListCommand {
        group_kind: None,
        offset: 0,
        limit: 10,
        visibility: Some("public".to_string()),
        label: None,
    }).await.unwrap();
    assert_eq!(result.total, 1);
    assert_eq!(result.items[0].visibility, "public");

    // Filter by label
    let result = service.list_groups(GroupListCommand {
        group_kind: None,
        offset: 0,
        limit: 10,
        visibility: Some("public".to_string()),
        label: Some("translation".to_string()),
    }).await.unwrap();
    assert_eq!(result.total, 1);
}

// ---------------------------------------------------------------------------
// Leave-group / remove-member permission tests
// ---------------------------------------------------------------------------

#[tokio::test]
async fn bot_can_self_leave_group() {
    let fixture = Fixture::new()
        .with_bot("bot_a", "Bot A", "public", None)
        .with_bot("bot_b", "Bot B", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);

    let cmd = create_cmd(Some("bot_a"), "bot_a", vec![
        participant("bot_a", Some("driver")),
        participant("bot_b", Some("consultant")),
    ]);
    service.create_group(cmd).await.unwrap();

    let result = service.remove_member(GroupRemoveMemberCommand {
        caller_actor_id: Some("bot_b".to_string()),
        group_id: "group-under-test".to_string(),
        bot_id: "bot_b".to_string(),
    }).await;
    assert!(result.is_ok(), "bot should be able to self-leave: {:?}", result.err());
}

#[tokio::test]
async fn human_owner_can_remove_their_bot_from_group() {
    let fixture = Fixture::new()
        .with_bot("bot_a", "Bot A", "public", None)
        .with_bot("bot_b", "Bot B", "public", Some("alice"));
    let service = fixture.service_with_limits(5, 10, 10);

    let cmd = create_cmd(Some("bot_a"), "bot_a", vec![
        participant("bot_a", Some("driver")),
        participant("bot_b", Some("consultant")),
    ]);
    service.create_group(cmd).await.unwrap();

    let result = service.remove_member(GroupRemoveMemberCommand {
        caller_actor_id: Some("human_alice".to_string()),
        group_id: "group-under-test".to_string(),
        bot_id: "bot_b".to_string(),
    }).await;
    assert!(result.is_ok(), "owner should be able to remove their bot: {:?}", result.err());
}

#[tokio::test]
async fn driver_cannot_self_leave_group() {
    let fixture = Fixture::new()
        .with_bot("bot_a", "Bot A", "public", None)
        .with_bot("bot_b", "Bot B", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);

    let cmd = create_cmd(Some("bot_a"), "bot_a", vec![
        participant("bot_a", Some("driver")),
        participant("bot_b", Some("consultant")),
    ]);
    service.create_group(cmd).await.unwrap();

    let result = service.remove_member(GroupRemoveMemberCommand {
        caller_actor_id: Some("bot_a".to_string()),
        group_id: "group-under-test".to_string(),
        bot_id: "bot_a".to_string(),
    }).await;
    assert!(result.is_err());
    assert!(result.unwrap_err().to_string().contains("driver/coordinator"));
}

#[tokio::test]
async fn non_owner_cannot_remove_others_bot_from_group() {
    let fixture = Fixture::new()
        .with_bot("bot_a", "Bot A", "public", None)
        .with_bot("bot_b", "Bot B", "public", Some("bob"))
        .with_human("human_alice", "Alice");
    let service = fixture.service_with_limits(5, 10, 10);

    let cmd = create_cmd(Some("bot_a"), "bot_a", vec![
        participant("bot_a", Some("driver")),
        participant("bot_b", Some("consultant")),
    ]);
    service.create_group(cmd).await.unwrap();

    let result = service.remove_member(GroupRemoveMemberCommand {
        caller_actor_id: Some("human_alice".to_string()),
        group_id: "group-under-test".to_string(),
        bot_id: "bot_b".to_string(),
    }).await;
    assert!(result.is_err());
    assert!(result.unwrap_err().to_string().contains("not authorized"));
}

#[tokio::test]
async fn coordinator_can_still_kick_members() {
    let fixture = Fixture::new()
        .with_bot("bot_a", "Bot A", "public", None)
        .with_bot("bot_b", "Bot B", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);

    let cmd = create_cmd(Some("bot_a"), "bot_a", vec![
        participant("bot_a", Some("driver")),
        participant("bot_b", Some("consultant")),
    ]);
    service.create_group(cmd).await.unwrap();

    let result = service.remove_member(GroupRemoveMemberCommand {
        caller_actor_id: Some("bot_a".to_string()),
        group_id: "group-under-test".to_string(),
        bot_id: "bot_b".to_string(),
    }).await;
    assert!(result.is_ok(), "coordinator should still be able to kick: {:?}", result.err());
}
