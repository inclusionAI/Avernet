//! Store handles and the two primitive `disp` operations every provider uses.
//!
//! [`Shared`] is built once by `StoreDispatchQueue::new` and shared (`Arc`)
//! by the queue, every held-dispatch handle and the command feed.
//!
//! The `disp` key is `bcn:v1:<cluster>:<ns>:s<shard>:disp:<did>`, e.g.
//! `bcn:v1:c01:main:s0042:disp:s0042.7f3a9c.b21e.n2.a1`; the value is the
//! dispatch JSON described in `doc.rs`.
//!
//! Every `disp` write here is conditional with `WriteCondition::Newer(v)`:
//! it applies only when `v` is above every version the document ever had,
//! and then `v` becomes its version. Equal is rejected. This is what fences
//! a holder: a claim jumps to `epoch x 10^9`, other holder writes use
//! `read + 1`; whoever wrote first wins and the other write is rejected.

use crate::doc::{self, ConnectorState, DispatchDoc};
use crate::queue::DispatchBoardConfig;
use bcs_dist_model::{Did, DispatchExecution};
use bcs_dist_store::error::{from_read, from_write};
use bcs_dist_store::inbox::Clock;
use bcs_dist_store::layout::KeyLayout;
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_service_api::port::repo::distributed::ownership::MembershipPort;
use bcs_store_api::json::JsonStorePlugin;
use bcs_store_api::stream::StreamStorePlugin;
use bcs_store_api::zset::ZSetStorePlugin;
use bcs_store_api::{Expiry, StoreError as PluginError, Version, WriteCondition, WriteOutcome};
use std::sync::Arc;

/// Everything the providers of this crate need, shared by all of them.
pub(crate) struct Shared {
    /// The `disp` documents.
    pub json: Arc<dyn JsonStorePlugin>,
    /// The held index (one sorted set per command partition).
    pub zset: Arc<dyn ZSetStorePlugin>,
    /// The command-stream partitions.
    pub stream: Arc<dyn StreamStorePlugin>,
    /// Asked only whether another holder is dead before a takeover.
    pub membership: Arc<dyn MembershipPort>,
    /// Builds every key name; validates ids before they reach the store.
    pub layout: KeyLayout,
    /// Wall clock; gives the held-index score (first listed, in ms).
    pub clock: Arc<dyn Clock>,
    pub config: DispatchBoardConfig,
}

/// What a conditional `disp` write did.
pub(crate) enum Wrote {
    /// The write landed; the document now has this version.
    Applied(Version),
    /// The condition was not met: someone wrote since the read.
    Rejected,
    /// The write may or may not have landed (e.g. the connection broke after
    /// sending); resolve by re-reading. It may also land later.
    Unknown,
}

impl Shared {
    /// Reads and decodes `disp:<did>`; `None` when it does not exist.
    /// Unfenced: callers check the holder themselves.
    pub async fn read(&self, did: &Did) -> Result<Option<DispatchDoc>, StoreError> {
        let key = self.layout.disp(did)?;
        match self.json.get_json(&key).await.map_err(|e| from_read(&e))? {
            Some(stored) => doc::decode(stored).map(Some),
            None => Ok(None),
        }
    }

    /// Writes the Connector's two objects at exactly `version`, which must be
    /// above the current persistent object revision (equal is rejected).
    pub async fn write(
        &self,
        did: &Did,
        version: Version,
        old: &DispatchDoc,
        execution: &DispatchExecution,
        connector: &ConnectorState,
    ) -> Result<Wrote, StoreError> {
        let mutation = doc::connector_write(old, execution, connector)?;
        self.mutate(did, version, mutation).await
    }

    /// Applies `mutation` to `disp:<did>` if `version` is above the stored
    /// one. Only a store error of unknown outcome becomes [`Wrote::Unknown`];
    /// every other store error is returned as an error.
    pub async fn mutate(
        &self,
        did: &Did,
        version: Version,
        mutation: bcs_store_api::json::JsonMutation,
    ) -> Result<Wrote, StoreError> {
        let key = self.layout.disp(did)?;
        let outcome = self
            .json
            .mutate_json(&key, WriteCondition::Newer(version), mutation, Expiry::Preserve)
            .await;
        match outcome {
            Ok(WriteOutcome::Applied { version }) => Ok(Wrote::Applied(version)),
            Ok(WriteOutcome::ConditionNotMet) => Ok(Wrote::Rejected),
            Err(PluginError::OutcomeUnknown(_)) => Ok(Wrote::Unknown),
            Err(other) => Err(from_write(&other, &did.0)),
        }
    }
}
