//! Shared fixtures of the plan T9 companion (same-process functional
//! assembly of the FSM-Group-as-A2A-Agent surface).
//!
//! Discipline (plan T9 + the 2026-11-25 split):
//!
//! - the joint roles are REAL crate assemblies — CoordinationStore,
//!   StoreInboxProducer, StoreTaskFeed, StoreDispatch, StoreDispatchQueue,
//!   WorkerRuntime/WorkerProcessor, DispatchSupervisor, ConnectorEngine,
//!   DistGateway/StoreTaskReader over the strict-round spine — all built by
//!   `server::group_a2a_wiring::assemble_inprocess`, not rebuilt here;
//! - the downstream A2A objects are controllable fixtures — the
//!   protocol-allowed boundary: a journal that executes each remote start
//!   exactly once (idempotent dedupe replay) and completes on demand;
//! - the group catalog (directory + D0 policy) is the host-registered
//!   fixture pair the composition root injects in the same process;
//! - everything here is same-process functional evidence; nothing replaces
//!   the blocked(P) external persistent provider, cross-process crash
//!   matrix, MySQL acceptance or capacity certification.
#![allow(
    dead_code,
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "companion fixtures with joint assertions"
)]

use std::collections::{BTreeMap, HashMap};
use std::future::pending;
use std::sync::atomic::{AtomicBool, AtomicU8, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, Weak};
use std::time::Duration;

use async_trait::async_trait;
use bcs::GroupA2aConfig;
use bcs::server::group_a2a_wiring::{GroupA2aAssembly, GroupA2aHost, GroupA2aProvider};
use bcs_dist_model::*;
use bcs_domain::collaboration::{
    ProjectionPolicy, StateMachineDefaults, StateMachineDefinition, StateMachineGraphMode,
    StateMachineNodeDefinition, StateMachineNodeKind, StateMachineTransition,
};
use bcs_service_api::application::dist_gateway::{GatewayCaller, GroupGrantPolicy, GroupRoundGrants};
use bcs_service_api::application::principal::{BotActor, CallerContext};
use bcs_service_api::port::dist_agent::{
    AgentReadError, CancelOutcome, CallAuthority, DistAgentClientPort, DistGroupDirectoryPort,
    FrozenGroupDefinition, GroupDirectoryError, QueryOutcome, RemoteEventStream, RemoteQuery,
    SendOutcome, StableSendRequest,
};
use bcs_service_api::port::repo::distributed::group_round::{
    GroupRoundView, GroupStartRequest,
};
use bcs_service_api::port::repo::distributed::ownership::{
    HandoffOutcome, HandoffRequest, MemberRecord, ShardClaim,
};
use bcs_store_api::document::DocumentStorePlugin;
use bcs_test_support::store_model::Model;
use serde_json::{Value, json};

pub const TENANT: &str = "tenant-companion";
pub const PRINCIPAL: &str = "caller-companion";
pub const GROUP: &str = "grp-research-01";
pub const NODE_RESEARCH: &str = "research";
pub const NODE_REVIEW: &str = "review";
pub const POOL: &str = "companion-group-a2a";
pub const WORKFLOW_ID: &str = "wf-research";
pub const WORKFLOW_REVISION: &str = "1";
/// The strict session of the companion's two-node group: `s{shard}.g<16hex>`
/// — the group-entrance namespace the source isolation of the public
/// listing requires (`CreationSource::of_sid`), with the shard lettering
/// the KeyLayout derives shard ids from.
pub const SID: &str = "s01.g0123456789abcdef";

pub const NS: &str = "companion-group-a2a";

/// The standard supervisor-on, cursor-secret-present assembly config.
pub fn enabled_config() -> GroupA2aConfig {
    GroupA2aConfig {
        enabled: true,
        cursor_secret: Some("companion-cursor-secret".to_string()),
        namespace: NS.to_string(),
        ..GroupA2aConfig::default()
    }
}

pub fn scope() -> TaskScope {
    // The production group scope of a bot caller: `scope_of(caller)` spells
    // the principal with the `bot:` namespace prefix (the listing's
    // source-isolation keys derive from this exact spelling).
    TaskScope {
        tenant: TenantId(TENANT.into()),
        principal: PrincipalId(format!("bot:{PRINCIPAL}")),
    }
}

pub fn sid() -> Sid {
    Sid(SID.into())
}

pub fn caller() -> GatewayCaller {
    GatewayCaller {
        tenant: TenantId(TENANT.into()),
        caller: CallerContext::Bot(BotActor {
            bot_uuid: PRINCIPAL.into(),
        }),
    }
}

pub fn group_id() -> GroupId {
    GroupId(GROUP.into())
}

fn now_ms() -> TimestampMillis {
    use bcs_dist_store::inbox::Clock as _;
    bcs_dist_store::inbox::SystemClock.now()
}

/// The authoritative dispatch identity of one node attempt: the ruling-1
/// spelling `<tid>.fsm:<generation>:<node>:<attempt>` — the same face the
/// supervisor, the queue and the held-dispatch port read.
pub fn node_did(tid: &Tid, generation: u64, node: &str) -> Did {
    Did(format!("{}.fsm:{generation}:{node}:1", tid.0))
}

// ---------------------------------------------------------------------------
// Submodules by responsibility (CI 1000-line file limit):
// catalog — the host fixtures of the published group (workflows, D0
//           policy, the in-process peer transport, fast-deadline variant);
// journal — the controllable downstream A2A ports;
// world    — the assembled CompanionWorld around the generic roles.
// ---------------------------------------------------------------------------
mod catalog;
mod journal;
mod world;

pub use catalog::{AllowPolicy, Catalog, FastDeadlineCatalog, Peers};
pub use journal::{DownstreamJournal, JournalPlan};
pub use world::CompanionWorld;
