#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "runtime assertions"
)]
use bcs_dist_model::{Instance, ShardId};
use bcs_dist_worker::{HrwIdentity, WorkerConfig, rank_candidates};
use std::time::Duration;
fn instance(node: &str, inc: &str) -> Instance {
    Instance {
        node: node.into(),
        incarnation: inc.into(),
    }
}
#[test]
fn defaults_and_timing_validation() {
    let mut cfg = WorkerConfig::default();
    assert_eq!(cfg.heartbeat_interval, Duration::from_secs(1));
    assert_eq!(cfg.lease_ttl, Duration::from_secs(3));
    assert_eq!(cfg.identity, HrwIdentity::Node);
    assert!(cfg.validate().is_ok());
    cfg.request_timeout = cfg.lease_ttl;
    assert!(cfg.validate().is_err());
}
#[test]
fn node_ranking_does_not_move_on_restart_and_is_deterministic() {
    let original = vec![
        instance("a", "a1"),
        instance("b", "b1"),
        instance("c", "c1"),
    ];
    let mut reversed = original.clone();
    reversed.reverse();
    for shard in 0..128 {
        let first = rank_candidates(ShardId(shard), &original, HrwIdentity::Node);
        assert_eq!(
            first,
            rank_candidates(ShardId(shard), &reversed, HrwIdentity::Node)
        );
        let restarted = vec![
            instance("a", "a2"),
            instance("b", "b2"),
            instance("c", "c2"),
        ];
        let second = rank_candidates(ShardId(shard), &restarted, HrwIdentity::Node);
        assert_eq!(
            first.iter().map(|i| &i.node).collect::<Vec<_>>(),
            second.iter().map(|i| &i.node).collect::<Vec<_>>()
        );
    }
}
