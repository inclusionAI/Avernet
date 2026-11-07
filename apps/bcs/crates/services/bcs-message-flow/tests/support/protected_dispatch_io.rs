use async_trait::async_trait;
use bcs_domain::message_delivery::PersistedMessageDelivery;
use bcs_domain::{BotDeliveryTarget, RedactedToken};
use bcs_service_api::application::caller_identity::{CallerExecutionToken, CallerProofFormat};
use bcs_service_api::port::{CallerContainerDescriptor, CallerContainerGatewayPort, CallerDispatchAck,
    CallerExecutionSendPort, EnsureCallerContainer};
use bcs_caller_identity_api::CallerIdentityPlugin;
use bcs_service_api::{BotAbortDeliveryCommand, BotAbortDeliveryResult, BotDeliveryCommand,
    BotDeliveryKind, BotDeliveryPort, BotDeliveryResult, ServiceResult};
use serde_json::json;

use super::now_ms;

/// Recording transport: the protected path must never hand the ordinary
/// transport a caller row.
#[derive(Default)]
pub(super) struct RecordingIo {
    pub(super) sent: tokio::sync::Mutex<Vec<String>>,
    pub(super) prepared: std::sync::atomic::AtomicUsize,
    /// When set, `resolve_caller_dispatch` refuses — the fail-closed
    /// target-resolution guard (review P1-5) without a Provider container.
    pub(super) refuse_caller_target: bool,
    pub(super) hang_caller_target: bool,
}

#[async_trait]
impl BotDeliveryPort for RecordingIo {
    async fn is_available(&self, _target: &BotDeliveryTarget) -> bool {
        true
    }
    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        self.sent
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
        Ok(BotAbortDeliveryResult {
            target_bot_id: cmd.target_bot_id().to_string(),
            aborted_run_ids: Vec::new(),
        })
    }
}

#[async_trait]
impl bcs_service_api::ManagedDeliveryPreparationService for RecordingIo {
    async fn is_available(&self, _bot_id: &str) -> bool {
        true
    }
    /// Real dispatch context (review P1-5): production resolves the row's
    /// Provider scope from the registry; the recording harness pins the
    /// same shape so the runtime exercises the real resolution branch.
    async fn resolve_caller_dispatch(
        &self,
        row: &PersistedMessageDelivery,
    ) -> Option<bcs_service_api::CallerDispatchContext> {
        if self.hang_caller_target {
            std::future::pending::<()>().await;
        }
        if self.refuse_caller_target {
            return None;
        }
        Some(bcs_service_api::CallerDispatchContext {
            target: BotDeliveryTarget::HttpProvider {
                bot_id: row.target_bot_id.clone(),
                provider_id: "provider-runtime".into(),
                provider_bot_ref: format!("ref-{}", row.target_bot_id),
                webhook_url: "http://127.0.0.1:1/webhook".into(),
                bcs_to_provider_token: RedactedToken::new("provider-test"),
                protocol_version: "2.0".into(),
            },
            service_credential: RedactedToken::new("provider-test"),
            provider_id: "provider-runtime".into(),
            provider_bot_ref: format!("ref-{}", row.target_bot_id),
        })
    }
    async fn prepare(
        &self,
        row: &PersistedMessageDelivery,
    ) -> ServiceResult<bcs_service_api::PreparedManagedDelivery> {
        self.prepared
            .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        let frame = bcs_protocol::BcsFrame::Request(bcs_protocol::RequestFrame::new(
            "frame", "chat.send", None,
        ));
        Ok(bcs_service_api::PreparedManagedDelivery {
            command: BotDeliveryCommand {
                target: BotDeliveryTarget::WebSocket { bot_id: row.target_bot_id.clone() },
                run_id: row.run_id.clone().unwrap(),
                frame,
                delivery_kind: BotDeliveryKind::Send,
                provider_transport: Default::default(),
                provider_bypass_headers: Vec::new(),
            },
            transport_context_json: json!({"provider_route_headers": []}),
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
    async fn before_send(
        &self,
        _started: &PersistedMessageDelivery,
        _cmd: &BotDeliveryCommand,
    ) -> ServiceResult<()> {
        Ok(())
    }
}

/// Gateway stub: accepts and returns a container with a far deadline.
#[derive(Default)]
pub(super) struct StubGateway { pub(super) targets: std::sync::Mutex<Vec<String>> }

#[async_trait]
impl CallerContainerGatewayPort for StubGateway {
    async fn ensure_container(
        &self,
        _target: &BotDeliveryTarget,
        _credential: &RedactedToken,
        request: EnsureCallerContainer,
    ) -> Result<CallerContainerDescriptor, bcs_service_api::port::CallerContainerError> {
        self.targets.lock().unwrap().push(request.target_bot_id.clone());
        Ok(CallerContainerDescriptor {
            sandbox_container_id: None,
            target_bot_id: request.target_bot_id,
            caller_subject_ref: request.caller_subject_ref,
            ready: true,
            isolation: bcs_service_api::port::CallerContainerIsolation::ContainerBound,
            binding_ref: None,
            runtime_generation: None,
            expires_at_ms: now_ms() + 600_000,
            observe_target: None,
        })
    }
}

#[derive(Debug, PartialEq)]
pub(super) enum Source {
    Human,
    Parent { run: String, attempt: u32, token: String },
}
#[derive(Default)]
pub(super) struct StubIdentity { pub(super) issued: std::sync::Mutex<Vec<Source>> }

#[async_trait]
impl CallerIdentityPlugin for StubIdentity {
    async fn establish_subject(
        &self,
        input: bcs_caller_identity_api::EstablishCallerSubject,
    ) -> Result<bcs_caller_identity_api::EstablishedCallerSubject, bcs_caller_identity_api::CallerIdentityError> {
        // Subject establishment is owned by the selected identity plugin.
        // The runtime stub derives an advisory subject lifetime from the
        // issuance clock the way the real JWT plugin does.
        let deadline = input.now_ms + 300_000;
        let subject = bcs_service_api::application::caller_identity::CallerSubjectRef::new(
            bcs_service_api::application::caller_identity::CallerSubjectKind::Human,
            bcs_service_api::application::caller_identity::CallerSubjectRefType::BcsEphemeral,
            input.ingress.actor().actor_id.clone(),
            deadline,
        )
        .map_err(|_| bcs_caller_identity_api::CallerIdentityError::InvalidRequest)?;
        let proof = CallerExecutionToken::new(CallerProofFormat::Jwt, "subject-proof".to_string(), deadline)
            .map_err(|_| bcs_caller_identity_api::CallerIdentityError::InvalidRequest)?;
        Ok(bcs_caller_identity_api::EstablishedCallerSubject {
            subject_ref: subject,
            subject_proof: proof,
            expires_at_ms: deadline,
        })
    }
    async fn issue_execution(
        &self,
        input: bcs_caller_identity_api::IssueCallerExecution<'_>,
    ) -> Result<CallerExecutionToken, bcs_caller_identity_api::CallerIdentityError> {
        use bcs_caller_identity_api::IssueCallerExecutionSource;
        let source = match input.source {
            IssueCallerExecutionSource::HumanOriginHandle => Source::Human,
            IssueCallerExecutionSource::ParentExecutionTokenRef { token, parent_run_id, parent_attempt } =>
                Source::Parent { run:parent_run_id.into(), attempt:parent_attempt, token:token.expose_token().into() },
            _ => panic!("new dispatches must use the exact parent token reference"),
        };
        self.issued.lock().unwrap().push(source);
        CallerExecutionToken::new(
            CallerProofFormat::Jwt,
            format!("<test-proof-{}>", input.run.run_id),
            input.not_after_ms,
        )
        .map_err(|_| bcs_caller_identity_api::CallerIdentityError::InvalidRequest)
    }
    async fn verify_execution(
        &self,
        _input: bcs_caller_identity_api::VerifyCallerExecution,
    ) -> Result<bcs_caller_identity_api::VerifiedCallerExecution, bcs_caller_identity_api::CallerIdentityError> {
        Err(bcs_caller_identity_api::CallerIdentityError::TokenInvalid)
    }
}

/// Recording protected sender: enumerates the ack under test.
#[derive(Default)]
pub(super) struct StubSender {
    pub(super) commands: std::sync::Mutex<Vec<bcs_service_api::port::SendCallerExecutionCommand>>,
}

#[async_trait]
impl CallerExecutionSendPort for StubSender {
    async fn send_caller_execution(
        &self,
        _target: &BotDeliveryTarget,
        _credential: &RedactedToken,
        command: bcs_service_api::port::SendCallerExecutionCommand,
    ) -> Result<CallerDispatchAck, bcs_service_api::port::CallerSendError> {
        assert_eq!(_target.bot_id(), command.association.target_bot_id);
        let ack = CallerDispatchAck::Accepted { control_revision:command.control_revision, request_digest:command.request_digest.clone() };
        self.commands.lock().unwrap().push(command);
        Ok(ack)
    }
}
