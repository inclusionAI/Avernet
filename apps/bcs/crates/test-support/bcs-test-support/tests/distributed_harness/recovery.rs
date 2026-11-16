use super::*;
use harness::state::session_recovery_contract_tests;

fn metadata() -> RecoveryMetadata {
    RecoveryMetadata {
        takeover_streak: 2,
        last_attempt: Some(RequestId("recovery-a".into())),
        quarantined: false,
        last_active_at: TimestampMillis(100),
    }
}

fn loaded() -> CheckpointEnvelope {
    CheckpointEnvelope {
        owner: owner().owner,
        checkpoint: checkpoint(),
        recovery: metadata(),
    }
}

fn recovery_script() -> Script {
    let attempt = metadata().last_attempt.unwrap();
    let result = RecoveryUpdate::Recorded {
        metadata: metadata(),
    };
    Script::default()
        .expect(
            "SessionStorePort::record_recovery_attempt",
            (&owner(), CommitSeq(8), &attempt),
            ok(result.clone()),
        )
        .expect(
            "SessionStorePort::record_recovery_attempt",
            (&owner(), CommitSeq(8), &attempt),
            ok(result),
        )
        .expect(
            "SessionStorePort::load",
            (&owner().sid,),
            ok(Some(loaded())),
        )
        .expect(
            "SessionStorePort::record_recovery_attempt",
            (&owner(), CommitSeq(7), RequestId("recovery-a-stale".into())),
            ok(RecoveryUpdate::Conflict),
        )
}

#[tokio::test]
async fn recovery_is_idempotent_preserves_checkpoint_and_rejects_stale_head() -> ContractResult {
    let script = recovery_script();
    session_recovery_contract_tests(
        &script,
        &owner(),
        &checkpoint(),
        RequestId("recovery-a".into()),
    )
    .await?;
    script.done();
    Ok(())
}

#[tokio::test]
async fn recovery_rejects_duplicate_count_changed_checkpoint_or_stale_write() {
    for index in [1, 2, 3] {
        let script = recovery_script();
        if index == 2 {
            let mut corrupted = loaded();
            corrupted.checkpoint.outbox = None;
            script.replace(index, ok(Some(corrupted)));
        } else {
            let mut changed = metadata();
            changed.takeover_streak += 1;
            script.replace(index, ok(RecoveryUpdate::Recorded { metadata: changed }));
        }
        rejects(
            session_recovery_contract_tests(
                &script,
                &owner(),
                &checkpoint(),
                RequestId("recovery-a".into()),
            ),
            "assertion",
        )
        .await;
        script.remaining(3 - index);
    }
}
