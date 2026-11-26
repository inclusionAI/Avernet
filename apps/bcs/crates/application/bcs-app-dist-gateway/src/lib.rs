//! Gateway use cases of the distributed executor plane.
//!
//! [`DistGateway`] implements `DistGatewayService` for delivery adapters. It
//! resolves the caller's scope, derives stable identities, builds the admission
//! command, and orchestrates query, subscription, waiting, cancel and listing
//! over the core service. Transport types stay in the adapters; storage stays
//! behind the core.

mod error;
mod identity;
mod submit;
mod subscribe;
mod wait;

pub use identity::{parse_session, session_of_task};

use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::application::dist_gateway::*;
use bcs_service_api::application::principal::CallerContext;
use bcs_service_api::core::dist_gateway::{
    CoreTaskQuery, DistGatewayCoreError, DistGatewayCoreService,
};
use bcs_service_api::port::dist_agent::DistSessionDirectoryPort;
use error::core_error;
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
}

impl DistGatewayConfig {
    pub fn new(shard_count: u32) -> Self {
        Self {
            shard_count,
            snapshot_cover_retry: Duration::from_millis(50),
            snapshot_cover_limit: Duration::from_secs(2),
            max_page: NonZeroU32::new(50).unwrap_or(NonZeroU32::MIN),
        }
    }
}

pub struct DistGateway {
    core: Arc<dyn DistGatewayCoreService>,
    directory: Arc<dyn DistSessionDirectoryPort>,
    config: DistGatewayConfig,
}

impl DistGateway {
    /// Fails when `shard_count` is zero.
    pub fn new(
        core: Arc<dyn DistGatewayCoreService>,
        directory: Arc<dyn DistSessionDirectoryPort>,
        _clock: Arc<dyn Clock>,
        config: DistGatewayConfig,
    ) -> Result<Self, GatewayError> {
        if config.shard_count == 0 {
            return Err(invalid("shard_count_zero"));
        }
        Ok(Self {
            core,
            directory,
            config,
        })
    }
}

impl DistGateway {
    /// A session is reached only through the target it was created for. The
    /// frozen spec holds that target; another one is answered as NotFound, so a
    /// caller learns nothing about sessions it did not address.
    pub(crate) async fn check_target(
        &self,
        scope: &TaskScope,
        sid: &Sid,
        target: &SessionTarget,
    ) -> Result<(), GatewayError> {
        let frozen = self.core.session_target(scope, sid).await.map_err(core_error)?;
        if &frozen == target {
            Ok(())
        } else {
            Err(GatewayError::NotFound)
        }
    }
}

pub(crate) fn invalid(code: &str) -> GatewayError {
    GatewayError::Invalid {
        code: code.to_owned(),
    }
}

/// The authorization scope of a caller: its tenant and a principal that names
/// the kind of identity, so a bot and a human with the same id never collide.
fn authenticated_scope(caller: &GatewayCaller) -> Result<TaskScope, GatewayError> {
    let (kind, id) = match &caller.caller {
        CallerContext::Public => return Err(GatewayError::AccessDenied),
        CallerContext::Human(actor) => ("human", &actor.actor_id),
        CallerContext::Bot(actor) => ("bot", &actor.bot_uuid),
        CallerContext::Integration(client) => ("integration", &client.client_id),
        CallerContext::Admin(actor) => ("admin", &actor.actor_id),
    };
    if caller.tenant.0.is_empty() || id.is_empty() {
        return Err(GatewayError::AccessDenied);
    }
    Ok(TaskScope {
        tenant: caller.tenant.clone(),
        principal: PrincipalId(format!("{kind}:{id}")),
    })
}

/// Keep delegated bot and organization sessions isolated across reads and writes.
/// Simple callers retain their original stored scope and request identities.
pub(crate) fn scope_of(caller: &GatewayCaller) -> Result<TaskScope, GatewayError> {
    let mut scope = authenticated_scope(caller)?;
    if caller.access.acting_agent.is_some() || caller.access.organization.is_some() {
        let identity = serde_json::json!([scope.principal.0, caller.access.acting_agent, caller.access.organization]);
        // A delegated tuple can exceed the store's key-segment limit even if
        // every component alone is valid. Hash its framed, canonical contents.
        let digest = identity::content_digest(&identity);
        scope.principal = PrincipalId(format!("delegated:{}", digest.0));
    }
    Ok(scope)
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
        target: &SessionTarget,
        tid: &Tid,
    ) -> Result<TaskView, GatewayError> {
        let scope = scope_of(caller)?;
        let sid = session_of_task(tid)?;
        self.check_target(&scope, &sid, target).await?;
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
        self.check_target(&scope, &sid, &request.target).await?;
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
}
