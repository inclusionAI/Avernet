//! Proposal authority cutover (bot-authority plan
//! Task 6.5, entry-map row 23): the
//! Human-steering-the-driver-Bot judgment on
//! `create_proposal` follows the CURRENT live management
//! edge through the real hook chain (authority Core over a
//! real store, not a mock). The historical
//! `driver.created_by` no longer authorizes anything.
//!
//! Matrix (plan 6.2): A(owner) / M(manager) / X(unrelated);
//! the A→B handover leaves the old owner fallback-free
//! denied (even though the display `created_by` still
//! reads alice); manager edges count; an authority-read
//! failure propagates as an internal error; the Bot-self
//! identity triad never consults the hook.

#![allow(clippy::expect_used, clippy::unwrap_used)]

#[path = "support/mod.rs"]
mod support;

use std::sync::Arc;

use bcs_proposal::{
    GroupProposalUseCases, GroupProposalUseCasesConfig, ProposalStore,
};
use bcs_service_api::port::bot_authority::BotAuthorityPort;
use bcs_service_api::{GroupProposalCreateCommand, GroupUseCaseError, ServiceError};
use bcs_service_api::GroupProposalService;
use support::{
    FakeFriendCoreService, FakeGroupStore, FakeRegistry,
    RecordingSessionManagement, RecordingSystemMessageService,
};

struct Fixture {
    group: Arc<FakeGroupStore>,
    proposal: Arc<ProposalStore>,
    registry: Arc<FakeRegistry>,
    friend: Arc<FakeFriendCoreService>,
    session_management: Arc<RecordingSessionManagement>,
    system_message: Arc<RecordingSystemMessageService>,
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

    /// Register the driver in the display registry AND seed
    /// its live owner edge; `created_by` mirrors the edge
    /// for display parity only (never the authorization
    /// source).
    fn with_owned_driver(self, owner: &str) -> Self {
        self.authority.seed_owner("driver", owner);
        self.registry
            .insert(support::bot("driver", "Driver Bot", "public", Some(owner)));
        self
    }

    fn service_with_authority(&self) -> GroupProposalUseCases {
        self.service_with_hook(self.authority.clone())
    }

    /// Build the service with a SPECIFIC hook — used by the
    /// failing-read pin.
    fn service_with_hook(
        &self,
        hook: Arc<dyn BotAuthorityPort>,
    ) -> GroupProposalUseCases {
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
                botchat_base_url: None,
            },
        )
        .with_bot_authority_hook(hook)
    }
}

fn command(caller_actor_id: &str, driver_bot_id: &str) -> GroupProposalCreateCommand {
    GroupProposalCreateCommand {
        caller_actor_id: Some(caller_actor_id.to_string()),
        driver_bot_id: driver_bot_id.to_string(),
        suggested_driver_bot_id: None,
        suggested_participants: Vec::new(),
        topic: "authority matrix".to_string(),
        context: None,
    }
}

#[tokio::test]
async fn create_proposal_follows_the_current_control_matrix() {
    let fixture = Fixture::new().with_owned_driver("alice");

    let service = fixture.service_with_authority();

    let granted = service
        .create_proposal(command("human_alice", "driver"))
        .await
        .expect("the CURRENT owner steers the driver Bot");
    assert!(granted.confirm_url.contains("/groups/"));

    // Manager edges count for this Human→Bot judgment.
    fixture.authority.seed_manager("driver", "morgan");
    let granted = service
        .create_proposal(command("human_morgan", "driver"))
        .await
        .expect("a CURRENT manager steers the driver Bot");
    assert!(granted.confirm_url.contains("/groups/"));

    let denied = service
        .create_proposal(command("human_unrelated", "driver"))
        .await
        .expect_err("an unrelated Human is denied");
    assert!(matches!(denied, GroupUseCaseError::Forbidden(_)));
}

#[tokio::test]
async fn handover_leaves_the_old_owner_fallback_free_denied() {
    let fixture = Fixture::new().with_owned_driver("alice");

    fixture.authority.revoke_owner("driver", "alice");
    fixture.authority.seed_owner("driver", "bob");
    // HISTORICAL display fact deliberately kept: `created_by`
    // still reads alice — it must NOT authorize her.

    let service = fixture.service_with_authority();
    let denied = service
        .create_proposal(command("human_alice", "driver"))
        .await
        .expect_err("the revoked owner is denied despite the legacy display row");
    assert!(matches!(denied, GroupUseCaseError::Forbidden(_)));

    let granted = service
        .create_proposal(command("human_bob", "driver"))
        .await
        .expect("the new owner steers the driver Bot");
    assert!(granted.confirm_url.contains("/groups/"));
}

#[tokio::test]
async fn manager_withdrawal_closes_the_steering_judgment() {
    let fixture = Fixture::new().with_owned_driver("alice");
    fixture.authority.seed_manager("driver", "morgan");

    fixture.authority.revoke_manager("driver", "morgan");

    let service = fixture.service_with_authority();
    let denied = service
        .create_proposal(command("human_morgan", "driver"))
        .await
        .expect_err("the withdrawn manager is fallback-free denied");
    assert!(matches!(denied, GroupUseCaseError::Forbidden(_)));
}

#[tokio::test]
async fn authority_read_failure_is_internal_never_a_deny() {
    let fixture = Fixture::new().with_owned_driver("alice");
    let service =
        fixture.service_with_hook(bcs_test_support::failing_authority_hook());

    let error = service
        .create_proposal(command("human_alice", "driver"))
        .await
        .expect_err("a failing authority read propagates");
    assert!(matches!(
        error,
        GroupUseCaseError::Service(ServiceError::InternalError(_))
    ));
}

#[tokio::test]
async fn bot_self_identity_never_consults_the_hook() {
    let fixture = Fixture::new();
    // No edges seeded at all: the Bot's own authentication is
    // the identity (the driver steering itself) — the hook is
    // not consulted.
    fixture
        .registry
        .insert(support::bot("driver", "Driver Bot", "public", None));

    let service =
        fixture.service_with_hook(bcs_test_support::failing_authority_hook());
    let granted = service
        .create_proposal(command("driver", "driver"))
        .await
        .expect("the Bot caller stays its own identity");
    assert!(granted.confirm_url.contains("/groups/"));
}

// Keep the helper module importable in-program: the shared
// fakes stay exactly where the regression suite uses them.
#[allow(unused_imports)]
use support as _support;
