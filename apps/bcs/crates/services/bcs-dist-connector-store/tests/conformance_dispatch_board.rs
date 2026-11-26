//! Conformance entries: the dispatch-board providers (`StoreDispatchQueue`,
//! `StoreHeldDispatch`, `StoreCommandFeed`) against the shared port harnesses.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "tests")]

mod common;

use bcs_dist_model::*;
use bcs_test_support::contract::distributed::dispatch::{
    command_feed_port_contract_tests, dispatch_queue_port_contract_tests, held_dispatch_port_contract_tests,
};
use common::*;
use bcs_service_api::port::repo::distributed::dispatch::DispatchQueuePort;
use std::sync::Arc;

#[tokio::test]
async fn conformance_store_dispatch_queue() {
    let store = Arc::new(Recorder::default());
    WorkerFixture::new(&store).create().await;
    dispatch_queue_port_contract_tests(&queue(&store), claim_request(&instance("conn-03")))
        .await
        .unwrap();
}

#[tokio::test]
async fn conformance_store_command_feed() {
    let store = Arc::new(Recorder::default());
    let worker = WorkerFixture::new(&store);
    worker.notify().await;
    let mut feed = queue(&store).notifications(pool(), instance("conn-03")).await.unwrap();
    feed.assign(&[0]);
    command_feed_port_contract_tests(feed.as_mut(), &did()).await.unwrap();
}

#[tokio::test]
async fn conformance_store_held_dispatch() {
    let store = Arc::new(Recorder::default());
    let worker = WorkerFixture::new(&store);
    worker.create().await;
    let mut held = acquire(&queue(&store), &instance("conn-03")).await;
    held.begin_send(start_op()).await.unwrap();
    held.record_acceptance(accepted(start_op())).await.unwrap();
    // The Connector persists the send before observing remote acceptance.
    worker.push(pending(1)).await;
    held.begin_send(input_op(1)).await.unwrap();
    held_dispatch_port_contract_tests(held.as_mut(), &worker, committed_input(1), accepted(input_op(1)))
        .await
        .unwrap();
    assert_eq!(held.view().execution.confirmed_input_through, Some(InputSeq(1)));
}
