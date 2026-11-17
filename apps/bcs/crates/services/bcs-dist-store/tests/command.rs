//! Command-stream partitioning shared by the Worker and the Connectors.
#![allow(clippy::unwrap_used, reason = "tests")]

use bcs_dist_model::Did;
use bcs_dist_store::command::{CommandStreams, DID_FIELD, GROUP};
use std::num::NonZeroU32;

/// (dispatch id, partition count, partition).
const VECTORS: [(&str, u32, u32); 5] = [
    ("s0042.7f3a9c.b21e.n2.a1", 8, 5),
    ("s0042.7f3a9c.b21e.n2.a1", 3, 1),
    ("s0001.000001.0001.n1.a1", 16, 12),
    ("s0007.abcdef.1234.n9.a3", 5, 4),
    ("s0042.7f3a9c.b21e.n1.a1", 1, 0),
];

fn streams(n: u32) -> CommandStreams {
    CommandStreams::new(NonZeroU32::new(n).unwrap())
}

#[test]
fn partition_values_are_fixed() {
    // Literal vectors: this mapping is a contract between the Worker (which
    // posts) and every Connector (which reads), in any language. A change
    // here strands hints.
    for (did, partitions, expected) in VECTORS {
        assert_eq!(streams(partitions).partition_of(&Did(did.into())), expected, "{did} / {partitions}");
    }
    assert_eq!(streams(8).partitions(), 8);
    assert_eq!(GROUP, "connectors");
    assert_eq!(DID_FIELD, "did");
}

#[test]
fn partition_is_in_range_and_spreads() {
    let s = streams(4);
    let mut seen = [false; 4];
    for i in 0..200 {
        let p = s.partition_of(&Did(format!("s0001.a.b.n{i}.a1")));
        assert!(p < 4);
        seen[p as usize] = true;
    }
    assert!(seen.iter().all(|hit| *hit));
}

#[test]
fn single_partition_is_always_zero() {
    assert_eq!(streams(1).partition_of(&Did("s0001.x.y.n1.a1".into())), 0);
}

fn sha256_head(text: &str) -> u64 {
    use sha2::{Digest, Sha256};
    let hash = Sha256::digest(text.as_bytes());
    u64::from_be_bytes(hash[..8].try_into().unwrap())
}

#[test]
fn the_vectors_follow_the_documented_formula() {
    for (did, partitions, expected) in VECTORS {
        assert_eq!((sha256_head(did) % u64::from(partitions)) as u32, expected);
    }
}
