//! Inbound distributed use cases. HTTP/A2A DTOs stay in delivery adapters.
use super::principal::CallerContext;
use async_trait::async_trait;
use bcs_dist_model::*;
use futures::stream::BoxStream;
pub use crate::port::dist_output::{FormattedOutputBody, FormattedOutputEvent};
pub use crate::port::repo::distributed::group_round::GroupRoundGrants;

/// Built by trusted authentication/tenant resolution, never deserialized from body.
#[derive(Debug, Clone)]
pub struct GatewayCaller {
    pub tenant: TenantId,
    pub caller: CallerContext,
}

/// The persistent creation source of a session, task or request: the
/// entrance that derived its identity. A resource of one source is never
/// reached through another entrance, whatever caller and group a request
/// names; the check is part of the durable identity namespace, not a runtime
/// preference. (Plan T8 source isolation.)
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CreationSource {
    /// The legacy A2A JSON-RPC binding: session suffix is 16 plain hex
    /// characters.
    Rpc,
    /// The group REST/SSE binding of published FSM groups: session suffix
    /// carries the `g` namespace marker (`g<16 hex>`; hex can never contain
    /// `g`, so the two namespaces are disjoint and unambiguous).
    GroupA2a,
}

impl CreationSource {
    /// The creation source durably encoded in a session id's namespace, or
    /// `None` for a shape no entrance accepts. The group namespace suffix is
    /// exactly `g<16 hex>`; a legacy suffix is anything else a session id
    /// may be — hex cannot contain `g`, so the namespaces stay disjoint.
    pub fn of_sid(sid: &Sid) -> Option<Self> {
        let sid = &sid.0;
        if sid.is_empty() || sid.len() > 200 {
            return None;
        }
        let (shard, rest) = sid.split_once('.')?;
        let digits = shard.strip_prefix('s')?;
        if digits.is_empty()
            || digits.len() > 9
            || !digits.bytes().all(|b| b.is_ascii_digit())
            || rest.is_empty()
            || rest.len() > 256
            || rest
                .bytes()
                .any(|b| !(b.is_ascii_alphanumeric() || b == b'-' || b == b'_'))
        {
            return None;
        }
        match rest.strip_prefix('g') {
            Some(suffix) if suffix.len() == 16 && suffix.bytes().all(|b| b.is_ascii_hexdigit()) => {
                Some(Self::GroupA2a)
            }
            _ => Some(Self::Rpc),
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct SubmitInput {
    pub source: CreationSource,
    pub group: GroupId,
    pub message_id: RequestId,
    pub session: Option<Sid>,
    pub task: Option<Tid>,
    pub interaction: Option<InteractionId>,
    pub content: Payload,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CancelTask {
    /// The group the request was addressed to; must be the task's session's.
    pub group: GroupId,
    pub tid: Tid,
    pub request_id: RequestId,
    pub reason: Option<String>,
    /// The entrance this cancel arrived through.
    pub source: CreationSource,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SubscribeMode {
    Snapshot,
    Resume(TaskCursor),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SubscribeTask {
    /// The group the request was addressed to; must be the task's session's.
    pub group: GroupId,
    pub tid: Tid,
    pub mode: SubscribeMode,
    /// The entrance this subscription arrived through.
    pub source: CreationSource,
}

#[derive(Debug, Clone, PartialEq)]
pub enum TaskStreamItem {
    Snapshot {
        view: TaskView,
        cursor: Option<TaskCursor>,
    },
    ResumeAccepted {
        cursor: TaskCursor,
    },
    Events {
        events: Vec<FormattedOutputEvent>,
        cursor: TaskCursor,
    },
}

pub type TaskSubscription = BoxStream<'static, Result<TaskStreamItem, GatewayError>>;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ListTasks {
    /// The group the request was addressed to; must be the listed session's.
    pub group: GroupId,
    pub session: Option<Sid>,
    pub statuses: Vec<TaskStatus>,
    pub after: Option<PageToken>,
    pub limit: std::num::NonZeroU32,
}

/// One page request of the group public listing (plan T8). `limit` is the
/// caller's own page size request already capped by the adapter.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GroupTaskList {
    /// The group the request was addressed to.
    pub group: GroupId,
    pub statuses: Vec<TaskStatus>,
    pub limit: std::num::NonZeroU32,
    /// Signed continuation cursor of a previous page.
    pub cursor: Option<PageToken>,
    /// Include full projections frozen at the listing cut; off by default.
    pub include_artifacts: bool,
}

/// The task state a wait ends on.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WaitUntil {
    /// The task asks for input or authorization, or is finished.
    InputOrFinal,
    /// The task is finished.
    Final,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WaitTask {
    /// The group the request was addressed to; must be the task's session's.
    pub group: GroupId,
    pub tid: Tid,
    pub until: WaitUntil,
    /// Coverage seen before the admission this wait follows. States at or
    /// before it don't end the wait (e.g. the question a reply answers).
    pub after: Option<EventId>,
    pub timeout: std::time::Duration,
    /// The entrance this wait arrived through.
    pub source: CreationSource,
}

#[derive(Debug, Clone, PartialEq)]
pub struct TaskPage {
    pub tasks: Vec<TaskView>,
    pub next: Option<PageToken>,
}

/// The host-registered authorization policy of the group surface: the D0
/// matrix driver (plan T8). Grants are supplied by trusted registration and
/// re-evaluated fresh at every operation, on every page turn and while a
/// subscription is open; they are never read from request metadata and an
/// undecided or absent registration is a refusal, never an anonymous
/// fallback.
#[async_trait]
pub trait GroupGrantPolicy: Send + Sync {
    async fn grants(&self, caller: &GatewayCaller, group: &GroupId) -> GroupRoundGrants;
}

/// The public view of one published group (plan T8): exactly what its frozen
/// publication declares and what this boundary implements. No unapproved
/// capability is ever declared, and the card of an unpublished group is not
/// disclosed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GroupCard {
    pub group: GroupId,
    pub streaming: bool,
    pub push_notifications: bool,
    /// Enabled by-request extension URIs; empty unless explicitly enabled.
    pub extensions: Vec<String>,
    pub input_modes: Vec<String>,
    pub output_modes: Vec<String>,
}

/// The independent read-only Card boundary (plan T8): it answers group
/// discovery for an authenticated caller and never reads or writes task,
/// admission or session state.
#[async_trait]
pub trait GroupDiscoveryService: Send + Sync {
    async fn card(&self, caller: &GatewayCaller, group: &GroupId) -> Result<GroupCard, GatewayError>;
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum GatewayError {
    #[error("access denied")]
    AccessDenied,
    #[error("task not found")]
    NotFound,
    #[error("request identity/content conflict")]
    Conflict,
    #[error("task is finished and cannot be canceled")]
    NotCancelable,
    #[error("session quarantined")]
    Quarantined,
    #[error("invalid input: {code}")]
    Invalid { code: String },
    #[error("dependency unavailable: {code}")]
    Unavailable { code: String },
    #[error("admission outcome unknown; retry the same request")]
    OutcomeUnknown { request_id: RequestId },
}

#[async_trait]
pub trait DistGatewayService: Send + Sync {
    /// Resolve authenticated scope, route, derive stable identity and admit.
    /// `request.source` is the entrance identity namespace: resources of one
    /// source are never created through another.
    async fn submit(
        &self,
        caller: &GatewayCaller,
        request: SubmitInput,
    ) -> Result<AdmissionReceipt, GatewayError>;
    /// A session or task is reached only through the group it was created for:
    /// here and in every request that names an existing session or task, another
    /// group is answered as NotFound. The durable creation source is validated
    /// the same way: another entrance's resource stays invisible.
    async fn get_task(
        &self,
        caller: &GatewayCaller,
        group: &GroupId,
        tid: &Tid,
        source: CreationSource,
    ) -> Result<TaskView, GatewayError>;
    /// Snapshot@H continues at H; resume@C never follows an unrelated newer snapshot.
    async fn subscribe(
        &self,
        caller: &GatewayCaller,
        request: SubscribeTask,
    ) -> Result<TaskSubscription, GatewayError>;
    /// Returns once the task reaches the requested state and the stored view
    /// covers that event; at the timeout returns the current view.
    async fn wait_task(
        &self,
        caller: &GatewayCaller,
        request: WaitTask,
    ) -> Result<TaskView, GatewayError>;
    /// Receipt acknowledges a durable cancellation request, not remote cancellation.
    /// A finished task is refused with NotCancelable.
    async fn cancel(
        &self,
        caller: &GatewayCaller,
        request: CancelTask,
    ) -> Result<AdmissionReceipt, GatewayError>;
    async fn list_tasks(
        &self,
        caller: &GatewayCaller,
        request: ListTasks,
    ) -> Result<TaskPage, GatewayError>;
    /// A send into a published FSM group through the REST/SSE group entrance:
    /// the D0 grants (`New` needs published and invoke) and the frozen workflow
    /// revision are checked before anything durable happens; the identity is
    /// then derived in the group source namespace and admitted like any send.
    async fn group_send(
        &self,
        caller: &GatewayCaller,
        request: SubmitInput,
    ) -> Result<AdmissionReceipt, GatewayError>;
    /// The group public listing: revalidates the reading authorization for
    /// every page, then serves the next page of a versioned materialized cut
    /// through the group listing port.
    async fn group_list_tasks(
        &self,
        caller: &GatewayCaller,
        request: GroupTaskList,
    ) -> Result<crate::port::repo::distributed::group_listing::GroupIndexPage, GatewayError>;
    /// A blocking group wait bounded by the caller's own budget: returns the
    /// current view and whether the budget expired (the task continues; its
    /// final state stays reachable later).
    async fn group_wait_task(
        &self,
        caller: &GatewayCaller,
        request: WaitTask,
    ) -> Result<(TaskView, bool), GatewayError>;
}
