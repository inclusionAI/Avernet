//! Full-stack caller e2e over the real HTTP router (spec §10.1.1/§7.5.1):
//! authenticated Human cookie → strict extraction → GroupChatCommand
//! ingress → real BcsMessageFlow DM seam → marker + parked identity, plus
//! the no-window fallback that keeps the ordinary path untouched. The only
//! doubles are the group/registry/routing fakes shared with the
//! message-flow contract suite and the stub auth plugin providing the
//! session JWT window; every service under test is real.

use std::sync::Arc;

use axum::{
    Router,
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_auth_api::{AuthError, AuthPlugin, AuthPluginChain, AuthPrincipal};
use bcs_message_store::MemoryMessageRepo;
use bcs_db_api::{DbPlugin, DbStatement, DbValue as DbValue};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_http::{
    router::build_router,
    state::{ChainUserIdentityPort, HttpAppState, UserIdentityPort},
};
use bcs_services_container::Services;
use bcs_service_api::application::message_delivery::ManagedMessageDeliveryService;
use bcs_service_api::port::repo::BotControlPlaneRepoPort;
use bcs_service_api::port::repo::MessageRepoPort;
use bcs_service_api::{
    BotControlPlaneCoreService, BotRegistryCoreService, GroupCoreService, MessageFlowService,
    Participant, ParticipantRole, ProviderBotBindingRepoPort, ProviderRepoPort, RoutingCoreService,
};
use bcs_bot_store::PersistentBotRepo;
use bcs_bot_store::provider::MemoryProviderStore;
use bcs_bot::core::BotControlPlaneCore;
use bcs_message_flow::caller_admission::CallerAdmissionDeps;
use bcs_service_api::application::caller_execution::HumanCallerDeliveryHandoffService;
use bcs_service_api::application::caller_execution::HandoffError;
use bcs_message_flow::managed_delivery::ManagedMessageDelivery;
use bcs_message_flow::BcsMessageFlow;
use bcs_message_flow::caller_delivery_handoff::HumanCallerDeliveryHandoff;
use serde_json::{Value, json};
use tower::ServiceExt;

#[path = "../../../../services/bcs-message-flow/tests/support/session.rs"]
mod session_support;
#[path = "../../../../test-support/message_flow_contract_support.rs"]
mod support;
use session_support::{StaticSessionManagement, test_session};

/// Yields a Human principal with a verified session window (the strict
/// extractor's only acceptable shape).
struct WindowedAuthPlugin;

#[async_trait::async_trait]
impl AuthPlugin for WindowedAuthPlugin {
    fn name(&self) -> &'static str { "windowed" }
    fn can_authenticate(&self, _h: &axum::http::HeaderMap) -> bool { true }
    async fn authenticate(&self, _h: &axum::http::HeaderMap) -> Result<Option<AuthPrincipal>, AuthError> {
        let mut p = AuthPrincipal::default();
        p.user_id = Some("12345678".to_string());
        p.user_name = Some("Alice".to_string());
        p.human_expires_at_ms = Some(
            std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_millis() as u64 + 300_000,
        );
        Ok(Some(p))
    }
    fn priority(&self) -> u8 { 100 }
}

/// Yields a Human principal WITHOUT a session window: strict extraction
/// refuses; the route falls back to the ordinary path.
struct WindowlessAuthPlugin;

#[async_trait::async_trait]
impl AuthPlugin for WindowlessAuthPlugin {
    fn name(&self) -> &'static str { "windowless" }
    fn can_authenticate(&self, _h: &axum::http::HeaderMap) -> bool { true }
    async fn authenticate(&self, _h: &axum::http::HeaderMap) -> Result<Option<AuthPrincipal>, AuthError> {
        let mut p = AuthPrincipal::default();
        p.user_id = Some("12345678".to_string());
        p.user_name = Some("Alice".to_string());
        Ok(Some(p))
    }
    fn priority(&self) -> u8 { 100 }
}

async fn sqlite_db() -> Arc<dyn DbPlugin> {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT, gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, bot_uuid TEXT NOT NULL, name TEXT NOT NULL,
            bot_info TEXT, env TEXT, actor_kind TEXT NOT NULL DEFAULT 'bot',
            status TEXT NOT NULL DEFAULT 'online', is_deleted INTEGER NOT NULL DEFAULT 0, UNIQUE (bot_uuid, env)
        )",
    )).await.expect("create bots");
    db
}

struct CallerApp {
    app: Router,
    handoff: Arc<HumanCallerDeliveryHandoff>,
    managed: Arc<ManagedMessageDelivery>,
}


struct FakeGroupQuery {
    group: Arc<dyn GroupCoreService>,
}

#[async_trait::async_trait]
impl bcs_service_api::GroupQueryService for FakeGroupQuery {
    async fn list_groups(
        &self,
        _cmd: bcs_service_api::GroupListCommand,
    ) -> Result<bcs_service_api::GroupListResult, bcs_service_api::GroupUseCaseError> {
        Ok(bcs_service_api::GroupListResult { items: Vec::new(), total: 0, offset: 0, limit: 0 })
    }

    async fn get_group(
        &self,
        cmd: bcs_service_api::GroupDetailCommand,
    ) -> Result<bcs_service_api::GroupDetailResult, bcs_service_api::GroupUseCaseError> {
        use bcs_service_api::GroupUseCaseError;
        let group = self
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| GroupUseCaseError::Forbidden(cmd.group_id.clone()))?;
        Ok(bcs_service_api::GroupDetailResult {
            group_id: group.id.clone(),
            label: group.label.clone(),
            status: group.status.clone(),
            driver_bot_id: group.driver_bot.clone(),
            context: group.context.clone(),
            opening_message: None,
            participants: Vec::new(),
            message_count: 0,
            service_group_uuid: None,
            service_mode: None,
            group_kind: group.group_kind.clone(),
            dm_pair_key: group.dm_pair_key.clone(),
            group_strategy: group.group_strategy.clone(),
            created_at: 0,
            updated_at: 0,
            chat_url: None,
            context_injected: 0,
            service_spec: None,
            latest_running_session_id: None,
            initial_run: None,
            originator: group.originator.clone(),
            visibility: group.visibility.clone(),
            human_mention_notify_mode: Default::default(),
        })
    }

    async fn list_bot_groups(
        &self,
        _cmd: bcs_service_api::BotGroupListCommand,
    ) -> Result<bcs_service_api::GroupListResult, bcs_service_api::GroupUseCaseError> {
        Ok(bcs_service_api::GroupListResult { items: Vec::new(), total: 0, offset: 0, limit: 0 })
    }
}

async fn caller_app(
    plugin: Box<dyn AuthPlugin + Send>,
    caller_enabled: bool,
    dm: bool,
) -> CallerApp {
    let fixture = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    fixture.registry.insert_named_actor("bot-driver:12345678", "Owned Driver").await;
    {
        use bcs_service_api::{GroupKind};
        let mut group = fixture.group.get("group-1").await.unwrap();
        group.group_kind = if dm { GroupKind::Dm } else { GroupKind::Normal };
        group.driver_bot = "bot-driver".to_string();
        group.dm_pair_key = Some("bot-driver|human_alice".to_string());
        let mut participants = vec![
            Participant::human("human_12345678", ParticipantRole::Observer),
            Participant::bot("bot-driver", ParticipantRole::Driver),
        ];
        if !dm {
            participants.push(Participant::bot(
                "bot-driver:12345678",
                ParticipantRole::Observer,
            ));
        }
        group.participants = participants;
        fixture.group.upsert(group).await.unwrap();
    }
    let db = sqlite_db().await;
    let bot_info = if caller_enabled {
        json!({"summary":"s","caller":{"source_revision":1,"caller_identity":{"enabled":true}}}).to_string()
    } else {
        json!({"summary":"s"}).to_string()
    };
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots (bot_uuid, name, bot_info, env) VALUES (?, ?, ?, ?)",
        vec![
            DbValue::from("bot-driver".to_string()),
            DbValue::from("Driver".to_string()),
            DbValue::from(bot_info),
            DbValue::from(bcs_config::resolve_env_str()),
        ],
    )).await.expect("seed caller bot");

    let message_repo = Arc::new(MemoryMessageRepo::new());
    let managed = Arc::new(ManagedMessageDelivery::new(message_repo.clone()));
    let repo_port: Arc<dyn BotControlPlaneRepoPort> = Arc::new(PersistentBotRepo::new(db.clone()));
    let provider_store = Arc::new(MemoryProviderStore::default());
    let control_plane: Arc<dyn BotControlPlaneCoreService> = Arc::new(BotControlPlaneCore::new(
        repo_port,
        provider_store.clone() as Arc<dyn ProviderRepoPort>,
        provider_store as Arc<dyn ProviderBotBindingRepoPort>,
    ));
    let handoff = Arc::new(HumanCallerDeliveryHandoff::new(8));
    let deps = Arc::new(CallerAdmissionDeps {
        handoff: handoff.clone(),
        admission: managed.clone() as Arc<dyn ManagedMessageDeliveryService>,
        control_plane,
        reserve_ttl_ms: 30_000,
    });
    let participants = fixture.group.get("group-1").await.unwrap().participants.clone();
    let mut flow = BcsMessageFlow::new(
        fixture.group.clone() as Arc<dyn GroupCoreService>,
        fixture.routing.clone() as Arc<dyn RoutingCoreService>,
        fixture.registry.clone() as Arc<dyn BotRegistryCoreService>,
        fixture.bot_delivery.clone() as Arc<dyn bcs_service_api::BotDeliveryPort>,
        fixture.frontend_delivery.clone() as Arc<dyn bcs_service_api::FrontendDeliveryPort>,
    )
    .with_message_repo(message_repo as Arc<dyn MessageRepoPort>)
    .with_managed_deliveries(managed.clone() as Arc<dyn ManagedMessageDeliveryService>)
    .with_group_delivery_limits(std::collections::BTreeMap::from([("bot-driver".to_string(), 100)]))
    .with_session_management(Arc::new(StaticSessionManagement::new(test_session(
        "group-1:e2e", "group-1", participants,
    ))));
    flow.caller_admission = Some(deps);

    let chain = Arc::new(AuthPluginChain::new(vec![plugin]));
    let extraction_port = Arc::new(ChainUserIdentityPort::new(chain));
    let user_identity: Arc<dyn UserIdentityPort> = extraction_port.clone();

    let services = Services::builder()
        .group_query(Arc::new(FakeGroupQuery { group: fixture.group.clone() as Arc<dyn GroupCoreService> }))
        .group(fixture.group.clone() as Arc<dyn GroupCoreService>)
        .routing(fixture.routing.clone() as Arc<dyn RoutingCoreService>)
        .registry(fixture.registry.clone() as Arc<dyn BotRegistryCoreService>)
        .message_flow(Arc::new(flow) as Arc<dyn MessageFlowService>)
        .build_for_test();
    let state = HttpAppState::new(services)
        .with_user_identity(user_identity)
        .with_caller_ingress_source(extraction_port);
    CallerApp { app: build_router(state), handoff, managed }
}

async fn post_chat(app: &Router, message: &str) -> (StatusCode, Value) {
    post_chat_from(app, message, None).await
}

async fn post_chat_from(
    app: &Router,
    message: &str,
    from: Option<&str>,
) -> (StatusCode, Value) {

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-1/chat")
                .header("content-type", "application/json")
                .header("cookie", "bcs_session=e2e-cookie")
                .body(Body::from(
                json!({"message": message, "from": from}).to_string(),
            ))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = response.status();
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    (status, serde_json::from_slice(&body).unwrap_or(Value::Null))
}

fn now_ms() -> i64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_millis() as i64
}

#[tokio::test]
async fn human_dm_chat_parks_identity_and_marks_the_row_end_to_end() {
    let app = caller_app(Box::new(WindowedAuthPlugin), true, true).await;
    let (status, body) = post_chat(&app.app, "hello caller").await;
    assert_eq!(status, StatusCode::OK, "body: {body}");

    // One marker row for the driver bot.
    let rows = app.managed.snapshot(None).await.expect("snapshot");
    let row = rows
        .iter()
        .find(|r| r.semantic_projection_json.get("caller_required").is_some())
        .expect("caller_required row after e2e chat");
    assert!(row.semantic_projection_json.get("caller_required") == Some(&json!(true)));

    // The identity is parked exactly once for the canonical scope.
    let scope = bcs_service_api::application::caller_execution::HandoffScope {
        env: bcs_config::resolve_env_str(),
        session_id: row.session_id.clone(),
        group_id: Some(row.group_id.clone()),
        source_message_id: row.source_message_id.clone(),
        target_bot_id: row.target_bot_id.clone(),
    };
    let parked = app
        .handoff
        .claim(
            &scope,
            bcs_service_api::application::caller_execution::HandoffClaim::new(
                row.delivery_id.clone(),
                row.state.state_version,
                None,
                1,
                now_ms() + 1_000,
            ),
        )
        .await
        .expect("identity parked end-to-end");
    assert_eq!(parked.actor().staff_no, "12345678");
    assert_eq!(
        app.handoff
            .claim(&scope, bcs_service_api::application::caller_execution::HandoffClaim::new(
                "again", 1, None, 1, now_ms() + 1_000))
            .await
            .unwrap_err(),
        HandoffError::AlreadyClaimed
    );
}

#[tokio::test]
async fn no_window_refuses_downgrade_for_a_caller_enabled_target() {
    let app = caller_app(Box::new(WindowlessAuthPlugin), true, true).await;
    let (status, body) = post_chat(&app.app, "hello without a session window").await;
    assert_eq!(status, StatusCode::BAD_REQUEST, "body: {body}");
    assert_eq!(body["error"], "caller identity missing");
    let rows = app.managed.snapshot(None).await.expect("snapshot");
    assert!(rows.is_empty());
}
#[tokio::test]
async fn caller_disabled_bot_stays_completely_ordinary_with_strict_ingress() {
    let app = caller_app(Box::new(WindowedAuthPlugin), false, true).await;
    let (status, body) = post_chat(&app.app, "hello to disabled bot").await;
    assert_eq!(status, StatusCode::OK, "body: {body}");
    let rows = app.managed.snapshot(None).await.expect("snapshot");
    // The seam's not-enabled judgment runs at the flow level; the route-level
    // ingress never becomes a marker or parked identity.
    assert!(rows.iter().all(|r| r.semantic_projection_json.get("caller_required").is_none()));
}

#[tokio::test]
async fn human_speaking_for_an_owned_bot_does_not_carry_human_ingress() {
    let app = caller_app(Box::new(WindowedAuthPlugin), false, false).await;
    let (status, body) = post_chat_from(&app.app, "hello as owned bot", Some("bot-driver:12345678")).await;
    assert_eq!(status, StatusCode::OK, "body: {body}");
    let rows = app.managed.snapshot(None).await.expect("snapshot");
    assert!(rows.iter().all(|row| {
        row.semantic_projection_json.get("caller_required").is_none()
    }));
}
