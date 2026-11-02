//! BCS outbound delivery using standard A2A Tasks and trusted local correlation.
use std::{collections::HashMap, sync::{Arc, Mutex, RwLock, Weak}, time::{Duration, SystemTime, UNIX_EPOCH}};
use async_trait::async_trait;
use a2a::{StreamResponse, TaskState};
use futures::{stream::BoxStream, StreamExt};
use bcs_protocol::{BcsFrame, MessageContent};
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::{BotAbortDeliveryCommand, BotAbortDeliveryResult, BotDeliveryCommand, BotDeliveryPort,
    BotDeliveryResult, BotDeliveryTarget, BotEventCommand, BotRunContextPort, ChatEventState,
    ProviderCredentialRepoPort, ProviderEventIngestCommand, ProviderEventIngestService, ProviderEventSource,
    ProviderRunTransport, ServiceError, ServiceResult};
use bcs_service_api::port::repo::a2a_run::{A2aContextScope, A2aRunRecord, A2aRunRepoPort, A2aRunState};
use bcs_service_api::types::a2a_bot::A2aBinding;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use crate::{client::bind_rest_client, binding_client::BindingClient, grpc_mesh::GrpcMeshConfig, projection::{Output, Projection}};

#[path = "delivery_driver.rs"]
mod driver;
#[path = "delivery_interaction.rs"]
mod interaction;
#[path = "delivery_error.rs"]
mod delivery_error;
pub use interaction::InputRuntime;

pub const DEDUP_EXTENSION: &str = "https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/message-dedup/v1.md";

#[derive(Clone)]
struct Runtime { ingest: Weak<dyn ProviderEventIngestService>, contexts: Arc<dyn BotRunContextPort> }

#[derive(Clone)]
struct Active {
    target:BotDeliveryTarget,
    stop:tokio::sync::watch::Sender<bool>,
    resolutions:tokio::sync::mpsc::Sender<interaction::Resolution>,
}

struct Inner {
    guard: OutboundUrlGuard,
    runs: Arc<dyn A2aRunRepoPort>,
    credentials: Arc<dyn ProviderCredentialRepoPort>,
    runtime: RwLock<Option<Runtime>>,
    active: Mutex<HashMap<String, Active>>,
    input: RwLock<Option<InputRuntime>>,
    slots: Arc<tokio::sync::Semaphore>,
    grpc: Option<GrpcMeshConfig>,
}

#[derive(Clone)]
pub struct A2aHttpTransport { inner: Arc<Inner> }

struct Lease { inner: Arc<Inner>, run_id: String, _slot: tokio::sync::OwnedSemaphorePermit }
impl Drop for Lease {
    fn drop(&mut self) {
        if let Ok(mut active) = self.inner.active.lock() { active.remove(&self.run_id); }
    }
}

fn invalid(message: &str) -> ServiceError {
    ServiceError::InvalidOperation { message: format!("A2A: {message}"), request_id: None }
}
fn rejected_input(bot_id:&str,message:&str)->BotDeliveryResult {
    BotDeliveryResult {target_bot_id:bot_id.into(),delivered:false,error:Some(invalid(message))}
}
fn now_ms() -> u64 { SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as u64 }

impl A2aHttpTransport {
    pub fn new(guard: OutboundUrlGuard, runs: Arc<dyn A2aRunRepoPort>, credentials: Arc<dyn ProviderCredentialRepoPort>) -> Self {
        Self { inner: Arc::new(Inner { guard, runs, credentials, runtime: RwLock::new(None),
            active: Mutex::new(HashMap::new()), input:RwLock::new(None), slots: Arc::new(tokio::sync::Semaphore::new(256)), grpc:None }) }
    }

    pub fn with_grpc_mesh(mut self, config: GrpcMeshConfig) -> ServiceResult<Self> {
        config.validate().map_err(|_| invalid("invalid gRPC mesh configuration"))?;
        Arc::get_mut(&mut self.inner).ok_or_else(||invalid("configure gRPC before sharing transport"))?.grpc=Some(config);
        Ok(self)
    }

    pub fn set_ingest(&self, ingest: Arc<dyn ProviderEventIngestService>, contexts: Arc<dyn BotRunContextPort>) -> ServiceResult<()> {
        *self.inner.runtime.write().map_err(|_|invalid("runtime lock failed"))? = Some(Runtime { ingest: Arc::downgrade(&ingest), contexts });
        Ok(())
    }

    fn runtime(&self) -> ServiceResult<Runtime> {
        self.inner.runtime.read().map_err(|_|invalid("runtime lock failed"))?.clone()
            .ok_or_else(||invalid("event ingestion not configured"))
    }

    fn route_fingerprint(&self, interface:&bcs_service_api::types::a2a_bot::A2aInterface)->ServiceResult<Option<String>> {
        if interface.binding!=A2aBinding::Grpc {return Ok(None);}
        let mesh=self.inner.grpc.as_ref().ok_or_else(||invalid("gRPC mesh is not configured"))?;
        Ok(Some(mesh.fingerprint(&interface.url)?))
    }

    async fn client(&self, record: &A2aRunRecord) -> ServiceResult<BindingClient> {
        let credential = self.inner.credentials.get_credential_by_kind(&record.scope.provider_id, &record.credential_kind).await?
            .ok_or_else(||invalid("credential unavailable"))?;
        if credential.disabled || credential.secret_value.trim().is_empty() { return Err(invalid("credential unavailable")); }
        let negotiated = record.extensions.iter().filter(|uri|matches!(uri.as_str(),crate::tools::EXTENSION | DEDUP_EXTENSION | crate::structured_exec::EXTENSION))
            .map(String::as_str).collect::<Vec<_>>().join(",");
        if record.interface.binding == A2aBinding::Grpc {
            if record.scope.route_fingerprint!=self.route_fingerprint(&record.interface)? {
                return Err(invalid("frozen gRPC route changed; remote Task must not be retargeted"));
            }
            let mesh=self.inner.grpc.as_ref().ok_or_else(||invalid("gRPC mesh is not configured"))?;
            return Ok(BindingClient::Grpc(mesh.client(&record.interface,&credential.secret_value,&negotiated)?
                .with_timeout(Duration::from_millis(record.deadline_ms.saturating_sub(now_ms()).max(5_000)))));
        }
        let mut headers = reqwest::header::HeaderMap::new();
        let mut value = reqwest::header::HeaderValue::from_str(&format!("Bearer {}", credential.secret_value))
            .map_err(|_|invalid("credential header invalid"))?;
        value.set_sensitive(true); headers.insert(reqwest::header::AUTHORIZATION, value);
        if !negotiated.is_empty() {
            headers.insert("a2a-extensions", reqwest::header::HeaderValue::from_str(&negotiated)
                .map_err(|_|invalid("extension header invalid"))?);
        }
        let http = crate::card_fetch::guarded_client_with_headers(&self.inner.guard, &record.interface.url, headers).await?;
        Ok(BindingClient::Rest(bind_rest_client(http, record.interface.url.clone(), record.interface.tenant.clone())))
    }

    async fn save(&self, record: &mut A2aRunRecord, state: A2aRunState) -> ServiceResult<()> {
        let expected = record.version;
        let mut updated = record.clone(); updated.state = state;
        updated.version = expected.checked_add(1).ok_or_else(||invalid("correlation version exhausted"))?;
        updated.updated_at_ms = now_ms();
        let committed=self.inner.runs.compare_and_set(&updated, expected).await?
            .ok_or_else(||ServiceError::Conflict("A2A run/Task correlation changed or already terminal".into()))?;
        *record = committed;
        Ok(())
    }

    async fn emit(&self, record: &A2aRunRecord, state: ChatEventState, delta: Option<String>, error: Option<&str>) -> ServiceResult<()> {
        let mut payload = json!({"state":state});
        if let Some(delta) = delta {
            payload["message"] = json!({"role":"assistant", "content":[{"type":"text","text":delta}],"timestamp":now_ms()});
            payload["delta_text"] = json!(delta);
        }
        if let Some(error) = error {
            payload["errorKind"] = json!("a2a_adapter"); payload["errorCode"] = json!(error);
            payload["errorMessage"] = json!(format!("A2A delivery failed: {error}"));
        }
        self.emit_payload(record, state, payload).await
    }

    async fn emit_payload(&self, record: &A2aRunRecord, state: ChatEventState, mut payload: Value) -> ServiceResult<()> {
        let terminal=matches!(state,ChatEventState::Final | ChatEventState::Error | ChatEventState::Aborted);
        if terminal && !record.state.is_terminal() {return Err(invalid("terminal correlation must be committed before ingestion"));}
        let runtime = self.runtime()?;
        let ingest = runtime.ingest.upgrade().ok_or_else(||invalid("event ingestion stopped"))?;
        payload["run_id"] = json!(record.run_id);
        payload["bcs_group_id"] = json!(record.group_id);
        payload["bcs_session_id"] = json!(record.scope.session_id);
        let event_type = if matches!(state, ChatEventState::ToolCallStart | ChatEventState::ToolCallEnd) {
            payload["ts"] = json!(now_ms()); "agent"
        } else { "chat.event" };
        let command=ProviderEventIngestCommand {
            source: ProviderEventSource::A2a { binding: record.interface.binding, update_mode: record.interface.update_mode },
            event: BotEventCommand { bot_id: record.scope.bot_id.clone(), run_id: record.run_id.clone(),
                group_id: record.group_id.clone(), bcs_session_id: Some(record.scope.session_id.clone()),
                event_type: event_type.into(), event_payload: payload, state },
        };
        if !terminal {ingest.ingest_provider_event(command).await?;return Ok(());}
        // Managed ingestion fences duplicate terminals by Run. Retry this same
        // terminal only: never replay its text, rewrite its confirmed state or
        // resubmit/cancel remote work after a local publication failure.
        for attempt in 0..3 {
            match ingest.ingest_provider_event(command.clone()).await {
                Ok(_) => return Ok(()),
                Err(error) if attempt==2 => {
                    tracing::error!(run_id=%record.run_id,"A2A terminal notification exhausted ingestion retries; local completion unconfirmed");
                    if self.tracking_lost(record).await.is_err() {
                        tracing::error!(run_id=%record.run_id,"A2A terminal ingestion failure could not preserve local tracking state");
                    }
                    return Err(error);
                }
                Err(_) => tokio::time::sleep(Duration::from_millis(250)).await,
            }
        }
        unreachable!("terminal ingestion loop returns on its final attempt")
    }
}

#[async_trait]
impl BotDeliveryPort for A2aHttpTransport {
    async fn is_available(&self, target: &BotDeliveryTarget) -> bool {
        let binding_ready=match target {
            BotDeliveryTarget::A2a {interface,..} if interface.binding==A2aBinding::Grpc =>
                self.inner.grpc.as_ref().is_some_and(|mesh|mesh.supports(&interface.url)),
            BotDeliveryTarget::A2a {..}=>true,
            _=>false,
        };
        binding_ready && self.runtime().is_ok_and(|runtime| runtime.ingest.upgrade().is_some())
    }

    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        let BotDeliveryTarget::A2a { bot_id, provider_id, provider_bot_ref, interface, credential_kind, extensions, .. } = &cmd.target else {
            return Err(invalid("incorrect delivery target"));
        };
        let BcsFrame::Request(frame) = &cmd.frame else { return Err(invalid("request frame required")); };
        if frame.method != "chat.send" {
            return Ok(BotDeliveryResult { target_bot_id: bot_id.clone(), delivered: false,
                error: Some(ServiceError::BotMethodUnsupported { bot_id: bot_id.clone(), method: frame.method.clone() }) });
        }
        let params = frame.params.as_ref().ok_or_else(||invalid("missing request parameters"))?;
        if params.get("attachments").and_then(Value::as_array).is_some_and(|a|!a.is_empty()) { return Ok(rejected_input(bot_id,"nontext attachments unsupported")); }
        let message: MessageContent = serde_json::from_value(params.get("message").cloned().ok_or_else(||invalid("message required"))?)?;
        let mut parts = Vec::new();
        for block in message.content {
            if block.block_type != "text" || block.url.is_some() { return Ok(rejected_input(bot_id,"nontext input unsupported")); }
            let Some(text)=block.text else {return Ok(rejected_input(bot_id,"text block missing text"));};
            parts.push(json!({"text":text}));
        }
        let session = params.get("bcs_session_id").or_else(||params.get("session_key")).and_then(Value::as_str)
            .filter(|v|!v.is_empty()).ok_or_else(||invalid("trusted session required"))?;
        let runtime = self.runtime()?;
        let context = runtime.contexts.get_context(&cmd.run_id).await.ok_or_else(||invalid("trusted local Run not registered"))?;
        if context.bot_id != *bot_id || context.bcs_session_id.as_deref() != Some(session) || context.terminal {
            return Err(invalid("local Run scope mismatch"));
        }
        let scope = A2aContextScope { bot_id: bot_id.clone(), session_id: session.into(), provider_id: provider_id.clone(),
            provider_bot_ref: provider_bot_ref.clone(), interface_url: interface.url.clone(), tenant: interface.tenant.clone(),
            route_fingerprint:self.route_fingerprint(interface)? };
        let digest = hex::encode(Sha256::digest(serde_json::to_vec(&parts)?));
        if let Some(existing) = self.inner.runs.get(&cmd.run_id).await? {
            if existing.scope != scope || existing.request_digest != digest { return Err(invalid("Run identity conflict")); }
            if self.inner.active.lock().map_err(|_|invalid("active map lock failed"))?.contains_key(&cmd.run_id)
                && existing.remote_task_id.is_some() {
                return Ok(BotDeliveryResult { target_bot_id: bot_id.clone(), delivered: true, error: None });
            }
            return Err(invalid("submission already exists; query outcome before retry"));
        }
        if !self.inner.runs.active(&scope).await?.is_empty() { return Err(invalid("context already has active or unknown work")); }
        let slot = self.inner.slots.clone().try_acquire_owned().map_err(|_|ServiceError::DeliveryNotSent { code: "a2a_capacity", retryable: true })?;
        let mut record = A2aRunRecord { run_id: cmd.run_id.clone(), scope, group_id: context.group_id,
            interface: interface.clone(), credential_kind: credential_kind.clone(), message_id: cmd.run_id.clone(), request_digest: digest,
            extensions: extensions.iter().filter(|declaration|matches!(declaration.uri.as_str(),crate::tools::EXTENSION | DEDUP_EXTENSION)
                || (declaration.uri==crate::structured_exec::EXTENSION && crate::structured_exec::compatible_params(&declaration.params)
                    && self.input_runtime().is_ok()))
                .map(|declaration|declaration.uri.clone()).collect(),
            remote_task_id: None, context_id: None, task_version:None, state: A2aRunState::Submitting, deadline_ms: context.deadline_ms,
            budget:None,pending_input:None,updated_at_ms: now_ms(), version: 1 };
        let mut initial_budget=None;
        if record.extensions.iter().any(|uri|uri==crate::structured_exec::EXTENSION) {
            let input=self.input_runtime()?;
            let started=input.budgets.initialize(now_ms(),record.deadline_ms,input.limits)?;
            // An ordinary request's existing execution deadline remains the
            // local authority; total caps may only shorten it before submission.
            record.budget=Some(started.state.clone());initial_budget=Some(started);
        }
        record.context_id = self.inner.runs.context(&record.scope).await?;
        let client = self.client(&record).await?;
        if !self.inner.runs.create(&record).await? { return Err(invalid("submission already reserved")); }
        if !runtime.contexts.begin_provider_transport(&record.run_id, record.deadline_ms).await
            || !runtime.contexts.bind_provider_transport(&record.run_id, ProviderRunTransport::A2a { binding: record.interface.binding, update_mode: record.interface.update_mode }).await {
            self.save(&mut record, A2aRunState::Failed).await?;
            return Err(invalid("Run event source conflict"));
        }
        if let Some(change)=initial_budget {
            if change.deadline_ms!=record.deadline_ms {
                self.change_budget(&mut record,bcs_service_api::types::run_budget::RunBudgetUpdate::InitialLimit(change),A2aRunState::Submitting).await?;
            }
        }
        let (stop, receiver) = tokio::sync::watch::channel(false);
        let (resolutions,resolution_receiver)=tokio::sync::mpsc::channel(64);
        self.inner.active.lock().map_err(|_|invalid("active map lock failed"))?.insert(record.run_id.clone(), Active {target:cmd.target.clone(),stop,resolutions});
        let lease = Lease { inner: self.inner.clone(), run_id: record.run_id.clone(), _slot: slot };
        let mut message = json!({"messageId":record.message_id,"role":"ROLE_USER","parts":parts});
        if let Some(context) = &record.context_id { message["contextId"] = json!(context); }
        let request = serde_json::from_value(json!({"message":message,"configuration":{"acceptedOutputModes":["text/plain"]}}))?;
        let attempts = if record.extensions.iter().any(|uri|uri == DEDUP_EXTENSION) { 2 } else { 1 };
        let mut received = None;
        for _ in 0..attempts {
            let budget = record.deadline_ms.saturating_sub(now_ms()).min(15_000);
            if budget == 0 { break; }
            let first = async {
                let mut stream = client.send_streaming_message(&request).await?;
                let event = stream.next().await.ok_or_else(||a2a::A2AError::internal("submission ended before first Task"))??;
                Ok::<_, a2a::A2AError>((stream, event))
            };
            match tokio::time::timeout(Duration::from_millis(budget), first).await {
                Ok(Ok(value)) => { received = Some(value); break; }
                Ok(Err(error)) => {
                    if let Some(payload)=delivery_error::initial_rejection(&error) {
                        return self.rejected(&mut record,payload).await;
                    }
                }
                Err(_) => {}
            }
        }
        let (stream, event) = match received {
            Some(value) => value,
            None => { self.save(&mut record, A2aRunState::Unknown).await?; return Err(invalid("submission outcome unknown; retry budget exhausted or dedup not negotiated")); }
        };
        let mut projection = Projection::default().with_tools(record.extensions.iter().any(|uri|uri == crate::tools::EXTENSION));
        let finished = match self.apply(&mut record, &mut projection, event).await {
            Ok(value) => value,
            Err(error) => { self.tracking_failed(&mut record).await; return Err(error); }
        };
        if !finished {
            let transport = self.clone();
            tokio::spawn(async move {
                let _lease = lease;
                if transport.drive(&mut record, &mut projection, stream, receiver,resolution_receiver).await.is_err() {
                    transport.tracking_failed(&mut record).await;
                }
            });
        }
        Ok(BotDeliveryResult { target_bot_id: bot_id.clone(), delivered: true, error: None })
    }

    async fn abort(&self, cmd: BotAbortDeliveryCommand) -> ServiceResult<BotAbortDeliveryResult> {
        self.cancel(cmd).await
    }
}
