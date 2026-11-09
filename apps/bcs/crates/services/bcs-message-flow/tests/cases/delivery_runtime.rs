use async_trait::async_trait;
use bcs_domain::message_delivery::{
    DeliveryFlowKind, MessageDeliveryStatus as Status, PersistedMessageDelivery,
};
use bcs_domain::{BotDeliveryTarget, DeliveryType, NewMessage, SenderType};
use bcs_message_flow::delivery_runtime::{
    DeliveryRuntime, DeliveryRuntimeConfig, DeliveryRuntimePolicy,
};
use bcs_message_flow::managed_delivery::ManagedMessageDelivery;
use bcs_message_store::MemoryMessageRepo;
use bcs_protocol::{BcsFrame, RequestFrame};
use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent as Event;
use bcs_service_api::port::repo::message_delivery::*;
use bcs_service_api::{
    BotAbortDeliveryCommand, BotAbortDeliveryResult, BotDeliveryCommand, BotDeliveryKind,
    BotDeliveryPort, BotDeliveryResult, ServiceError, ServiceResult,
};
use bcs_service_api::{
    DeliveryTransitionCommand, ManagedDeliveryError, ManagedDeliveryPreparationService,
    ManagedMessageDeliveryService, PreparedManagedDelivery,
};
use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::Duration;

#[path = "../support/failing_delivery.rs"]
mod failing_delivery;




struct RecordingIo {
    service: Arc<ManagedMessageDelivery>,
    sent: tokio::sync::Mutex<Vec<String>>,
    aborts: tokio::sync::Mutex<Vec<String>>,
    fail_send: bool,
    fail_registration: bool,
}

struct AvailabilityPreparation {
    inner: Arc<RecordingIo>,
    available: Arc<std::sync::atomic::AtomicBool>,
}

#[async_trait]
impl ManagedDeliveryPreparationService for AvailabilityPreparation {
    async fn is_available(&self, _: &str) -> bool {
        self.available.load(std::sync::atomic::Ordering::SeqCst)
    }

    async fn prepare(
        &self,
        row: &PersistedMessageDelivery,
    ) -> ServiceResult<PreparedManagedDelivery> {
        self.inner.prepare(row).await
    }

    async fn before_send(
        &self,
        row: &PersistedMessageDelivery,
        command: &BotDeliveryCommand,
    ) -> ServiceResult<()> {
        self.inner.before_send(row, command).await
    }

    async fn prepare_abort(
        &self,
        row: &PersistedMessageDelivery,
    ) -> ServiceResult<BotAbortDeliveryCommand> {
        self.inner.prepare_abort(row).await
    }
}





#[async_trait]
impl ManagedDeliveryPreparationService for RecordingIo {
    async fn is_available(&self, _: &str) -> bool {
        true
    }
    async fn prepare(
        &self,
        row: &PersistedMessageDelivery,
    ) -> ServiceResult<PreparedManagedDelivery> {
        Ok(PreparedManagedDelivery {
            command: BotDeliveryCommand {
                target: BotDeliveryTarget::WebSocket {
                    bot_id: row.target_bot_id.clone(),
                },
                run_id: row
                    .run_id
                    .clone()
                    .ok_or_else(|| ServiceError::InternalError("missing run".into()))?,
                frame: BcsFrame::Request(RequestFrame::new("prepared", "chat.send", None)),
                delivery_kind: BotDeliveryKind::Send,
                provider_transport: Default::default(),
                provider_bypass_headers: Vec::new(),
            },
            transport_context_json: serde_json::json!({"version":1, "kind":"websocket", "policy_version":0}),
        })
    }
    async fn before_send(
        &self,
        row: &PersistedMessageDelivery,
        _: &BotDeliveryCommand,
    ) -> ServiceResult<()> {
        assert_eq!(row.state.status, Status::Dispatching);
        assert!(row.request_id.is_some());
        assert!(row.transport_context_json.is_some());
        if self.fail_registration {
            return Err(ServiceError::InternalError(
                "registration failed before I/O".into(),
            ));
        }
        Ok(())
    }
    async fn prepare_abort(
        &self,
        row: &PersistedMessageDelivery,
    ) -> ServiceResult<BotAbortDeliveryCommand> {
        Ok(BotAbortDeliveryCommand {
            target: BotDeliveryTarget::WebSocket {
                bot_id: row.target_bot_id.clone(),
            },
            command_id: "prepared".into(),
            group_id: row.group_id.clone(),
            session_id: row.session_id.clone(),
            run_id: row.run_id.clone(),
            provider_bypass_headers: Vec::new(),
            timeout_ms: 1000,
        })
    }
}

#[async_trait]
impl BotDeliveryPort for RecordingIo {
    async fn is_available(&self, _: &BotDeliveryTarget) -> bool {
        true
    }
    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        let rows = self
            .service
            .snapshot(None)
            .await
            .map_err(|e| ServiceError::InternalError(e.to_string()))?;
        let row = rows
            .iter()
            .find(|d| d.run_id.as_deref() == Some(cmd.run_id.as_str()))
            .ok_or_else(|| ServiceError::InternalError("I/O before persistence".into()))?;
        assert!(row.state.may_have_been_sent);
        if let BcsFrame::Request(frame) = &cmd.frame {
            assert_eq!(Some(&frame.id), row.request_id.as_ref());
            assert_ne!(&frame.id, &cmd.run_id);
        } else {
            panic!("send must use a request frame");
        }
        self.sent.lock().await.push(row.source_message_id.clone());
        if self.fail_send {
            return Err(ServiceError::InternalError(
                "ambiguous transport error".into(),
            ));
        }
        Ok(BotDeliveryResult {
            target_bot_id: cmd.target_bot_id().into(),
            delivered: true,
            error: None,
        })
    }
    async fn abort(&self, cmd: BotAbortDeliveryCommand) -> ServiceResult<BotAbortDeliveryResult> {
        let rows = self
            .service
            .snapshot(None)
            .await
            .map_err(|e| ServiceError::InternalError(e.to_string()))?;
        let row = rows
            .iter()
            .find(|d| d.run_id == cmd.run_id)
            .ok_or_else(|| ServiceError::InternalError("run missing".into()))?;
        assert_eq!(row.abort_request_id.as_ref(), Some(&cmd.command_id));
        assert!(row.abort_started_at_ms.is_some());
        self.aborts.lock().await.push(row.source_message_id.clone());
        Ok(BotAbortDeliveryResult {
            target_bot_id: cmd.target_bot_id().into(),
            aborted_run_ids: cmd.run_id.into_iter().collect(),
        })
    }
}

fn command(id: &str, session: &str) -> AdmitMessageDeliveries {
    AdmitMessageDeliveries {
        display_message: None,
        message_id: id.into(),
        flow_kind: DeliveryFlowKind::Group,
        now_ms: 1,
        expire_at_ms: None,
        event: None,
        message: NewMessage { visibility_domain: bcs_domain::MessageVisibilityDomain::Chat, audience: None,
            group_id: "group".into(),
            session_id: session.into(),
            sender_id: "human".into(),
            sender_type: SenderType::Human,
            message_type: "chat".into(),
            content: serde_json::json!({"text":id}),
            client_msg_id: Some(id.into()),
            owner_bot_id: None,
            created_at: 1,
            run_id: String::new(),
        },
        targets: vec![DeliveryAdmissionTarget { rejection: None,
            target_bot_id: "bot".into(),
            kind: DeliveryType::Send,
            max_queued: 10,
            semantic_projection_json: serde_json::json!({"version":1}), run_id: None,}],
    }
}

fn runtime(service: Arc<ManagedMessageDelivery>, io: Arc<RecordingIo>) -> DeliveryRuntime {
    DeliveryRuntime {
        caller_dispatch: None,
        policy: None,
        service,
        preparation: io.clone(),
        transport: io,
        config: DeliveryRuntimeConfig {
            max_safe_retries: 0,
            pause_dispatch: false,
            bots: BTreeMap::from([(
                "bot".into(),
                DeliveryRuntimePolicy {
                    max_running: 2,
                    min_send_interval_ms: 0,
                },
            )]),
            tick: Duration::from_millis(1),
            expiry_tick: Duration::from_millis(10),
            io_timeout: Duration::from_secs(1),
            run_timeout: Duration::from_secs(30),
            cancel_timeout: Duration::from_secs(1),
            startup_recovery_grace: Duration::ZERO,
            max_tasks: 2,
            max_abort_tasks: 1,
        },
    }
}





async fn wait_status(
    service: &ManagedMessageDelivery,
    source: &str,
    status: Status,
) -> Result<PersistedMessageDelivery, Box<dyn std::error::Error>> {
    Ok(tokio::time::timeout(Duration::from_secs(3), async {
        loop {
            if let Some(row) = service
                .snapshot(None)
                .await?
                .into_iter()
                .find(|d| d.source_message_id == source && d.state.status == status)
            {
                return Ok::<_, ManagedDeliveryError>(row);
            }
            tokio::time::sleep(Duration::from_millis(1)).await;
        }
    })
    .await??)
}



fn event(row: &PersistedMessageDelivery, event: Event) -> DeliveryTransitionCommand {
    DeliveryTransitionCommand {
        caller_dispatch_commit: None,
        caller_cancel: None,
        caller_terminal: None,
        delivery_id: row.delivery_id.clone(),
        expected_state_version: row.state.state_version,
        event,
        now_ms: chrono::Utc::now().timestamp_millis(),
        request_id: None,
        actor_id: Some("human".into()),
        reply: None,
        transport_context_json: None,
        deadline_at_ms: None,
    }
}

async fn wait_submitted(
    service: &ManagedMessageDelivery,
    source: &str,
) -> Result<PersistedMessageDelivery, Box<dyn std::error::Error>> {
    Ok(tokio::time::timeout(Duration::from_secs(3), async {
        loop {
            if let Some(row) = service
                .snapshot(None)
                .await?
                .into_iter()
                .find(|d| d.source_message_id == source && d.submitted_at_ms.is_some())
            {
                return Ok::<_, ManagedDeliveryError>(row);
            }
            tokio::time::sleep(Duration::from_millis(1)).await;
        }
    })
    .await??)
}





fn caller_command(id: &str, expire_at_ms: Option<i64>) -> AdmitMessageDeliveries {
    let mut admission = command(id, "session");
    admission.expire_at_ms = expire_at_ms;
    admission.targets[0].semantic_projection_json =
        serde_json::json!({"version": 1, "caller_required": true});
    admission
}

fn caller_transition(
    row: &PersistedMessageDelivery,
    event: Event,
) -> DeliveryTransitionCommand {
    DeliveryTransitionCommand {
        caller_dispatch_commit: None,
        delivery_id: row.delivery_id.clone(),
        expected_state_version: row.state.state_version,
        event,
        now_ms: chrono::Utc::now().timestamp_millis(),
        request_id: None,
        actor_id: None,
        reply: None,
        transport_context_json: Some(serde_json::json!({})),
        deadline_at_ms: Some(chrono::Utc::now().timestamp_millis() + 60_000),
        caller_cancel: None,
        caller_terminal: None,
    }
}



#[path = "delivery_runtime/queue.rs"]
mod queue;
#[path = "delivery_runtime/lifecycle.rs"]
mod lifecycle;
#[path = "delivery_runtime/isolation.rs"]
mod isolation;
