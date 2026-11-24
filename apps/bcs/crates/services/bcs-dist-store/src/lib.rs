//! Shared storage pieces of the distributed executor plane (Gateway, Connector,
//! Worker), written against public `bcs-store-api` / `bcs-db-api` plugins.
//!
//! - [`archive`]: immutable Result/Blob persistence and committed-effect evidence.
//! - [`task_feed`]: committed output, snapshot publication and logical retention.
//! - [`layout`]: every key name; no shard hash tag (TBase replicates writes
//!   synchronously, so a shard's keys need not share a slot).
//! - [`version`]: the conditional-write versions ("ts") of the design.
//! - [`error`]: store errors mapped onto the port `StoreError`.
//! - [`capability`]: fail fast when the store lacks a required capability.
//! - [`identity`]: stable digests for ids and content.
//! - [`codec`]: stored shapes of `admit`, `inbox`, `wake`, `spec`, `task` and `ev`.
//! - [`inbox`]: the `InboxProducerPort` provider (admission write order).
//! - [`command`]: command-stream partitions shared by the Worker and Connectors.

pub mod archive;
pub mod capability;
pub mod codec;
pub mod command;
pub mod dispatch;
pub mod error;
pub mod evidence;
pub mod group_admission;
pub mod identity;
pub mod inbox;
pub mod layout;
pub mod node_history;
pub mod task_feed;
pub mod workflow_revision;
pub mod version;

pub mod coordination;

pub mod budget;
