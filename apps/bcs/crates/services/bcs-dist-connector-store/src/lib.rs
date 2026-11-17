//! Store providers of the Connector, written only against `bcs-store-api`.
//!
//! - [`queue`]: `DispatchQueuePort` — the fenced claim of a dispatch.
//! - [`held`]: `HeldDispatchPort` — the holder's writes to its dispatch.
//! - [`feed`]: `CommandFeedPort` — the command stream through a consumer group.
//! - [`held_index`]: the dispatches an instance holds.
//! - [`doc`]: the stored dispatch document.
//! - [`membership`], [`delayed`]: explicit stubs of contracts that are not final.
//!
//! Key names and versions come from `bcs-dist-store`; nothing here builds one.
//! The dispatch document is the truth: the command stream is only a hint and
//! the held index only speeds recovery up.

pub mod delayed;
pub mod doc;
pub mod feed;
pub mod held;
pub mod held_index;
pub mod membership;
pub mod queue;

mod shared;

pub use queue::{DispatchBoardConfig, StoreDispatchQueue};
