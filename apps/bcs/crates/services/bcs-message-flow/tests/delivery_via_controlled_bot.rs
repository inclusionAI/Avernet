//! End-to-end service-layer regression for the reported flow:
//! an Owner adds a Manager to their Bot, the Manager creates a
//! Group with that Bot, and the Owner (NOT a Session member)
//! reads the delivery status of real messages through the Bot
//! they currently control (spec §8 row 6; the cutover's
//! group/session history reads already grant the same path).
//!
//! The suite pins BOTH directions of the contract: the legitimate
//! non-member read succeeds, while revocation, session removal,
//! private messages of uncontrolled Bots, and the write gates
//! (cancel stays sender-only) all keep denying.
#![allow(clippy::expect_used, clippy::unwrap_used)]

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

use bcs_domain::{DeliveryType, MessageVisibilityDomain, NewMessage, SenderType};
use bcs_message_flow::{managed_delivery::ManagedMessageDelivery, BcsMessageFlow};
use bcs_service_api::application::v1::{
    AuthenticatedCaller, AuthenticatedUserIdentity, GetGroup, GroupService,
};
use bcs_service_api::port::bot_authority::BotAuthorityPort;
use bcs_service_api::port::repo::message_delivery::{
    AdmitMessageDeliveries, DeliveryAdmissionTarget,
};
use bcs_service_api::port::repo::SessionRepoPort;
use bcs_service_api::types::bot_authority::{BotAccessRelation, BotRoleQuery};
use bcs_service_api::*;
use bcs_test_support::{
    authority_hook_for, grant_manager_edge, grant_owner_edge, revoke_authority_edge,
    NoopSystemMessageService,
};

#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;

const GROUP: &str = "manager-created";

struct Rig {
    bots: Arc<bcs_bot_store::MemoryBotRepo>,
    session_repo: Arc<bcs_session_store::MemorySessionRepo>,
    delivery: Arc<ManagedMessageDelivery>,
    flow: BcsMessageFlow,
    session_id: String,
}

/// Builds the full chain from the report: real MemoryBotRepo
/// authority, real Group/Session management, a Manager-created
/// Group, and a flow wired with the REAL authority chain.
/// `manager_as_human` toggles the two creation identities a
/// frontend can produce (human originator adds `human_manager` to
/// the participants; bot originator does not).
async fn rig(manager_as_human: bool) -> Rig {
    rig_with_authority(manager_as_human, None).await
}

async fn rig_with_authority(
    manager_as_human: bool,
    wrap: Option<Arc<AtomicUsize>>,
) -> Rig {
    let bots = Arc::new(bcs_bot_store::MemoryBotRepo::new());
    let registry = Arc::new(bcs_bot::BotCore::with_repo(bots.clone()));
    registry
        .register(
            "shared-bot".into(),
            BotCapabilities {
                name: Some("Shared Bot".into()),
                visibility: "protected".into(),
                ..Default::default()
            },
        )
        .await
        .unwrap();
    grant_owner_edge(&bots, "shared-bot", "owner").await;
    grant_manager_edge(&bots, "shared-bot", "manager").await;
    registry.ensure_human_actor("manager", "Manager").await.unwrap();
    let authority: Arc<dyn BotAuthorityPort> = match wrap {
        Some(counter) => Arc::new(CountingAuthority {
            inner: authority_hook_for(bots.clone()),
            calls: counter,
        }),
        None => authority_hook_for(bots.clone()),
    };
    let groups_repo = Arc::new(bcs_group::MemoryGroupRepo::new());
    let groups = Arc::new(bcs_group::GroupCore::with_repo(groups_repo.clone()));
    let session_repo = Arc::new(bcs_session_store::MemorySessionRepo::new());
    let sessions = Arc::new(bcs_session::SessionManagementServiceImpl::new(
        session_repo.clone(),
        groups_repo,
    ));
    let friends = Arc::new(bcs_friend::FriendCore::memory());
    let management = Arc::new(
        bcs_group::GroupManagement::new(
            groups.clone(),
            registry.clone(),
            friends.clone(),
            Arc::new(bcs_relation::RelationCore::memory()),
            bcs_group::GroupConfig::default(),
            sessions.clone(),
            Arc::new(NoopSystemMessageService),
        )
        .with_bot_authority(authority.clone()),
    );
    let created = management
        .create_group(GroupCreateCommand {
            create_initial_session: true,
            human_mention_notify_mode: Default::default(),
            group_id: Some(GROUP.into()),
            caller_actor_id: Some("human_manager".into()),
            driver_bot_id: "shared-bot".into(),
            originator: Some(if manager_as_human { "human_manager" } else { "shared-bot" }.into()),
            label: Some("Manager's group".into()),
            topic: None,
            context: None,
            opening_message: None,
            routing_policy: None,
            participants: vec![
                GroupCreateParticipantCommand {
                    bot_id: "shared-bot".into(),
                    role: Some("driver".into()),
                    tags: vec![],
                    message_view_scope: None,
                },
                GroupCreateParticipantCommand {
                    bot_id: "human_manager".into(),
                    role: Some("observer".into()),
                    tags: vec![],
                    message_view_scope: None,
                },
            ]
            .into_iter()
            .filter(|p| manager_as_human || p.bot_id != "human_manager")
            .collect(),
            member_bot_ids: vec![],
            group_kind: None,
            service_spec: None,
            group_strategy: Some(GroupStrategy::Chat),
            visibility: Some("private".into()),
            provisioning: false,
        })
        .await
        .expect("Manager can create the group using the shared Bot");
    let session_id = created.latest_running_session_id.unwrap();

    // The Owner reads the Group detail through the Bot they
    // control — the sibling path the delivery query must agree
    // with.
    let facade = bcs_app_group::GroupServiceImpl::new(
        groups.clone(),
        registry.clone(),
        friends,
        sessions.clone(),
        management,
    )
    .with_bot_authority_hook(authority.clone());
    facade
        .get(GetGroup {
            caller: AuthenticatedCaller {
                tenant: None,
                user: Some(AuthenticatedUserIdentity {
                    id: "owner".into(),
                    username: "owner".into(),
                    display_name: None,
                    full_name: None,
                }),
                bot: None,
                app: None,
                access_key: None,
            },
            group_id: GROUP.into(),
        })
        .await
        .expect("Owner reads the Manager-created group");

    let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
    let delivery = Arc::new(ManagedMessageDelivery::new(repo.clone()));
    let flow = BcsMessageFlow::new(
        groups,
        Arc::new(support::FakeRoutingCoreService::default()),
        registry,
        Arc::new(support::RecordingBotDelivery::default()),
        Arc::new(support::RecordingFrontendDelivery::default()),
    )
    .with_session_management(sessions)
    .with_bot_authority_hook(Some(authority))
    .with_message_repo(repo)
    .with_managed_deliveries(delivery.clone());
    Rig {
        bots,
        session_repo,
        delivery,
        flow,
        session_id,
    }
}

fn caller(user: &str) -> CallerContext {
    CallerContext::Human(HumanActor {
        actor_id: format!("human_{user}"),
        staff_no: user.into(),
    })
}

fn query(user: &str, session_id: &str, message_ids: Vec<String>) -> DeliveryStatusQuery {
    DeliveryStatusQuery {
        caller: caller(user),
        session_id: session_id.into(),
        message_ids,
        client_msg_id: None,
    }
}

/// Admits one real public chat message (default: sent by the
/// Manager Human) with one real delivery row targeting the
/// shared Bot.
async fn admit(rig: &Rig, message_id: &str) {
    rig.delivery
        .admit(AdmitMessageDeliveries {
            display_message: None,
            message_id: message_id.into(),
            flow_kind: bcs_domain::message_delivery::DeliveryFlowKind::Group,
            now_ms: 100,
            expire_at_ms: None,
            event: None,
            message: NewMessage {
                group_id: GROUP.into(),
                session_id: rig.session_id.clone(),
                sender_id: "human_manager".into(),
                sender_type: SenderType::Human,
                message_type: "chat".into(),
                content: serde_json::json!({"text": "hello from Manager"}),
                client_msg_id: None,
                owner_bot_id: None,
                created_at: 100,
                run_id: "run-1".into(),
                visibility_domain: MessageVisibilityDomain::Chat,
                audience: None,
            },
            targets: vec![DeliveryAdmissionTarget {
                rejection: None,
                target_bot_id: "shared-bot".into(),
                kind: DeliveryType::Send,
                max_queued: 10,
                semantic_projection_json: serde_json::json!({"version": 1}), run_id: None,}],
        })
        .await
        .unwrap();
}

struct CountingAuthority {
    inner: Arc<dyn BotAuthorityPort>,
    calls: Arc<AtomicUsize>,
}

#[async_trait::async_trait]
impl BotAuthorityPort for CountingAuthority {
    async fn role(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> ServiceResult<Option<BotAccessRelation>> {
        self.inner.role(user_id, bot_id).await
    }

    async fn roles_for(
        &self,
        queries: &[BotRoleQuery],
    ) -> ServiceResult<Vec<Option<BotAccessRelation>>> {
        self.inner.roles_for(queries).await
    }

    async fn can_manage(&self, user_id: &str, bot_id: &str) -> ServiceResult<bool> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        self.inner.can_manage(user_id, bot_id).await
    }

    async fn require_owner(&self, user_id: &str, bot_id: &str) -> ServiceResult<()> {
        self.inner.require_owner(user_id, bot_id).await
    }

    async fn require_manage(&self, user_id: &str, bot_id: &str) -> ServiceResult<()> {
        self.inner.require_manage(user_id, bot_id).await
    }
}

/// THE user-reported regression, in both creation identities:
/// before the fix the Owner's query of a REAL delivery was
/// rejected with "session membership required" while an empty
/// query passed.
async fn owner_reads_through_controlled_bot(manager_as_human: bool) {
    let rig = rig(manager_as_human).await;
    let session = rig.flow.session_management.as_ref().unwrap().get(&rig.session_id).await.unwrap().unwrap();
    let participants: Vec<_> = session.participants.iter().map(|p| p.bot_uuid.clone()).collect();
    assert_eq!(participants.contains(&"human_manager".to_string()), manager_as_human);
    assert!(!participants.contains(&"human_owner".to_string()));

    // Empty result stays cheap and successful.
    assert!(rig
        .flow
        .query_message_deliveries(query("owner", &rig.session_id, vec!["m-1".into()]))
        .await
        .unwrap()
        .is_empty());

    admit(&rig, "m-1").await;

    // The Owner reads the REAL delivery through the Bot they
    // currently control — this was the 403 before the fix.
    let rows = rig
        .flow
        .query_message_deliveries(query("owner", &rig.session_id, vec!["m-1".into()]))
        .await
        .expect("Owner reads the real delivery through the controlled Bot");
    assert_eq!(rows.len(), 1);

    // The Manager reads it too: as a direct member when they
    // created as Human, or through their own manager edge on the
    // same Bot when they created as the Bot.
    let rows = rig
        .flow
        .query_message_deliveries(query("manager", &rig.session_id, vec!["m-1".into()]))
        .await
        .expect("Manager reads the real delivery");
    assert_eq!(rows.len(), 1);
}

#[tokio::test]
async fn manager_creates_as_human_owner_reads_real_delivery() {
    owner_reads_through_controlled_bot(true).await;
}

#[tokio::test]
async fn manager_creates_as_bot_owner_reads_real_delivery() {
    owner_reads_through_controlled_bot(false).await;
}

#[tokio::test]
async fn transferred_ownership_loses_the_former_owner_read() {
    let rig = rig(true).await;
    admit(&rig, "m-1").await;
    // Owner transfer: the new owner edge REPLACES the old one
    // (one Bot has exactly one current owner, spec §3.2.1), so
    // the former owner keeps no role — the read must lapse.
    grant_owner_edge(&rig.bots, "shared-bot", "new-owner").await;
    let error = rig
        .flow
        .query_message_deliveries(query("owner", &rig.session_id, vec!["m-1".into()]))
        .await
        .unwrap_err();
    assert!(
        matches!(&error, ServiceError::Forbidden(reason) if reason == "session membership required"),
        "a transferred-away owner keeps no read, got {error:?}"
    );
    // …while the new owner immediately reads through the Bot.
    let rows = rig
        .flow
        .query_message_deliveries(query("new-owner", &rig.session_id, vec!["m-1".into()]))
        .await
        .expect("the new owner reads through the Bot");
    assert_eq!(rows.len(), 1);
}

#[tokio::test]
async fn revoked_manager_edge_loses_the_session_read() {
    // Bot-originated creation: the Manager Human is NOT a
    // Session member and reads ONLY through the manager edge.
    let rig = rig(false).await;
    admit(&rig, "m-1").await;
    revoke_authority_edge(
        &rig.bots,
        "shared-bot",
        "manager",
        bcs_bot_store::memory_authority::MemoryAuthorityEdgeKind::Manager,
    )
    .await;
    let error = rig
        .flow
        .query_message_deliveries(query("manager", &rig.session_id, vec!["m-1".into()]))
        .await
        .unwrap_err();
    assert!(
        matches!(&error, ServiceError::Forbidden(reason) if reason == "session membership required"),
        "a revoked manager edge must lapse immediately, got {error:?}"
    );
}

#[tokio::test]
async fn removing_the_bot_from_the_session_loses_the_read() {
    let rig = rig(true).await;
    admit(&rig, "m-1").await;
    rig.session_repo
        .remove_participant(&rig.session_id, "shared-bot")
        .await
        .unwrap();
    let error = rig
        .flow
        .query_message_deliveries(query("owner", &rig.session_id, vec!["m-1".into()]))
        .await
        .unwrap_err();
    assert!(
        matches!(&error, ServiceError::Forbidden(reason) if reason == "session membership required"),
        "a Bot outside the session grants no read, got {error:?}"
    );
}

#[tokio::test]
async fn private_message_of_uncontrolled_bot_stays_hidden() {
    let rig = rig(true).await;
    // A private message owned by a Bot the Owner does NOT
    // control must stay hidden even though the Owner controls a
    // participating Bot.
    rig.flow
        .registry
        .register(
            "other-bot".into(),
            BotCapabilities {
                name: Some("Other Bot".into()),
                ..Default::default()
            },
        )
        .await
        .unwrap();
    rig.delivery
        .admit(AdmitMessageDeliveries {
            display_message: None,
            message_id: "m-private".into(),
            flow_kind: bcs_domain::message_delivery::DeliveryFlowKind::Group,
            now_ms: 100,
            expire_at_ms: None,
            event: None,
            message: NewMessage {
                group_id: GROUP.into(),
                session_id: rig.session_id.clone(),
                sender_id: "other-bot".into(),
                sender_type: SenderType::Bot,
                message_type: "chat".into(),
                content: serde_json::json!({"text": "not for the Owner"}),
                client_msg_id: None,
                owner_bot_id: Some("other-bot".into()),
                created_at: 100,
                run_id: "run-1".into(),
                visibility_domain: MessageVisibilityDomain::Chat,
                audience: None,
            },
            targets: vec![DeliveryAdmissionTarget {
                rejection: None,
                target_bot_id: "shared-bot".into(),
                kind: DeliveryType::Send,
                max_queued: 10,
                semantic_projection_json: serde_json::json!({"version": 1}), run_id: None,}],
        })
        .await
        .unwrap();
    let error = rig
        .flow
        .query_message_deliveries(query("owner", &rig.session_id, vec!["m-private".into()]))
        .await
        .unwrap_err();
    assert!(
        matches!(&error, ServiceError::Forbidden(reason) if reason == "private message is not visible"),
        "controlling ONE participant never unlocks every private message, got {error:?}"
    );
}

#[tokio::test]
async fn cancel_stays_sender_only_for_a_reading_owner() {
    let rig = rig(true).await;
    admit(&rig, "m-1").await;
    // Read access through the controlled Bot must NOT become
    // write access: the message was sent by `human_manager`, so
    // the Owner's cancel attempt keeps failing the sender gate.
    let error = rig
        .flow
        .cancel_message_deliveries(CancelMessageDeliveryCommand {
            caller: caller("owner"),
            session_id: rig.session_id.clone(),
            message_id: "m-1".into(),
            delivery_id: None,
        })
        .await
        .unwrap_err();
    assert!(
        matches!(&error, ServiceError::Forbidden(reason) if reason == "only the message sender can cancel its delivery"),
        "read access never widens into a write, got {error:?}"
    );
}

#[tokio::test]
async fn authority_reads_stay_bounded_by_participants_not_messages() {
    let counter = Arc::new(AtomicUsize::new(0));
    let rig = rig_with_authority(false, Some(counter.clone())).await;
    admit(&rig, "m-1").await;
    admit(&rig, "m-2").await;
    admit(&rig, "m-3").await;

    let baseline = counter.load(Ordering::SeqCst);
    let rows = rig
        .flow
        .query_message_deliveries(query(
            "owner",
            &rig.session_id,
            vec!["m-1".into(), "m-2".into(), "m-3".into()],
        ))
        .await
        .expect("Owner reads three real deliveries");
    assert_eq!(rows.len(), 3);
    // One entry scan per request, one live role read per Bot
    // participant (exactly ONE here) — authority reads never
    // scale with the message count.
    let first_scan = counter.load(Ordering::SeqCst) - baseline;
    assert_eq!(first_scan, 1, "authority reads for one 3-message query: {first_scan}");

    // A second query re-checks the live edge (no cross-request
    // authority caching): same bounded count again.
    let baseline = counter.load(Ordering::SeqCst);
    rig.flow
        .query_message_deliveries(query("owner", &rig.session_id, vec!["m-1".into()]))
        .await
        .expect("second query");
    let second_scan = counter.load(Ordering::SeqCst) - baseline;
    assert_eq!(second_scan, 1, "fresh live read per request: {second_scan}");
}
