//! Anti-downgrade enforcement conformance (spec §7.5.1): a delivery whose
//! semantic projection carries the `caller_required` marker — regardless of
//! the value, including anomalous `false`/`null` — must never enter the
//! ordinary preparation/transport path of this runtime. The schedulable row
//! converges to the ordinary `Failed` terminal state via the existing
//! `PreparationFailed` CAS before any context read, StartSend or transport
//! call. Ordinary rows (field absent) are untouched.

use async_trait::async_trait;
use bcs_domain::message_delivery::MessageDeliveryStatus as Status;
use bcs_domain::message_delivery::{DeliveryFlowKind, PersistedMessageDelivery};
use bcs_domain::{BotDeliveryTarget, DeliveryType, NewMessage, SenderType};
use bcs_message_flow::delivery_runtime::{
    DeliveryRuntime, DeliveryRuntimeConfig, DeliveryRuntimePolicy,
};
use bcs_message_flow::managed_delivery::ManagedMessageDelivery;
use bcs_message_store::MemoryMessageRepo;
use bcs_protocol::{BcsFrame, RequestFrame};
use bcs_service_api::port::repo::message_delivery::*;
use bcs_service_api::{
    BotAbortDeliveryCommand, BotAbortDeliveryResult, BotDeliveryCommand, BotDeliveryKind,
    BotDeliveryPort, BotDeliveryResult, ServiceResult,
};
use bcs_service_api::{ManagedDeliveryPreparationService, ManagedMessageDeliveryService, PreparedManagedDelivery};
use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::Duration;

struct RecordingIo {
    sent: tokio::sync::Mutex<Vec<String>>,
    prepared: std::sync::atomic::AtomicUsize,
}

#[async_trait]
impl BotDeliveryPort for RecordingIo {
    async fn is_available(&self, _target: &BotDeliveryTarget) -> bool {
        true
    }
    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        self.sent.lock().await.push(cmd.target_bot_id().to_string());
        Ok(BotDeliveryResult { target_bot_id: cmd.target_bot_id().to_string(), delivered: true, error: None })
    }
    async fn abort(&self, cmd: BotAbortDeliveryCommand) -> ServiceResult<BotAbortDeliveryResult> {
        Ok(BotAbortDeliveryResult { target_bot_id: cmd.target_bot_id().to_string(), aborted_run_ids: Vec::new() })
    }
}

#[async_trait]
impl ManagedDeliveryPreparationService for RecordingIo {
    async fn is_available(&self, _bot_id: &str) -> bool {
        true
    }
    async fn before_send(
        &self,
        started: &PersistedMessageDelivery,
        cmd: &BotDeliveryCommand,
    ) -> ServiceResult<()> {
        let _ = (started, cmd);
        Ok(())
    }
    async fn prepare(
        &self,
        row: &PersistedMessageDelivery,
    ) -> ServiceResult<PreparedManagedDelivery> {
        self.prepared
            .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        let frame = BcsFrame::Request(RequestFrame::new("frame", "chat.send", None));
        Ok(PreparedManagedDelivery {
            command: BotDeliveryCommand {
                target: BotDeliveryTarget::WebSocket { bot_id: row.target_bot_id.clone() },
                run_id: row.run_id.clone().unwrap_or_default(),
                frame,
                delivery_kind: BotDeliveryKind::Send,
                provider_transport: Default::default(),
                provider_bypass_headers: Vec::new(),
            },
            transport_context_json: serde_json::json!({"provider_route_headers": []}),
        })
    }
    async fn prepare_abort(
        &self,
        row: &PersistedMessageDelivery,
    ) -> ServiceResult<BotAbortDeliveryCommand> {
        Ok(BotAbortDeliveryCommand {
            target: BotDeliveryTarget::WebSocket { bot_id: row.target_bot_id.clone() },
            command_id: format!("abort-{}", row.delivery_id),
            group_id: row.group_id.clone(),
            session_id: row.session_id.clone(),
            run_id: row.run_id.clone(),
            provider_bypass_headers: Vec::new(),
            timeout_ms: 1000,
        })
    }
}

fn admission(id: &str, projection: serde_json::Value) -> AdmitMessageDeliveries {
    AdmitMessageDeliveries {
        display_message: None,
        message_id: id.into(),
        flow_kind: DeliveryFlowKind::Group,
        now_ms: 1,
        expire_at_ms: None,
        event: None,
        message: NewMessage {
            visibility_domain: bcs_domain::MessageVisibilityDomain::Chat,
            audience: None,
            group_id: "group".into(),
            session_id: format!("session-{id}"),
            sender_id: "human".into(),
            sender_type: SenderType::Human,
            message_type: "chat".into(),
            content: serde_json::json!({"text": id}),
            client_msg_id: Some(id.into()),
            owner_bot_id: None,
            created_at: 1,
            run_id: String::new(),
        },
        targets: vec![DeliveryAdmissionTarget {
                run_id: None,
            rejection: None,
            target_bot_id: "bot".into(),
            kind: DeliveryType::Send,
            max_queued: 10,
            semantic_projection_json: projection,
        }],
    }
}

fn runtime(service: Arc<ManagedMessageDelivery>, io: Arc<RecordingIo>) -> DeliveryRuntime {
    DeliveryRuntime {
        policy: None,
        service,
        preparation: io.clone(),
        transport: io,
        config: DeliveryRuntimeConfig {
            max_safe_retries: 0,
            pause_dispatch: false,
            bots: BTreeMap::from([(
                "bot".into(),
                DeliveryRuntimePolicy { max_running: 2, min_send_interval_ms: 0 },
            )]),
            tick: Duration::from_millis(1),
            expiry_tick: Duration::from_millis(50),
            io_timeout: Duration::from_secs(1),
            run_timeout: Duration::from_secs(30),
            cancel_timeout: Duration::from_secs(1),
            startup_recovery_grace: Duration::from_secs(2),
            max_tasks: 2,
            max_abort_tasks: 1,
        },
        caller_dispatch: None,
    }
}

async fn run_until(
    service: &ManagedMessageDelivery,
    source: &str,
    predicate: impl Fn(&PersistedMessageDelivery) -> bool,
) -> PersistedMessageDelivery {
    for _ in 0..2000 {
        for row in service.snapshot(None).await.unwrap() {
            if row.source_message_id == source && predicate(&row) {
                return row;
            }
        }
        tokio::time::sleep(Duration::from_millis(2)).await;
    }
    panic!("row for {source} never reached the expected state");
}

#[tokio::test]
async fn caller_required_marker_converges_to_failed_before_any_prepare() {
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let io = Arc::new(RecordingIo { sent: Default::default(), prepared: Default::default() });
    service
        .admit(admission(
            "caller-row",
            serde_json::json!({"version": 1, "caller_required": true}),
        ))
        .await
        .expect("admit caller-required row");

    let worker = runtime(service.clone(), io.clone());
    let (stop, shutdown) = tokio::sync::watch::channel(false);
    let task = tokio::spawn(worker.run(shutdown));

    let row = run_until(&service, "caller-row", |row| row.state.status == Status::Failed).await;
    assert_eq!(row.state.status, Status::Failed, "marker must converge to the ordinary Failed terminal");
    // No ordinary path work happened: no prepare, no transport submit.
    assert_eq!(io.prepared.load(std::sync::atomic::Ordering::SeqCst), 0);
    assert!(io.sent.lock().await.is_empty());

    stop.send(true).unwrap();
    let _ = tokio::time::timeout(Duration::from_secs(2), task).await;
}

#[tokio::test]
async fn anomalous_marker_values_are_refused_not_interpreted() {
    // A falsy or null marker is data corruption until proven otherwise: the
    // presence of the key decides, never the value. None of the three rows
    // below may ever reach a transport.
    for (label, marker) in [
        ("false-marker", serde_json::json!(false)),
        ("null-marker", serde_json::Value::Null),
        ("string-marker", serde_json::json!("oops")),
    ] {
        let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
        let io = Arc::new(RecordingIo { sent: Default::default(), prepared: Default::default() });
        service
            .admit(admission(
                label,
                serde_json::json!({"version": 1, "caller_required": marker}),
            ))
            .await
            .expect("admit anomalous caller row");

        let worker = runtime(service.clone(), io.clone());
        let (stop, shutdown) = tokio::sync::watch::channel(false);
        let task = tokio::spawn(worker.run(shutdown));
        let row = run_until(&service, label, |row| row.state.status == Status::Failed).await;
        assert_eq!(row.state.status, Status::Failed, "{label} must converge Failed");
        assert_eq!(io.prepared.load(std::sync::atomic::Ordering::SeqCst), 0, "{label} must not prepare");
        assert!(io.sent.lock().await.is_empty(), "{label} must not transport");
        stop.send(true).unwrap();
        let _ = tokio::time::timeout(Duration::from_secs(2), task).await;
    }
}

#[tokio::test]
async fn ordinary_rows_keep_their_schedule_untouched() {
    // Field absent → the guard is invisible: the ordinary preparation and
    // transport path still runs (this harness's recording transport).
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let io = Arc::new(RecordingIo { sent: Default::default(), prepared: Default::default() });
    service
        .admit(admission("plain-row", serde_json::json!({"version": 1})))
        .await
        .expect("admit ordinary row");

    let worker = runtime(service.clone(), io.clone());
    let (stop, shutdown) = tokio::sync::watch::channel(false);
    let task = tokio::spawn(worker.run(shutdown));
    let row = run_until(&service, "plain-row", |row| row.state.status == Status::Dispatching).await;
    assert_eq!(row.state.status, Status::Dispatching);
    assert!(io.prepared.load(std::sync::atomic::Ordering::SeqCst) >= 1);

    stop.send(true).unwrap();
    let _ = tokio::time::timeout(Duration::from_secs(2), task).await;
}