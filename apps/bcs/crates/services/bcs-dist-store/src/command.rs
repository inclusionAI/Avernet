//! Command streams: the partitioned hint streams that tell Connectors a
//! dispatch needs attention.
//!
//! The Worker (which posts a hint) and every Connector (which reads all
//! partitions) must agree on the partition count and on the partition of a
//! dispatch, so both live here. The count comes from configuration; there is
//! deliberately no default. Changing it needs a coordinated restart: hints
//! are only hints, the dispatch document is the truth.
//!
//! A hint is a stream entry with one field, `did`: "go look at `disp:<did>`".
//! It is never an instruction; whoever reads it re-reads the dispatch
//! document and decides from that. The Worker posts one right after it
//! creates a dispatch, and its dispatch supervisor posts again on later
//! visits (a re-post), so repeated or late hints must be harmless.
//!
//! ```text
//! key:   bcn:v1:cmd:<cluster>:<ns>:<pool>:p<n>    e.g. bcn:v1:cmd:c01:main:a2a:p3
//! entry: id 1794370950123-0, fields {"did": "s0042.7f3a9c.b21e.n2.a1"}
//! ```
//!
//! Consumer group delivery: every Connector joins the group [`GROUP`] on
//! every partition. The store hands each new entry to exactly one consumer of
//! the group: the first group read served wins. There is no routing to the
//! Connector that holds the dispatch, so a hint for a call held by Connector
//! A may well be read by Connector B. B's claim then finds the call held by
//! another instance and acknowledges the hint; A learns of replies and
//! cancels from its own periodic re-read of the call instead (spec rule B14).

use bcs_dist_model::Did;
use sha2::{Digest as _, Sha256};
use std::num::NonZeroU32;

/// Consumer group every Connector joins on each partition. One shared group,
/// so each hint is delivered to one Connector, not to all of them.
pub const GROUP: &str = "connectors";

/// Field of a command-stream entry that holds the dispatch id; the only field
/// of a hint.
pub const DID_FIELD: &str = "did";

/// The configured partitioning of one pool's command stream.
///
/// A pool's hints are spread over N streams (partitions) so that reading and
/// trimming them scales; partition `n` is the key ending in `:p<n>`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CommandStreams {
    partitions: NonZeroU32,
}

impl CommandStreams {
    /// `partitions` is the configured count N; it must be the same in the
    /// Worker and in every Connector of the pool.
    pub fn new(partitions: NonZeroU32) -> Self {
        Self { partitions }
    }

    /// The partition count N; partitions are numbered `0..N`.
    pub fn partitions(&self) -> u32 {
        self.partitions.get()
    }

    /// Partition of a dispatch: the first eight bytes (big-endian) of
    /// `sha256(did)` modulo the partition count. Stable across processes,
    /// restarts and languages. Every hint of one dispatch lands in the same
    /// partition.
    pub fn partition_of(&self, did: &Did) -> u32 {
        let hash = Sha256::digest(did.0.as_bytes());
        let mut head = [0u8; 8];
        head.copy_from_slice(&hash[..8]);
        let partition = u64::from_be_bytes(head) % u64::from(self.partitions.get());
        // The remainder is below a u32 divisor.
        u32::try_from(partition).unwrap_or_default()
    }
}
