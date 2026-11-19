//! Shared storage pieces of the distributed executor plane (Gateway, Connector,
//! Worker), written only against `bcs-store-api`.
//!
//! - [`layout`]: every key name; no shard hash tag (TBase replicates writes
//!   synchronously, so a shard's keys need not share a slot).
//! - [`version`]: the conditional-write versions ("ts") of the design.
//! - [`error`]: store errors mapped onto the port `StoreError`.
//! - [`capability`]: fail fast when the store lacks a required capability.
//! - [`identity`]: stable digests for ids and content.
//! - [`codec`]: stored shapes of `admit`, `inbox`, `wake`, `spec`, `task` and `ev`.
//! - [`inbox`]: the `InboxProducerPort` provider (admission write order).

pub mod capability;
pub mod codec;
pub mod error;
pub mod identity;
pub mod inbox;
pub mod layout;
pub mod version;

pub mod coordination;
