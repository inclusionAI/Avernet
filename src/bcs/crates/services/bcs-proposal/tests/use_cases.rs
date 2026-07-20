use std::{
    collections::{HashMap, HashSet},
    sync::atomic::{AtomicUsize, Ordering},
    sync::{Arc, RwLock},
};

use async_trait::async_trait;

use bcs_service_api::{
    ActorKind, AgentCredentials, BotCapabilities, BotDynamicStatus, BotRegistryCoreService,
    CreateOrReactivateCommand, CreateOrReactivateOutcome, DmActorSpec,
    FriendCoreService, Group, GroupCoreService, GroupKind, GroupMessage,
    GroupProposalConfirmCommand, GroupProposalCreateCommand, GroupProposalPreviewCommand,
    GroupProposalService, GroupStatus, GroupUseCaseError, Participant, ParticipantMode,
    ProposalCoreService, RegisteredBot, ServiceError, ServiceResult, Session, SessionKind,
    SessionManagementService, SessionStatus, SessionUseCaseError, SystemMessageEvent,
    SystemMessageService, Workspace,
};

use bcs_proposal::{
    GroupProposalUseCases, GroupProposalUseCasesConfig, ProposalBuilder, ProposalStore,
};
use tokio::sync::Mutex;

#[tokio::test]
async fn create_proposal_stores_a_proposal_token() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver Bot", "public", Some("alice"))
        .with_bot("dba", "DBA Bot", "public", None);
    let service = fixture.service();

    let result = service
        .create_proposal(GroupProposalCreateCommand {
            caller_actor_id: Some("human_alice".to_string()),
            driver_bot_id: "driver".to_string(),
            suggested_driver_bot_id: None,
            suggested_participants: vec!["dba".to_string()],
            topic: "database latency".to_string(),
            context: None,
        })
        .await
        .unwrap();

    let token = result
        .confirm_url
        .trim_start_matches("http://bcs.example.test/groups/")
        .trim_end_matches("/confirm");
    assert!(!token.is_empty());
    let stored = fixture.proposal.get(token).await.expect("proposal stored");
    assert_eq!(stored.token, token);
    assert_eq!(stored.driver_bot, "driver");
    assert_eq!(stored.participants, ["dba", "driver"]);
}

#[tokio::test]
async fn create_proposal_requires_authorized_caller() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver Bot", "public", Some("alice"))
        .with_bot("dba", "DBA Bot", "public", None);
    let service = fixture.service();

    let missing_caller = service
        .create_proposal(GroupProposalCreateCommand {
            caller_actor_id: None,
            driver_bot_id: "driver".to_string(),
            suggested_driver_bot_id: None,
            suggested_participants: vec!["dba".to_string()],
            topic: "database latency".to_string(),
            context: None,
        })
        .await
        .expect_err("proposal creation must fail closed without caller");
    assert!(matches!(missing_caller, GroupUseCaseError::Unauthorized(_)));

    let wrong_creator = service
        .create_proposal(GroupProposalCreateCommand {
            caller_actor_id: Some("human_bob".to_string()),
            driver_bot_id: "driver".to_string(),
            suggested_driver_bot_id: None,
            suggested_participants: vec!["dba".to_string()],
            topic: "database latency".to_string(),
            context: None,
        })
        .await
        .expect_err("proposal creation must reject non-owner caller");
    assert!(matches!(wrong_creator, GroupUseCaseError::Forbidden(_)));
}

#[tokio::test]
async fn create_proposal_rejects_human_caller_for_ownerless_driver() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver Bot", "public", None)
        .with_bot("dba", "DBA Bot", "public", None);
    let service = fixture.service();

    let forbidden = service
        .create_proposal(GroupProposalCreateCommand {
            caller_actor_id: Some("human_alice".to_string()),
            driver_bot_id: "driver".to_string(),
            suggested_driver_bot_id: None,
            suggested_participants: vec!["dba".to_string()],
            topic: "database latency".to_string(),
            context: None,
        })
        .await
        .expect_err("ownerless driver should not authorize arbitrary human caller");
    assert!(matches!(forbidden, GroupUseCaseError::Forbidden(_)));

    let created = service
        .create_proposal(GroupProposalCreateCommand {
            caller_actor_id: Some("driver".to_string()),
            driver_bot_id: "driver".to_string(),
            suggested_driver_bot_id: None,
            suggested_participants: vec!["dba".to_string()],
            topic: "database latency".to_string(),
            context: None,
        })
        .await
        .expect("driver bot should still be authorized as itself");
    assert!(created.confirm_url.contains("/groups/"));
}

#[tokio::test]
async fn create_proposal_allows_private_friend_participant() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver Bot", "public", None)
        .with_bot("private-friend", "Private Friend", "private", None)
        .with_friendship("driver", "private-friend");
    let service = fixture.service();

    let result = service
        .create_proposal(GroupProposalCreateCommand {
            caller_actor_id: Some("driver".to_string()),
            driver_bot_id: "driver".to_string(),
            suggested_driver_bot_id: None,
            suggested_participants: vec!["private-friend".to_string()],
            topic: "private collaboration".to_string(),
            context: None,
        })
        .await
        .expect("private friends are reachable during proposal creation");

    assert_eq!(result.participant_bot_ids, ["private-friend", "driver"]);
    assert!(result.member_intros.contains("Private Friend"));
}

#[tokio::test]
async fn create_proposal_rejects_protected_non_friend_participant() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver Bot", "public", None)
        .with_bot(
            "protected-stranger",
            "Protected Stranger",
            "protected",
            None,
        );
    let service = fixture.service();

    let err = service
        .create_proposal(GroupProposalCreateCommand {
            caller_actor_id: Some("driver".to_string()),
            driver_bot_id: "driver".to_string(),
            suggested_driver_bot_id: None,
            suggested_participants: vec!["protected-stranger".to_string()],
            topic: "protected collaboration".to_string(),
            context: None,
        })
        .await
        .expect_err("protected non-friends should be forbidden");

    assert!(matches!(err, GroupUseCaseError::Forbidden(_)));
}

#[tokio::test]
async fn create_proposal_hides_private_non_friend_participant() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver Bot", "public", None)
        .with_bot("private-stranger", "Private Stranger", "private", None);
    let service = fixture.service();

    let err = service
        .create_proposal(GroupProposalCreateCommand {
            caller_actor_id: Some("driver".to_string()),
            driver_bot_id: "driver".to_string(),
            suggested_driver_bot_id: None,
            suggested_participants: vec!["private-stranger".to_string()],
            topic: "private collaboration".to_string(),
            context: None,
        })
        .await
        .expect_err("private non-friends should be hidden");

    assert!(matches!(
        err,
        GroupUseCaseError::Service(ServiceError::BotNotFound(bot_id))
            if bot_id == "private-stranger"
    ));
}

#[tokio::test]
async fn confirm_proposal_consumes_the_token_and_creates_group() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver Bot", "public", None)
        .with_bot("dba", "DBA Bot", "public", None);
    fixture
        .proposal
        .store(
            ProposalBuilder::new("driver", "database latency")
                .driver("driver")
                .participants(vec!["driver".to_string(), "dba".to_string()])
                .member_intros("Driver and DBA")
                .build_with_token("proposal-token"),
        )
        .await;
    let service = fixture.service();

    let confirmed = service
        .confirm_proposal(GroupProposalConfirmCommand {
            caller_actor_id: Some("human_alice".to_string()),
            token: "proposal-token".to_string(),
        })
        .await
        .unwrap();

    assert!(confirmed.created);
    assert!(confirmed.group_id.starts_with("bcs_grp_"));
    assert_eq!(confirmed.group_id.chars().count(), 40);
    assert_eq!(confirmed.driver_bot_id, "driver");
    assert_eq!(confirmed.participant_bot_ids, ["driver", "dba"]);
    assert!(fixture.proposal.get("proposal-token").await.is_none());
    assert!(fixture.group.get(&confirmed.group_id).await.is_some());
}

#[tokio::test]
async fn preview_proposal_returns_active_proposal_without_consuming_token() {
    let fixture = Fixture::new();
    fixture
        .proposal
        .store(
            ProposalBuilder::new("driver", "database latency")
                .driver("driver")
                .participants(vec!["driver".to_string(), "dba".to_string()])
                .member_intros("Driver and DBA")
                .build_with_token("preview-token"),
        )
        .await;
    let service = fixture.service();

    let preview = service
        .preview_proposal(GroupProposalPreviewCommand {
            token: "preview-token".to_string(),
        })
        .await
        .unwrap();

    assert_eq!(preview.token, "preview-token");
    assert_eq!(preview.proposal.driver_bot, "driver");
    assert!(fixture.proposal.get("preview-token").await.is_some());
}

#[tokio::test]
async fn preview_proposal_maps_missing_and_expired_to_typed_errors() {
    let fixture = Fixture::new();
    let mut expired = ProposalBuilder::new("driver", "database latency")
        .driver("driver")
        .participants(vec!["driver".to_string(), "dba".to_string()])
        .member_intros("Driver and DBA")
        .build_with_token("expired-preview-token");
    expired.created_at = expired
        .created_at
        .saturating_sub(bcs_service_api::GroupChatProposal::EXPIRY_MS + 1);
    fixture.proposal.store(expired).await;
    let service = fixture.service();

    let missing = service
        .preview_proposal(GroupProposalPreviewCommand {
            token: "missing-preview-token".to_string(),
        })
        .await
        .expect_err("missing preview should return typed not found");
    assert!(matches!(
        missing,
        GroupUseCaseError::ProposalNotFound(token) if token == "missing-preview-token"
    ));

    let expired = service
        .preview_proposal(GroupProposalPreviewCommand {
            token: "expired-preview-token".to_string(),
        })
        .await
        .expect_err("expired preview should return typed expired");
    assert!(matches!(
        expired,
        GroupUseCaseError::ProposalExpired(token) if token == "expired-preview-token"
    ));
}

#[tokio::test]
async fn confirm_proposal_creates_initial_session_and_dispatches_session_context() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver Bot", "public", None)
        .with_bot("dba", "DBA Bot", "public", None);
    fixture
        .proposal
        .store(
            ProposalBuilder::new("driver", "database latency")
                .driver("driver")
                .participants(vec!["driver".to_string(), "dba".to_string()])
                .build_with_token("context-injection-token"),
        )
        .await;
    fixture.system_message.set_successful_deliveries(7);
    let service = fixture.service();

    let confirmed = service
        .confirm_proposal(GroupProposalConfirmCommand {
            caller_actor_id: None,
            token: "context-injection-token".to_string(),
        })
        .await
        .unwrap();

    assert_eq!(confirmed.context_injected, 7);
    let commands = fixture.session_management.commands.lock().await;
    assert_eq!(commands.len(), 1);
    let command = &commands[0];
    assert_eq!(command.group_id, confirmed.group_id);
    assert!(command.session_id.is_none());
    assert_eq!(command.params.session_kind, SessionKind::Chat);
    assert_eq!(command.params.session_title.as_deref(), Some("新会话"));
    assert_eq!(command.params.group_version, Some(1));
    assert_eq!(command.params.participants.len(), 2);
    drop(commands);

    let notifications = fixture.system_message.notifications.lock().await;
    assert_eq!(notifications.len(), 1);
    let notification = &notifications[0];
    assert_eq!(notification.group_id, confirmed.group_id);
    assert_eq!(notification.session_id, format!("{}:initial", confirmed.group_id));
    assert_eq!(notification.participants.len(), 2);
    match &notification.event {
        SystemMessageEvent::SessionContext {
            group_id,
            session_id,
            reason,
            session_input,
            task_ledger,
        } => {
            assert_eq!(group_id, &confirmed.group_id);
            assert_eq!(session_id, &format!("{}:initial", confirmed.group_id));
            assert_eq!(reason, "database latency");
            assert!(session_input.is_none());
            assert!(task_ledger.is_none());
        }
        other => panic!("expected SessionContext event, got {other:?}"),
    }
}

#[tokio::test]
async fn concurrent_confirm_proposal_is_single_use() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver Bot", "public", None)
        .with_bot("dba", "DBA Bot", "public", None);
    fixture
        .proposal
        .store(
            ProposalBuilder::new("driver", "database latency")
                .driver("driver")
                .participants(vec!["driver".to_string(), "dba".to_string()])
                .member_intros("Driver and DBA")
                .build_with_token("concurrent-token"),
        )
        .await;
    let service = Arc::new(fixture.service());

    let first = {
        let service = Arc::clone(&service);
        async move {
            service
                .confirm_proposal(GroupProposalConfirmCommand {
                    caller_actor_id: None,
                    token: "concurrent-token".to_string(),
                })
                .await
        }
    };
    let second = {
        let service = Arc::clone(&service);
        async move {
            service
                .confirm_proposal(GroupProposalConfirmCommand {
                    caller_actor_id: None,
                    token: "concurrent-token".to_string(),
                })
                .await
        }
    };

    let (first, second) = tokio::join!(first, second);
    let successes = [first.as_ref().ok(), second.as_ref().ok()]
        .into_iter()
        .flatten()
        .count();

    assert_eq!(successes, 1);
    assert_eq!(fixture.group.count().await, 1);
    assert!(fixture.proposal.get("concurrent-token").await.is_none());
}

#[tokio::test]
async fn confirm_proposal_keeps_token_when_member_limit_fails() {
    let fixture = Fixture::new().with_bot("driver", "Driver Bot", "public", None);
    fixture
        .proposal
        .store(
            ProposalBuilder::new("driver", "too many members")
                .driver("driver")
                .participants(vec![
                    "driver".to_string(),
                    "a".to_string(),
                    "b".to_string(),
                    "c".to_string(),
                    "d".to_string(),
                    "e".to_string(),
                ])
                .build_with_token("member-limit-token"),
        )
        .await;
    let service = fixture.service();

    let err = service
        .confirm_proposal(GroupProposalConfirmCommand {
            caller_actor_id: None,
            token: "member-limit-token".to_string(),
        })
        .await
        .expect_err("member limit failure should reject confirmation");

    assert!(matches!(err, GroupUseCaseError::InvalidProposal(_)));
    assert!(fixture.proposal.get("member-limit-token").await.is_some());
}

#[tokio::test]
async fn confirm_proposal_keeps_token_when_visibility_fails() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver Bot", "public", None)
        .with_bot("private-stranger", "Private Stranger", "private", None);
    fixture
        .proposal
        .store(
            ProposalBuilder::new("driver", "private stranger")
                .driver("driver")
                .participants(vec!["driver".to_string(), "private-stranger".to_string()])
                .build_with_token("visibility-failure-token"),
        )
        .await;
    let service = fixture.service();

    let err = service
        .confirm_proposal(GroupProposalConfirmCommand {
            caller_actor_id: None,
            token: "visibility-failure-token".to_string(),
        })
        .await
        .expect_err("visibility failure should reject confirmation");

    assert!(matches!(
        err,
        GroupUseCaseError::Service(ServiceError::BotNotFound(bot_id))
            if bot_id == "private-stranger"
    ));
    assert!(
        fixture
            .proposal
            .get("visibility-failure-token")
            .await
            .is_some()
    );
}

#[tokio::test]
async fn confirm_proposal_keeps_token_when_group_persistence_fails() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver Bot", "public", None)
        .with_bot("dba", "DBA Bot", "public", None)
        .with_group_upsert_failure();
    fixture
        .proposal
        .store(
            ProposalBuilder::new("driver", "database latency")
                .driver("driver")
                .participants(vec!["driver".to_string(), "dba".to_string()])
                .build_with_token("storage-failure-token"),
        )
        .await;
    let service = fixture.service();

    let err = service
        .confirm_proposal(GroupProposalConfirmCommand {
            caller_actor_id: None,
            token: "storage-failure-token".to_string(),
        })
        .await
        .expect_err("group persistence failure should reject confirmation");

    assert!(matches!(
        err,
        GroupUseCaseError::Service(ServiceError::InvalidOperation { .. })
    ));
    assert!(
        fixture
            .proposal
            .get("storage-failure-token")
            .await
            .is_some()
    );
}

#[tokio::test]
async fn confirm_proposal_allows_private_friend_participant() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver Bot", "public", None)
        .with_bot("private-friend", "Private Friend", "private", None)
        .with_friendship("driver", "private-friend");
    fixture
        .proposal
        .store(
            ProposalBuilder::new("driver", "private collaboration")
                .driver("driver")
                .participants(vec!["driver".to_string(), "private-friend".to_string()])
                .member_intros("Driver and private friend")
                .build_with_token("private-friend-token"),
        )
        .await;
    let service = fixture.service();

    let confirmed = service
        .confirm_proposal(GroupProposalConfirmCommand {
            caller_actor_id: None,
            token: "private-friend-token".to_string(),
        })
        .await
        .expect("private friends are reachable for collaboration");

    assert_eq!(confirmed.participant_bot_ids, ["driver", "private-friend"]);
}

#[tokio::test]
async fn expired_proposal_returns_not_found_or_expired() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver Bot", "public", None)
        .with_bot("dba", "DBA Bot", "public", None);
    let mut proposal = ProposalBuilder::new("driver", "database latency")
        .driver("driver")
        .participants(vec!["driver".to_string(), "dba".to_string()])
        .build_with_token("expired-token");
    proposal.created_at =
        now_ms().saturating_sub(bcs_service_api::GroupChatProposal::EXPIRY_MS + 1);
    fixture.proposal.store(proposal).await;
    let service = fixture.service();

    let err = service
        .confirm_proposal(GroupProposalConfirmCommand {
            caller_actor_id: None,
            token: "expired-token".to_string(),
        })
        .await
        .expect_err("expired proposal should not confirm");

    assert!(
        matches!(err, GroupUseCaseError::InvalidProposal(message) if message.contains("expired"))
    );
    assert!(fixture.proposal.get("expired-token").await.is_some());
}

#[tokio::test]
async fn generated_member_intros_are_built_in_the_service() {
    let fixture = Fixture::new()
        .with_bot("driver", "Driver Bot", "public", None)
        .with_bot("dba", "DBA Bot", "public", None);
    let service = fixture.service();

    let result = service
        .create_proposal(GroupProposalCreateCommand {
            caller_actor_id: Some("driver".to_string()),
            driver_bot_id: "driver".to_string(),
            suggested_driver_bot_id: None,
            suggested_participants: vec!["dba".to_string()],
            topic: "database latency".to_string(),
            context: None,
        })
        .await
        .unwrap();

    assert!(result.member_intros.contains("**Driver Bot** (Driver)"));
    assert!(result.member_intros.contains("**DBA Bot** (成员)"));
    assert!(result.message.contains("database latency"));
    assert!(result.message.contains(&result.member_intros));
}

struct Fixture {
    group: Arc<FakeGroupStore>,
    proposal: Arc<ProposalStore>,
    registry: Arc<FakeRegistry>,
    friend: Arc<FakeFriendCoreService>,
    session_management: Arc<RecordingSessionManagement>,
    system_message: Arc<RecordingSystemMessageService>,
}

impl Fixture {
    fn new() -> Self {
        Self {
            group: Arc::new(FakeGroupStore::default()),
            proposal: Arc::new(ProposalStore::new()),
            registry: Arc::new(FakeRegistry::default()),
            friend: Arc::new(FakeFriendCoreService::default()),
            session_management: Arc::new(RecordingSessionManagement::default()),
            system_message: Arc::new(RecordingSystemMessageService::default()),
        }
    }

    fn service(&self) -> GroupProposalUseCases {
        GroupProposalUseCases::new(
            self.group.clone(),
            self.registry.clone(),
            self.friend.clone(),
            self.proposal.clone(),
            self.session_management.clone(),
            self.system_message.clone(),
            GroupProposalUseCasesConfig {
                max_group_members: 5,
                max_groups_as_driver: 10,
                max_groups_as_member: 10,
                proposal_base_url: "http://bcs.example.test".to_string(),
                botchat_base_url: Some("http://chat.example.test".to_string()),
            },
        )
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

    fn with_friendship(self, a: &str, b: &str) -> Self {
        self.friend.insert(a, b);
        self
    }

    fn with_group_upsert_failure(self) -> Self {
        self.group.fail_upsert();
        self
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

    async fn discover(&self, query: &str) -> Vec<RegisteredBot> {
        self.bots
            .read()
            .unwrap()
            .values()
            .filter(|bot| {
                bot.bot_uuid != query
                    || bot
                        .capabilities
                        .summary
                        .as_deref()
                        .unwrap_or_default()
                        .contains(query)
            })
            .cloned()
            .collect()
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
        self.pairs
            .write()
            .unwrap()
            .insert(canonical_pair(bot_a, bot_b));
        Ok(())
    }

    async fn remove_all_friendships(&self, bot_id: &str) -> ServiceResult<usize> {
        let mut pairs = self.pairs.write().unwrap();
        let before = pairs.len();
        pairs.retain(|(a, b)| a != bot_id && b != bot_id);
        Ok(before - pairs.len())
    }
}

#[derive(Default)]
struct FakeGroupStore {
    groups: RwLock<HashMap<String, Group>>,
    fail_upsert: RwLock<bool>,
}

impl FakeGroupStore {
    fn fail_upsert(&self) {
        *self.fail_upsert.write().unwrap() = true;
    }
}

#[async_trait]
impl GroupCoreService for FakeGroupStore {
    async fn upsert(&self, group: Group) -> ServiceResult<()> {
        if *self.fail_upsert.read().unwrap() {
            return Err(ServiceError::InvalidOperation {
                message: "group persistence failed".to_string(),
                request_id: None,
            });
        }
        self.groups.write().unwrap().insert(group.id.clone(), group);
        Ok(())
    }

    async fn get(&self, id: &str) -> Option<Group> {
        self.groups.read().unwrap().get(id).cloned()
    }

    async fn add_message(&self, id: &str, message: GroupMessage) -> ServiceResult<()> {
        let mut groups = self.groups.write().unwrap();
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
        group.messages.push(message);
        Ok(())
    }

    async fn add_participant(&self, id: &str, participant: Participant) -> ServiceResult<()> {
        let mut groups = self.groups.write().unwrap();
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
        group.participants.push(participant);
        Ok(())
    }

    async fn remove_participant(&self, group_id: &str, bot_uuid: &str) -> ServiceResult<()> {
        let mut groups = self.groups.write().unwrap();
        let group = groups
            .get_mut(group_id)
            .ok_or_else(|| ServiceError::GroupNotFound(group_id.to_string()))?;
        group.participants.retain(|p| p.bot_uuid != bot_uuid);
        Ok(())
    }

    async fn update_participant_mode(
        &self,
        id: &str,
        actor_id: &str,
        mode: ParticipantMode,
    ) -> ServiceResult<()> {
        let mut groups = self.groups.write().unwrap();
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
        let participant = group
            .participants
            .iter_mut()
            .find(|participant| participant.bot_uuid == actor_id)
            .ok_or_else(|| ServiceError::BotNotFound(actor_id.to_string()))?;
        participant.mode = Some(mode);
        Ok(())
    }

    async fn update_workspace(&self, id: &str, workspace: Workspace) -> ServiceResult<()> {
        let mut groups = self.groups.write().unwrap();
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
        group.workspace = workspace;
        Ok(())
    }

    async fn update_label(&self, id: &str, label: Option<String>) -> ServiceResult<()> {
        let mut groups = self.groups.write().unwrap();
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
        group.label = label;
        Ok(())
    }

    async fn update_status(&self, id: &str, status: GroupStatus) -> ServiceResult<()> {
        let mut groups = self.groups.write().unwrap();
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
        group.status = status;
        Ok(())
    }

    async fn update_service_spec(
        &self,
        id: &str,
        service_spec: Option<bcs_service_api::ServiceSpec>,
    ) -> ServiceResult<()> {
        let mut groups = self.groups.write().unwrap();
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
        group.service_spec = service_spec;
        Ok(())
    }

    async fn terminate(&self, id: &str, _caller_bot_id: &str) -> ServiceResult<Group> {
        let mut groups = self.groups.write().unwrap();
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
        group.status = GroupStatus::Completed;
        Ok(group.clone())
    }

    async fn delete(&self, id: &str) -> ServiceResult<Option<Group>> {
        Ok(self.groups.write().unwrap().remove(id))
    }

    async fn list(&self) -> Vec<Group> {
        self.groups.read().unwrap().values().cloned().collect()
    }

    async fn list_paginated(&self, offset: u64, limit: u64) -> Vec<Group> {
        let mut groups = self
            .groups
            .read()
            .unwrap()
            .values()
            .cloned()
            .collect::<Vec<_>>();
        Group::sort_by_updated_at_desc(&mut groups);
        groups
            .into_iter()
            .skip(offset as usize)
            .take(limit as usize)
            .collect()
    }

    async fn find_by_participant(&self, bot_uuid: &str) -> Vec<Group> {
        self.groups
            .read()
            .unwrap()
            .values()
            .filter(|group| {
                group
                    .participants
                    .iter()
                    .any(|participant| participant.bot_uuid == bot_uuid)
            })
            .cloned()
            .collect()
    }

    async fn count(&self) -> u64 {
        self.groups.read().unwrap().len() as u64
    }

    async fn count_by_participant(&self, bot_uuid: &str) -> u64 {
        self.find_by_participant(bot_uuid).await.len() as u64
    }

    async fn find_by_participant_paginated(
        &self,
        bot_uuid: &str,
        offset: u64,
        limit: u64,
    ) -> Vec<Group> {
        let mut groups = self.find_by_participant(bot_uuid).await;
        Group::sort_by_updated_at_desc(&mut groups);
        groups
            .into_iter()
            .skip(offset as usize)
            .take(limit as usize)
            .collect()
    }

    async fn message_count(&self, id: &str) -> ServiceResult<usize> {
        Ok(self
            .groups
            .read()
            .unwrap()
            .get(id)
            .map(|group| group.messages.len())
            .unwrap_or(0))
    }

    async fn increment_message_count(&self, _id: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn reset_message_count(&self, _id: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn create_or_reuse_actor_dm_group(
        &self,
        _id: &str,
        _actor_a: DmActorSpec,
        _actor_b: DmActorSpec,
        _legacy_driver_bot: &str,
        _originator_actor_id: &str,
        _label: Option<String>,
        _context: Option<String>,
    ) -> ServiceResult<(Group, bool)> {
        Err(ServiceError::InternalError(
            "dm group creation is not supported by FakeGroupStore".to_string(),
        ))
    }

    async fn count_by_kind(&self, kind: Option<GroupKind>) -> u64 {
        self.groups
            .read()
            .unwrap()
            .values()
            .filter(|group| kind.is_none_or(|kind| group.group_kind == kind))
            .count() as u64
    }
}

#[derive(Default)]
struct RecordingSessionManagement {
    commands: Mutex<Vec<CreateOrReactivateCommand>>,
}

#[async_trait]
impl SessionManagementService for RecordingSessionManagement {
    async fn create_or_reactivate(
        &self,
        cmd: CreateOrReactivateCommand,
    ) -> Result<CreateOrReactivateOutcome, SessionUseCaseError> {
        self.commands.lock().await.push(cmd.clone());
        Ok(CreateOrReactivateOutcome {
            session: test_session(
                &format!("{}:initial", cmd.group_id),
                &cmd.group_id,
                cmd.params.participants.clone(),
                cmd.params.session_kind,
                cmd.params.group_version,
            ),
            created: true,
        })
    }

    async fn get(&self, _session_id: &str) -> Result<Option<Session>, SessionUseCaseError> {
        Ok(None)
    }

    async fn belongs_to_group(
        &self,
        _session_id: &str,
        _group_id: &str,
    ) -> Result<bool, SessionUseCaseError> {
        Ok(false)
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
        Ok(Vec::new())
    }

    async fn count_running_service(&self, _group_id: &str) -> Result<u64, SessionUseCaseError> {
        Ok(0)
    }

    async fn list_running_service(
        &self,
        _offset: u64,
        _limit: u64,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        Ok(Vec::new())
    }

    async fn update_callback_status(
        &self,
        _session_id: &str,
        _status: &str,
    ) -> Result<(), SessionUseCaseError> {
        Ok(())
    }

    async fn complete_if_running(
        &self,
        _session_id: &str,
        _output: Option<serde_json::Value>,
        _error: Option<String>,
    ) -> Result<Option<Session>, SessionUseCaseError> {
        Ok(None)
    }

    async fn add_participant(
        &self,
        _session_id: &str,
        _participant: Participant,
    ) -> Result<Session, SessionUseCaseError> {
        Err(SessionUseCaseError::Conflict("not implemented".to_string()))
    }

    async fn remove_participant(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
    ) -> Result<Session, SessionUseCaseError> {
        Err(SessionUseCaseError::Conflict("not implemented".to_string()))
    }

    async fn update_participant_mode(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
        _mode: ParticipantMode,
    ) -> Result<Session, SessionUseCaseError> {
        Err(SessionUseCaseError::Conflict("not implemented".to_string()))
    }

    async fn update_title(
        &self,
        _session_id: &str,
        _title: Option<String>,
    ) -> Result<Session, SessionUseCaseError> {
        Err(SessionUseCaseError::Conflict("not implemented".to_string()))
    }

    async fn list_group_ids_by_session_participant(
        &self,
        _bot_uuid: &str,
    ) -> Result<Vec<String>, SessionUseCaseError> {
        Ok(Vec::new())
    }

    async fn delete(&self, _session_id: &str) -> Result<bool, SessionUseCaseError> {
        Ok(false)
    }
}

#[derive(Default)]
struct RecordingSystemMessageService {
    successful_deliveries: AtomicUsize,
    notifications: Mutex<Vec<RecordingSystemMessageNotification>>,
}

impl RecordingSystemMessageService {
    fn set_successful_deliveries(&self, value: usize) {
        self.successful_deliveries.store(value, Ordering::SeqCst);
    }
}

struct RecordingSystemMessageNotification {
    group_id: String,
    event: SystemMessageEvent,
    session_id: String,
    participants: Vec<Participant>,
}

#[async_trait]
impl SystemMessageService for RecordingSystemMessageService {
    async fn notify(
        &self,
        group_id: &str,
        event: SystemMessageEvent,
        session_id: &str,
        session_participants: &[Participant],
    ) -> ServiceResult<usize> {
        self.notifications.lock().await.push(RecordingSystemMessageNotification {
            group_id: group_id.to_string(),
            event,
            session_id: session_id.to_string(),
            participants: session_participants.to_vec(),
        });
        Ok(self.successful_deliveries.load(Ordering::SeqCst))
    }
}

fn test_session(
    session_id: &str,
    group_id: &str,
    participants: Vec<Participant>,
    session_kind: SessionKind,
    group_version: Option<i32>,
) -> Session {
    Session {
        id: session_id.to_string(),
        group_id: group_id.to_string(),
        session_title: None,
        env: None,
        status: SessionStatus::Running,
        session_kind,
        participants,
        group_version,
        caller_id: None,
        input: None,
        output: None,
        error_message: None,
        callback_status: None,
        activation_count: 1,
        caller_principal: None,
        created_by: None,
        current_msg_seq: 0,
        participant_join_seq: None,
        created_at: 1,
        updated_at: 1,
        completed_at: None,
        meta: None,
        collected_at: None,
    }
}

fn canonical_pair(a: &str, b: &str) -> (String, String) {
    if a <= b {
        (a.to_string(), b.to_string())
    } else {
        (b.to_string(), a.to_string())
    }
}

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or(0)
}
