//! Forced managed admission for caller-enabled targets under a fully
//! disabled delivery policy (the production "inline direct" shape:
//! flow_enabled.group=false, defaults Off, no bot Enforce override).
//!
//! A Human's group speech whose routed target is caller-enabled must still
//! become a managed caller_required row — the protected dispatch's only
//! substrate. It must never fall to the ordinary inline transport, where
//! CallerConfigurationDeliveryGate rejects caller-enabled targets with
//! CALLER_EXECUTION_UNAVAILABLE (the 2026-11-11 prod incident: every
//! user-agent group chat failed delivery because no policy row existed).
//!
//! Plain targets under the same disabled policy stay ordinary: only the
//! caller-enabled routed target is force-admitted, so the fix cannot
//! widen the managed queue onto unconfigured deployments' other bots.

use std::sync::Arc;

use bcs_config::resolve_env_str as resolve_env;
use bcs_domain::message_delivery::MessageDeliveryStatus as Status;
use bcs_message_flow::caller_admission::CallerAdmissionDeps;
use bcs_message_flow::caller_delivery_handoff::HumanCallerDeliveryHandoff;
use bcs_message_flow::delivery_policy::LiveDeliveryPolicy;
use bcs_message_flow::managed_delivery::ManagedMessageDelivery;
use bcs_message_flow::{BcsMessageFlow, MemoryBotRunContextStore};
use bcs_message_store::MemoryMessageRepo;
use bcs_service_api::application::caller_handoff::{
    HandoffClaim, HandoffScope, HumanCallerDeliveryHandoffService,
};
use bcs_service_api::application::caller_identity::{
    AuthenticatedHumanCallerIngress, HumanCallerAuthSource,
};
use bcs_service_api::application::principal::HumanActor;
use bcs_service_api::port::repo::session_speaking_lease::{AcquireLease, LeaseAcquire};
use bcs_service_api::{
    BotCallerConfiguration, BotCandidateReadQuery, BotControlPlaneCandidate,
    BotControlPlaneCoreService, BotControlPlaneRecord, BotControlPlaneView, CallerContext,
    CallerIdentityConfig, CallerIdentitySnapshot, CallerMinimumIsolation, GroupCoreService,
    ManagedMessageDeliveryService, MessageFlowService, ServiceResult, WebSendCommand,
    WebSendLeaseClaim,
};
use bcs_service_api::HumanActor as CallerPrincipal;
use bcs_session_store::MemorySpeakingLeaseRepo;
use serde_json::json;

use crate::session_support as session;
use crate::flow_support as support;

const SESSION: &str = "group-1:inline-policy-off";
const CALLER_TARGET: &str = "bot-driver";
const PLAIN_TARGET: &str = "bot-observer";

/// Caller-enabled for the routed target only; every other bot reads
/// caller-unconfigured so the forced admission branch is observable in
/// isolation from the policy's managed selection.
struct EnabledDriverOnlyControlPlane;

#[async_trait::async_trait]
impl BotControlPlaneCoreService for EnabledDriverOnlyControlPlane {
    async fn get_caller_configuration(
        &self,
        bot_id: &str,
        _env: &str,
    ) -> ServiceResult<Option<BotCallerConfiguration>> {
        Ok(Some(BotCallerConfiguration {
            snapshot: (bot_id == CALLER_TARGET).then(|| CallerIdentitySnapshot {
                source_revision: 1,
                caller_identity: CallerIdentityConfig {
                    enabled: true,
                    minimum_isolation: CallerMinimumIsolation::CallerContainer,
                },
            }),
        }))
    }

    async fn get_record(&self, _: &str, _: &str) -> ServiceResult<Option<BotControlPlaneRecord>> {
        unimplemented!("not needed by this test")
    }
    async fn get(&self, _: &str, _: &str) -> ServiceResult<Option<BotControlPlaneView>> {
        unimplemented!("not needed by this test")
    }
    async fn get_by_ids(&self, _: &[String], _: &str) -> ServiceResult<Vec<BotControlPlaneView>> {
        unimplemented!("not needed by this test")
    }
    async fn list_candidates(&self, _: BotCandidateReadQuery)
        -> ServiceResult<(Vec<BotControlPlaneCandidate>, u64)> {
        unimplemented!("not needed by this test")
    }
    async fn list_by_creator(
        &self,
        _: bcs_service_api::BotControlPlaneOwnedQuery,
    ) -> ServiceResult<Vec<BotControlPlaneView>> {
        unimplemented!("not needed by this test")
    }
    async fn patch(
        &self,
        _: &str,
        _: &str,
        _: bcs_service_api::BotControlPlanePatch,
    ) -> ServiceResult<Option<BotControlPlaneView>> {
        unimplemented!("not needed by this test")
    }
}

struct Fixture {
    flow: Arc<BcsMessageFlow>,
    service: Arc<ManagedMessageDelivery>,
    handoff: Arc<HumanCallerDeliveryHandoff>,
}

async fn fixture() -> Fixture {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let repo = Arc::new(MemoryMessageRepo::new().with_environment(resolve_env()));
    // The prod shape under test: a live policy with every flow disabled.
    // DeliveryPolicy::default() is exactly that (flow_enabled all false,
    // defaults Off, no per-bot entries). The service carries the SAME
    // live policy as flow.delivery_policy — mirroring production
    // (message_delivery_wiring composes with_policy(live)) — so the
    // admit_batch policy gate actually runs; a policy-less service skips
    // the gate entirely and would not prove the fix.
    let live = Arc::new(LiveDeliveryPolicy::new(repo.clone(), Default::default()));
    let service = Arc::new(ManagedMessageDelivery::new(repo.clone()).with_policy(live.clone()));
    let handoff = Arc::new(HumanCallerDeliveryHandoff::new(8));
    let group = support.group.get("group-1").await.unwrap();

    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo)
    .with_managed_deliveries(service.clone())
    .with_bot_run_context(Arc::new(MemoryBotRunContextStore::default()))
    .with_session_management(Arc::new(session::StaticSessionManagement::new(
        session::test_session(SESSION, "group-1", group.participants.clone()),
    )))
    .with_speaking_leases(Arc::new(MemorySpeakingLeaseRepo::new()));

    let mut flow = Arc::new(flow);
    {
        let flow_mut = Arc::get_mut(&mut flow).unwrap();
        flow_mut.delivery_policy = Some(live);
        flow_mut.caller_admission = Some(Arc::new(CallerAdmissionDeps {
            handoff: handoff.clone() as Arc<dyn HumanCallerDeliveryHandoffService>,
            admission: service.clone(),
            control_plane: Arc::new(EnabledDriverOnlyControlPlane),
            reserve_ttl_ms: 30_000,
        }));
    }
    Fixture { flow, service, handoff }
}

async fn claim_lease(flow: &BcsMessageFlow) -> WebSendLeaseClaim {
    let repo = flow.speaking_leases.as_ref().expect("lease store wired");
    match repo
        .acquire(AcquireLease {
            session_id: "group-1",
            requester_actor_id: "human_1",
            now_ms: chrono::Utc::now().timestamp_millis(),
            idle_timeout_ms: 60_000,
        })
        .await
        .unwrap()
    {
        LeaseAcquire::Granted(lease) | LeaseAcquire::SelfHeld(lease) =>
            WebSendLeaseClaim { lease_id: lease.lease_id, epoch: lease.epoch },
        other => panic!("expected a held lease, got {other:?}"),
    }
}

fn ingress() -> AuthenticatedHumanCallerIngress {
    AuthenticatedHumanCallerIngress::new(
        HumanActor { actor_id: "human_1".into(), staff_no: "1".into() },
        HumanCallerAuthSource::HttpAuth,
        1,
        &resolve_env(),
    )
    .expect("valid ingress")
}

/// The caller group shape: a Human speaking under lease with a strict
/// caller ingress, mirroring an authenticated workbench chat.send.
async fn web_send(flow: &BcsMessageFlow) -> WebSendCommand {
    WebSendCommand {
        caller_execution: None,
        caller_ingress: Some(ingress()),
        speaking_lease: Some(claim_lease(flow).await),
        caller: CallerContext::Human(CallerPrincipal { actor_id: "human_1".into(), staff_no: "1".into() }),
        group_id: "group-1".into(),
        session_id: Some(SESSION.into()),
        from_actor_id: "human_1".into(),
        from_name: Some("Human One".into()),
        message: "hello".into(),
        mentions: vec![],
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
        channel_sender_identity: None,
        sender_conn_id: None,
        provider_bypass_headers: Vec::new(),
    }
}

#[tokio::test]
async fn policy_off_speech_to_a_caller_target_is_force_admitted_as_a_managed_row() {
    let f = fixture().await;

    let command = web_send(f.flow.as_ref()).await;
    let outcome = f
        .flow
        .handle_web_send(command)
        .await
        .expect("caller-enabled target must be force-admitted under a disabled policy");

    // The speech became a managed queued admission (never lost to the
    // inline path where the gate would refuse it).
    assert!(outcome.queue_admission.is_some(), "outcome: {:?}", outcome.status);
    // No ordinary direct delivery may carry the caller-enabled target.
    assert!(
        outcome.bot_deliveries.iter().all(|result| result.target_bot_id != CALLER_TARGET),
        "the caller-enabled target must not ride the ordinary direct transport"
    );

    // The persisted row is the protected dispatch's substrate: queued,
    // marked caller_required, addressed to the caller bot.
    let rows = f.service.snapshot(None).await.expect("snapshot");
    let row = rows
        .iter()
        .find(|r|
            r.target_bot_id == CALLER_TARGET
                && r.semantic_projection_json.get("caller_required").is_some())
        .expect("a caller_required row for the caller-enabled target")
        .clone();
    assert_eq!(row.state.status, Status::Queued);
    assert!(row.semantic_projection_json.get("caller_required") == Some(&json!(true)));

    // The Human identity is parked on the row's scope, claimable exactly once.
    let scope = HandoffScope {
        env: resolve_env(),
        session_id: row.session_id.clone(),
        group_id: Some(row.group_id.clone()),
        source_message_id: row.source_message_id.clone(),
        target_bot_id: row.target_bot_id.clone(),
    };
    let now = chrono::Utc::now().timestamp_millis();
    let parked = f
        .handoff
        .claim(&scope, HandoffClaim::new(row.delivery_id.clone(), row.state.state_version, None, 1, now + 1_000))
        .await
        .expect("parked identity claimable");
    assert_eq!(parked.actor().actor_id, "human_1");
    assert_eq!(
        f.handoff
            .claim(&scope, HandoffClaim::new(row.delivery_id.clone(), 99, None, 1, now + 2_000))
            .await
            .unwrap_err(),
        bcs_service_api::application::caller_handoff::HandoffError::AlreadyClaimed
    );
}

#[tokio::test]
async fn policy_off_speech_to_plain_targets_keeps_the_ordinary_inline_course() {
    let f = fixture().await;

    let outcome = f
        .flow
        .handle_web_send(web_send(f.flow.as_ref()).await)
        .await
        .expect("ordinary speech stays deliverable with a disabled policy");

    // The plain observer never becomes a managed row — the forced branch
    // only ever claims caller-enabled targets.
    let rows = f.service.snapshot(None).await.expect("snapshot");
    assert!(
        rows.iter().all(|row| row.target_bot_id != PLAIN_TARGET),
        "plain targets are never force-admitted"
    );
    // The plain observer keeps its ordinary inline observation injection
    // (delivery_results carries the non-managed inline legs).
    assert!(
        outcome.delivery_results.iter().any(|result| result.bot_uuid == PLAIN_TARGET),
        "the plain observer keeps its ordinary inline injection: {:?}",
        outcome.status
    );
}
