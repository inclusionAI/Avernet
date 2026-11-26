use super::ContractResult;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{common::*, dispatch::*};
use std::{num::NonZeroU32, time::Duration};

pub async fn dispatch_control_port_contract_tests(
    port: &dyn DispatchControlPort,
    fresh: CreateCommittedDispatch,
    cancellation: CommittedCancelIntent,
) -> ContractResult {
    assert_eq!(port.create(fresh.clone()).await?, IdempotentWrite::Applied);
    assert_eq!(
        port.create(fresh.clone()).await?,
        IdempotentWrite::AlreadyApplied
    );
    assert!(port.get(&fresh.spec.did).await?.is_some());
    assert_eq!(
        port.request_cancel(cancellation.clone()).await?,
        ControlOutcome::Applied
    );
    assert_eq!(
        port.request_cancel(cancellation).await?,
        ControlOutcome::AlreadyApplied
    );
    Ok(())
}

/// The fixture dispatch exists without a holder; repeated claim must not spawn twice.
pub async fn dispatch_queue_port_contract_tests(
    port: &dyn DispatchQueuePort,
    request: DispatchClaimRequest,
) -> ContractResult {
    let held = match port.claim(request.clone()).await? {
        ClaimOutcome::Acquired(held) => held,
        _ => panic!("fresh dispatch was not acquired"),
    };
    match port.claim(request).await? {
        ClaimOutcome::OwnedByMe(holder) => assert_eq!(&holder, held.holder()),
        _ => panic!("same owner must not receive a second actor"),
    }
    Ok(())
}

/// The feed has the expected dispatch's partition assigned.
pub async fn command_feed_port_contract_tests(
    feed: &mut dyn CommandFeedPort,
    expected: &Did,
) -> ContractResult {
    let commands = feed
        .receive(NonZeroU32::MIN, Duration::from_secs(1))
        .await?;
    assert_eq!(commands.len(), 1);
    let delivery = &commands[0];
    assert_eq!(&delivery.did, expected);
    feed.ack(delivery).await?;
    feed.ack(delivery).await?;
    Ok(())
}

/// Input is already queued and remotely accepted. Replay after dequeue stays deduped.
pub async fn held_dispatch_port_contract_tests(
    held: &mut dyn HeldDispatchPort,
    control: &dyn DispatchControlPort,
    input: CommittedDispatchInput,
    accepted: AcceptanceEvidence,
) -> ContractResult {
    let seq = input.input.seq;
    assert_eq!(
        held.confirm_input(seq, accepted).await?,
        IdempotentWrite::Applied
    );
    assert_eq!(
        control.push_input(input).await?,
        ControlOutcome::AlreadyApplied
    );
    let view = held.refresh().await?;
    assert_eq!(view.execution.confirmed_input_through, Some(seq));
    assert!(!view.control.pending_input.iter().any(|p| p.seq == seq));
    Ok(())
}
