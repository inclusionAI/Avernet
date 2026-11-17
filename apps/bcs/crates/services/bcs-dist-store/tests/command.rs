//! Command-stream partitioning shared by the Worker and the Connectors.
#![allow(clippy::unwrap_used, reason = "tests")]

use bcs_dist_model::Did;
use bcs_dist_store::command::{CommandStreams, DID_FIELD, GROUP};
use std::num::NonZeroU32;

fn streams(n: u32) -> CommandStreams {
    CommandStreams::new(NonZeroU32::new(n).unwrap())
}

#[test]
fn partition_values_are_fixed() {
    // Fixed vectors: a change here strands hints between Worker and Connector.
    let did = Did("s0042.7f3a9c.b21e.n2.a1".into());
    let hash = sha256_head(&did.0);
    assert_eq!(streams(8).partition_of(&did), (hash % 8) as u32);
    assert_eq!(streams(8).partition_of(&did), streams(8).partition_of(&did));
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
