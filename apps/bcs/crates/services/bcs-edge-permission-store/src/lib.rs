//! Database-backed implementation of the edge-permission repository ports.
//!
//! Owns the SQL for `edge_grants` + `permission_profiles` +
//! `permission_requests` + the narrow `bcs_bots` config read, and depends
//! only on the driver-level `bcs-db-api` contract. The composition root
//! decides which concrete DB plugin backs each store. Mirrors the
//! `bcs-relation-store` plumbing (MySQL + SQLite via `DbSqlFlavor`).
//!
//! Implemented ports (one responsibility file per port, split out of the
//! former over-limit `lib.rs` in plan Task 3):
//! - [`EdgeGrantRepoPort`] — `edge_grant.rs` (friend/admission edges);
//! - [`PermissionProfileRepoPort`] — `permission_profile.rs`;
//! - [`PermissionRequestRepoPort`] — `permission_request.rs`;
//! - [`BotActorConfigRepoPort`] — `bot_actor_config.rs`;
//! - [`BotAuthorityRepoPort`] — `authority` (strict owner/manager reads
//!   over the Task 2 schema; role rows deliberately never join the friend
//!   or admission paths).
//!
//! Shared DbRow/DbValue plumbing lives in `common.rs`.

mod bot_actor_config;
mod common;
mod edge_grant;
mod permission_profile;
mod permission_request;
pub mod authority;

pub use bot_actor_config::DbBotActorConfigStore;
pub use edge_grant::{
    DbEdgeGrantStore, EdgeGrantSqlFlavor, MysqlEdgeGrantRepo, SqliteEdgeGrantRepo,
};
pub use permission_profile::DbPermissionProfileStore;
pub use permission_request::DbPermissionRequestStore;
pub use authority::DbBotAuthorityStore;

pub use bcs_service_api::port::repo::EdgeGrantRepoPort;
pub use bcs_service_api::port::repo::PermissionProfileRepoPort;
pub use bcs_service_api::port::repo::PermissionRequestRepoPort;
pub use bcs_service_api::port::repo::BotActorConfigRepoPort;
pub use bcs_service_api::port::repo::BotAuthorityRepoPort;