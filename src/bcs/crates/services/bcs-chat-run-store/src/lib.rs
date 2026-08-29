//! Direct Chat run persistence stores for BCS.
//!
//! Implementations of [`bcs_service_api::port::repo::ChatRunRepoPort`]:
//! - [`MemoryChatRunRepo`] — in-process, behavior-equivalent to the pre-#1546
//!   `ChatRunStore`, used by dev/test and as the default config selection.
//! - `SqlChatRunRepo` (added in a follow-up task) — MySQL-authoritative +
//!   Redis-hot-cache, restart- and replica-safe.
//!
//! The run state machine (terminal guard, allowed transitions, version bump)
//! lives in the `ChatRunStore` engine in `bcs-message-flow`; this crate is a
//! thin persistence + compare-and-set + scan layer. See
//! `docs/superpowers/specs/2026-08-27-bcs-run-governance-design.md`.

pub mod memory;
pub mod sql;

pub use memory::MemoryChatRunRepo;
pub use sql::SqlChatRunRepo;

pub use bcs_service_api::port::repo::{
    CasOutcome, ChatRunCompletionPolicy, ChatRunRecord, ChatRunRepoError, ChatRunRepoPort,
    ChatRunState, MAX_CONTENT_BYTES,
};