//! Rule 25 driver: runs the shared conformance
//! harness against the REAL composed
//! `DeliveryAuthorizationServiceImpl` (real
//! Group/Session authorization snapshots over the real
//! memory stores + a REAL authority-hook state).

use std::collections::HashSet;
use std::sync::{Arc, Mutex};

use async_trait::async_trait;

use bcs_app_session::DeliveryAuthorizationServiceImpl;
use bcs_domain::{ActorKind, GroupStrategy, MessageAudience, MessageVisibilityDomain};
use bcs_group::GroupCore;
use bcs_group_store::MemoryGroupRepo;
use bcs_service_api::application::v1::delivery_authorization::{
    DeliveryAuthorizationService, DeliveryDecision,
    DeliveryVisibility, ProtectedDeliveryBinding,
};
use bcs_service_api::port::bot_authority::BotAuthorityHook;
use bcs_service_api::port::repo::{NewSessionParams, GroupRepoPort, SessionRepoPort};
use bcs_service_api::types::bot_authority::{BotAccessRelation, BotRoleQuery};
use bcs_service_api::{Group as DomainGroup, Participant, ParticipantMode, ParticipantRole, ServiceResult, SessionKind};
use bcs_session::SessionAuthorizationCoreImpl;
use bcs_session_store::MemorySessionRepo;
use bcs_test_support::contract::delivery_authorization::{
    DeliveryAuthorizationFixture, delivery_authorization_service_contract_tests,
};

/// A REAL authority-hook state: management edges
/// granted/revoked through the same semantic surface the
/// runtime consults (no permissive shortcut — a revoke
/// really removes the edge from the consulted set).
#[derive(Default)]
struct RealHookState {
    owners: Mutex<HashSet<(String, String)>>,
    managers: Mutex<HashSet<(String, String)>>,
}

impl RealHookState {
    fn grant_manager(&self, user: &str, bot: &str) {
        self.managers
            .lock()
            .expect("hook state")
            .insert((user.to_string(), bot.to_string()));
    }

    fn revoke(&self, user: &str, bot: &str) {
        self.owners
            .lock()
            .expect("hook state")
            .remove(&(user.to_string(), bot.to_string()));
        self.managers
            .lock()
            .expect("hook state")
            .remove(&(user.to_string(), bot.to_string()));
    }
}

#[async_trait]
impl BotAuthorityHook for RealHookState {
    async fn role(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> ServiceResult<Option<BotAccessRelation>> {
        if self.owners.lock().expect("hook state").contains(&(user_id.to_string(), bot_id.to_string())) {
            Ok(Some(BotAccessRelation::Owner))
        } else if self.managers.lock().expect("hook state").contains(&(user_id.to_string(), bot_id.to_string())) {
            Ok(Some(BotAccessRelation::Manager))
        } else {
            Ok(None)
        }
    }

    async fn roles_for(
        &self,
        queries: &[BotRoleQuery],
    ) -> ServiceResult<Vec<Option<BotAccessRelation>>> {
        let mut out = Vec::with_capacity(queries.len());
        for query in queries {
            out.push(self.role(&query.user_id, &query.bot_id).await?);
        }
        Ok(out)
    }

    async fn can_manage(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> ServiceResult<bool> {
        Ok(self.role(user_id, bot_id).await?.is_some())
    }

    async fn require_owner(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> ServiceResult<()> {
        if self.role(user_id, bot_id).await?
            == Some(BotAccessRelation::Owner)
        {
            Ok(())
        } else {
            Err(bcs_service_api::ServiceError::Forbidden(
                "no owner edge".into(),
            ))
        }
    }

    async fn require_manage(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> ServiceResult<()> {
        if self.can_manage(user_id, bot_id).await? {
            Ok(())
        } else {
            Err(bcs_service_api::ServiceError::Forbidden(
                "no owner/manager edge".into(),
            ))
        }
    }
}

/// The real composed fixtures: real memory groups and
/// sessions, the real session core, and the hook above.
struct ComposedFixture {
    group_repo: Arc<dyn GroupRepoPort>,
    session_repo: Arc<dyn SessionRepoPort>,
    hook: Arc<RealHookState>,
    group_id: String,
    session_id: String,
}

fn human_participant(actor_id: &str) -> Participant {
    Participant {
        bot_uuid: actor_id.to_string(),
        bot_name: Some(actor_id.to_string()),
        kind: None,
        role: bcs_service_api::ParticipantRole::Observer,
        actor_kind: bcs_domain::ActorKind::Human,
        mode: Some(bcs_service_api::ParticipantMode::Present),
        tags: Vec::new(),
        message_view_scope: bcs_domain::MessageViewScope::Participant,
    }
}

fn bot_participant(bot_id: &str, role: bcs_service_api::ParticipantRole) -> Participant {
    Participant {
        bot_uuid: bot_id.to_string(),
        bot_name: Some(bot_id.to_string()),
        kind: None,
        role,
        actor_kind: bcs_domain::ActorKind::Bot,
        mode: Some(bcs_service_api::ParticipantMode::Present),
        tags: Vec::new(),
        message_view_scope: bcs_domain::MessageViewScope::Participant,
    }
}

async fn composed() -> ComposedFixture {
    let group_repo = Arc::new(MemoryGroupRepo::new());
    let session_repo = Arc::new(MemorySessionRepo::new());
    let mut group = DomainGroup::new(
        "harness-group",
        "harness-driver",
        Vec::new(),
    );
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.participants = vec![
        bot_participant(
            "harness-driver",
            bcs_service_api::ParticipantRole::Driver,
        ),
        bot_participant(
            "harness-worker",
            bcs_service_api::ParticipantRole::Worker,
        ),
        human_participant("human_view"),
    ];
    group_repo
        .upsert(group.clone())
        .await
        .expect("seed group");
    let session = session_repo
        .create(
            "harness-group",
            NewSessionParams {
                session_kind: SessionKind::Chat,
                participants: group.participants.clone(),
                created_by: Some("harness-driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed session");
    let hook = Arc::new(RealHookState::default());
    ComposedFixture {
        group_repo: group_repo.clone(),
        session_repo: session_repo.clone(),
        hook,
        group_id: "harness-group".to_string(),
        session_id: session.id.clone(),
    }
}

impl ComposedFixture {
    fn binding(
        &self,
        user_id: &str,
        view_actor_id: &str,
    ) -> ProtectedDeliveryBinding {
        ProtectedDeliveryBinding {
            tenant: Some("tenant-a".to_string()),
            user_id: user_id.to_string(),
            group_id: self.group_id.clone(),
            session_id: Some(self.session_id.clone()),
            view_actor_id: view_actor_id.to_string(),
            connection_instance_id: 7,
        }
    }
}

#[async_trait]
impl DeliveryAuthorizationFixture for ComposedFixture {
    fn service(
        &self,
    ) -> Arc<dyn DeliveryAuthorizationService> {
        Arc::new(DeliveryAuthorizationServiceImpl::new(
            Arc::new(GroupCore::with_repo(self.group_repo.clone())),
            Arc::new(SessionAuthorizationCoreImpl::new(
                self.session_repo.clone(),
            )),
            Some(self.hook.clone() as Arc<dyn BotAuthorityHook>),
        ))
    }

    async fn authorized_frame(
        &self,
    ) -> (ProtectedDeliveryBinding, DeliveryVisibility) {
        self.hook.grant_manager("user-a", "harness-worker");
        (
            self.binding("user-a", "human_view"),
            DeliveryVisibility {
                domain: MessageVisibilityDomain::Chat,
                audience: None,
            },
        )
    }

    async fn scope_restricted_frame(
        &self,
    ) -> (ProtectedDeliveryBinding, DeliveryVisibility) {
        self.hook.grant_manager("user-a", "harness-worker");
        (
            self.binding("user-a", "human_view"),
            DeliveryVisibility {
                domain: MessageVisibilityDomain::ManagerWorker,
                audience: Some(MessageAudience::FullOnly),
            },
        )
    }

    async fn revoked_frame(
        &self,
    ) -> (ProtectedDeliveryBinding, DeliveryVisibility) {
        // A committed revocation: remove the consulted edge.
        self.hook.revoke("user-a", "harness-worker");
        (
            self.binding("user-a", "harness-worker"),
            DeliveryVisibility {
                domain: MessageVisibilityDomain::Chat,
                audience: None,
            },
        )
    }
}

/// The final-writer decision contract on the REAL
/// composed implementation.
#[tokio::test]
async fn delivery_authorization_service_contract_composed() {
    let fixture = composed().await;
    delivery_authorization_service_contract_tests(&fixture)
        .await;
}

/// The binding kept after a Skip is REAL: the same
/// view still receives the frames its scope allows.
#[tokio::test]
async fn delivery_authorization_skip_keeps_the_live_frame() {
    let fixture = composed().await;
    let binding = {
        fixture.hook.grant_manager("user-a", "harness-worker");
        fixture.binding("user-a", "human_view")
    };
    assert_eq!(
        fixture
                    .service()
            .authorize_delivery(
                &binding,
                &DeliveryVisibility {
                    domain: MessageVisibilityDomain::ManagerWorker,
                    audience: Some(MessageAudience::FullOnly),
                },
            )
            .await
            .expect("tightened frame"),
        DeliveryDecision::SkipMessage
    );
    assert_eq!(
        fixture
                    .service()
            .authorize_delivery(
                &binding,
                &DeliveryVisibility {
                    domain: MessageVisibilityDomain::Chat,
                    audience: None,
                },
            )
            .await
            .expect("live frame after the skip"),
        DeliveryDecision::Deliver,
        "skip keeps the binding; the live frame still delivers"
    );
}
