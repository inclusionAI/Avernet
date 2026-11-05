use super::*;

use bcs_service_api::{
    BotCallerConfiguration, BotCandidateReadQuery, BotControlPlaneCandidate,
    BotControlPlaneCoreService, BotControlPlaneOwnedQuery, BotControlPlanePatch,
    BotControlPlaneRecord, BotControlPlaneView, CallerIdentityConfig, CallerIdentitySnapshot,
    CallerMinimumIsolation,
};

/// Minimal caller-config plane fake: every single caller-configuration
/// read answers with an ENABLED caller-identity snapshot, regardless of
/// which bot or environment is asked.
struct CallerEnabledControlPlane;

#[async_trait]
impl BotControlPlaneCoreService for CallerEnabledControlPlane {
    async fn get_caller_configuration(
        &self,
        _bot_id: &str,
        _env: &str,
    ) -> ServiceResult<Option<BotCallerConfiguration>> {
        Ok(Some(BotCallerConfiguration {
            snapshot: Some(CallerIdentitySnapshot {
                source_revision: 1,
                caller_identity: CallerIdentityConfig {
                    enabled: true,
                    minimum_isolation: CallerMinimumIsolation::CallerContainer,
                },
            }),
        }))
    }

    async fn get_record(
        &self,
        _bot_id: &str,
        _env: &str,
    ) -> ServiceResult<Option<BotControlPlaneRecord>> {
        unimplemented!("not needed by this test")
    }

    async fn get(
        &self,
        _bot_id: &str,
        _env: &str,
    ) -> ServiceResult<Option<BotControlPlaneView>> {
        unimplemented!("not needed by this test")
    }

    async fn get_by_ids(
        &self,
        _bot_ids: &[String],
        _env: &str,
    ) -> ServiceResult<Vec<BotControlPlaneView>> {
        unimplemented!("not needed by this test")
    }

    async fn list_candidates(
        &self,
        _query: BotCandidateReadQuery,
    ) -> ServiceResult<(Vec<BotControlPlaneCandidate>, u64)> {
        unimplemented!("not needed by this test")
    }

    async fn list_by_creator(
        &self,
        _query: BotControlPlaneOwnedQuery,
    ) -> ServiceResult<Vec<BotControlPlaneView>> {
        unimplemented!("not needed by this test")
    }

    async fn patch(
        &self,
        _bot_id: &str,
        _env: &str,
        _patch: BotControlPlanePatch,
    ) -> ServiceResult<Option<BotControlPlaneView>> {
        unimplemented!("not needed by this test")
    }
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

    let legacy_connected = service
        .connect(WorkbenchConnectCommand {
            bound_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            session_id: None,
            view_actor_id: None,
        })
        .await
        .expect("legacy full view must allow access through an owned Bot");

    assert_eq!(legacy_connected.participants.len(), 2);
    assert!(legacy_connected
        .participants
        .iter()
        .all(|participant| participant.bot_uuid != "human_alice"));

    let connected = service
        .connect(WorkbenchConnectCommand {
            bound_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            session_id: None,
            view_actor_id: Some("driver".to_string()),
        })
        .await
        .expect("human owner can connect through an owned Bot view");

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
async fn state_machine_group_connect_requires_lease_with_caller_enabled_members() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", None)
        .with_bot("helper", "Helper", "public", None)
        .with_human("human_alice", "Alice");
    let service = fixture
        .service_with_limits(5, 10, 10)
        .with_caller_config_plane(Arc::new(CallerEnabledControlPlane));

    let mut cmd = create_cmd(
        Some("human_alice"),
        "driver",
        vec![
            participant("driver", Some("driver")),
            participant("helper", Some("consultant")),
            participant("human_alice", Some("consultant")),
        ],
    );
    cmd.group_strategy = Some(GroupStrategy::StateMachine);
    service.create_group(cmd).await.unwrap();

    let connected = service
        .connect(WorkbenchConnectCommand {
            bound_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            session_id: None,
            view_actor_id: Some("human_alice".to_string()),
        })
        .await
        .expect("state_machine group with human + caller bot must connect");

    assert_eq!(
        connected.speaking_mode,
        bcs_domain::speaking_lease::SessionSpeakingMode::LeaseRequired,
        "state_machine groups expose lease_required when a caller bot is enabled"
    );
}

#[tokio::test]
async fn chat_group_connect_aggregates_a_caller_enabled_member_to_lease_required() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver", "public", None)
        .with_human("human_alice", "Alice");
    let service = fixture
        .service_with_limits(5, 10, 10)
        .with_caller_config_plane(Arc::new(CallerEnabledControlPlane));

    service
        .create_group(create_cmd(
            Some("human_alice"),
            "driver",
            vec![
                participant("driver", Some("driver")),
                participant("human_alice", Some("consultant")),
            ],
        ))
        .await
        .unwrap();

    let connected = service
        .connect(WorkbenchConnectCommand {
            bound_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            session_id: None,
            view_actor_id: Some("human_alice".to_string()),
        })
        .await
        .expect("chat group with human + caller bot must connect");

    assert_eq!(
        connected.speaking_mode,
        bcs_domain::speaking_lease::SessionSpeakingMode::LeaseRequired,
        "chat groups aggregate a caller-enabled member into LeaseRequired"
    );
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
                human.message_view_scope = MessageViewScope::Participant;
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
            session_id: Some(session_id.clone()),
            view_actor_id: None,
        })
        .await
        .expect("session participants should be allowed to connect to that session");

    assert_eq!(connected.group_id, "group-under-test");
    assert!(connected
        .participants
        .iter()
        .any(|participant| participant.bot_uuid == "human_alice"));

    let participant_view = service
        .connect(WorkbenchConnectCommand {
            bound_actor_id: Some("human_alice".to_string()),
            group_id: "group-under-test".to_string(),
            session_id: Some(session_id),
            view_actor_id: Some("human_alice".to_string()),
        })
        .await
        .expect("explicit Human view should resolve the persisted participant scope");
    let human = participant_view
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == "human_alice")
        .expect("Human participant");
    assert_eq!(human.message_view_scope, MessageViewScope::Participant);
}
