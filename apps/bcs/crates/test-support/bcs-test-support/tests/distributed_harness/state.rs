use super::*;
use harness::state::*;

fn commit_script() -> (Script, CommitRequest) {
    let request = CommitRequest {
        owner: owner(),
        expected_commit_seq: CommitSeq(7),
        commit_id: proof().commit_id,
        content_digest: proof().content_digest,
        candidate: checkpoint(),
    };
    let mut competing = request.clone();
    competing.commit_id.0.push_str("-competing");
    let mut unsealed = request.clone();
    unsealed.owner.owner.epoch = WorkerEpoch(15);
    unsealed.commit_id.0.push_str("-unsealed");
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
                checkpoint: request.candidate.clone(),
                recovery: RecoveryMetadata {
                    takeover_streak: 0,
                    last_attempt: None,
                    quarantined: false,
                    last_active_at: TimestampMillis(100),
                },
            })),
        )
        .expect(
            "SessionStorePort::commit",
            (&competing,),
            ok(CommitOutcome::CommitSeqConflict),
        )
        .expect(
            "SessionStorePort::commit",
            (&unsealed,),
            ok(CommitOutcome::Fenced),
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
        script.remaining(5 - index);
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
    script.remaining(5);
}

#[tokio::test]
async fn checkpoint_rejects_competing_commit_unsealed_owner_and_incorrect_receipt() {
    for index in [0, 4, 5] {
        let (script, request) = commit_script();
        if index == 0 {
            let mut receipt = proof();
            receipt.sequence = CommitSeq(9);
            script.replace(index, ok(CommitOutcome::Committed(receipt)));
        } else {
            script.replace(index, ok(CommitOutcome::AlreadyCommitted(proof())));
        }
        rejects(
            session_store_port_contract_tests(&script, request),
            "assertion",
        )
        .await;
        script.remaining(5 - index);
    }
}

fn lease_script(expected_epoch: Option<WorkerEpoch>) -> (Script, ClaimShard) {
    let request = ClaimShard {
        shard: owner().shard,
        me: instance(),
        expected_epoch,
        request_id: RequestId("claim-a".into()),
    };
    let mut claimed_owner = owner().owner;
    claimed_owner.epoch = WorkerEpoch(expected_epoch.map_or(1, |epoch| epoch.0 + 1));
    let claim = ShardClaim {
        shard: owner().shard,
        owner: claimed_owner,
    };
    let mut competing = request.clone();
    competing.request_id.0.push_str("-competing");
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
        )
        .expect(
            "ShardLeasePort::claim",
            (&competing,),
            ok(LeaseClaim::Conflict),
        );
    (script, request)
}

#[tokio::test]
async fn lease_checks_initial_epoch_takeover_and_stable_retry() -> ContractResult {
    for expected in [None, Some(WorkerEpoch(13))] {
        let (script, request) = lease_script(expected);
        shard_lease_port_contract_tests(&script, request).await?;
        script.done();
    }
    Ok(())
}

#[tokio::test]
async fn lease_rejects_unchanged_epoch_or_a_second_winner() {
    for index in [0, 2] {
        let (script, request) = lease_script(Some(WorkerEpoch(13)));
        let mut claimed_owner = owner().owner;
        if index == 0 {
            claimed_owner.epoch = WorkerEpoch(13);
        }
        script.replace(
            index,
            ok(LeaseClaim::Claimed(ShardClaim {
                shard: request.shard,
                owner: claimed_owner,
            })),
        );
        rejects(
            shard_lease_port_contract_tests(&script, request),
            "assertion",
        )
        .await;
        script.remaining(2 - index);
    }
}

#[tokio::test]
async fn membership_checks_lease_identity_and_drain_transition() -> ContractResult {
    let lease = MembershipLease {
        instance: instance(),
    };
    let script = Script::default()
        .expect(
            "MembershipPort::register",
            (instance(), RuntimeRole::Worker, String::from("http://worker.example.test:8080"), Duration::from_secs(60)),
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

fn lost_membership_script() -> (Script, MembershipLease) {
    let lost = MembershipLease {
        instance: Instance {
            node: instance().node,
            incarnation: "retired-boot".into(),
        },
    };
    let script = Script::default()
        .expect(
            "MembershipPort::renew",
            (&lost, Duration::from_secs(60)),
            ok(Renewal::Lost),
        )
        .expect("MembershipPort::set_draining", (&lost,), ok(Renewal::Lost))
        .expect(
            "MembershipPort::liveness",
            (&instance(),),
            ok(Liveness::Alive),
        );
    (script, lost)
}

#[tokio::test]
async fn lost_membership_cannot_revive_or_change_the_replacement() -> ContractResult {
    let (script, lost) = lost_membership_script();
    membership_lost_lease_contract_tests(&script, &lost, &instance()).await?;
    script.done();
    Ok(())
}

#[tokio::test]
async fn membership_rejects_reviving_old_lease_or_draining_new_instance() {
    for index in [0, 1, 2] {
        let (script, lost) = lost_membership_script();
        if index == 2 {
            script.replace(index, ok(Liveness::Dead));
        } else {
            script.replace(index, ok(Renewal::Renewed));
        }
        rejects(
            membership_lost_lease_contract_tests(&script, &lost, &instance()),
            "assertion",
        )
        .await;
        script.remaining(2 - index);
    }
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
