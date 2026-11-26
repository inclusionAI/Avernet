//! Gateway use cases of the distributed executor plane.
//!
//! [`DistGateway`] implements `DistGatewayService` for delivery adapters. It
//! resolves the caller's scope, derives stable identities, builds the admission
//! command, and orchestrates query, subscription, waiting, cancel and listing
//! over the core service. Transport types stay in the adapters; storage stays
//! behind the core.
//!
//! The group-A2A surface (plan T8) rides the same use cases through its own
//! entry points: every request validates the D0 grants of the
//! host-registered [`GroupGrantPolicy`], the frozen workflow revision, and
//! the durable creation source of the resources it names. Unpublished groups
//! and undecided registrations are refused — there is no anonymous fallback.

mod discovery;
mod error;
mod identity;
mod submit;
mod subscribe;
mod wait;

pub use bcs_service_api::application::dist_gateway::CreationSource;
pub use discovery::{GroupDiscovery, GroupDiscoveryConfig};
pub use identity::{parse_session, session_of_task};
pub(crate) use identity::Derivation;

use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::application::dist_gateway::*;
use bcs_service_api::application::principal::CallerContext;
use bcs_service_api::core::dist_gateway::{
    CoreTaskQuery, DistGatewayCoreError, DistGatewayCoreService,
};
use bcs_service_api::port::dist_agent::DistGroupDirectoryPort;
use bcs_service_api::port::repo::distributed::group_listing::{
    GroupIndexPage, GroupIndexQuery, GroupListingPort,
};
use bcs_service_api::port::repo::distributed::group_round::{
    GroupRoundGrants, GroupRoundOperation, RoundBudgetProfile,
};
use error::core_error;
use identity::creation_source_of_sid;
use std::num::NonZeroU32;
use std::sync::Arc;
use std::time::Duration;

/// Legacy application clock. Task initialization uses the Worker admission clock.
pub trait Clock: Send + Sync {
    fn now(&self) -> TimestampMillis;
}

/// The system clock.
#[derive(Debug, Default, Clone, Copy)]
pub struct SystemClock;

impl Clock for SystemClock {
    fn now(&self) -> TimestampMillis {
        let millis = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis())
            .unwrap_or_default();
        TimestampMillis(i64::try_from(millis).unwrap_or(i64::MAX))
    }
}

#[derive(Debug, Clone)]
pub struct DistGatewayConfig {
    /// Number of shards new sessions are spread over. Must not change while
    /// clients may still retry a first message: the session id is derived
    /// from the request and this count.
    pub shard_count: u32,
    /// Pause between task reads while waiting for the snapshot to cover an event.
    pub snapshot_cover_retry: Duration,
    /// Longest such wait; afterwards the stored snapshot is returned as is.
    pub snapshot_cover_limit: Duration,
    /// Largest page of a task listing.
    pub max_page: NonZeroU32,
    /// Largest page of a group listing (candidate profile bound).
    pub max_group_page: NonZeroU32,
    /// Refresh interval of the current authorization while a group
    /// subscription stays open: a revoked grant ends the stream within one
    /// interval, whether or not the feed ever delivers another item.
    pub grants_recheck: Duration,
}

impl DistGatewayConfig {
    pub fn new(shard_count: u32) -> Self {
        Self {
            shard_count,
            snapshot_cover_retry: Duration::from_millis(50),
            snapshot_cover_limit: Duration::from_secs(2),
            max_page: NonZeroU32::new(50).unwrap_or(NonZeroU32::MIN),
            max_group_page: NonZeroU32::new(RoundBudgetProfile::v1().max_page_rows)
                .unwrap_or(NonZeroU32::MIN),
            grants_recheck: Duration::from_secs(1),
        }
    }
}

pub struct DistGateway {
    core: Arc<dyn DistGatewayCoreService>,
    groups: Arc<dyn DistGroupDirectoryPort>,
    policy: Arc<dyn GroupGrantPolicy>,
    listing: Arc<dyn GroupListingPort>,
    clock: Arc<dyn Clock>,
    config: DistGatewayConfig,
}

impl DistGateway {
    /// Fails when `shard_count` is zero. The policy and the group listing
    /// port are the host-registered seams of the group surface; nothing in
    /// their absence is served.
    pub fn new(
        core: Arc<dyn DistGatewayCoreService>,
        groups: Arc<dyn DistGroupDirectoryPort>,
        policy: Arc<dyn GroupGrantPolicy>,
        listing: Arc<dyn GroupListingPort>,
        clock: Arc<dyn Clock>,
        config: DistGatewayConfig,
    ) -> Result<Self, GatewayError> {
        if config.shard_count == 0 {
            return Err(invalid("shard_count_zero"));
        }
        Ok(Self {
            core,
            groups,
            policy,
            listing,
            clock,
            config,
        })
    }
}

impl DistGateway {
    /// A session is reached only through the group it was created for. The
    /// frozen spec holds that group; another one is answered as NotFound, so a
    /// caller learns nothing about sessions it did not address.
    pub(crate) async fn check_group(
        &self,
        scope: &TaskScope,
        sid: &Sid,
        group: &GroupId,
    ) -> Result<(), GatewayError> {
        let frozen = self.core.session_group(scope, sid).await.map_err(core_error)?;
        if &frozen == group {
            Ok(())
        } else {
            Err(GatewayError::NotFound)
        }
    }

    /// The durable creation source of a session id, validated against the
    /// entrance the request arrived through. A resource of another entrance
    /// stays invisible (plan T8).
    pub(crate) fn check_source(&self, sid: &Sid, source: CreationSource) -> Result<(), GatewayError> {
        if creation_source_of_sid(sid)? != source {
            return Err(GatewayError::NotFound);
        }
        Ok(())
    }

    /// Current authorization of one group operation: the host-registered
    /// policy answers; a denied or undecided operation is a refusal. The
    /// frozen revision is checked separately by the session spec / directory.
    pub(crate) async fn check_grants(
        &self,
        caller: &GatewayCaller,
        group: &GroupId,
        operation: GroupRoundOperation,
    ) -> Result<GroupRoundGrants, GatewayError> {
        let grants = self.policy.grants(caller, group).await;
        if !grants.allows(operation) {
            return Err(GatewayError::AccessDenied);
        }
        Ok(grants)
    }

    pub(crate) fn policy(&self) -> &Arc<dyn GroupGrantPolicy> {
        &self.policy
    }

    pub(crate) fn states_config(&self) -> &DistGatewayConfig {
        &self.config
    }
}

pub(crate) fn invalid(code: &str) -> GatewayError {
    GatewayError::Invalid {
        code: code.to_owned(),
    }
}

/// The authorization scope of a caller: its tenant and a principal that names
/// the kind of identity, so a bot and a human with the same id never collide.
pub(crate) fn scope_of(caller: &GatewayCaller) -> Result<TaskScope, GatewayError> {
    let principal = match &caller.caller {
        CallerContext::Public => return Err(GatewayError::AccessDenied),
        CallerContext::Human(actor) => format!("human:{}", actor.actor_id),
        CallerContext::Bot(actor) => format!("bot:{}", actor.bot_uuid),
        CallerContext::Integration(client) => format!("integration:{}", client.client_id),
        CallerContext::Admin(actor) => format!("admin:{}", actor.actor_id),
    };
    if caller.tenant.0.is_empty() {
        return Err(GatewayError::AccessDenied);
    }
    Ok(TaskScope {
        tenant: caller.tenant.clone(),
        principal: PrincipalId(principal),
    })
}

#[async_trait]
impl DistGatewayService for DistGateway {
    async fn submit(
        &self,
        caller: &GatewayCaller,
        request: SubmitInput,
    ) -> Result<AdmissionReceipt, GatewayError> {
        submit::submit(self, caller, request).await
    }

    async fn get_task(
        &self,
        caller: &GatewayCaller,
        group: &GroupId,
        tid: &Tid,
        source: CreationSource,
    ) -> Result<TaskView, GatewayError> {
        // Durable visibility first, then the current grant: a resource of
        // another source (or group) is NotFound whatever the policy says —
        // the cross-source contract pins invisibility over denial — while a
        // revoked subject with a reachable task gets the 403 of the matrix
        // (plan T8: a revoked subject cannot keep reading through a task id
        // it learned earlier).
        let scope = scope_of(caller)?;
        let sid = session_of_task(tid)?;
        self.check_source(&sid, source)?;
        self.check_group(&scope, &sid, group).await?;
        if source == CreationSource::GroupA2a {
            self.check_grants(caller, group, GroupRoundOperation::Read).await?;
        }
        let cut = self.core.restore_task(&scope, tid).await.map_err(core_error)?;
        Ok(cut.view)
    }

    async fn subscribe(
        &self,
        caller: &GatewayCaller,
        request: SubscribeTask,
    ) -> Result<TaskSubscription, GatewayError> {
        subscribe::subscribe(self, caller, request).await
    }

    async fn wait_task(
        &self,
        caller: &GatewayCaller,
        request: WaitTask,
    ) -> Result<TaskView, GatewayError> {
        wait::wait_task(self, caller, request).await
    }

    async fn cancel(
        &self,
        caller: &GatewayCaller,
        request: CancelTask,
    ) -> Result<AdmissionReceipt, GatewayError> {
        // The group surface drives the D0 matrix (plan T8): Cancel needs the
        // read grant plus the explicit cancel grant, evaluated fresh with
        // this request, before anything durable happens. Durable visibility
        // comes first (the same checks the shared path re-does): a resource
        // of another entrance or group stays NotFound whatever the policy
        // says; a reachable task with a revoked grant gets the 403.
        if request.source == CreationSource::GroupA2a {
            let scope = scope_of(caller)?;
            let sid = session_of_task(&request.tid)?;
            self.check_source(&sid, request.source)?;
            self.check_group(&scope, &sid, &request.group).await?;
            self.check_grants(caller, &request.group, GroupRoundOperation::Cancel)
                .await?;
        }
        submit::cancel(self, caller, request).await
    }

    async fn list_tasks(
        &self,
        caller: &GatewayCaller,
        request: ListTasks,
    ) -> Result<TaskPage, GatewayError> {
        let scope = scope_of(caller)?;
        let Some(sid) = request.session else {
            return Err(invalid("session_filter_required"));
        };
        parse_session(&sid)?;
        self.check_group(&scope, &sid, &request.group).await?;
        let query = CoreTaskQuery {
            sid: Some(sid),
            statuses: request.statuses,
            after: request.after,
            limit: request.limit.min(self.config.max_page),
        };
        let page = self.core.list_tasks(&scope, query).await.map_err(|error| match error {
            // The store rejected the token's format: invalid input, not a
            // failed dependency a client should retry.
            DistGatewayCoreError::Unavailable { code } if code == "page_token" => invalid("page_token"),
            other => core_error(other),
        })?;
        Ok(TaskPage {
            tasks: page.tasks,
            next: page.next,
        })
    }

    async fn group_send(
        &self,
        caller: &GatewayCaller,
        request: SubmitInput,
    ) -> Result<AdmissionReceipt, GatewayError> {
        if request.source != CreationSource::GroupA2a {
            return Err(invalid("group_source_required"));
        }
        // New rounds need the published-invoke pair; a reply to an existing
        // task is a read-surface replay. The frozen revision is checked in
        // the shared submit path (directory resolve / session spec).
        let operation = match request.task {
            Some(_) => GroupRoundOperation::Replay,
            None => GroupRoundOperation::New,
        };
        self.check_grants(caller, &request.group, operation).await?;
        submit::submit(self, caller, request).await
    }

    async fn group_list_tasks(
        &self,
        caller: &GatewayCaller,
        request: GroupTaskList,
    ) -> Result<GroupIndexPage, GatewayError> {
        // Every page turn revalidates the current authorization; the port
        // re-verifies the cursor's signature, subject, filter and expiry.
        self.check_grants(caller, &request.group, GroupRoundOperation::Read)
            .await?;
        let scope = scope_of(caller)?;
        // The candidate profile bound is a maximum, never silently raised or
        // shrunk-to-fit: a page above it is refused as invalid input.
        if request.limit.get() > self.config.max_group_page.get() {
            return Err(invalid("page_size_budget"));
        }
        let limit = request.limit;
        let query = GroupIndexQuery {
            statuses: request.statuses.clone(),
            limit,
            load_rows: request.include_artifacts,
        };
        let now = self.clock.now();
        let page = match &request.cursor {
            None => self.listing.first_page(&scope, &request.group, &query, now).await,
            Some(token) => self.listing.continue_page(&scope, &request.group, token, request.include_artifacts, now).await,
        }
        .map_err(group_listing_error)?;
        Ok(page)
    }

    async fn group_wait_task(
        &self,
        caller: &GatewayCaller,
        request: WaitTask,
    ) -> Result<(TaskView, bool), GatewayError> {
        if request.source != CreationSource::GroupA2a {
            return Err(invalid("group_source_required"));
        }
        let scope = scope_of(caller)?;
        let sid = session_of_task(&request.tid)?;
        self.check_source(&sid, request.source)?;
        self.check_group(&scope, &sid, &request.group).await?;
        wait::wait_view(self, &scope, request).await
    }
}

/// Read errors of the group public index: invalid cursor discipline answers
/// as invalid input, a released or unavailable cut as a named dependency the
/// caller may retry from a fresh first page; the codes stay machine-stable
/// for the adapter's HTTP mapping.
pub(crate) fn group_listing_error(error: bcs_service_api::port::repo::distributed::common::StoreError) -> GatewayError {
    use bcs_service_api::port::repo::distributed::common::StoreError;
    match error {
        StoreError::Invalid { code } => invalid(&code),
        StoreError::NotApplied { code } => GatewayError::Unavailable { code },
        StoreError::OutcomeUnknown { .. } => GatewayError::Unavailable {
            code: "group_listing_outcome_unknown".to_owned(),
        },
        StoreError::UnsupportedFormat { format } => invalid(&format!("unsupported_cut_format_{format}")),
    }
}
