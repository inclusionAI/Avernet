use super::ContractResult;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{common::*, ownership::*, result::*, session::*};
use std::time::Duration;

/// Request is the next valid commit for an acquired, isolated session.
pub async fn session_store_port_contract_tests(
    port: &dyn SessionStorePort,
    request: CommitRequest,
) -> ContractResult {
    let proof = match port.commit(request.clone()).await? {
        CommitOutcome::Committed(proof) => proof,
        other => panic!("first commit failed: {other:?}"),
    };
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
    assert_eq!(loaded.map(|v| v.checkpoint), Some(request.candidate));
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
    assert_eq!(
        port.claim(request).await?,
        LeaseClaim::AlreadyClaimed(claim)
    );
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
    assert_eq!(port.liveness(&instance).await?, Liveness::Alive);
    assert_eq!(
        port.renew(&lease, Duration::from_secs(60)).await?,
        Renewal::Renewed
    );
    assert_eq!(port.set_draining(&lease).await?, Renewal::Renewed);
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
