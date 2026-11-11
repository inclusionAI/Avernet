//! Database-backed implementation of the `edge_grants` repository port.
//!
//! Owns the SQL for `edge_grants` + `permission_profiles` (default-profile
//! cache) and depends only on the driver-level `bcs-db-api` contract. The
//! composition root decides which concrete DB plugin backs it. Mirrors the
//! `bcs-relation-store` plumbing (MySQL + SQLite via `DbSqlFlavor`).
//!
//! Implements [`EdgeGrantRepoPort`] (installment 1) for [`DbEdgeGrantStore`]
//! and [`PermissionProfileRepoPort`] (T8) for [`DbPermissionProfileStore`].
//! `PermissionRequestRepoPort` (T9) will be added to this same crate later.
//!
//! The bot-authority store ([`DbBotAuthorityStore`]) lives in the
//! `authority` module; it implements `BotAuthorityRepoPort` over
//! `edge_grants` / `bcs_bots` / the two authority tables.

pub mod authority;
/// In-process implementations of the four edge-permission repo ports, used by
/// the in-memory bootstrap path and by tests.
pub mod memory;

pub use authority::DbBotAuthorityStore;
/// Pure SQL lifecycle step builders (Task 4): reused by
/// `bcs-bot-store`'s registration composites, never executed here.
pub use authority::lifecycle;

use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use async_trait::async_trait;
use bcs_db_api::{DbError, DbExecuteResult, DbPlugin, DbRow, DbSqlFlavor, DbStatement, DbValue};
use bcs_domain::edge_permission::{
    BotActorConfig, EdgeGrant, EdgeStatus, GrantKind, OriginatorPolicyType, PermissionProfile,
    ProfileStatus, PermissionRequest, RequestKind, RequestStatus,
};
pub use bcs_service_api::port::repo::EdgeGrantRepoPort;
use bcs_service_api::port::repo::edge_grant::{FriendIdsPage, FriendListQuery};
use bcs_domain::ActorKind;
pub use bcs_service_api::port::repo::PermissionProfileRepoPort;
pub use bcs_service_api::port::repo::PermissionRequestRepoPort;
pub use bcs_service_api::port::repo::BotActorConfigRepoPort;
use bcs_service_api::{ServiceError, ServiceResult};
use sha2::{Digest, Sha256};
use tracing::warn;

pub type EdgeGrantSqlFlavor = DbSqlFlavor;

/// MySQL-backed edge-grant repository.
pub type MysqlEdgeGrantRepo = DbEdgeGrantStore;

/// SQLite-backed edge-grant repository.
pub type SqliteEdgeGrantRepo = DbEdgeGrantStore;

mod edge_grants;
pub use edge_grants::DbEdgeGrantStore;
mod profiles;
pub use profiles::DbPermissionProfileStore;
mod requests;
pub use requests::DbPermissionRequestStore;
mod bot_config;
pub use bot_config::DbBotActorConfigStore;
mod mapping;
use mapping::*;

#[cfg(test)]
mod tests;
