#![allow(clippy::unwrap_used, reason = "real reconstructed reader conformance")]
mod processor_support;
#[path = "../src/rebuilding_feed.rs"]
mod rebuilding_feed;
mod support;
use bcs_service_api::port::repo::distributed::inbox::*;
use bcs_test_support::contract::distributed::inbox::shard_feed_port_contract_tests;
use processor_support::*;
use rebuilding_feed::RebuildingFeed;
use std::{num::NonZeroU32, sync::Arc, time::Duration};

#[tokio::test]
async fn conformance_rebuilding_feed_retains_replayable_input() {
    let f = Fixture::new(Arc::new(FinishingRunner::default())).await;
    f.admit("rebuilding-feed").await;
    let inner = f.inbox.open(spec().shard).await.unwrap();
    let mut feed = RebuildingFeed::new(inner, vec![(spec().sid, None)], Duration::from_secs(1));
    let batch = feed
        .next(
            ReadLimits {
                max_sessions: NonZeroU32::MIN,
                max_entries: NonZeroU32::MIN,
                max_bytes: NonZeroU32::new(1024 * 1024).unwrap(),
            },
            Duration::ZERO,
        )
        .await
        .unwrap();
    let expected = batch.sessions[0].entries[0].clone();
    shard_feed_port_contract_tests(&mut feed, &expected)
        .await
        .unwrap();
}
