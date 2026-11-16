use super::ContractResult;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{
    common::*,
    inbox::{AdmitOutcome, AdmitRequest, InboxProducerPort},
    ownership::*,
    result::*,
    session::*,
};
use std::time::Duration;

/// Fresh session fixture, with spec/task created by admission and no checkpoint.
/// These readbacks validate persistence; they are not required runtime round trips.
pub async fn session_spec_admission_contract_tests(
    producer: &dyn InboxProducerPort,
    store: &dyn SessionStorePort,
    request: AdmitRequest,
) -> ContractResult {
    let expected = request.create_session.clone().expect("new-session fixture");
    let sid = &request.input.sid;
    assert_eq!(store.get_spec(sid).await?, None);
    let receipt = match producer.admit(request.clone()).await? {
        AdmitOutcome::Accepted(receipt) => receipt,
        other => panic!("fresh admission was not accepted: {other:?}"),
    };
    assert_eq!(store.get_spec(sid).await?, Some(expected.clone()));
    assert_eq!(
        producer.admit(request.clone()).await?,
        AdmitOutcome::Duplicate(receipt)
    );
    assert_eq!(store.get_spec(sid).await?, Some(expected));
    Ok(())
}

/// Request is the next valid commit for an acquired, isolated session.
pub async fn session_store_port_contract_tests(
    port: &dyn SessionStorePort,
    request: CommitRequest,
) -> ContractResult {
    assert_eq!(
        request.expected_commit_seq.0.checked_add(1),
        Some(request.candidate.commit_seq.0)
    );
    let proof = match port.commit(request.clone()).await? {
        CommitOutcome::Committed(proof) => proof,
        other => panic!("first commit failed: {other:?}"),
    };
    assert_eq!(proof.sid, request.owner.sid);
    assert_eq!(proof.owner, request.owner.owner);
    assert_eq!(proof.sequence, request.candidate.commit_seq);
    assert_eq!(proof.commit_id, request.commit_id);
    assert_eq!(proof.content_digest, request.content_digest);
    assert_eq!(proof.consumed, request.candidate.consumed);
    assert_eq!(
        port.commit(request.clone()).await?,
        CommitOutcome::AlreadyCommitted(proof.clone())
    );
    assert_eq!(
        port.resolve_commit(&request.owner, &request.commit_id, &request.content_digest)
            .await?,
        CommitResolution::Committed(proof)
    );
    let loaded = port.load(&request.owner.sid).await?;
    assert_eq!(
        loaded.map(|v| v.checkpoint),
        Some(request.candidate.clone())
    );
    let mut unsealed = request.clone();
    unsealed.owner.owner.epoch = WorkerEpoch(
        unsealed
            .owner
            .owner
            .epoch
            .0
            .checked_add(1)
            .expect("fixture epoch"),
    );
    unsealed.commit_id.0.push_str("-unsealed");
    let mut competing = request;
    competing.commit_id.0.push_str("-competing");
    assert_eq!(
        port.commit(competing).await?,
        CommitOutcome::CommitSeqConflict
    );
    assert_eq!(port.commit(unsealed).await?, CommitOutcome::Fenced);
    Ok(())
}

/// Recovery control writes cannot advance/replace the committed business batch.
pub async fn session_recovery_contract_tests(
    port: &dyn SessionStorePort,
    owner: &SessionOwnership,
    checkpoint: &Checkpoint,
    attempt: RequestId,
) -> ContractResult {
    let recorded = port
        .record_recovery_attempt(owner, checkpoint.commit_seq, attempt.clone())
        .await?;
    let metadata = match &recorded {
        RecoveryUpdate::Recorded { metadata } => metadata,
        other => panic!("recovery attempt failed: {other:?}"),
    };
    assert_eq!(metadata.last_attempt.as_ref(), Some(&attempt));
    assert_eq!(
        port.record_recovery_attempt(owner, checkpoint.commit_seq, attempt.clone())
            .await?,
        recorded
    );
    let loaded = port.load(&owner.sid).await?.expect("missing checkpoint");
    assert_eq!(loaded.owner, owner.owner);
    assert_eq!(&loaded.checkpoint, checkpoint);
    assert_eq!(&loaded.recovery, metadata);
    let previous = CommitSeq(
        checkpoint
            .commit_seq
            .0
            .checked_sub(1)
            .expect("nonzero fixture"),
    );
    assert_eq!(
        port.record_recovery_attempt(owner, previous, RequestId(format!("{}-stale", attempt.0)))
            .await?,
        RecoveryUpdate::Conflict
    );
    Ok(())
}

pub async fn shard_lease_port_contract_tests(
    port: &dyn ShardLeasePort,
    request: ClaimShard,
) -> ContractResult {
    let claim = match port.claim(request.clone()).await? {
        LeaseClaim::Claimed(claim) => claim,
        other => panic!("fresh lease failed: {other:?}"),
    };
    assert_eq!(claim.shard, request.shard);
    assert_eq!(claim.owner.instance, request.me);
    assert_eq!(
        Some(claim.owner.epoch.0),
        request
            .expected_epoch
            .map_or(Some(1), |epoch| epoch.0.checked_add(1))
    );
    assert_eq!(
        port.claim(request.clone()).await?,
        LeaseClaim::AlreadyClaimed(claim)
    );
    let mut competing = request;
    competing.request_id.0.push_str("-competing");
    assert_eq!(port.claim(competing).await?, LeaseClaim::Conflict);
    Ok(())
}

pub async fn membership_port_contract_tests(
    port: &dyn MembershipPort,
    instance: Instance,
) -> ContractResult {
    let lease = port
        .register(
            instance.clone(),
            RuntimeRole::Worker,
            Duration::from_secs(60),
        )
        .await?;
    assert_eq!(lease.instance, instance);
    assert_eq!(port.liveness(&instance).await?, Liveness::Alive);
    assert_eq!(
        port.renew(&lease, Duration::from_secs(60)).await?,
        Renewal::Renewed
    );
    assert_eq!(port.set_draining(&lease).await?, Renewal::Renewed);
    Ok(())
}

/// Fixture: the old registration is expired/invalidated and a different
/// incarnation of the same node is currently alive.
pub async fn membership_lost_lease_contract_tests(
    port: &dyn MembershipPort,
    lost: &MembershipLease,
    replacement: &Instance,
) -> ContractResult {
    assert_eq!(lost.instance.node, replacement.node);
    assert_ne!(lost.instance.incarnation, replacement.incarnation);
    assert_eq!(
        port.renew(lost, Duration::from_secs(60)).await?,
        Renewal::Lost
    );
    assert_eq!(port.set_draining(lost).await?, Renewal::Lost);
    assert_eq!(port.liveness(replacement).await?, Liveness::Alive);
    Ok(())
}

pub async fn result_store_port_contract_tests(
    port: &dyn ResultStorePort,
    source: EffectSource,
    record: FinalTaskRecord,
) -> ContractResult {
    let reference = match port.save_final(source.clone(), record.clone()).await? {
        SavedResult::Saved(reference) => reference,
        other => panic!("fresh final record not saved: {other:?}"),
    };
    assert_eq!(
        port.save_final(source, record.clone()).await?,
        SavedResult::AlreadySaved(reference)
    );
    assert_eq!(
        port.get_final(&record.scope, &record.tid).await?,
        Some(record)
    );
    Ok(())
}

/// identity carries the real digest of bytes; changed is valid different content.
pub async fn blob_store_port_contract_tests(
    port: &dyn BlobStorePort,
    identity: BlobRef,
    bytes: Vec<u8>,
    changed: Vec<u8>,
) -> ContractResult {
    assert_eq!(
        port.put_immutable(identity.clone(), bytes.clone()).await?,
        IdempotentWrite::Applied
    );
    assert_eq!(
        port.put_immutable(identity.clone(), bytes.clone()).await?,
        IdempotentWrite::AlreadyApplied
    );
    assert!(matches!(
        port.put_immutable(identity.clone(), changed).await,
        Ok(IdempotentWrite::ContentConflict) | Err(StoreError::Invalid { .. })
    ));
    assert_eq!(port.get(&identity).await?, Some(bytes));
    Ok(())
}
