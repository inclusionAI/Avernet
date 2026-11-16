use super::*;
use harness::state::*;

fn commit_script() -> (Script, CommitRequest) {
    let request = CommitRequest {
        owner: owner(),
        expected_revision: Revision("revision-7".into()),
        commit_id: proof().commit_id,
        content_digest: proof().content_digest,
        candidate: checkpoint(),
    };
    let script = Script::default()
        .expect(
            "SessionStorePort::commit",
            (&request,),
            ok(CommitOutcome::Committed(proof())),
        )
        .expect(
            "SessionStorePort::commit",
            (&request,),
            ok(CommitOutcome::AlreadyCommitted(proof())),
        )
        .expect(
            "SessionStorePort::resolve_commit",
            (&request.owner, &request.commit_id, &request.content_digest),
            ok(CommitResolution::Committed(proof())),
        )
        .expect(
            "SessionStorePort::load",
            (&request.owner.sid,),
            ok(Some(CheckpointEnvelope {
                owner: owner().owner,
                revision: proof().revision,
                checkpoint: request.candidate.clone(),
                recovery: RecoveryMetadata {
                    takeover_streak: 0,
                    last_attempt: None,
                    quarantined: false,
                    last_active_at: TimestampMillis(100),
                },
            })),
        );
    (script, request)
}

#[tokio::test]
async fn checkpoint_checks_replay_proof_resolution_and_atomic_candidate() -> ContractResult {
    let (script, request) = commit_script();
    session_store_port_contract_tests(&script, request).await?;
    script.done();
    Ok(())
}

#[tokio::test]
async fn checkpoint_rejects_unresolved_commit_and_missing_persistence() {
    for index in [2, 3] {
        let (script, request) = commit_script();
        if index == 2 {
            script.replace(index, ok(CommitResolution::OutcomeUnknown));
        } else {
            script.replace(index, ok(None::<CheckpointEnvelope>));
        }
        rejects(
            session_store_port_contract_tests(&script, request),
            "assertion",
        )
        .await;
        script.remaining(3 - index);
    }
}

#[tokio::test]
async fn checkpoint_does_not_turn_a_fenced_commit_into_success() {
    let (script, request) = commit_script();
    script.replace(0, ok(CommitOutcome::Fenced));
    rejects(
        session_store_port_contract_tests(&script, request),
        "first commit failed",
    )
    .await;
    script.remaining(3);
}

#[tokio::test]
async fn lease_checks_stable_claim_identity_on_retry() -> ContractResult {
    let request = ClaimShard {
        shard: owner().shard,
        me: instance(),
        expected: None,
        request_id: RequestId("claim-a".into()),
    };
    let claim = ShardClaim {
        shard: owner().shard,
        owner: owner().owner,
        revision: Revision("claim-revision".into()),
        authority: "claim-proof".into(),
    };
    let script = Script::default()
        .expect(
            "ShardLeasePort::claim",
            (&request,),
            ok(LeaseClaim::Claimed(claim.clone())),
        )
        .expect(
            "ShardLeasePort::claim",
            (&request,),
            ok(LeaseClaim::AlreadyClaimed(claim)),
        );
    shard_lease_port_contract_tests(&script, request).await?;
    script.done();
    Ok(())
}

#[tokio::test]
async fn membership_checks_lease_identity_and_drain_transition() -> ContractResult {
    let lease = MembershipLease {
        instance: instance(),
        authority: "membership-proof".into(),
    };
    let script = Script::default()
        .expect(
            "MembershipPort::register",
            (instance(), RuntimeRole::Worker, Duration::from_secs(60)),
            ok(lease.clone()),
        )
        .expect(
            "MembershipPort::liveness",
            (instance(),),
            ok(Liveness::Alive),
        )
        .expect(
            "MembershipPort::renew",
            (&lease, Duration::from_secs(60)),
            ok(Renewal::Renewed),
        )
        .expect(
            "MembershipPort::set_draining",
            (&lease,),
            ok(Renewal::Renewed),
        );
    membership_port_contract_tests(&script, instance()).await?;
    script.done();
    Ok(())
}

#[tokio::test]
async fn final_result_checks_idempotency_and_authorized_readback() -> ContractResult {
    let record = final_record();
    let script = Script::default()
        .expect(
            "ResultStorePort::save_final",
            (source(), &record),
            ok(SavedResult::Saved(blob())),
        )
        .expect(
            "ResultStorePort::save_final",
            (source(), &record),
            ok(SavedResult::AlreadySaved(blob())),
        )
        .expect(
            "ResultStorePort::get_final",
            (&record.scope, &record.tid),
            ok(Some(record.clone())),
        );
    result_store_port_contract_tests(&script, source(), record).await?;
    script.done();
    Ok(())
}

#[tokio::test]
async fn blob_checks_immutable_content_and_rejects_overwrite() -> ContractResult {
    use sha2::{Digest as _, Sha256};
    let bytes = b"original".to_vec();
    let changed = b"changed".to_vec();
    let identity = BlobRef {
        key: "blob-a".into(),
        digest: Digest(format!("{:x}", Sha256::digest(&bytes))),
    };
    for response in [
        ok(IdempotentWrite::ContentConflict),
        Err(StoreError::Invalid {
            code: "digest_mismatch".into(),
        }),
        ok(IdempotentWrite::Applied),
    ] {
        let violates = response == ok(IdempotentWrite::Applied);
        let script = Script::default()
            .expect(
                "BlobStorePort::put_immutable",
                (&identity, &bytes),
                ok(IdempotentWrite::Applied),
            )
            .expect(
                "BlobStorePort::put_immutable",
                (&identity, &bytes),
                ok(IdempotentWrite::AlreadyApplied),
            )
            .expect(
                "BlobStorePort::put_immutable",
                (&identity, &changed),
                response,
            )
            .expect("BlobStorePort::get", (&identity,), ok(Some(bytes.clone())));
        let test = blob_store_port_contract_tests(
            &script,
            identity.clone(),
            bytes.clone(),
            changed.clone(),
        );
        if violates {
            rejects(test, "assertion").await;
            script.remaining(1);
        } else {
            test.await?;
            script.done();
        }
    }
    Ok(())
}
