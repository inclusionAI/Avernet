//! Strict bot authority reads over the Task 2 schema (plan Task 3).
//!
//! [`DbBotAuthorityStore`] implements `BotAuthorityRepoPort` for the
//! authority slice of `edge_grants` + `bcs_bots.ownership_version`:
//! - reads are env-bound to the store instance (the composition root knows
//!   the process env; callers never inject env per request);
//! - role rows decode STRICTLY through [`codec`] (fail closed, no
//!   serde-default into a valid role);
//! - the approved owner slot is validated (unique; initialized Bots missing
//!   it are corrupt);
//! - `roles_for` correlates by full (user_id, bot_id) pairs in ONE batched
//!   statement (never two independent IN sets / per-pair N+1).
//!
//! The legacy friend/admission paths (`edge_grant.rs`) deliberately keep
//! rejecting the `owner`/`manager` kinds: role rows never turn into friend or
//! runtime grants. Only this module reads them.

pub(crate) mod audit;
mod codec;
mod manager;
mod reads;
mod team_sync;
mod team_sync_sql;
mod transfer_create;
mod transfer_decide;
mod transfer_query;

pub use reads::DbBotAuthorityStore;