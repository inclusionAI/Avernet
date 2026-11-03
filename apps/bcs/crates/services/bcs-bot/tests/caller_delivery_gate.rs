//! Caller configuration delivery gate conformance (P1.4 production
//! consumer). Every check runs against the real gate plus a real SQLite
//! PersistentBotRepo through the real BotControlPlaneCore; only the wrapped
//! transport is a recorder, because the transport is not under test.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_bot::application::CallerConfigurationDeliveryGate;
use bcs_bot::core::BotControlPlaneCore;
use bcs_bot_store::PersistentBotRepo;
use bcs_config::resolve_env_str;
use bcs_db_api::{DbPlugin, DbStatement, DbValue as Value};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::port::repo::CallerIdentitySnapshot;
use bcs_service_api::{
    BotCallerBlockedReason, BotCallerEffectiveStatus, BotDeliveryTarget, CallerIdentityConfig,
    CallerMinimumIsolation,
};
use bcs_protocol::{BcsFrame, RequestFrame};
use bcs_service_api::port::delivery::{
    BotAbortDeliveryCommand, BotAbortDeliveryResult, BotDeliveryCommand, BotDeliveryKind,
    BotDeliveryPort, BotDeliveryResult,
};
use bcs_service_api::port::repo::BotControlPlaneRepoPort;
use bcs_service_api::{
    ProviderBotBindingRepoPort, ProviderRepoPort, ServiceResult,
};
use bcs_service_api::BotControlPlaneCoreService;
use serde_json::json;
use tokio::sync::Mutex;

use bcs_bot_store::provider::MemoryProviderStore;

#[derive(Default)]
struct RecordingTransport {
    delivered: Mutex<Vec<String>>,
    aborted: Mutex<Vec<String>>,
    available: bool,
}

#[async_trait]
impl BotDeliveryPort for RecordingTransport {
    async fn is_available(&self, _target: &BotDeliveryTarget) -> bool {
        self.available
    }

    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        self.delivered
            .lock()
            .await
            .push(cmd.target_bot_id().to_string());
        Ok(BotDeliveryResult {
            target_bot_id: cmd.target_bot_id().to_string(),
            delivered: true,
            error: None,
        })
    }

    async fn abort(&self, cmd: BotAbortDeliveryCommand) -> ServiceResult<BotAbortDeliveryResult> {
        self.aborted
            .lock()
            .await
            .push(cmd.target_bot_id().to_string());
        Ok(BotAbortDeliveryResult {
            target_bot_id: cmd.target_bot_id().to_string(),
            aborted_run_ids: Vec::new(),
        })
    }

    async fn abort_on_connection(
        &self,
        cmd: BotAbortDeliveryCommand,
        _connection_id: &str,
    ) -> bcs_service_api::ServiceResult<BotAbortDeliveryResult> {
        self.aborted
            .lock()
            .await
            .push(cmd.target_bot_id().to_string());
        Ok(BotAbortDeliveryResult {
            target_bot_id: cmd.target_bot_id().to_string(),
            aborted_run_ids: Vec::new(),
        })
    }
}

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

async fn seed_bot(db: &dyn DbPlugin, bot_id: &str, bot_info: &str) {
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots (bot_uuid, name, bot_info, env) VALUES (?, ?, ?, ?)",
        vec![
            Value::from(bot_id.to_string()),
            Value::from(format!("Bot {bot_id}")),
            Value::from(bot_info.to_string()),
            Value::from(resolve_env_str()),
        ],
    ))
    .await
    .expect("seed bot");
}

fn snapshot(revision: u64, enabled: bool) -> CallerIdentitySnapshot {
    serde_json::from_value(json!({
        "source_revision": revision,
        "caller_identity": { "enabled": enabled }
    }))
    .expect("valid snapshot")
}

/// Any command against one target; the envelope contents are irrelevant to
/// the gate (no bypass by run kind or frame method).
fn nil_frame_params() -> Option<serde_json::Value> {
    None
}

fn command(bot_id: &str) -> BotDeliveryCommand {
    BotDeliveryCommand {
        target: BotDeliveryTarget::WebSocket { bot_id: bot_id.to_string() },
        run_id: "run-1".to_string(),
        frame: BcsFrame::Request(RequestFrame::new("frame-1", "chat.send", nil_frame_params())),
        delivery_kind: BotDeliveryKind::Send,
        provider_transport: Default::default(),
        provider_bypass_headers: Vec::new(),
    }
}

struct GateFixture {
    #[allow(dead_code)]
    repo: Arc<dyn BotControlPlaneRepoPort>,
    control_plane: Arc<dyn BotControlPlaneCoreService>,
    transport: Arc<RecordingTransport>,
}

async fn gate_with(db: Arc<dyn DbPlugin>) -> (CallerConfigurationDeliveryGate, Arc<RecordingTransport>) {
    let fixture = gate_fixture(db).await;
    let gate = CallerConfigurationDeliveryGate::new(fixture.transport.clone(), fixture.control_plane);
    (gate, fixture.transport)
}

async fn gate_fixture(db: Arc<dyn DbPlugin>) -> GateFixture {
    gate_with_repo(Arc::new(PersistentBotRepo::with_sql_flavor(
        db,
        bcs_db_api::DbSqlFlavor::Sqlite,
    )))
    .await
}

/// Same as the db fixture, but the caller shares the ONE repo instance with
/// the gate — the production topology, and required since the §15.96
/// read-through cache invalidates per store instance.
async fn gate_with_repo(repo: Arc<dyn BotControlPlaneRepoPort>) -> GateFixture {
    let provider_store = Arc::new(MemoryProviderStore::default());
    let control_plane: Arc<dyn BotControlPlaneCoreService> = Arc::new(BotControlPlaneCore::new(
        repo.clone(),
        provider_store.clone() as Arc<dyn ProviderRepoPort>,
        provider_store.clone() as Arc<dyn ProviderBotBindingRepoPort>,
    ));
    let transport = Arc::new(RecordingTransport::default());
    GateFixture { repo, control_plane, transport }
}

#[tokio::test]
async fn disabled_and_unconfigured_bots_dispatch_through() {
    let db = sqlite_db().await;
    seed_bot(db.as_ref(), "bot-off", r#"{"summary":"s","caller":{"source_revision":3,"caller_identity":{"enabled":false}}}"#).await;
    seed_bot(db.as_ref(), "bot-plain", r#"{"summary":"s"}"#).await;
    let (gate, transport) = gate_with(db).await;

    for bot in ["bot-off", "bot-plain"] {
        let result = gate.deliver(command(bot)).await.expect("pass-through");
        assert!(result.delivered, "{bot} must pass through");
    }
    assert_eq!(transport.delivered.lock().await.len(), 2);
}

#[tokio::test]
async fn enabled_bots_are_refused_without_touching_the_transport() {
    let db = sqlite_db().await;
    seed_bot(db.as_ref(), "bot-on", r#"{"summary":"s","caller":{"source_revision":1,"caller_identity":{"enabled":true}}}"#).await;
    seed_bot(db.as_ref(), "legacy", r#"{"summary":"s","caller":null}"#).await;
    let (gate, transport) = gate_with(db).await;

    let result = gate.deliver(command("bot-on")).await.expect("closed refusal");
    assert!(!result.delivered);
    let error = result.error.expect("explicit rejection carries an error");
    assert!(error.to_string().contains("CALLER_EXECUTION_UNAVAILABLE"));

    // Broken caller data (caller:null is a storage error, not "disabled") is
    // also an explicit refusal, never an owner dispatch.
    let result = gate.deliver(command("legacy")).await.expect("closed refusal");
    assert!(!result.delivered);

    assert!(transport.delivered.lock().await.is_empty());
}

#[tokio::test]
async fn both_delivery_paths_share_the_check_and_aborts_stay_free() {
    let db = sqlite_db().await;
    seed_bot(db.as_ref(), "bot-on", r#"{"summary":"s","caller":{"source_revision":1,"caller_identity":{"enabled":true}}}"#).await;
    let (gate, transport) = gate_with(db).await;

    let result = gate
        .deliver_on_connection(command("bot-on"), "conn-1")
        .await
        .expect("closed refusal");
    assert!(!result.delivered);
    assert!(transport.delivered.lock().await.is_empty());

    // Control-plane operations are never blocked by the gate: a disabled
    // configuration must not make cancels impossible.
    let abort = BotAbortDeliveryCommand {
        target: command("bot-on").target,
        command_id: "cmd-1".to_string(),
        group_id: "g".to_string(),
        session_id: "s".to_string(),
        run_id: Some("run-1".to_string()),
        provider_bypass_headers: Vec::new(),
        timeout_ms: 1000,
    };
    let result = gate.abort(abort).await.expect("abort forwarded");
    assert_eq!(result.target_bot_id, "bot-on");
    let result = gate
        .abort_on_connection(
            BotAbortDeliveryCommand {
                target: command("bot-on").target,
                command_id: "cmd-2".to_string(),
                group_id: "g".to_string(),
                session_id: "s".to_string(),
                run_id: None,
                provider_bypass_headers: Vec::new(),
                timeout_ms: 1000,
            },
            "conn-1",
        )
        .await;
    assert!(result.is_ok());
    assert_eq!(transport.aborted.lock().await.len(), 2);
}

/// A core whose caller-configuration read fails (all defaults refuse) makes
/// the gate fail closed instead of owner-dispatching.
struct FailingCore;

#[async_trait]
impl bcs_service_api::BotControlPlaneCoreService for FailingCore {
    async fn get_record(&self, _bot_id: &str, _env: &str) -> bcs_service_api::ServiceResult<Option<bcs_service_api::BotControlPlaneRecord>> {
        Err(bcs_service_api::ServiceError::InvalidOperation { message: "stub".into(), request_id: None })
    }
    async fn get(&self, _bot_id: &str, _env: &str) -> bcs_service_api::ServiceResult<Option<bcs_service_api::core::BotControlPlaneView>> {
        Err(bcs_service_api::ServiceError::InvalidOperation { message: "stub".into(), request_id: None })
    }
    async fn get_by_ids(&self, _bot_ids: &[String], _env: &str) -> bcs_service_api::ServiceResult<Vec<bcs_service_api::core::BotControlPlaneView>> {
        Err(bcs_service_api::ServiceError::InvalidOperation { message: "stub".into(), request_id: None })
    }
    async fn list_candidates(&self, _query: bcs_service_api::BotCandidateReadQuery) -> bcs_service_api::ServiceResult<(Vec<bcs_service_api::core::BotControlPlaneCandidate>, u64)> {
        Err(bcs_service_api::ServiceError::InvalidOperation { message: "stub".into(), request_id: None })
    }
    async fn list_by_creator(&self, _query: bcs_service_api::BotControlPlaneOwnedQuery) -> bcs_service_api::ServiceResult<Vec<bcs_service_api::core::BotControlPlaneView>> {
        Err(bcs_service_api::ServiceError::InvalidOperation { message: "stub".into(), request_id: None })
    }
    async fn patch(&self, _bot_id: &str, _env: &str, _patch: bcs_service_api::BotControlPlanePatch) -> bcs_service_api::ServiceResult<Option<bcs_service_api::core::BotControlPlaneView>> {
        Err(bcs_service_api::ServiceError::InvalidOperation { message: "stub".into(), request_id: None })
    }
    // get_caller_configuration inherits the failing default.
}

#[tokio::test]
async fn config_read_failure_fails_closed() {
    // Missing rows are the transport's own concern (pass-through target);
    // a failing configuration read is not: the gate refuses instead of
    // owner-dispatching a possibly-enabled Bot.
    let transport = Arc::new(RecordingTransport::default());
    let gate = CallerConfigurationDeliveryGate::new(transport.clone(), Arc::new(FailingCore));
    let result = gate.deliver(command("bot-any")).await.expect("closed refusal");
    assert!(!result.delivered);
    assert!(transport.delivered.lock().await.is_empty());

    let db = sqlite_db().await;
    let (gate, transport) = gate_with(db).await;
    let result = gate.deliver(command("ghost")).await.expect("pass-through");
    assert!(result.delivered, "unknown target is the transport's concern");
}

#[tokio::test]
async fn a_disabled_higher_revision_reopens_owner_dispatch() {
    let db = sqlite_db().await;
    seed_bot(db.as_ref(), "bot-toggle", r#"{"summary":"s"}"#).await;
    // ONE repo instance shared by the sync writer and the gate's reads —
    // production topology; the §15.96 cache invalidates per store
    // instance, so a second instance over the same DB would only see the
    // change after its TTL (exactly the documented cross-instance window).
    let repo: Arc<dyn BotControlPlaneRepoPort> = Arc::new(PersistentBotRepo::with_sql_flavor(
        db.clone(),
        bcs_db_api::DbSqlFlavor::Sqlite,
    ));
    repo.sync_caller_configuration("bot-toggle", &resolve_env_str(), snapshot(1, true))
        .await
        .expect("enable");
    let fixture = gate_with_repo(repo.clone()).await;
    let gate = CallerConfigurationDeliveryGate::new(fixture.transport.clone(), fixture.control_plane);
    let transport = fixture.transport;
    // Enabled: blocked.
    assert!(!gate.deliver(command("bot-toggle")).await.expect("refusal").delivered);
    // Explicit disable at a higher revision: owner dispatch reopens
    // immediately in the same process (write-through invalidation).
    repo.sync_caller_configuration("bot-toggle", &resolve_env_str(), snapshot(2, false))
        .await
        .expect("disable");
    assert!(gate.deliver(command("bot-toggle")).await.expect("pass-through").delivered);
    assert_eq!(transport.delivered.lock().await.len(), 1);
}

#[test]
fn gate_reuses_the_frozen_effective_status_view() {
    // The gate consumes the domain merge view (spec §10.7.3) rather than a
    // bespoke policy: with the P1 execution chain not ready, enabled Bots are
    // Blocked and disabled ones pass.
    let disabled = CallerIdentityConfig {
        enabled: false,
        minimum_isolation: CallerMinimumIsolation::CallerContainer,
    };
    assert_eq!(
        BotCallerEffectiveStatus::merge(&disabled, None, false),
        BotCallerEffectiveStatus::Disabled
    );
    let enabled = CallerIdentityConfig {
        enabled: true,
        minimum_isolation: CallerMinimumIsolation::CallerContainer,
    };
    assert_eq!(
        BotCallerEffectiveStatus::merge(&enabled, None, false),
        BotCallerEffectiveStatus::Blocked(BotCallerBlockedReason::ProviderCallerUnsupported)
    );
}