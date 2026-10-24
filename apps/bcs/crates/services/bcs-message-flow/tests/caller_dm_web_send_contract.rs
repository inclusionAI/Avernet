//! End-to-end web_send DM caller admission (spec §7.5.1 write side): the
//! real BcsMessageFlow carries a runtime-only caller ingress through the
//! real queued admission, into the real caller orchestration seam, against
//! a real SQLite caller configuration. The ordinary path stays untouched
//! for non-Caller targets; Caller targets reject missing/invalid ingress.

use bcs_bot::core::BotControlPlaneCore;
use bcs_bot_store::provider::MemoryProviderStore;
use bcs_config::resolve_env_str;
use bcs_db_api::{DbPlugin, DbStatement, DbValue as Value};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_domain::message_delivery::MessageDeliveryStatus as Status;
use bcs_message_flow::caller_admission::CallerAdmissionDeps;
use bcs_message_flow::caller_delivery_handoff::HumanCallerDeliveryHandoff;
use bcs_message_flow::managed_delivery::ManagedMessageDelivery;
use bcs_message_flow::BcsMessageFlow;
use bcs_message_store::MemoryMessageRepo;
use bcs_service_api::application::caller_execution::{
    HandoffClaim, HandoffScope, HumanCallerDeliveryHandoffService,
};
use bcs_service_api::application::caller_identity::{
    AuthenticatedHumanCallerIngress, HumanCallerAuthSource,
};
use bcs_service_api::application::principal::HumanActor;
use bcs_service_api::application::MessageFlowService;
use bcs_service_api::port::repo::BotControlPlaneRepoPort;
use bcs_service_api::{
    BotRegistryCoreService, BotControlPlaneCoreService, CallerContext, GroupCoreService,
    HumanActor as CallerPrincipal, ManagedMessageDeliveryService, ProviderBotBindingRepoPort,
    ProviderRepoPort, WebSendCommand,
};
use bcs_bot_store::PersistentBotRepo;
use serde_json::json;
use std::sync::Arc;
#[path = "support/session.rs"]
mod session_support;
#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;
use session_support::{StaticSessionManagement, test_session};

async fn sqlite_db() -> Arc<dyn DbPlugin> {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            bot_uuid TEXT NOT NULL,
            name TEXT NOT NULL,
            bot_info TEXT,
            env TEXT,
            actor_kind TEXT NOT NULL DEFAULT 'bot',
            status TEXT NOT NULL DEFAULT 'online',
            is_deleted INTEGER NOT NULL DEFAULT 0,
            UNIQUE (bot_uuid, env)
        )",
    ))
    .await
    .expect("create bots table");
    db
}

async fn seed_caller_enabled_bearer_bot(db: &dyn DbPlugin, bot_id: &str) {
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots (bot_uuid, name, bot_info, env) VALUES (?, ?, ?, ?)",
        vec![
            Value::from(bot_id.to_string()),
            Value::from(format!("Bot {bot_id}")),
            Value::from(
                json!({"summary":"s","caller":{"source_revision":1,"caller_identity":{"enabled":true}}}).to_string(),
            ),
            Value::from(resolve_env_str()),
        ],
    ))
    .await
    .expect("seed caller-enabled bot");
}

fn now_clock() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as i64
}

fn ingress() -> AuthenticatedHumanCallerIngress {
    let now = now_clock();
    AuthenticatedHumanCallerIngress::new(
        HumanActor { actor_id: "human_1".to_string(), staff_no: "1".to_string() },
        HumanCallerAuthSource::HttpAuth,
        now,
        now + 300_000,
        resolve_env_str(),
    )
    .expect("valid ingress")
}

async fn dm_flow(db: Arc<dyn DbPlugin>) -> (BcsMessageFlow, Arc<HumanCallerDeliveryHandoff>, Arc<ManagedMessageDelivery>) {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    {
        use bcs_service_api::{Group, GroupKind, ParticipantMode, ParticipantRole};
        let mut group = support.group.get("group-1").await.unwrap();
        group.group_kind = GroupKind::Dm;
        group.driver_bot = "bot-driver".to_string();
        group.dm_pair_key = Some("bot-driver|human_1".to_string());
        group.participants = vec![
            Group::human_participant_merge("human_1", ParticipantRole::Observer, ParticipantMode::Present),
            Group::bot_participant_merge("bot-driver", ParticipantRole::Driver),
        ];
        support.group.upsert(group).await.unwrap();
    }
    let repo = Arc::new(MemoryMessageRepo::new());
    let managed = Arc::new(ManagedMessageDelivery::new(repo.clone()));
    let repo_port: Arc<dyn BotControlPlaneRepoPort> = Arc::new(PersistentBotRepo::new(db.clone()));
    let provider_store = Arc::new(MemoryProviderStore::default());
    let control_plane: Arc<dyn BotControlPlaneCoreService> = Arc::new(BotControlPlaneCore::new(
        repo_port,
        provider_store.clone() as Arc<dyn ProviderRepoPort>,
        provider_store as Arc<dyn ProviderBotBindingRepoPort>,
    ));
    let handoff = Arc::new(HumanCallerDeliveryHandoff::new(16));
    let deps = Arc::new(CallerAdmissionDeps {
        handoff: handoff.clone(),
        admission: managed.clone() as Arc<dyn ManagedMessageDeliveryService>,
        control_plane,
        reserve_ttl_ms: 10_000,
    });
    let group = support.group.get("group-1").await.unwrap();
    let mut flow = BcsMessageFlow::new(
        support.group,
        support.routing,
        support.registry.clone() as Arc<dyn BotRegistryCoreService>,
        support.bot_delivery,
        support.frontend_delivery,
    )
    .with_message_repo(repo)
    .with_managed_deliveries(managed.clone())
    .with_group_delivery_limits(std::collections::BTreeMap::from([(
        "bot-driver".to_string(),
        100,
    )]));
    flow.caller_admission = Some(deps);

    let participants = group.participants.clone();
    flow = flow.with_session_management(Arc::new(StaticSessionManagement::new(test_session(
        "group-1:caller-dm",
        "group-1",
        participants,
    ))));
    (flow, handoff, managed)
}

trait GroupFixtureExt {
    fn human_participant_merge(id: &str, role: bcs_service_api::ParticipantRole, mode: bcs_service_api::ParticipantMode) -> bcs_service_api::Participant;
    fn bot_participant_merge(id: &str, role: bcs_service_api::ParticipantRole) -> bcs_service_api::Participant;
}

impl GroupFixtureExt for bcs_service_api::Group {
    fn human_participant_merge(id: &str, role: bcs_service_api::ParticipantRole, mode: bcs_service_api::ParticipantMode) -> bcs_service_api::Participant {
        let mut p = bcs_service_api::Participant::human(id, role);
        p.mode = Some(mode);
        p
    }
    fn bot_participant_merge(id: &str, role: bcs_service_api::ParticipantRole) -> bcs_service_api::Participant {
        bcs_service_api::Participant::bot(id, role)
    }
}

fn web_send(caller_ingress: Option<AuthenticatedHumanCallerIngress>) -> WebSendCommand {
    WebSendCommand {
        caller_ingress,
        speaking_lease: None,
        caller: CallerContext::Human(CallerPrincipal { actor_id: "human_1".into(), staff_no: "1".into() }),
        group_id: "group-1".into(),
        session_id: None,
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
async fn dm_web_send_parks_identity_and_marks_the_projection() {
    let db = sqlite_db().await;
    seed_caller_enabled_bearer_bot(db.as_ref(), "bot-driver").await;
    let (flow, handoff, managed) = dm_flow(db.clone()).await;

    let outcome = flow.handle_web_send(web_send(Some(ingress()))).await.expect("caller DM admission");

    // Queued admission: the marker only exists in the persisted projection;
    // the seam caller sees the ordinary queued outcome.
    assert!(outcome.queue_admission.is_some(), "outcome: {:?}", outcome.status);

    // The persisted row carries the anti-downgrade marker.
    let rows = managed.snapshot(None).await.expect("snapshot");
    let row = rows.iter().find(|r| r.semantic_projection_json.get("caller_required").is_some())
        .expect("a marker row exists")
        .clone();
    assert_eq!(row.state.status, Status::Queued);
    assert!(row.semantic_projection_json.get("caller_required") == Some(&json!(true)));

    // The identity is parked for this scope and claimable exactly once.
    let scope = HandoffScope {
        env: resolve_env_str(),
        session_id: row.session_id.clone(),
        group_id: Some(row.group_id.clone()),
        source_message_id: row.source_message_id.clone(),
        target_bot_id: row.target_bot_id.clone(),
    };
    let parked = handoff
        .claim(&scope, HandoffClaim::new(row.delivery_id.clone(), row.state.state_version, None, 1, now_clock() + 1_000))
        .await
        .expect("parked identity claimable");
    assert_eq!(parked.expires_at_ms(), parked.authenticated_at_ms() + 300_000);
    assert_eq!(
        handoff.claim(&scope, HandoffClaim::new(row.delivery_id.clone(), 99, None, 1, now_clock() + 2_000)).await.unwrap_err(),
        bcs_service_api::application::caller_execution::HandoffError::AlreadyClaimed
    );
}

#[tokio::test]
async fn dm_web_send_without_ingress_refuses_owner_downgrade() {
    let db = sqlite_db().await;
    seed_caller_enabled_bearer_bot(db.as_ref(), "bot-driver").await;
    let (flow, _handoff, managed) = dm_flow(db.clone()).await;

    assert!(flow.handle_web_send(web_send(None)).await.is_err());
    assert!(managed.snapshot(None).await.expect("snapshot").is_empty());
}

#[tokio::test]
async fn caller_dm_to_a_not_enabled_target_runs_ordinary_untouched() {
    let db = sqlite_db().await;
    // bot-driver exists in the registry fixture but carries NO caller config.
    // Route semantics (spec §5.2): ingress attaches whenever the chain can
    // produce it; a not-enabled target keeps the ordinary admission — no
    // marker, no parked identity.
    let (flow, handoff, managed) = dm_flow(db.clone()).await;
    let outcome = flow
        .handle_web_send(web_send(Some(ingress())))
        .await
        .expect("ordinary admission for a not-enabled target");
    assert!(outcome.queue_admission.is_some(), "ordinary queue admission");
    let rows = managed.snapshot(None).await.expect("snapshot");
    assert!(rows.iter().all(|r| r.semantic_projection_json.get("caller_required").is_none()));
    let scope = HandoffScope {
        env: resolve_env_str(),
        session_id: rows[0].session_id.clone(),
        group_id: Some(rows[0].group_id.clone()),
        source_message_id: rows[0].source_message_id.clone(),
        target_bot_id: rows[0].target_bot_id.clone(),
    };
    assert_eq!(
        handoff
            .claim(&scope, HandoffClaim::new(rows[0].delivery_id.clone(), 1, None, 1, now_clock() + 1_000))
            .await
            .unwrap_err(),
        bcs_service_api::application::caller_execution::HandoffError::NotReserved
    );
}
#[path = "support/caller_relay_guard_case.rs"]
mod caller_relay_guard_case;

#[tokio::test]
async fn ingress_must_match_authenticated_principal_sender_and_environment() {
    for case in ["actor", "staff", "sender", "principal_kind", "environment"] {
        let db = sqlite_db().await;
        seed_caller_enabled_bearer_bot(db.as_ref(), "bot-driver").await;
        let (flow, _, managed) = dm_flow(db).await;
        let mut cmd = web_send(Some(ingress()));
        match case {
            "actor" => cmd.caller = CallerContext::Human(CallerPrincipal {
                actor_id: "human_other".into(), staff_no: "1".into(),
            }),
            "staff" => cmd.caller = CallerContext::Human(CallerPrincipal {
                actor_id: "human_1".into(), staff_no: "other".into(),
            }),
            "sender" => cmd.from_actor_id = "human_other".into(),
            "principal_kind" => cmd.caller = CallerContext::Public,
            "environment" => {
                let now = now_clock();
                cmd.caller_ingress = Some(AuthenticatedHumanCallerIngress::new(
                    HumanActor { actor_id: "human_1".into(), staff_no: "1".into() },
                    HumanCallerAuthSource::HttpAuth, now, now + 300_000, "other-environment",
                ).unwrap());
            }
            _ => unreachable!(),
        }
        let error = flow.handle_web_send(cmd).await.expect_err(case);
        assert!(error.to_string().contains("caller identity mismatch"), "{case}: {error}");
        assert!(managed.snapshot(None).await.unwrap().is_empty(), "{case}");
    }
}

#[tokio::test]
async fn expired_ingress_is_rejected_even_for_an_owner_target() {
    let db = sqlite_db().await;
    let (flow, _, managed) = dm_flow(db).await;
    let expired = AuthenticatedHumanCallerIngress::new(
        HumanActor { actor_id: "human_1".into(), staff_no: "1".into() },
        HumanCallerAuthSource::HttpAuth, 1, 2, resolve_env_str(),
    ).unwrap();
    let error = flow.handle_web_send(web_send(Some(expired))).await.unwrap_err();
    assert!(error.to_string().contains("caller identity expired"));
    assert!(managed.snapshot(None).await.unwrap().is_empty());
}

#[tokio::test]
async fn caller_target_without_managed_admission_never_uses_legacy_delivery() {
    let db = sqlite_db().await;
    seed_caller_enabled_bearer_bot(db.as_ref(), "bot-driver").await;
    let (mut flow, _, managed) = dm_flow(db).await;
    flow.managed_deliveries = None;
    assert!(flow.handle_web_send(web_send(Some(ingress()))).await.is_err());
    assert!(managed.snapshot(None).await.unwrap().is_empty());
}

#[tokio::test]
async fn command_clone_strips_ingress_but_explicit_move_preserves_admission() {
    let db = sqlite_db().await;
    seed_caller_enabled_bearer_bot(db.as_ref(), "bot-driver").await;
    let (flow, _, managed) = dm_flow(db).await;
    let original = web_send(Some(ingress()));
    let replay = original.clone();
    assert!(replay.caller_ingress.is_none());
    assert!(original.caller_ingress.is_some());
    assert!(flow.handle_web_send(replay).await.is_err());
    assert!(managed.snapshot(None).await.unwrap().is_empty());
    assert!(flow.handle_web_send(original).await.unwrap().queue_admission.is_some());
}

#[tokio::test]
async fn owner_target_without_ingress_keeps_ordinary_admission() {
    let (flow, _, managed) = dm_flow(sqlite_db().await).await;
    assert!(flow.handle_web_send(web_send(None)).await.unwrap().queue_admission.is_some());
    assert!(managed.snapshot(None).await.unwrap().iter()
        .all(|row| row.semantic_projection_json.get("caller_required").is_none()));
}

#[tokio::test]
async fn group_chat_moves_ingress_to_web_send_while_clone_drops_it() {
    let db = sqlite_db().await;
    seed_caller_enabled_bearer_bot(db.as_ref(), "bot-driver").await;
    let (flow, _, managed) = dm_flow(db).await;
    let cmd = bcs_service_api::GroupChatCommand {
        caller: web_send(None).caller, caller_ingress: Some(ingress()),
        group_id: "group-1".into(), session_id: Some("group-1:caller-dm".into()),
        requested_sender_id: None, message: "hello".into(), provider_bypass_headers: vec![],
    };
    let replay = cmd.clone();
    assert!(replay.caller_ingress.is_none());
    assert!(cmd.caller_ingress.is_some());
    assert!(flow.handle_group_chat(replay).await.is_err());
    assert!(managed.snapshot(None).await.unwrap().is_empty());
    assert!(flow.handle_group_chat(cmd).await.unwrap().queue_admission.is_some());
}

#[tokio::test]
async fn unreadable_caller_configuration_must_not_enable_owner_fallback() {
    let db = sqlite_db().await;
    seed_caller_enabled_bearer_bot(db.as_ref(), "bot-driver").await;
    let (flow, _, managed) = dm_flow(db.clone()).await;
    db.execute(DbStatement::new("DROP TABLE bcs_bots")).await.unwrap();
    assert!(flow.handle_web_send(web_send(Some(ingress()))).await.is_err());
    assert!(managed.snapshot(None).await.unwrap().is_empty());
}

#[tokio::test]
async fn owned_bot_submission_cannot_bypass_a_caller_target() {
    let db = sqlite_db().await;
    seed_caller_enabled_bearer_bot(db.as_ref(), "bot-driver").await;
    let (flow, _, managed) = dm_flow(db).await;
    let mut group = flow.group.get("group-1").await.unwrap();
    group.group_kind = bcs_service_api::GroupKind::Normal;
    flow.group.upsert(group).await.unwrap();
    let mut command = web_send(None);
    command.from_actor_id = "bot-driver".into();
    let error = flow.handle_web_send(command).await.expect_err("owned Bot speech cannot bypass Caller");
    assert!(error.to_string().contains("caller target requires human speech"));
    assert!(managed.snapshot(None).await.unwrap().is_empty());
}
