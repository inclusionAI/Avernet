#![allow(clippy::unwrap_used, reason = "actual Dispatch provider conformance")]
mod common;
mod dispatch_support;
mod task_feed_support;
use bcs_dist_model::*;
use bcs_dist_store::dispatch::StoreDispatch;
use bcs_service_api::port::repo::distributed::{dispatch::*, session::*};
use bcs_test_support::contract::distributed::dispatch::*;
use dispatch_support::*;
#[tokio::test]
async fn conformance_dispatch_queue_and_commands() {
    let f = Fixture::new().await;
    f.create().await;
    let port = &f.queue;
    dispatch_queue_port_contract_tests(
        port,
        DispatchClaimRequest {
            did: spec().did,
            me: me(),
            request_id: RequestId("contract-claim".into()),
        },
    )
    .await
    .unwrap();
    f.board.notify(&spec().pool, &spec().did).await.unwrap();
    // Commands is the real private command-feed implementation returned here.
    let mut feed = port.notifications(spec().pool, me()).await.unwrap();
    command_feed_port_contract_tests(feed.as_mut(), &spec().did)
        .await
        .unwrap();
}
#[tokio::test]
async fn conformance_dispatch_proof_and_cancel() {
    let f = Fixture::new().await;
    let definition = spec();
    let intent = CancelIntent {
        request_id: RequestId("cancel".into()),
        reason: None,
    };
    let sources = f
        .base
        .commit(
            1,
            vec![
                EffectKind::CreateDispatch {
                    spec: definition.clone(),
                },
                EffectKind::RequestCancel {
                    did: definition.did.clone(),
                    intent: intent.clone(),
                },
            ],
        )
        .await;
    let port: &StoreDispatch = &f.board;
    dispatch_control_port_contract_tests(
        port,
        CreateCommittedDispatch {
            source: sources[0].clone(),
            spec: definition.clone(),
        },
        CommittedCancelIntent {
            source: sources[1].clone(),
            did: definition.did,
            intent,
        },
    )
    .await
    .unwrap();
}
#[tokio::test]
async fn conformance_held_input_survives_dequeue() {
    let f = Fixture::new().await;
    f.create().await;
    let input = f.input(2, 1).await;
    f.board.push_input(input.clone()).await.unwrap();
    // Held is the real private holder implementation returned by claim.
    let mut held = f.claim().await;
    held.begin_send(start(), proof(&start())).await.unwrap();
    held.record_acceptance(acceptance(start())).await.unwrap();
    let operation = SendOperation::Input {
        seq: InputSeq(1),
        request_id: RequestId("input-one".into()),
    };
    held.begin_send(operation.clone(), proof(&operation)).await.unwrap();
    held_dispatch_port_contract_tests(held.as_mut(), &f.board, input, acceptance(operation))
        .await
        .unwrap();
}
