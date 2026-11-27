//! REST/A2A boundary of published FSM groups (plan T8) over the same stack the
//! JSON-RPC binding runs on: HTTP+JSON and SSE under
//! `/api/v1/collaboration/a2a/groups/{group_id}`, without touching the legacy
//! binding. The fake authentication chain installs the trusted `GatewayCaller`
//! the way the real front layer does; the grants policy is the host-registered
//! D0 fixture.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]

#![allow(dead_code, reason = "shared harness of the group vector targets")]

#[path = "../../../../../services/bcs-dist-gateway-store/tests/common/mod.rs"]
mod common;

use a2a::{Message, Part, Role};
use async_trait::async_trait;
use axum::Router;
use axum::body::Body;
use axum::http::{Request, StatusCode};
use bcs_app_dist_gateway::{
    Clock, DistGateway, DistGatewayConfig, GroupDiscovery, GroupDiscoveryConfig,
};
use bcs_dist_gateway::DistGatewayCore;
use bcs_dist_store::inbox::StoreInboxProducer;
use bcs_dist_worker::WorkerTaskAdmission;
use bcs_service_api::core::dist_admission::{TaskAdmissionCommand, TaskAdmissionCoreService};
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_service_api::port::repo::distributed::group_listing::GroupListingPort;
use bcs_service_api::port::repo::distributed::group_round::GroupRoundGrants;
use bcs_service_api::port::repo::distributed::inbox::{AdmitOutcome, AdmitRequest, InboxProducerPort};
use bcs_store_api::stream::StreamStorePlugin;

use futures::StreamExt;
use serde_json::{Value, json};
use std::num::NonZeroU32;
use std::ops::Bound;
use std::sync::atomic::{AtomicBool, AtomicI64};
use std::sync::Arc;
use tower::ServiceExt;

const GROUP: &str = "grp-research-01";

// Re-exported so each vector target keeps one flat `use rest_common::*`.
pub use bcs_dist_gateway_http::{GatewayHttpConfig, GROUPS_PREFIX, router};
pub use bcs_dist_model::*;
pub use bcs_dist_gateway_http::GatewayCaller;
pub use bcs_service_api::application::dist_gateway::*;
pub use bcs_service_api::application::principal::{BotActor, CallerContext};
pub use bcs_service_api::port::dist_agent::*;
pub use axum::extract::Extension;
pub use std::time::Duration;
pub use common::{NOW, Ports, ports, question, snapshot};
pub use std::sync::atomic::Ordering;

pub struct Groups;
#[async_trait]
impl DistGroupDirectoryPort for Groups {
    async fn resolve(
        &self,
        _scope: &TaskScope,
        group: &GroupId,
    ) -> Result<FrozenGroupDefinition, GroupDirectoryError> {
        Ok(FrozenGroupDefinition {
            group: group.clone(),
            workflow: WorkflowRef {
                definition_id: "wf-research".into(),
                revision: "7".into(),
                digest: Digest("sha256:ab12".into()),
            },
            participants: vec![],
            limits: SessionLimits {
                max_queued_tasks: 16,
                max_task_steps: 200,
                task_timeout_millis: 1_800_000,
                max_inline_bytes: 65_536,
            },
        })
    }
    async fn load_workflow(
        &self,
        _scope: &TaskScope,
        _reference: &WorkflowRef,
    ) -> Result<bcs_domain::collaboration::StateMachineDefinition, GroupDirectoryError> {
        Err(GroupDirectoryError::Unavailable)
    }
}

/// The host-registered D0 fixture: published, invocable, readable, cancelable
/// — with switches the revocation vectors flip.
pub struct AllowGrants {
    pub read: AtomicBool,
    pub cancel: AtomicBool,
}

impl AllowGrants {
    fn allow_all() -> Self {
        Self {
            read: AtomicBool::new(true),
            cancel: AtomicBool::new(true),
        }
    }
}

#[async_trait]
impl GroupGrantPolicy for AllowGrants {
    async fn grants(&self, _: &GatewayCaller, _: &GroupId) -> GroupRoundGrants {
        GroupRoundGrants {
            published: true,
            invoke: true,
            read: self.read.load(Ordering::SeqCst),
            cancel: self.cancel.load(Ordering::SeqCst),
        }
    }
}

/// A clock the test advances: cursor expiry follows it.
pub struct TestClock(pub AtomicI64);
impl Clock for TestClock {
    fn now(&self) -> TimestampMillis {
        TimestampMillis(self.0.load(Ordering::SeqCst))
    }
}

pub fn rest_caller(principal: &str) -> GatewayCaller {
    GatewayCaller {
        tenant: TenantId("t-ant".into()),
        caller: CallerContext::Bot(BotActor {
            bot_uuid: principal.into(),
        }),
    }
}

pub fn caller() -> GatewayCaller {
    rest_caller("pa-001")
}

/// The application stack of the group surface: the same core, inbox and
/// reader as the legacy binding, plus the host seams of T8 — the grant
/// policy, the group listing port over the store model, the read-only
/// group discovery, and the injectable clock.
pub struct GroupStack {
    pub gateway: Arc<DistGateway>,
    pub discovery: Arc<GroupDiscovery>,
    pub grants: Arc<AllowGrants>,
    pub clock: Arc<TestClock>,
}

pub fn group_stack(p: &Ports) -> GroupStack {
    let core = gateway_core(p);
    let mut config = DistGatewayConfig::new(64);
    config.snapshot_cover_retry = Duration::from_millis(10);
    config.snapshot_cover_limit = Duration::from_millis(300);
    config.grants_recheck = Duration::from_millis(50);
    let grants = Arc::new(AllowGrants::allow_all());
    let listing: Arc<dyn GroupListingPort> = p.tasks.clone();
    let clock = Arc::new(TestClock(AtomicI64::new(NOW)));
    let gateway = DistGateway::new(
        Arc::new(core),
        Arc::new(Groups),
        grants.clone(),
        listing,
        clock.clone(),
        config,
    )
    .unwrap();
    let discovery = Arc::new(GroupDiscovery::new(
        Arc::new(Groups),
        grants.clone(),
        GroupDiscoveryConfig::default(),
    ));
    GroupStack {
        gateway: Arc::new(gateway),
        discovery,
        grants,
        clock,
    }
}

pub fn test_router(stack: &GroupStack) -> Router {
    test_router_for(stack, caller())
}

pub fn test_router_for(stack: &GroupStack, caller: GatewayCaller) -> Router {
    let mut config = GatewayHttpConfig::new("https://gw.example");
    config.blocking_send_wait = Duration::from_millis(200);
    config.cancel_wait = Duration::from_millis(100);
    router(stack.gateway.clone(), stack.discovery.clone(), config).layer(Extension(caller))
}

/// Companion to `FakeWorker`: model an accepted GroupA2a start in the real
/// Inbox and task index, so HTTP fixtures can independently drive publication
/// and archive retention. This is not the checkpoint admission test; the
/// bootstrap REST bridge exercises that with a real licensed Worker.
struct FixtureAdmission {
    legacy: WorkerTaskAdmission,
    inbox: Arc<StoreInboxProducer>,
}

#[async_trait]
impl TaskAdmissionCoreService for FixtureAdmission {
    async fn admit(
        &self,
        scope: &TaskScope,
        command: TaskAdmissionCommand,
    ) -> Result<AdmitOutcome, StoreError> {
        if !command.start_task
            || CreationSource::of_sid(&command.input.sid) != Some(CreationSource::GroupA2a)
        {
            return self.legacy.admit(scope, command).await;
        }
        let input = command.input;
        let create_session = command.create_session.map(|definition| {
            let mut spec = common::session_spec(&input.sid.0, scope.clone());
            spec.group = definition.group;
            spec.workflow = definition.workflow;
            spec.participants = definition.participants;
            spec.limits = definition.limits;
            spec
        });
        let mut task = snapshot(&input.tid.0, TaskStatus::Submitted, None, NOW);
        task.scope = scope.clone();
        task.sid = input.sid.clone();
        self.inbox.admit(AdmitRequest {
            scope: scope.clone(),
            input,
            create_session,
            create_task: Some(task),
        }).await
    }
}

pub fn gateway_core(p: &Ports) -> DistGatewayCore {
    let admission = FixtureAdmission {
        legacy: WorkerTaskAdmission::new(p.inbox.clone(), Arc::new(|| TimestampMillis(NOW))),
        inbox: p.inbox.clone(),
    };
    DistGatewayCore::new(
        Arc::new(admission),
        Arc::new(bcs_dist_worker::WorkerOutputReader::new(
            p.tasks.clone(),
            p.blobs.clone(),
            Arc::new(bcs_dist_a2a_output::A2aOutputAdapter),
        )),
        p.sessions.clone(),
    )
}

pub fn message_of(id: &str, text: &str, context: Option<&str>) -> Value {
    let mut message = Message::new(Role::User, vec![Part::text(text)]);
    message.message_id = id.into();
    message.context_id = context.map(str::to_owned);
    serde_json::to_value(message).unwrap()
}

/// POST `{prefix}/{group}/message:send` (the action rides the dynamic POST
/// path).
pub fn rest_send(id: &str, text: &str, context: Option<&str>, configuration: Value) -> Request<Body> {
    Request::post(format!("{GROUPS_PREFIX}/{GROUP}/message:send"))
        .header("content-type", "application/json")
        .body(Body::from(
            json!({ "message": message_of(id, text, context), "configuration": configuration }).to_string(),
        ))
        .unwrap()
}

/// GET `{prefix}/{group}/tasks` with a query string.
pub fn rest_list(query: &str) -> Request<Body> {
    Request::get(format!("{GROUPS_PREFIX}/{GROUP}/tasks?{query}"))
        .header("content-type", "application/json")
        .body(Body::empty())
        .unwrap()
}

/// GET/POST on `{prefix}/{group}/tasks/{action}`.
pub fn rest_task(method: &str, action: &str) -> Request<Body> {
    Request::builder()
        .method(method)
        .uri(format!("{GROUPS_PREFIX}/{GROUP}/tasks/{action}"))
        .header("content-type", "application/json")
        .body(Body::from(json!({}).to_string()))
        .unwrap()
}

pub fn rest_cancel(tid: &str, request_id: &str) -> Request<Body> {
    Request::post(format!("{GROUPS_PREFIX}/{GROUP}/tasks/{tid}:cancel"))
        .header("content-type", "application/json")
        .header("x-request-id", request_id)
        .body(Body::from(json!({ "reason": "user asked" }).to_string()))
        .unwrap()
}

pub async fn response_of(app: &Router, request: Request<Body>) -> (StatusCode, Option<Value>) {
    let response = app.clone().oneshot(request).await.unwrap();
    let status = response.status();
    let bytes = axum::body::to_bytes(response.into_body(), 16 << 20).await.unwrap();
    if bytes.is_empty() {
        (status, None)
    } else {
        (status, serde_json::from_slice(&bytes).ok())
    }
}

pub fn code_of(body: &Option<Value>) -> String {
    body.as_ref()
        .and_then(|v| v["error"]["code"].as_str())
        .unwrap_or_default()
        .to_owned()
}

/// One parsed SSE frame: its `id:` line and the JSON of its `data:` line.
#[derive(Debug)]
pub struct Frame {
    pub id: Option<String>,
    pub data: Value,
}

pub fn parse_frames(text: &str) -> Vec<Frame> {
    text.split("\n\n")
        .filter_map(|block| {
            let mut id = None;
            let mut data = None;
            for line in block.lines() {
                if let Some(value) = line.strip_prefix("id:") {
                    id = Some(value.trim().to_owned());
                } else if let Some(value) = line.strip_prefix("data:") {
                    data = serde_json::from_str(value.trim()).ok();
                }
            }
            data.map(|data| Frame { id, data })
        })
        .collect()
}

/// Reads SSE frames until `count` arrived, the stream ended, or the bounded
/// wait expired: a subscription never hangs on a feed that stopped.
pub async fn read_frames(response: axum::response::Response, count: usize) -> Vec<Frame> {
    assert_eq!(response.status(), StatusCode::OK);
    assert!(
        response.headers()["content-type"].to_str().unwrap().starts_with("text/event-stream"),
        "a stream answers with SSE"
    );
    let mut body = response.into_body().into_data_stream();
    let mut text = String::new();
    loop {
        let frames = parse_frames(&text);
        if frames.len() >= count {
            return frames;
        }
        match tokio::time::timeout(Duration::from_secs(5), body.next()).await {
            Ok(Some(Ok(chunk))) => text.push_str(std::str::from_utf8(&chunk).unwrap()),
            Ok(_) => return parse_frames(&text),
            Err(_) => panic!("timed out after frames: {:?}", parse_frames(&text)),
        }
    }
}

/// Publishes `status` for `tid` with a `status_updated_at` `delta` beyond the
/// fixture now, so list rows carry distinct sort channels.
pub async fn publish_at(p: &Ports, tid: &str, status: TaskStatus, delta: i64, interactions: &[&str]) {
    let mut task = snapshot(tid, status, None, NOW);
    task.sid = Sid(tid.rsplit_once('.').unwrap().0.to_owned());
    task.scope = TaskScope {
        tenant: TenantId("t-ant".into()),
        principal: PrincipalId("bot:pa-001".into()),
    };
    task.content.interactions = interactions.iter().map(|id| question(id)).collect();
    task.updated_at = TimestampMillis(NOW + delta);
    p.worker.publish(&task).await;
}

pub async fn inbox_len(p: &Ports, sid: &str) -> usize {
    let shard = sid.split_once('.').unwrap().0;
    let key = format!("bcn:v1:c01:main:{shard}:inbox:{sid}");
    StreamStorePlugin::range(
        p.model.as_ref(),
        &key,
        Bound::Unbounded,
        Bound::Unbounded,
        NonZeroU32::new(1000).unwrap(),
        false,
    )
    .await
    .unwrap()
    .entries
    .len()
}

pub fn final_record_of(tid: &str) -> FinalTaskRecord {
    let mut record = common::final_record(tid);
    record.scope = TaskScope {
        tenant: TenantId("t-ant".into()),
        principal: PrincipalId("bot:pa-001".into()),
    };
    record.sid = Sid(tid.rsplit_once('.').unwrap().0.to_owned());
    record
}

/// One legacy JSON-RPC request.
pub fn rpc_request(method: &str, params: Value, id: &str) -> Request<Body> {
    Request::post(format!("/a2a/{GROUP}"))
        .header("content-type", "application/json")
        .header("a2a-version", "1.0")
        .body(Body::from(
            json!({ "jsonrpc": "2.0", "id": id, "method": method, "params": params }).to_string(),
        ))
        .unwrap()
}

pub fn rpc_send(id: &str, context: Option<&str>) -> Request<Body> {
    let context = match context {
        None => Value::Null,
        Some(sid) => json!(sid),
    };
    rpc_request(
        "SendMessage",
        json!({
            "message": message_of(id, "rpc", None).with_key("contextId", context),
            "configuration": { "returnImmediately": true }
        }),
        "r1",
    )
}

pub trait ValueExt {
    fn with_key(self, key: &str, value: Value) -> Value;
}

impl ValueExt for Value {
    fn with_key(self, key: &str, value: Value) -> Value {
        let mut object = self;
        if let Some(map) = object.as_object_mut() {
            map.insert(key.to_owned(), value);
        }
        object
    }
}

