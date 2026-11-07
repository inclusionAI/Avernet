//! Speaking-lease contract (spec §6.3/§6.3.1 B7) over the in-memory
//! repository: acquisition idempotency without renewal, preemption
//! fencing with ever-increasing epochs, release guarded by lease id +
//! epoch, admission as the only renewal event, and lapsed leases that
//! never revive. The SQL implementation must reproduce these semantics
//! under database transactions.

use bcs_service_api::port::repo::session_speaking_lease::{
    AcquireLease, AdmitMessage, LeaseAcquire, LeaseDecision, LeaseRelease, PreemptLease,
    ReleaseLease, SpeakingLeaseRepoPort,
};
use bcs_session_store::MemorySpeakingLeaseRepo;

const NOW: i64 = 1_780_000_000_000;
const IDLE: i64 = 60_000;
const ALICE: &str = "human_alice";
const BOB: &str = "human_bob";

#[tokio::test]
async fn acquire_is_idempotent_and_never_renews_or_steals() {
    let repo = MemorySpeakingLeaseRepo::new();
    let granted = repo
        .acquire(AcquireLease { session_id: "s", requester_actor_id: ALICE, now_ms: NOW, idle_timeout_ms: IDLE })
        .await
        .unwrap();
    let LeaseAcquire::Granted(first) = granted else { panic!("{granted:?}") };
    assert_eq!(first.epoch, 1);
    assert_eq!(first.expires_at_ms, NOW + IDLE);

    // Self re-entry: same lease, same epoch, no renewal.
    let after = NOW + 30_000;
    match repo
        .acquire(AcquireLease { session_id: "s", requester_actor_id: ALICE, now_ms: after, idle_timeout_ms: IDLE })
        .await
        .unwrap()
    {
        LeaseAcquire::SelfHeld(lease) => {
            assert_eq!(lease.lease_id, first.lease_id);
            assert_eq!(lease.epoch, first.epoch);
            assert_eq!(lease.expires_at_ms, first.expires_at_ms, "re-entry must not renew");
        }
        other => panic!("expected SelfHeld, got {other:?}"),
    }

    // A foreign holder keeps the lease: ordinary acquire never steals.
    match repo
        .acquire(AcquireLease { session_id: "s", requester_actor_id: BOB, now_ms: after, idle_timeout_ms: IDLE })
        .await
        .unwrap()
    {
        LeaseAcquire::Occupied { holder_actor_id, epoch, expires_at_ms } => {
            assert_eq!(holder_actor_id, ALICE);
            assert_eq!(epoch, 1);
            assert_eq!(expires_at_ms, first.expires_at_ms);
        }
        other => panic!("expected Occupied, got {other:?}"),
    }
}

#[tokio::test]
async fn admission_is_the_only_renewal_event_under_the_same_epoch() {
    let repo = MemorySpeakingLeaseRepo::new();
    let LeaseAcquire::Granted(lease) = repo
        .acquire(AcquireLease { session_id: "s", requester_actor_id: ALICE, now_ms: NOW, idle_timeout_ms: IDLE })
        .await
        .unwrap()
    else { panic!() };

    let sent_at = NOW + 45_000;
    match repo
        .admit_message(AdmitMessage {
            session_id: "s",
            expected_holder: ALICE,
            expected_lease_id: &lease.lease_id,
            expected_epoch: lease.epoch,
            now_ms: sent_at,
            idle_timeout_ms: IDLE,
        })
        .await
        .unwrap()
    {
        LeaseDecision::Admitted(renewed) => {
            assert_eq!(renewed.lease_id, lease.lease_id, "renewal keeps the lease id");
            assert_eq!(renewed.epoch, lease.epoch, "renewal keeps the epoch");
            assert_eq!(renewed.expires_at_ms, sent_at + IDLE, "renewed from the acceptance time");
        }
        other => panic!("expected Admitted, got {other:?}"),
    }

    // A submission under an older epoch after preemption is refused even
    // though the holder name matches.
    let preempted = repo
        .preempt(PreemptLease { session_id: "s", requester_actor_id: BOB, now_ms: NOW + 50_000, idle_timeout_ms: IDLE })
        .await
        .unwrap();
    assert_eq!(preempted.epoch, 2, "preemption strictly increases the epoch");
    assert_eq!(preempted.holder_actor_id, BOB);

    match repo
        .admit_message(AdmitMessage {
            session_id: "s",
            expected_holder: ALICE,
            expected_lease_id: &lease.lease_id,
            expected_epoch: lease.epoch,
            now_ms: NOW + 55_000,
            idle_timeout_ms: IDLE,
        })
        .await
        .unwrap()
    {
        LeaseDecision::Superseded { current_epoch } => assert_eq!(current_epoch, 2),
        other => panic!("expected Superseded, got {other:?}"),
    }

    // Quoting the NEW lease id under the OLD holder's name: the id is
    // current, so the holder mismatch surfaces as WrongHolder.
    match repo
        .admit_message(AdmitMessage {
            session_id: "s",
            expected_holder: ALICE,
            expected_lease_id: &preempted.lease_id,
            expected_epoch: preempted.epoch,
            now_ms: NOW + 56_000,
            idle_timeout_ms: IDLE,
        })
        .await
        .unwrap()
    {
        LeaseDecision::WrongHolder { holder_actor_id } => assert_eq!(holder_actor_id, BOB),
        other => panic!("expected WrongHolder, got {other:?}"),
    }

    // A submission quoting an unknown lease id is fenced as superseded —
    // the lease-identity check fires first and reveals the live epoch,
    // which is more actionable than an opaque Lost.
    assert_eq!(
        repo.admit_message(AdmitMessage {
            session_id: "s",
            expected_holder: ALICE,
            expected_lease_id: "never-existed",
            expected_epoch: 9,
            now_ms: NOW + 57_000,
            idle_timeout_ms: IDLE,
        })
        .await
        .unwrap(),
        LeaseDecision::Superseded { current_epoch: 2 }
    );
}

#[tokio::test]
async fn lapsed_leases_never_revive_and_reacquisition_rotates_epoch() {
    let repo = MemorySpeakingLeaseRepo::new();
    let LeaseAcquire::Granted(lease) = repo
        .acquire(AcquireLease { session_id: "s", requester_actor_id: ALICE, now_ms: NOW, idle_timeout_ms: IDLE })
        .await
        .unwrap()
    else { panic!() };

    // Past the idle window: admission and lapsed-holder release semantics.
    let late = lease.expires_at_ms + 1;
    assert_eq!(
        repo.admit_message(AdmitMessage {
            session_id: "s",
            expected_holder: ALICE,
            expected_lease_id: &lease.lease_id,
            expected_epoch: lease.epoch,
            now_ms: late,
            idle_timeout_ms: IDLE,
        })
        .await
        .unwrap(),
        LeaseDecision::Lost,
        "a late message cannot revive a lapsed lease"
    );

    // Another member acquires the freed rights: fresh lease id, next epoch.
    let LeaseAcquire::Granted(next) = repo
        .acquire(AcquireLease { session_id: "s", requester_actor_id: BOB, now_ms: late, idle_timeout_ms: IDLE })
        .await
        .unwrap()
    else { panic!() };
    assert_eq!(next.epoch, 2, "reacquisition after lapse rotates the epoch");
    assert_ne!(next.lease_id, lease.lease_id);

    // The old holder's release (matching their old lease) must not touch
    // the successor's row.
    assert_eq!(
        repo.release(ReleaseLease {
            session_id: "s",
            holder_actor_id: ALICE,
            lease_id: &lease.lease_id,
            epoch: lease.epoch,
            now_ms: late,
        })
        .await
        .unwrap(),
        LeaseRelease::NotHeld,
        "a stale release never clears a transferred lease"
    );
}

#[tokio::test]
async fn release_drops_only_a_live_matching_lease() {
    let repo = MemorySpeakingLeaseRepo::new();
    let LeaseAcquire::Granted(lease) = repo
        .acquire(AcquireLease { session_id: "s", requester_actor_id: ALICE, now_ms: NOW, idle_timeout_ms: IDLE })
        .await
        .unwrap()
    else { panic!() };

    // Wrong epoch refuses.
    assert_eq!(
        repo.release(ReleaseLease {
            session_id: "s",
            holder_actor_id: ALICE,
            lease_id: &lease.lease_id,
            epoch: lease.epoch + 7,
            now_ms: NOW,
        })
        .await
        .unwrap(),
        LeaseRelease::NotHeld
    );
    // Exact triple releases.
    assert_eq!(
        repo.release(ReleaseLease {
            session_id: "s",
            holder_actor_id: ALICE,
            lease_id: &lease.lease_id,
            epoch: lease.epoch,
            now_ms: NOW,
        })
        .await
        .unwrap(),
        LeaseRelease::Released
    );
    // Post-release admission is lost and a re-grant starts a new lease.
    assert_eq!(
        repo.admit_message(AdmitMessage {
            session_id: "s",
            expected_holder: ALICE,
            expected_lease_id: &lease.lease_id,
            expected_epoch: lease.epoch,
            now_ms: NOW + 1,
            idle_timeout_ms: IDLE,
        })
        .await
        .unwrap(),
        LeaseDecision::Lost
    );
    let LeaseAcquire::Granted(reacquired) = repo
        .acquire(AcquireLease { session_id: "s", requester_actor_id: ALICE, now_ms: NOW + 2, idle_timeout_ms: IDLE })
        .await
        .unwrap()
    else { panic!() };
    assert_eq!(reacquired.epoch, 2);
    assert_ne!(reacquired.lease_id, lease.lease_id);
}

#[tokio::test]
async fn preempt_transfers_without_touching_submission_epoch_of_old_turns() {
    let repo = MemorySpeakingLeaseRepo::new();
    let LeaseAcquire::Granted(alice_lease) = repo
        .acquire(AcquireLease { session_id: "s", requester_actor_id: ALICE, now_ms: NOW, idle_timeout_ms: IDLE })
        .await
        .unwrap()
    else { panic!() };

    // Preempting a foreign, valid lease is allowed and takes the next epoch.
    let bob_lease = repo
        .preempt(PreemptLease { session_id: "s", requester_actor_id: BOB, now_ms: NOW + 5_000, idle_timeout_ms: IDLE })
        .await
        .unwrap();
    assert_eq!(bob_lease.epoch, alice_lease.epoch + 1);
    assert_ne!(bob_lease.lease_id, alice_lease.lease_id);

    // Preempting an EXPIRED lease also transfers with the next epoch;
    // preemption ignores the current validity by contract.
    let later = bob_lease.expires_at_ms + 5_000;
    let carol = repo
        .preempt(PreemptLease { session_id: "s", requester_actor_id: "human_carol", now_ms: later, idle_timeout_ms: IDLE })
        .await
        .unwrap();
    assert_eq!(carol.epoch, bob_lease.epoch + 1);

    // The old holder's ordinary acquire conflicts against the new holder.
    match repo
        .acquire(AcquireLease { session_id: "s", requester_actor_id: BOB, now_ms: later, idle_timeout_ms: IDLE })
        .await
        .unwrap()
    {
        LeaseAcquire::Occupied { holder_actor_id, .. } => assert_eq!(holder_actor_id, "human_carol"),
        other => panic!("expected Occupied, got {other:?}"),
    }
}

#[tokio::test]
async fn invalid_inputs_reject_before_any_state_change() {
    let repo = MemorySpeakingLeaseRepo::new();
    // Non-positive timeout.
    assert!(repo
        .acquire(AcquireLease { session_id: "s", requester_actor_id: ALICE, now_ms: NOW, idle_timeout_ms: 0 })
        .await
        .is_err());
    // Non-positive clock.
    assert!(repo
        .preempt(PreemptLease { session_id: "s", requester_actor_id: ALICE, now_ms: 0, idle_timeout_ms: IDLE })
        .await
        .is_err());
    assert!(repo
        .admit_message(AdmitMessage { session_id: "s", expected_holder: ALICE, expected_lease_id: "x", expected_epoch: 1, now_ms: -1, idle_timeout_ms: IDLE })
        .await
        .is_err());
    // Nothing was written under rejection.
    assert!(repo.current("s").await.unwrap().is_none());
}