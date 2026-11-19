use super::ContractResult;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{common::*, inbox::*};
use std::{num::NonZeroU32, time::Duration};

/// Fresh request and conflicting normalized business content under the SAME ID.
pub async fn inbox_producer_port_contract_tests(
    port: &dyn InboxProducerPort,
    fresh: AdmitRequest,
    changed: AdmitRequest,
) -> ContractResult {
    let receipt = match port.admit(fresh.clone()).await? {
        AdmitOutcome::Accepted(receipt) => receipt,
        other => panic!("fresh admission was not accepted: {other:?}"),
    };
    assert_eq!(port.admit(fresh).await?, AdmitOutcome::Duplicate(receipt));
    assert_eq!(port.admit(changed).await?, AdmitOutcome::ContentConflict);
    Ok(())
}

/// Fixture has one pending session and a real committed batch/acknowledgement.
pub async fn inbox_consumer_port_contract_tests(
    port: &dyn InboxConsumerPort,
    shard: ShardId,
    receipt: &DeliveryReceipt,
    proof: &CommitReceipt,
) -> ContractResult {
    let pending = port
        .enumerate_pending(
            shard,
            PageRequest {
                after: None,
                limit: NonZeroU32::new(128).expect("positive page bound"),
            },
        )
        .await?;
    assert!(pending.items.contains(&proof.sid));
    port.confirm_committed(receipt, proof).await?;
    // Acknowledging again is safe, even on backends without a physical ACK command.
    port.confirm_committed(receipt, proof).await?;
    assert!(
        !port
            .has_unconsumed(&proof.sid, proof.consumed.as_ref())
            .await?
    );
    Ok(())
}

/// Fixture contains exactly one retained unconsumed input for this session.
pub async fn shard_feed_port_contract_tests(
    feed: &mut dyn ShardFeedPort,
    expected: &InputEnvelope,
) -> ContractResult {
    feed.watch(expected.sid.clone(), None).await?;
    let batch = feed
        .next(
            ReadLimits {
                max_sessions: NonZeroU32::MIN,
                max_entries: NonZeroU32::MIN,
                max_bytes: NonZeroU32::new(1024 * 1024).expect("positive byte bound"),
            },
            Duration::from_secs(1),
        )
        .await?;
    assert!(batch.sessions.iter().any(|s| s.entries.contains(expected)));
    feed.unwatch(&expected.sid).await?;
    Ok(())
}

/// New admission between observing and settling MUST invalidate the old decision.
pub async fn inbox_scheduling_port_contract_tests(
    schedule: &dyn InboxSchedulingPort,
    producer: &dyn InboxProducerPort,
    owner: &SessionOwnership,
    arrival: AdmitRequest,
) -> ContractResult {
    let observed = match schedule.observe(owner).await? {
        ScheduleObservation::Observed(token) => token,
        ScheduleObservation::Fenced => panic!("fixture owner was already fenced"),
    };
    assert!(matches!(
        producer.admit(arrival).await?,
        AdmitOutcome::Accepted(_)
    ));
    assert_eq!(
        schedule
            .settle(SettleSession {
                owner: owner.clone(),
                observed,
                next_check: None
            })
            .await?,
        SettleOutcome::NeedsRecheck
    );
    Ok(())
}
