//! Command streams: the partitioned hint streams that tell Connectors a
//! dispatch needs attention.
//!
//! The Worker (which posts a hint) and every Connector (which reads all
//! partitions) must agree on the partition count and on the partition of a
//! dispatch, so both live here. The count comes from configuration; there is
//! deliberately no default. Changing it needs a coordinated restart: hints
//! are only hints, the dispatch document is the truth.

use bcs_dist_model::Did;
use sha2::{Digest as _, Sha256};
use std::num::NonZeroU32;

/// Consumer group every Connector joins on each partition.
pub const GROUP: &str = "connectors";

/// Field of a command-stream entry that holds the dispatch id.
pub const DID_FIELD: &str = "did";

/// The configured partitioning of one pool's command stream.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CommandStreams {
    partitions: NonZeroU32,
}

impl CommandStreams {
    pub fn new(partitions: NonZeroU32) -> Self {
        Self { partitions }
    }

    pub fn partitions(&self) -> u32 {
        self.partitions.get()
    }

    /// Partition of a dispatch: the first eight bytes (big-endian) of
    /// `sha256(did)` modulo the partition count. Stable across processes,
    /// restarts and languages.
    pub fn partition_of(&self, did: &Did) -> u32 {
        let hash = Sha256::digest(did.0.as_bytes());
        let mut head = [0u8; 8];
        head.copy_from_slice(&hash[..8]);
        let partition = u64::from_be_bytes(head) % u64::from(self.partitions.get());
        // The remainder is below a u32 divisor.
        u32::try_from(partition).unwrap_or_default()
    }
}
