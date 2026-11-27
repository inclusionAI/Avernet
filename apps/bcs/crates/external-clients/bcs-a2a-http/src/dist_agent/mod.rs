//! The distributed Connector's agent client: the provider of
//! `DistAgentClientPort` (the port the Connector's call actor uses to talk to
//! a remote agent), built on the existing A2A bindings of this crate.
//!
//! Where it sits: a Worker writes a dispatch, a Connector claims it, and the
//! Connector's actor for that call uses this client to start the remote task,
//! send the human's replies to it, follow it (subscribe or query) and cancel
//! it. This module only speaks A2A; every decision (when to send, whether a
//! failed send may be repeated, what to report) is the Connector's.
//!
//! Bindings (the wire protocol an agent declares in its card), chosen by the
//! resolved interface:
//! - HTTP+JSON (`Rest`) and JSON-RPC: plain HTTP to the agent URL, through a
//!   client checked by `OutboundUrlGuard` (see SSRF below).
//! - gRPC: never dialed directly. Requests go to a local service-mesh sidecar
//!   (MOSN), a proxy process running next to this one that routes calls to
//!   other services by service name. `GrpcMeshConfig` gives the sidecar's
//!   literal loopback address and explicit routes from an agent interface URL
//!   to a mesh service; the agent URL itself is only a lookup key. No route,
//!   or no `GrpcMeshConfig` at all, means the agent cannot be called.
//! - Custom: a binding installed by a plugin in `bcs_a2a_binding_api`'s
//!   process-wide registry, which picks the binding from the URL.
//!
//! SSRF (server-side request forgery): agent URLs come from outside (agent
//! cards, registrations). Without a check, a URL could make this server call
//! its own internal network. `OutboundUrlGuard` resolves the host, refuses
//! addresses its policy forbids (loopback, and non-public ranges in strict
//! mode), and the checked addresses are pinned into the HTTP client so DNS
//! cannot change them between the check and the connection.
//!
//! Glossary:
//! - send: one message to the agent, either the first one (`start`, creates the
//!   remote task) or a human reply (`send_input`, continues the same remote
//!   task, B12). The request id is the A2A message id.
//! - first event: the first response the agent produces for a send. It decides
//!   whether the agent accepted the message (see the table).
//! - unary vs streaming: an interface resolved with the `Poll` update mode
//!   (the agent declares no streaming) gets the unary `SendMessage` with
//!   `returnImmediately`: one answer, then the Connector follows the task by
//!   query or subscribe. Every other interface gets `SendStreamingMessage`:
//!   the first event, then the rest of the stream as remote events.
//!
//! It adds no retry: a send, query, subscribe or cancel is attempted once and
//! the outcome goes back to the Connector, which owns retry and backoff.
//! A send has exactly one of these outcomes:
//!
//! | What happened | Outcome |
//! | --- | --- |
//! | target not resolvable, address refused by policy, client not buildable | `NotSent` |
//! | no connection was established (DNS, refused, connect timeout) | `NotSent` |
//! | the request cannot be expressed as an A2A message (see `build_message`) | `Rejected` |
//! | HTTP 400/401/403/404/405/406/415/422, gRPC 3/5/7/12/16, or a JSON-RPC error on the initial response that says the request itself was refused (parse error, invalid request, method not found, invalid params, unsupported operation, content type / extension / version not supported) | `Rejected` |
//! | first stream event (or the unary answer) carries a task | `Accepted` |
//! | first stream event (or the unary answer) is the agent's direct message | `Completed` |
//! | anything else once the request was handed to the transport | `OutcomeUnknown` |
//!
//! Why three failure kinds: `NotSent` is proof the agent never saw the
//! request, so the Connector may send it again without risking a duplicate.
//! `Rejected` is proof the agent refused the request as a whole and created
//! nothing, so sending it again would only be refused again. `OutcomeUnknown`
//! means the agent may or may not have started work; the Connector must not
//! resend blindly and treats the send as uncertain. When in doubt this module
//! answers `OutcomeUnknown`, the safe side. Whether no connection was
//! established is kept by `crate::transport_error`.

mod events;
mod yaml_registry;

pub use yaml_registry::YamlAgentResolver;

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
    /// The selected interface: binding, URL, optional tenant and update mode
    /// (`Poll` selects the unary send, see the module doc).
    pub interface: A2aInterface,
    /// Token sent as `Authorization: Bearer <token>`; `None` sends none.
    pub bearer: Option<String>,
    /// Negotiated extension URIs, sent as `a2a-extensions`. When it contains
    /// our tools extension (`crate::tools::EXTENSION`), tool records in the
    /// agent's messages become `Tool` events (see `events`).
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

/// Turns the trusted `CallAuthority` of one operation into a callable agent
/// (plan T6): the resolver re-checks the current authorization of the round,
/// node, attempt and binding for that operation, and returns the same
/// approved target. Client metadata and bare `AgentBinding` callbacks are
/// not substitutes.
///
/// Delayed (walkthrough §17): what resolves `CredentialRef` and what
/// `BindingVersion` means are not settled. Until then the composition injects
/// [`YamlAgentResolver`], a static registry read from a YAML file of agent
/// registrations, keyed by `AgentBinding.agent` (`authority.binding.agent`).
#[async_trait]
pub trait DistAgentResolver: Send + Sync {
    /// The error is a machine code, never a credential or a URL: an
    /// unauthorized target must answer `Err`, never some other agent.
    async fn resolve(&self, authority: &CallAuthority) -> Result<ResolvedAgent, String>;
}

/// Negotiated request-id query extension (plan T6): the target keeps a
/// durable index from an accepted message id to its task, so a start whose
/// answer was lost can still be found. Declared in an agent's card
/// extensions; standard A2A profiles do not have it and their lookups
/// stay `Unsupported`.
pub const QUERY_BY_REQUEST_EXT: &str = "urn:avernet:a2a:query-by-request:1";

/// The JSON-RPC method of the negotiated index.
const QUERY_BY_REQUEST_METHOD: &str = "GetTaskByRequest";

/// Longest wait for the first event of a send (or for the unary answer). It is
/// also the timeout handed to a custom binding's client. When it expires the
/// request is already with the transport, so the send is `OutcomeUnknown`.
/// Later events of a stream have no timeout here.
const FIRST_EVENT_TIMEOUT: Duration = Duration::from_secs(120);

/// The `DistAgentClientPort` provider. Holds no per-call state: every
/// operation resolves the target and builds a fresh binding client.
pub struct A2aDistAgentClient {
    resolver: Arc<dyn DistAgentResolver>,
    /// SSRF check for HTTP+JSON and JSON-RPC agent URLs.
    guard: OutboundUrlGuard,
    /// The mesh sidecar and its routes; `None` makes every gRPC target fail
    /// with `grpc_not_configured`: a send answers `NotSent`, a query
    /// `Unavailable`, a subscribe an `AgentReadError`, a cancel
    /// `OutcomeUnknown`.
    grpc: Option<GrpcMeshConfig>,
}

/// The agent's responses as the bindings return them.
type Responses = BoxStream<'static, Result<a2a::StreamResponse, A2AError>>;

/// Why a send did not produce a task. Each variant carries a machine code.
enum SendFailure {
    /// No connection was established: the agent never saw the request.
    NotSent(String),
    /// The agent's initial answer refused the request itself.
    Rejected(String),
    /// Anything else: the agent may have started work.
    Unknown(String),
}

/// A machine code for an error; never the peer's text. The `A2A HTTP <status>`
/// and `A2A gRPC <code>` messages are written by this crate's decoders, not
/// copied from the peer, so they are safe to read: `http_<status>`,
/// `grpc_<code>`, `a2a_<reason>` for a typed A2A error, else `transport`.
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
/// Only for the error of the initial request (the send call itself, or the
/// cancel call); an error that arrives later as a stream event is always
/// unknown (see `send`). gRPC codes: 3 invalid argument, 5 not found,
/// 7 permission denied, 12 unimplemented, 16 unauthenticated.
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

/// JSON-RPC and A2A codes that mean the request was not taken at all
/// (malformed, unknown method, bad parameters, or a version / extension /
/// content type / operation the agent does not support).
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
    /// `grpc`: pass `None` when the deployment has no mesh sidecar.
    pub fn new(resolver: Arc<dyn DistAgentResolver>, guard: OutboundUrlGuard, grpc: Option<GrpcMeshConfig>) -> Self {
        Self {
            resolver,
            guard,
            grpc,
        }
    }

    /// Builds the binding client of the operation's approved target. An
    /// error means nothing was sent — a refused authorization never reaches
    /// an agent. The `(bool, bool)` says whether tools v1 / v2 were
    /// negotiated, i.e. whether tool records in the agent's messages are
    /// decoded (`events`).
    async fn client(&self, authority: &CallAuthority) -> Result<(BindingClient, (bool, bool)), String> {
        self.client_and_mode(authority).await.map(|(client, tools, _)| (client, tools))
    }

    /// The resolved agent together with the binding client and update mode.
    /// Resolves through the operation's `CallAuthority` (plan T6), then
    /// builds the client of its binding; the error is a machine code
    /// (`custom_client_unavailable`, `grpc_not_configured`,
    /// `grpc_route_unavailable`, `credential_invalid`, `extensions_invalid`,
    /// `address_rejected`, or the resolver's own code). Building an HTTP or
    /// gRPC client sends nothing to the agent: the gRPC channel connects
    /// lazily on the first request, and the HTTP check only resolves DNS.
    async fn client_and_resolved(
        &self,
        authority: &CallAuthority,
    ) -> Result<(BindingClient, ResolvedAgent, A2aUpdateMode), String> {
        let agent = self.resolver.resolve(authority).await?;
        let extensions = agent.extensions.join(",");
        let interface = agent.interface.clone();
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
                mesh.client_with_optional_bearer(&interface, agent.bearer.as_deref(), &extensions)
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
                // SSRF check: resolve the host, refuse internal addresses,
                // pin the checked addresses into the client.
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
        Ok((client, agent, interface.update_mode))
    }

    /// `client` plus the interface's update mode (`Poll` = unary send).
    async fn client_and_mode(
        &self,
        authority: &CallAuthority,
    ) -> Result<(BindingClient, (bool, bool), A2aUpdateMode), String> {
        let (client, agent, mode) = self.client_and_resolved(authority).await?;
        let tools = (
            agent.extensions.iter().any(|uri| uri == crate::tools::EXTENSION),
            agent.extensions.iter().any(|uri| uri == crate::tools::EXTENSION_V2),
        );
        Ok((client, tools, mode))
    }

    /// One send (start or continue-input); never repeated here.
    ///
    /// Steps: build the client (failure: `NotSent`), build the message
    /// (failure: `Rejected`, see `build_message`), hand it to the transport,
    /// wait up to `FIRST_EVENT_TIMEOUT` for the first event, then map that
    /// event as in the module table. `remote` is `Some` for a reply: the
    /// message then names the stored remote task.
    async fn send(
        &self,
        authority: &CallAuthority,
        remote: Option<&RemoteTaskRef>,
        request: StableSendRequest,
    ) -> SendOutcome {
        let (client, tools, mode) = match self.client_and_mode(authority).await {
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
        // Every failure below is `OutcomeUnknown`; only the first event can
        // show whether the agent accepted the message.
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
        // A task, status update or artifact update: the agent accepted the
        // message and the task id is the one to store. A task that cannot be
        // addressed later must not be recorded as accepted.
        if remote.task_id.is_empty() {
            return SendOutcome::OutcomeUnknown { code: "invalid_agent_response".into() };
        }
        // The first event is put back in front, so the caller reports it too.
        // For a unary send the stream holds only that answer.
        let rest = futures::stream::once(async move { Ok(first) }).chain(responses).boxed();
        SendOutcome::Accepted {
            remote,
            events: Some(remote_events(rest, tools)),
        }
    }

    /// One `GetTask` read of a remote task.
    async fn task(&self, client: &BindingClient, task_id: &str) -> Result<a2a::Task, A2AError> {
        let request = serde_json::from_value(json!({ "id": task_id }))
            .map_err(|_| A2AError::invalid_params("task id"))?;
        client.get_task(&request).await
    }

    /// Resolves a request id to its task through the negotiated index
    /// extension, then reads and verifies the task's own evidence through
    /// the standard GetTask (plan T6). Bound to the approved target and the
    /// original operation's request identity; the protocol and adapter
    /// version travel in the extension declaration this call is gated on.
    /// A `NotFound` from a late index stays unknown — it is never a stop
    /// fact about the remote.
    async fn query_by_request(&self, authority: &CallAuthority, request_id: RequestId) -> QueryOutcome {
        let (client, agent) = match self.client_and_resolved(authority).await {
            Ok((client, agent, _)) => (client, agent),
            Err(code) => return QueryOutcome::Unavailable { code },
        };
        // The extension must be declared by the resolved agent: its URI and
        // protocol version are the record of which adapter contract answered.
        if !agent
            .extensions
            .iter()
            .any(|uri| uri == QUERY_BY_REQUEST_EXT)
        {
            tracing::debug!(request_id = %request_id.0, "no negotiated request-id index; standard profile");
            return QueryOutcome::Unsupported;
        }
        let BindingClient::JsonRpc(rpc) = &client else {
            // The extension is negotiated on the JSON-RPC binding only.
            return QueryOutcome::Unsupported;
        };
        tracing::info!(
            request_id = %request_id.0,
            extension = QUERY_BY_REQUEST_EXT,
            protocol = %agent.interface.protocol_version,
            authority_round = %authority.round,
            authority_node = %authority.node.0,
            "request resolved through the negotiated request-id index"
        );
        let answer: Result<Value, A2AError> = rpc
            .call_extension(QUERY_BY_REQUEST_METHOD, &json!({ "requestId": request_id.0 }))
            .await;
        let found = match answer {
            // The agent does not know the request yet: its index may lag.
            // No proof either way — the call stays unresolved upstream.
            Err(error) if error.code == a2a::error_code::TASK_NOT_FOUND => {
                return QueryOutcome::NotFound
            }
            Err(error) => return QueryOutcome::Unavailable { code: code_of(&error) },
            Ok(found) => found,
        };
        let task_id = found
            .get("id")
            .and_then(Value::as_str)
            .filter(|id| !id.is_empty())
            .ok_or_else(A2AError::invalid_agent_response);
        let task_id = match task_id {
            Ok(task_id) => task_id,
            Err(error) => return QueryOutcome::Unavailable { code: code_of(&error) },
        };
        match self.task(&client, task_id).await {
            Ok(task) => {
                let (_, snapshot) = events::task_event(&task);
                QueryOutcome::Found {
                    remote: events::remote_of(&task.id, &task.context_id),
                    status: events::status_of(&task.status.state),
                    snapshot: Payload::Inline(snapshot),
                }
            }
            Err(error) if error.code == a2a::error_code::TASK_NOT_FOUND => QueryOutcome::NotFound,
            Err(error) => return QueryOutcome::Unavailable { code: code_of(&error) },
        }
    }
}

/// The A2A message of a send: the request id is the message id, so a resend
/// is recognisable by an agent that de-duplicates.
///
/// The payload must be inline: a payload stored by reference (`Payload::Ref`,
/// a blob elsewhere) is not fetched by this adapter, so such a request can
/// never be sent (`request_by_reference`). Accepted inline shapes, anything
/// else is `request_invalid`:
///
/// ```text
/// {"parts": [{"text": "hello"}]}    A2A parts, used as they are
/// "hello"                            one text part
/// ```
///
/// The context id is the request's, else the stored remote task's; a reply
/// also names the remote task (`taskId`). `return_immediately` asks a unary
/// send to answer at once with the task instead of waiting for its end.
fn build_message(
    remote: Option<&RemoteTaskRef>,
    request: &StableSendRequest,
    return_immediately: bool,
) -> Result<a2a::SendMessageRequest, &'static str> {
    let Payload::Inline(payload) = &request.payload else {
        return Err("request_by_reference");
    };
    let mut parts = match payload {
        Value::Object(object) => object.get("parts").and_then(Value::as_array).cloned(),
        Value::String(text) => Some(vec![json!({ "text": text })]),
        _ => None,
    }.ok_or("request_invalid")?;
    if payload.get("schema_version").and_then(Value::as_u64) == Some(1)
        && payload.get("generation").and_then(Value::as_u64).is_some()
        && payload.get("node").and_then(Value::as_str).is_some()
        && let Some(predecessors) = payload.get("upstream")
    {
        let mut results = serde_json::Map::new();
        for (node, encoded) in predecessors.as_object().ok_or("request_invalid")? {
            match serde_json::from_value(encoded.clone()).map_err(|_| "request_invalid")? {
                Payload::Inline(content) => {
                    results.insert(node.clone(), predecessor_product(&content).ok_or("upstream_product_invalid")?);
                }
                Payload::Ref(_) => return Err("request_by_reference"),
            }
        }
        if !results.is_empty() {
            parts.push(json!({"data": {"upstream": results}}));
        }
    }
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
/// `tools`: whether the tools extension was negotiated. One `Mapper` per
/// stream, so repeated tool records are recognised within this stream only.
fn remote_events(responses: Responses, tools: (bool, bool)) -> RemoteEventStream {
    let mut mapper = Mapper::new(tools.0, tools.1);
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

/// The end of a call a terminal task state stands for; `None` while running
/// or waiting for input. A `rejected` task counts as failed.
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
    /// Sends the first message of a call; creates the remote task.
    async fn start(&self, authority: &CallAuthority, request: StableSendRequest) -> SendOutcome {
        self.send(authority, None, request).await
    }

    /// Sends a human reply to the same remote task (B12). Classified exactly
    /// like `start`; an `Accepted` answer may carry a new event stream.
    async fn send_input(
        &self,
        authority: &CallAuthority,
        remote: &RemoteTaskRef,
        request: StableSendRequest,
    ) -> SendOutcome {
        self.send(authority, Some(remote), request).await
    }

    /// Reads a remote task once. `NotFound` only for the typed A2A "task not
    /// found" error (a bare HTTP 404 or gRPC 5 is `Unavailable`): a missing
    /// task must be proven, not guessed. The snapshot is the payload of the
    /// event the task's state stands for (see `events::task_event`).
    async fn query(&self, authority: &CallAuthority, query: RemoteQuery) -> QueryOutcome {
        let remote = match query {
            // A2A has no standard lookup by message id: only the negotiated
            // request-id index extension answers (plan T6).
            RemoteQuery::ByRequest(request_id) => {
                return self.query_by_request(authority, request_id).await
            }
            RemoteQuery::ByTask(remote) => remote,
        };
        let client = match self.client(authority).await {
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

    /// Opens a new event stream for an existing remote task, e.g. after a
    /// takeover or a resume. Events may repeat what was already reported.
    async fn subscribe(
        &self,
        authority: &CallAuthority,
        remote: &RemoteTaskRef,
        _after: Option<ReplayToken>,
    ) -> Result<RemoteEventStream, AgentReadError> {
        // The bindings expose no replay position, so `after` cannot be used:
        // the stream starts wherever the agent starts it.
        let (client, tools) = self.client(authority).await.map_err(|code| AgentReadError { code })?;
        let request = serde_json::from_value(json!({ "id": remote.task_id }))
            .map_err(|_| AgentReadError { code: "task_id_invalid".into() })?;
        let responses = client
            .subscribe_to_task(&request)
            .await
            .map_err(|error| AgentReadError { code: code_of(&error) })?;
        Ok(remote_events(responses, tools))
    }

    /// Asks the agent to cancel a remote task. A2A cancel carries no message
    /// id, so `_request_id` is unused. Outcomes:
    /// - `ConfirmedCanceled`: the task is `canceled`.
    /// - `AlreadyFinished`: it ended otherwise first.
    /// - `RequestAccepted`: the agent took the request, the task still runs;
    ///   not a confirmation.
    /// - `Rejected`: refused (request-refusal codes, "not cancelable" while
    ///   the task is still running, or a task id that cannot be expressed).
    /// - `OutcomeUnknown`: anything else, including a client that could not be
    ///   built or no connection: the port has no "not sent" for a cancel, and
    ///   the Connector keeps an unknown cancel supervised.
    async fn cancel(
        &self,
        authority: &CallAuthority,
        remote: &RemoteTaskRef,
        _request_id: RequestId,
    ) -> CancelOutcome {
        let unknown = |code: String| CancelOutcome::OutcomeUnknown { code };
        let client = match self.client(authority).await {
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
