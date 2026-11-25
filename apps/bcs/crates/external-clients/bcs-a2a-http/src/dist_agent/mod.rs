//! The distributed Connector's agent client: `DistAgentClientPort` over the
//! existing A2A bindings (REST, JSON-RPC, gRPC and custom bindings).
//!
//! It adds no retry. An interface resolved with the `Poll` update mode (the
//! agent declares no streaming) is sent to with the unary operation and
//! `returnImmediately`; every other interface with the streaming operation.
//! A send has exactly one of these outcomes:
//!
//! | What happened | Outcome |
//! | --- | --- |
//! | target not resolvable, address refused by policy, client not buildable | `NotSent` |
//! | no connection was established (DNS, refused, connect timeout) | `NotSent` |
//! | HTTP 400/401/403/404/405/406/415/422, gRPC 3/5/7/12/16, or a JSON-RPC error on the initial response that says the request itself was refused (parse error, invalid request, method not found, invalid params, unsupported operation, content type / extension / version not supported) | `Rejected` |
//! | first stream event (or the unary answer) carries a task | `Accepted` |
//! | first stream event (or the unary answer) is the agent's direct message | `Completed` |
//! | anything else once the request was handed to the transport | `OutcomeUnknown` |

mod events;

use crate::binding_client::BindingClient;
use crate::client::bind_rest_client;
use crate::grpc_mesh::GrpcMeshConfig;
use crate::transport_error::NOT_CONNECTED;
use a2a::A2AError;
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::port::dist_agent::*;
use bcs_service_api::types::a2a_bot::{A2aBinding, A2aInterface, A2aUpdateMode};
use events::Mapper;
use futures::StreamExt;
use futures::stream::BoxStream;
use serde_json::{Value, json};
use std::sync::Arc;
use std::time::Duration;

/// Where an agent is and how to authenticate to it.
#[derive(Clone)]
pub struct ResolvedAgent {
    pub interface: A2aInterface,
    pub bearer: Option<String>,
    /// Negotiated extension URIs, sent as `a2a-extensions`.
    pub extensions: Vec<String>,
}

/// The credential never appears in debug output.
impl std::fmt::Debug for ResolvedAgent {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ResolvedAgent")
            .field("interface", &self.interface)
            .field("bearer", &self.bearer.as_ref().map(|_| "<redacted>"))
            .field("extensions", &self.extensions)
            .finish()
    }
}

/// Turns the frozen `AgentBinding` of a dispatch into a callable agent.
///
/// Delayed (walkthrough §17): what resolves `CredentialRef` and what
/// `BindingVersion` means are not settled, so no implementation ships here;
/// the composition injects one.
#[async_trait]
pub trait DistAgentResolver: Send + Sync {
    /// The error is a machine code, never a credential or a URL.
    async fn resolve(&self, target: &AgentBinding) -> Result<ResolvedAgent, String>;
}

/// Longest wait for the first event of a send, and of a custom binding call.
const FIRST_EVENT_TIMEOUT: Duration = Duration::from_secs(120);

pub struct A2aDistAgentClient {
    resolver: Arc<dyn DistAgentResolver>,
    guard: OutboundUrlGuard,
    grpc: Option<GrpcMeshConfig>,
}

type Responses = BoxStream<'static, Result<a2a::StreamResponse, A2AError>>;

/// Why a send did not produce a task.
enum SendFailure {
    NotSent(String),
    Rejected(String),
    Unknown(String),
}

/// A machine code for an error; never the peer's text.
fn code_of(error: &A2AError) -> String {
    if error.message == NOT_CONNECTED {
        return "not_connected".to_owned();
    }
    if let Some(status) = error.message.strip_prefix("A2A HTTP ") {
        return format!("http_{status}");
    }
    if let Some(code) = error.message.strip_prefix("A2A gRPC ") {
        return format!("grpc_{code}");
    }
    if error.code != a2a::error_code::INTERNAL_ERROR {
        return format!("a2a_{}", a2a::error_reason(error.code).to_ascii_lowercase());
    }
    "transport".to_owned()
}

/// Sorts a send error: provably not sent, a complete rejection, or unknown.
fn classify(error: &A2AError) -> SendFailure {
    if error.message == NOT_CONNECTED {
        return SendFailure::NotSent(code_of(error));
    }
    let http = error.message.strip_prefix("A2A HTTP ").and_then(|v| v.parse::<u16>().ok());
    let grpc = error.message.strip_prefix("A2A gRPC ").and_then(|v| v.parse::<i32>().ok());
    // The same table as the existing delivery's initial rejection: 408, 409,
    // 429 and every 5xx stay uncertain.
    let rejected = http.is_some_and(|status| matches!(status, 400 | 401 | 403 | 404 | 405 | 406 | 415 | 422))
        || grpc.is_some_and(|code| matches!(code, 3 | 5 | 7 | 12 | 16))
        // A JSON-RPC error that says the request itself was refused. Any
        // other code (internal error, a server-defined "overloaded" or
        // "timeout") says nothing about whether the agent started work.
        || (error.message == "A2A JSON-RPC failure" && request_refused(error.code));
    if rejected {
        SendFailure::Rejected(code_of(error))
    } else {
        SendFailure::Unknown(code_of(error))
    }
}

/// JSON-RPC and A2A codes that mean the request was not taken at all.
fn request_refused(code: i32) -> bool {
    use a2a::error_code::*;
    matches!(
        code,
        PARSE_ERROR
            | INVALID_REQUEST
            | METHOD_NOT_FOUND
            | INVALID_PARAMS
            | UNSUPPORTED_OPERATION
            | CONTENT_TYPE_NOT_SUPPORTED
            | EXTENSION_SUPPORT_REQUIRED
            | VERSION_NOT_SUPPORTED
    )
}

impl A2aDistAgentClient {
    pub fn new(resolver: Arc<dyn DistAgentResolver>, guard: OutboundUrlGuard, grpc: Option<GrpcMeshConfig>) -> Self {
        Self {
            resolver,
            guard,
            grpc,
        }
    }

    /// Builds the binding client of a target. An error means nothing was sent.
    async fn client(&self, target: &AgentBinding) -> Result<(BindingClient, bool), String> {
        self.client_and_mode(target).await.map(|(client, tools, _)| (client, tools))
    }

    async fn client_and_mode(&self, target: &AgentBinding) -> Result<(BindingClient, bool, A2aUpdateMode), String> {
        let agent = self.resolver.resolve(target).await?;
        let tools = agent.extensions.iter().any(|uri| uri == crate::tools::EXTENSION);
        let extensions = agent.extensions.join(",");
        let interface = &agent.interface;
        let client = match interface.binding {
            A2aBinding::Custom => bcs_a2a_binding_api::registry()
                .client(
                    &interface.url,
                    interface.tenant.as_deref(),
                    agent.bearer.as_deref(),
                    &extensions,
                    FIRST_EVENT_TIMEOUT,
                )
                .map(BindingClient::Custom)
                .map_err(|_| "custom_client_unavailable".to_owned())?,
            A2aBinding::Grpc => {
                let mesh = self.grpc.as_ref().ok_or("grpc_not_configured")?;
                mesh.client_with_optional_bearer(interface, agent.bearer.as_deref(), &extensions)
                    .map(BindingClient::Grpc)
                    .map_err(|_| "grpc_route_unavailable".to_owned())?
            }
            A2aBinding::HttpJson | A2aBinding::JsonRpc => {
                let mut headers = reqwest::header::HeaderMap::new();
                if let Some(bearer) = &agent.bearer {
                    let mut value = reqwest::header::HeaderValue::from_str(&format!("Bearer {bearer}"))
                        .map_err(|_| "credential_invalid".to_owned())?;
                    value.set_sensitive(true);
                    headers.insert(reqwest::header::AUTHORIZATION, value);
                }
                if !extensions.is_empty() {
                    let value = reqwest::header::HeaderValue::from_str(&extensions)
                        .map_err(|_| "extensions_invalid".to_owned())?;
                    headers.insert("a2a-extensions", value);
                }
                let http = crate::card_fetch::guarded_client_with_headers(&self.guard, &interface.url, headers)
                    .await
                    .map_err(|_| "address_rejected".to_owned())?;
                if interface.binding == A2aBinding::JsonRpc {
                    BindingClient::JsonRpc(crate::jsonrpc::JsonRpcClient::new(
                        http,
                        interface.url.clone(),
                        interface.tenant.clone(),
                    ))
                } else {
                    BindingClient::Rest(bind_rest_client(http, interface.url.clone(), interface.tenant.clone()))
                }
            }
        };
        Ok((client, tools, interface.update_mode))
    }

    /// One send (start or continue-input); never repeated here.
    async fn send(
        &self,
        target: &AgentBinding,
        remote: Option<&RemoteTaskRef>,
        request: StableSendRequest,
    ) -> SendOutcome {
        let (client, tools, mode) = match self.client_and_mode(target).await {
            Ok(client) => client,
            Err(code) => return SendOutcome::NotSent { code },
        };
        let unary = mode == A2aUpdateMode::Poll;
        let message = match build_message(remote, &request, unary) {
            Ok(message) => message,
            // A request that cannot be expressed can never be sent.
            Err(code) => return SendOutcome::Rejected { code: code.to_owned() },
        };
        let failed = |error: &A2AError| match classify(error) {
            SendFailure::NotSent(code) => SendOutcome::NotSent { code },
            SendFailure::Rejected(code) => SendOutcome::Rejected { code },
            SendFailure::Unknown(code) => SendOutcome::OutcomeUnknown { code },
        };
        let mut responses: Responses = if unary {
            // The agent declares no streaming: one answer, then the task is
            // followed by the caller (query or subscribe, as declared).
            let answer = match tokio::time::timeout(FIRST_EVENT_TIMEOUT, client.send_message(&message)).await {
                Ok(Ok(a2a::SendMessageResponse::Task(task))) => a2a::StreamResponse::Task(task),
                Ok(Ok(a2a::SendMessageResponse::Message(answer))) => a2a::StreamResponse::Message(answer),
                Ok(Err(error)) => return failed(&error),
                Err(_) => return SendOutcome::OutcomeUnknown { code: "first_event_timeout".into() },
            };
            futures::stream::once(async move { Ok(answer) }).boxed()
        } else {
            match client.send_streaming_message(&message).await {
                Ok(responses) => responses,
                Err(error) => return failed(&error),
            }
        };
        // The request is with the transport: from here nothing is "not sent".
        let first = match tokio::time::timeout(FIRST_EVENT_TIMEOUT, responses.next()).await {
            Ok(Some(Ok(first))) => first,
            // An error as the first stream event: the agent answered 200 and
            // may already have created the task, so this is never a rejection.
            Ok(Some(Err(error))) => return SendOutcome::OutcomeUnknown { code: code_of(&error) },
            Ok(None) => return SendOutcome::OutcomeUnknown { code: "stream_ended_before_first_event".into() },
            Err(_) => return SendOutcome::OutcomeUnknown { code: "first_event_timeout".into() },
        };
        let remote = match &first {
            a2a::StreamResponse::Task(task) => events::remote_of(&task.id, &task.context_id),
            a2a::StreamResponse::StatusUpdate(update) => events::remote_of(&update.task_id, &update.context_id),
            a2a::StreamResponse::ArtifactUpdate(update) => events::remote_of(&update.task_id, &update.context_id),
            // A direct answer without a task: the call is complete.
            a2a::StreamResponse::Message(answer) => {
                let mut result = json!({ "state": "completed" });
                if let Ok(answer) = serde_json::to_value(answer) {
                    result["message"] = answer;
                }
                return SendOutcome::Completed {
                    result: Payload::Inline(result),
                };
            }
        };
        // A task that cannot be addressed later must not be recorded as accepted.
        if remote.task_id.is_empty() {
            return SendOutcome::OutcomeUnknown { code: "invalid_agent_response".into() };
        }
        let rest = futures::stream::once(async move { Ok(first) }).chain(responses).boxed();
        SendOutcome::Accepted {
            remote,
            events: Some(remote_events(rest, tools)),
        }
    }

    async fn task(&self, client: &BindingClient, task_id: &str) -> Result<a2a::Task, A2AError> {
        let request = serde_json::from_value(json!({ "id": task_id }))
            .map_err(|_| A2AError::invalid_params("task id"))?;
        client.get_task(&request).await
    }
}

/// The A2A message of a send: the request id is the message id, so a resend
/// is recognisable by an agent that de-duplicates.
fn build_message(
    remote: Option<&RemoteTaskRef>,
    request: &StableSendRequest,
    return_immediately: bool,
) -> Result<a2a::SendMessageRequest, &'static str> {
    let Payload::Inline(payload) = &request.payload else {
        return Err("request_by_reference");
    };
    let parts = match payload {
        Value::Object(object) => object.get("parts").filter(|parts| parts.is_array()).cloned(),
        Value::String(text) => Some(json!([{ "text": text }])),
        _ => None,
    }
    .ok_or("request_invalid")?;
    let mut message = json!({ "messageId": request.request_id.0, "role": "ROLE_USER", "parts": parts });
    let context = request
        .context_id
        .clone()
        .or_else(|| remote.and_then(|remote| remote.context_id.clone()));
    if let Some(context) = context {
        message["contextId"] = json!(context);
    }
    if let Some(remote) = remote {
        message["taskId"] = json!(remote.task_id);
    }
    let mut configuration = json!({ "acceptedOutputModes": ["text/plain"] });
    if return_immediately {
        configuration["returnImmediately"] = json!(true);
    }
    serde_json::from_value(json!({ "message": message, "configuration": configuration }))
    .map_err(|_| "request_invalid")
}

/// Maps a response stream into remote events; a read failure carries a code only.
fn remote_events(responses: Responses, tools: bool) -> RemoteEventStream {
    let mut mapper = Mapper::new(tools);
    responses
        .flat_map(move |response| {
            let events: Vec<Result<RemoteEvent, AgentReadError>> = match response {
                Ok(response) => mapper.map(response).into_iter().map(Ok).collect(),
                Err(error) => vec![Err(AgentReadError { code: code_of(&error) })],
            };
            futures::stream::iter(events)
        })
        .boxed()
}

fn finished(state: &a2a::TaskState) -> Option<FinalDispatchOutcome> {
    match state {
        a2a::TaskState::Completed => Some(FinalDispatchOutcome::Completed),
        a2a::TaskState::Failed | a2a::TaskState::Rejected => Some(FinalDispatchOutcome::Failed),
        a2a::TaskState::Canceled => Some(FinalDispatchOutcome::Canceled),
        _ => None,
    }
}

#[async_trait]
impl DistAgentClientPort for A2aDistAgentClient {
    async fn start(&self, target: &AgentBinding, request: StableSendRequest) -> SendOutcome {
        self.send(target, None, request).await
    }

    async fn send_input(
        &self,
        target: &AgentBinding,
        remote: &RemoteTaskRef,
        request: StableSendRequest,
    ) -> SendOutcome {
        self.send(target, Some(remote), request).await
    }

    async fn query(&self, target: &AgentBinding, query: RemoteQuery) -> QueryOutcome {
        let remote = match query {
            // A2A has no lookup by message id.
            RemoteQuery::ByRequest(_) => return QueryOutcome::Unsupported,
            RemoteQuery::ByTask(remote) => remote,
        };
        let client = match self.client(target).await {
            Ok((client, _)) => client,
            Err(code) => return QueryOutcome::Unavailable { code },
        };
        match self.task(&client, &remote.task_id).await {
            Ok(task) => {
                let (_, snapshot) = events::task_event(&task);
                QueryOutcome::Found {
                    remote: events::remote_of(&task.id, &task.context_id),
                    status: events::status_of(&task.status.state),
                    // The payload of the event this state stands for.
                    snapshot: Payload::Inline(snapshot),
                }
            }
            Err(error) if error.code == a2a::error_code::TASK_NOT_FOUND => QueryOutcome::NotFound,
            Err(error) => QueryOutcome::Unavailable { code: code_of(&error) },
        }
    }

    async fn subscribe(
        &self,
        target: &AgentBinding,
        remote: &RemoteTaskRef,
        _after: Option<ReplayToken>,
    ) -> Result<RemoteEventStream, AgentReadError> {
        // The bindings expose no replay position, so `after` cannot be used.
        let (client, tools) = self.client(target).await.map_err(|code| AgentReadError { code })?;
        let request = serde_json::from_value(json!({ "id": remote.task_id }))
            .map_err(|_| AgentReadError { code: "task_id_invalid".into() })?;
        let responses = client
            .subscribe_to_task(&request)
            .await
            .map_err(|error| AgentReadError { code: code_of(&error) })?;
        Ok(remote_events(responses, tools))
    }

    async fn cancel(&self, target: &AgentBinding, remote: &RemoteTaskRef, _request_id: RequestId) -> CancelOutcome {
        let unknown = |code: String| CancelOutcome::OutcomeUnknown { code };
        let client = match self.client(target).await {
            Ok((client, _)) => client,
            Err(code) => return unknown(code),
        };
        let Ok(request) = serde_json::from_value(json!({ "id": remote.task_id })) else {
            return CancelOutcome::Rejected { code: "task_id_invalid".into() };
        };
        match client.cancel_task(&request).await {
            Ok(task) => match finished(&task.status.state) {
                Some(FinalDispatchOutcome::Canceled) => CancelOutcome::ConfirmedCanceled,
                Some(outcome) => CancelOutcome::AlreadyFinished(outcome),
                // The agent took the request; the task is still running.
                None => CancelOutcome::RequestAccepted,
            },
            // "Not cancelable" is either a task that already finished or a
            // refusal: one read tells them apart.
            Err(error) if error.code == a2a::error_code::TASK_NOT_CANCELABLE => {
                match self.task(&client, &remote.task_id).await {
                    Ok(task) => match finished(&task.status.state) {
                        Some(FinalDispatchOutcome::Canceled) => CancelOutcome::ConfirmedCanceled,
                        Some(outcome) => CancelOutcome::AlreadyFinished(outcome),
                        None => CancelOutcome::Rejected { code: "task_not_cancelable".into() },
                    },
                    Err(error) => unknown(code_of(&error)),
                }
            }
            Err(error) => match classify(&error) {
                SendFailure::Rejected(code) => CancelOutcome::Rejected { code },
                SendFailure::NotSent(code) | SendFailure::Unknown(code) => unknown(code),
            },
        }
    }
}
