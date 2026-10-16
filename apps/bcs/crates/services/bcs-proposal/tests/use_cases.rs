use std::sync::Arc;

use bcs_service_api::{
    GroupCoreService, GroupProposalConfirmCommand, GroupProposalCreateCommand,
    GroupProposalPreviewCommand, GroupProposalService, GroupUseCaseError,
    ProposalCoreService, ServiceError, SessionKind,
    SystemMessageEvent,
};

use bcs_proposal::{
    GroupProposalUseCases, GroupProposalUseCasesConfig, ProposalBuilder,
    ProposalStore,
};


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
    assert_eq!(
        confirmed.session_id,
        format!("{}:initial", confirmed.group_id)
    );
    let expected_chat_url = format!(
        "http://chat.example.test/bcn/chat/detail?id={}&bot_uuid=driver&session={}%3Ainitial",
        confirmed.group_id, confirmed.group_id
    );
    assert_eq!(
        confirmed.chat_url.as_deref(),
        Some(expected_chat_url.as_str())
    );
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
            ..
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
    /// The REAL authority chain backing the fixture Bots
    /// (bot-authority plan Task 6.5); `created_by` here is a
    /// display mirror of the seeded owner edge, never the
    /// authorization source. Edge seeds flush lazily on the
    /// first authority read (sync builder chain).
    authority: Arc<bcs_test_support::LazySeedAuthorityHook>,
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
            authority: bcs_test_support::lazy_authority_chain().hook,
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
        .with_bot_authority_hook(self.authority.clone())
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
        if let Some(owner) = created_by {
            self.authority.seed_owner(bot_uuid, owner);
        }
        self
    }

    /// Grant a live MANAGER edge (the Human→Bot control
    /// fact for the cutover matrix).
    fn with_manager(self, bot_uuid: &str, user: &str) -> Self {
        self.authority.seed_manager(bot_uuid, user);
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

#[path = "support/mod.rs"]
mod support;
use support::*;
