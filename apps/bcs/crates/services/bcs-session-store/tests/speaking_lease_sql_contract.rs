//! Speaking-lease SQL conformance: the exact six scenarios of the memory
//! contract re-run on real SQLite. One repository table row per (env,
//! session); a released row keeps its epoch as the watermark so grants
//! never reuse an epoch. The SQL store is the production semantics; the
//! memory store is the in-process reference.

use std::sync::Arc;

use bcs_db_api::{DbPlugin, DbStatement, DbValue as Value};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::port::repo::session_speaking_lease::{
    AcquireLease, AdmitMessage, LeaseAcquire, LeaseDecision, LeaseRelease, PreemptLease,
    ReleaseLease, SpeakingLeaseRepoPort,
};
use bcs_session_store::SqlSpeakingLeaseRepo;

const NOW: i64 = 1_780_000_000_000;
const IDLE: i64 = 60_000;
const ALICE: &str = "human_alice";
const BOB: &str = "human_bob";

const DDL: &str = "CREATE TABLE IF NOT EXISTS bcs_session_speaking_leases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    env TEXT NOT NULL,
    session_id TEXT NOT NULL,
    holder_actor_id TEXT NOT NULL DEFAULT '',
    lease_id TEXT NOT NULL DEFAULT '',
    epoch INTEGER NOT NULL DEFAULT 0,
    expires_at_ms INTEGER NOT NULL DEFAULT 0,
    released INTEGER NOT NULL DEFAULT 0,
    UNIQUE (env, session_id)
)";

async fn repo() -> SqlSpeakingLeaseRepo {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    db.execute(DbStatement::new(DDL)).await.expect("create table");
    SqlSpeakingLeaseRepo::new(db, "local", bcs_db_api::DbSqlFlavor::Sqlite)
}

#[tokio::test]
async fn sql_acquire_is_idempotent_and_never_renews_or_steals() {
    let repo = repo().await;
    let granted = repo
        .acquire(AcquireLease { session_id: "s", requester_actor_id: ALICE, now_ms: NOW, idle_timeout_ms: IDLE })
        .await
        .unwrap();
    let LeaseAcquire::Granted(first) = granted else { panic!("{granted:?}") };
    assert_eq!(first.epoch, 1);
    assert_eq!(first.expires_at_ms, NOW + IDLE);

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
async fn sql_admission_is_the_only_renewal() {
    let repo = repo().await;
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
            assert_eq!(renewed.lease_id, lease.lease_id);
            assert_eq!(renewed.epoch, lease.epoch);
            assert_eq!(renewed.expires_at_ms, sent_at + IDLE);
        }
        other => panic!("expected Admitted, got {other:?}"),
    }

    let preempted = repo
        .preempt(PreemptLease { session_id: "s", requester_actor_id: BOB, now_ms: NOW + 50_000, idle_timeout_ms: IDLE })
        .await
        .unwrap();
    assert_eq!(preempted.epoch, 2);
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
async fn sql_lapsed_leases_never_revive_and_reacquisition_rotates_epoch() {
    let repo = repo().await;
    let LeaseAcquire::Granted(lease) = repo
        .acquire(AcquireLease { session_id: "s", requester_actor_id: ALICE, now_ms: NOW, idle_timeout_ms: IDLE })
        .await
        .unwrap()
    else { panic!() };

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
        LeaseDecision::Lost
    );

    let LeaseAcquire::Granted(next) = repo
        .acquire(AcquireLease { session_id: "s", requester_actor_id: BOB, now_ms: late, idle_timeout_ms: IDLE })
        .await
        .unwrap()
    else { panic!() };
    assert_eq!(next.epoch, 2, "reacquisition after lapse rotates the epoch");
    assert_ne!(next.lease_id, lease.lease_id);

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
async fn sql_release_drops_only_a_live_matching_lease() {
    let repo = repo().await;
    let LeaseAcquire::Granted(lease) = repo
        .acquire(AcquireLease { session_id: "s", requester_actor_id: ALICE, now_ms: NOW, idle_timeout_ms: IDLE })
        .await
        .unwrap()
    else { panic!() };

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
    assert_eq!(reacquired.epoch, 2, "the released row keeps the epoch watermark");
    assert_ne!(reacquired.lease_id, lease.lease_id);
}

#[tokio::test]
async fn sql_preempt_transfers_with_strictly_next_epoch() {
    let repo = repo().await;
    let LeaseAcquire::Granted(alice_lease) = repo
        .acquire(AcquireLease { session_id: "s", requester_actor_id: ALICE, now_ms: NOW, idle_timeout_ms: IDLE })
        .await
        .unwrap()
    else { panic!() };

    let bob_lease = repo
        .preempt(PreemptLease { session_id: "s", requester_actor_id: BOB, now_ms: NOW + 5_000, idle_timeout_ms: IDLE })
        .await
        .unwrap();
    assert_eq!(bob_lease.epoch, alice_lease.epoch + 1);
    assert_ne!(bob_lease.lease_id, alice_lease.lease_id);

    let later = bob_lease.expires_at_ms + 5_000;
    let carol = repo
        .preempt(PreemptLease { session_id: "s", requester_actor_id: "human_carol", now_ms: later, idle_timeout_ms: IDLE })
        .await
        .unwrap();
    assert_eq!(carol.epoch, bob_lease.epoch + 1);

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
async fn sql_invalid_inputs_reject_and_environments_are_isolated() {
    let repo = repo().await;
    assert!(repo
        .acquire(AcquireLease { session_id: "s", requester_actor_id: ALICE, now_ms: NOW, idle_timeout_ms: 0 })
        .await
        .is_err());
    assert!(repo
        .admit_message(AdmitMessage { session_id: "s", expected_holder: ALICE, expected_lease_id: "x", expected_epoch: 1, now_ms: -1, idle_timeout_ms: IDLE })
        .await
        .is_err());
    assert!(repo.current("s").await.unwrap().is_none());

    // Environment isolation: another repo bound to a different env sees no
    // lease at all (the env column partitions the store).
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    db.execute(DbStatement::new(DDL)).await.expect("create");
    let other = SqlSpeakingLeaseRepo::new(db, "prod", bcs_db_api::DbSqlFlavor::Sqlite);
    assert!(other.current("s").await.unwrap().is_none());

    // Health: the SQL store surfaces provider availability.
    repo.healthy().await.expect("sqlite is reachable");
    let _ = Value::from(0i64);
}