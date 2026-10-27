//! `ConnectService` real implementation for the edge-permission model.
//!
//! T13 of the friend→edge-permission reform (installment 3). Implements
//! [`ConnectService`] over the Installment 2 repo ports
//! (`EdgeGrantRepoPort` + `PermissionProfileRepoPort` +
//! `PermissionRequestRepoPort`) plus the T12 [`BotActorConfigRepoPort`] narrow
//! read of `bcs_bots`.
//!
//! The service holds an injected `env` (env-isolation: every repo call is
//! scoped to it) and builds friend edges per D3:
//!
//! - Human→Bot: 1 edge (caller→to_bot, ref=to_bot.default) + 1 request.
//! - Bot↔Bot: 2 edges (caller→to_bot ref=to_bot.default AND to_bot→caller
//!   ref=caller.default) + 2 requests (single approve approves both, §4.1).
//! - Human↔Human / Bot→Human: rejected (`InvalidOperation`).
//!
//! See `docs/superpowers/specs/2026-08-18-friend-edge-permission-reform.md`
//! §4.1 (connect lifecycle), §4.2 (decision tree), D3 (4-case), D11
//! (id-by-prefix), D12 (friend edge = grant_ref_id == target.default).
//!
//! `AdmissionService` (T14) has its own implementation in
//! `admission.rs`; `BotAuthorityCoreService` (Bot-authority plan
//! Task 1) lives in `authority.rs`.

mod admission;
mod authority;
mod connect;
#[cfg(test)]
mod tests;

pub use admission::DbAdmissionService;
pub use authority::BotAuthorityCoreServiceImpl;
pub use connect::DbConnectService;
#[cfg(test)]
pub(crate) use connect::{
    actor_kind_of, bot_friend_ext_no_check_scope_friend_deps,
    bot_friend_ext_view_scope_agent_friend_deps, bot_friend_ext_view_scope_user_friend_deps,
};
