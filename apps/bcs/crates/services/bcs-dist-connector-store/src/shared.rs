//! Store handles and the two primitive `disp` operations every provider uses.

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

pub(crate) struct Shared {
    pub json: Arc<dyn JsonStorePlugin>,
    pub zset: Arc<dyn ZSetStorePlugin>,
    pub stream: Arc<dyn StreamStorePlugin>,
    pub membership: Arc<dyn MembershipPort>,
    pub layout: KeyLayout,
    pub clock: Arc<dyn Clock>,
    pub config: DispatchBoardConfig,
}

/// What a conditional `disp` write did.
pub(crate) enum Wrote {
    Applied(Version),
    /// The condition was not met: someone wrote since the read.
    Rejected,
    /// The write may or may not have landed; resolve by re-reading.
    Unknown,
}

impl Shared {
    pub async fn read(&self, did: &Did) -> Result<Option<DispatchDoc>, StoreError> {
        let key = self.layout.disp(did)?;
        match self.json.get_json(&key).await.map_err(|e| from_read(&e))? {
            Some(stored) => doc::decode(stored).map(Some),
            None => Ok(None),
        }
    }

    /// Writes the Connector's two objects at exactly `version`, which must be
    /// above everything the key ever had (greater wins, equal is rejected).
    pub async fn write(
        &self,
        did: &Did,
        version: Version,
        execution: &DispatchExecution,
        connector: &ConnectorState,
    ) -> Result<Wrote, StoreError> {
        let mutation = doc::connector_write(execution, connector)?;
        self.mutate(did, version, mutation).await
    }

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
