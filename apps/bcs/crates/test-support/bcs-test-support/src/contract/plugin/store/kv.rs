use super::applied;
use bcs_store_api::{
    kv::{KvStorePlugin, VersionedKvStorePlugin},
    *,
};
use std::{future::Future, time::Duration};

pub async fn kv_store_plugin_contract_tests<P: KvStorePlugin + ?Sized>(p: &P, prefix: &str) {
    assert!(p.supports(StoreCapability::Kv));
    let key = format!("{prefix}:kv");
    let absent = format!("{prefix}:missing");
    let bytes = [0, 255, 0, 128];
    assert_eq!(p.get(&key).await.unwrap(), None);
    p.set(&key, &bytes, Expiry::Persistent).await.unwrap();
    assert_eq!(
        p.mget(&[key.clone(), absent, key.clone()]).await.unwrap(),
        vec![Some(bytes.to_vec()), None, Some(bytes.to_vec())]
    );
    assert_eq!(p.ttl(&key).await.unwrap(), KeyTtl::Persistent);
    assert!(
        !p.put_if_absent(&key, b"overwrite", Expiry::After(Duration::from_secs(60)))
            .await
            .unwrap()
    );
    assert_eq!(p.get(&key).await.unwrap(), Some(bytes.to_vec()));
    assert_eq!(p.ttl(&key).await.unwrap(), KeyTtl::Persistent);
    let race = format!("{prefix}:nx-race");
    let (a, b) = tokio::join!(
        p.put_if_absent(&race, b"a", Expiry::Persistent),
        p.put_if_absent(&race, b"b", Expiry::Persistent)
    );
    let a = a.unwrap();
    let b = b.unwrap();
    assert_ne!(a, b, "exactly one concurrent create wins");
    assert_eq!(
        p.get(&race).await.unwrap(),
        Some(if a { b"a" } else { b"b" }.to_vec())
    );
    assert!(p.expire(&key, Duration::from_secs(60)).await.unwrap());
    p.set(&key, b"updated", Expiry::Preserve).await.unwrap();
    assert!(matches!(p.ttl(&key).await.unwrap(), KeyTtl::ExpiresIn(_)));
    assert!(p.delete(&key).await.unwrap());
    assert!(!p.delete(&key).await.unwrap());
    assert_eq!(p.ttl(&key).await.unwrap(), KeyTtl::Missing);
    assert!(matches!(
        p.set(&key, b"bad", Expiry::After(Duration::ZERO)).await,
        Err(StoreError::InvalidInput(_))
    ));
    assert_eq!(p.get(&key).await.unwrap(), None);
}

/// Basic versioned storage remains usable without exact CAS.
pub async fn kv_versioned_contract_tests<P: VersionedKvStorePlugin + ?Sized>(p: &P, prefix: &str) {
    assert!(p.supports(StoreCapability::VersionedKv));
    let key = format!("{prefix}:versioned");
    assert_eq!(p.get_versioned(&key).await.unwrap(), None);
    assert_eq!(p.ttl(&key).await.unwrap(), KeyTtl::Missing);
    let (a, b) = tokio::join!(
        p.create_versioned(&key, &[0, 255], Expiry::Persistent),
        p.create_versioned(&key, b"other", Expiry::Persistent)
    );
    let (a, b) = (a.unwrap(), b.unwrap());
    let (version, value) = match (a, b) {
        (WriteOutcome::Applied { version }, WriteOutcome::ConditionNotMet) => {
            (version, vec![0, 255])
        }
        (WriteOutcome::ConditionNotMet, WriteOutcome::Applied { version }) => {
            (version, b"other".to_vec())
        }
        other => panic!("versioned creation must have one winner: {other:?}"),
    };
    assert!(version.0 > 0);
    assert_eq!(
        p.get_versioned(&key).await.unwrap(),
        Some(Versioned { version, value })
    );
    assert_eq!(p.ttl(&key).await.unwrap(), KeyTtl::Persistent);
    assert!(p.delete(&key).await.unwrap());
    assert!(!p.delete(&key).await.unwrap());
    assert_eq!(p.get_versioned(&key).await.unwrap(), None);
    assert_eq!(p.ttl(&key).await.unwrap(), KeyTtl::Missing);
    applied(
        p.create_versioned(&key, b"fresh", Expiry::Persistent)
            .await
            .unwrap(),
    );
}

/// Strict exact-version updates, independently injectable without ordinary Kv.
pub async fn kv_conditional_contract_tests<P: VersionedKvStorePlugin + ?Sized>(
    p: &P,
    prefix: &str,
) {
    assert!(p.supports(StoreCapability::ConditionalKv));
    let key = format!("{prefix}:cas");
    assert_eq!(p.get_versioned(&key).await.unwrap(), None);
    assert_eq!(p.ttl(&key).await.unwrap(), KeyTtl::Missing);
    let initial = applied(
        p.create_versioned(&key, &[0, 255, 1], Expiry::Persistent)
            .await
            .unwrap(),
    );
    assert_eq!(
        p.create_versioned(&key, b"duplicate", Expiry::Persistent)
            .await
            .unwrap(),
        WriteOutcome::ConditionNotMet
    );
    let base = p.get_versioned(&key).await.unwrap().unwrap();
    assert_eq!(base.value, vec![0, 255, 1]);
    assert_eq!(base.version, initial);
    assert_eq!(p.ttl(&key).await.unwrap(), KeyTtl::Persistent);
    let (a, b) = tokio::join!(
        p.compare_and_set(&key, initial, b"a", Expiry::Persistent),
        p.compare_and_set(&key, initial, b"b", Expiry::Persistent)
    );
    let (a, b) = (a.unwrap(), b.unwrap());
    assert!(
        matches!(
            (&a, &b),
            (WriteOutcome::Applied { .. }, WriteOutcome::ConditionNotMet)
                | (WriteOutcome::ConditionNotMet, WriteOutcome::Applied { .. })
        ),
        "CAS must have one winner"
    );
    let latest = p.get_versioned(&key).await.unwrap().unwrap();
    assert!(latest.version > initial);
    assert_eq!(
        latest.value,
        if matches!(a, WriteOutcome::Applied { .. }) {
            b"a"
        } else {
            b"b"
        }
    );
    assert_eq!(
        p.compare_and_set(&key, initial, b"stale", Expiry::Persistent)
            .await
            .unwrap(),
        WriteOutcome::ConditionNotMet
    );
    let next = applied(
        p.compare_and_set(&key, latest.version, &latest.value, Expiry::Persistent)
            .await
            .unwrap(),
    );
    assert!(next > latest.version);
    assert!(matches!(
        p.compare_and_set(&key, Version(0), b"bad", Expiry::Persistent)
            .await,
        Err(StoreError::InvalidInput(_))
    ));
    assert!(p.delete(&key).await.unwrap());
    assert!(!p.delete(&key).await.unwrap());
    assert_eq!(p.get_versioned(&key).await.unwrap(), None);
    assert_eq!(p.ttl(&key).await.unwrap(), KeyTtl::Missing);
    assert_eq!(
        p.compare_and_set(&key, next, b"resurrect", Expiry::Persistent)
            .await
            .unwrap(),
        WriteOutcome::ConditionNotMet
    );
    applied(
        p.create_versioned(&key, b"fresh", Expiry::Persistent)
            .await
            .unwrap(),
    );
}

/// Optional capability refusals must not fall back to weaker operations.
pub async fn kv_versioned_optional_contract_tests<P: VersionedKvStorePlugin + ?Sized>(
    p: &P,
    prefix: &str,
) {
    let key = format!("{prefix}:optional");
    let absent = format!("{prefix}:optional-absent");
    let version = applied(
        p.create_versioned(&key, b"unchanged", Expiry::Persistent)
            .await
            .unwrap(),
    );
    if !p.supports(StoreCapability::ConditionalKv) {
        for expiry in [
            Expiry::Persistent,
            Expiry::Preserve,
            Expiry::After(Duration::from_secs(1)),
        ] {
            for target in [&key, &absent] {
                assert_eq!(
                    p.compare_and_set(target, version, b"bad", expiry).await,
                    Err(StoreError::Unsupported(StoreCapability::ConditionalKv))
                );
            }
        }
    }
    if !p.supports(StoreCapability::VersionedKvTtl) {
        for expiry in [Expiry::Preserve, Expiry::After(Duration::from_secs(1))] {
            assert_eq!(
                p.create_versioned(&absent, b"bad", expiry).await,
                Err(StoreError::Unsupported(StoreCapability::VersionedKvTtl))
            );
            if p.supports(StoreCapability::ConditionalKv) {
                assert_eq!(
                    p.compare_and_set(&key, version, b"bad", expiry).await,
                    Err(StoreError::Unsupported(StoreCapability::VersionedKvTtl))
                );
            }
        }
        assert_eq!(
            p.expire_if_version(&key, version, Duration::from_secs(1))
                .await,
            Err(StoreError::Unsupported(StoreCapability::VersionedKvTtl))
        );
    }
    if !p.supports(StoreCapability::VersionedKvConditionalDelete) {
        assert_eq!(
            p.delete_if_version(&key, version).await,
            Err(StoreError::Unsupported(
                StoreCapability::VersionedKvConditionalDelete
            ))
        );
    }
    if !p.supports(StoreCapability::MonotonicKv) {
        assert_eq!(
            p.set_if_newer(&key, Version(u64::MAX), b"bad", Expiry::Persistent)
                .await,
            Err(StoreError::Unsupported(StoreCapability::MonotonicKv))
        );
    }
    assert_eq!(p.get_versioned(&absent).await.unwrap(), None);
    assert_eq!(
        p.get_versioned(&key).await.unwrap(),
        Some(Versioned {
            value: b"unchanged".to_vec(),
            version
        })
    );
    assert_eq!(p.ttl(&key).await.unwrap(), KeyTtl::Persistent);
}

pub async fn kv_conditional_delete_contract_tests<P: VersionedKvStorePlugin + ?Sized>(
    p: &P,
    prefix: &str,
) {
    assert!(p.supports(StoreCapability::VersionedKvConditionalDelete));
    let key = format!("{prefix}:conditional-delete");
    let old = applied(
        p.create_versioned(&key, b"old", Expiry::Persistent)
            .await
            .unwrap(),
    );
    let current = applied(
        p.compare_and_set(&key, old, b"replacement", Expiry::Persistent)
            .await
            .unwrap(),
    );
    assert!(
        !p.delete_if_version(&key, old).await.unwrap(),
        "stale delete must preserve the replacement"
    );
    assert_eq!(
        p.get_versioned(&key).await.unwrap().unwrap().value,
        b"replacement"
    );
    let (a, b) = tokio::join!(
        p.delete_if_version(&key, current),
        p.delete_if_version(&key, current)
    );
    assert_ne!(
        a.unwrap(),
        b.unwrap(),
        "conditional delete must have one winner"
    );
    assert!(!p.delete_if_version(&key, current).await.unwrap());
    assert_eq!(p.get_versioned(&key).await.unwrap(), None);
    assert!(matches!(
        p.delete_if_version(&key, Version(0)).await,
        Err(StoreError::InvalidInput(_))
    ));
}

pub async fn kv_versioned_ttl_contract_tests<P: VersionedKvStorePlugin + ?Sized>(
    p: &P,
    prefix: &str,
) {
    assert!(p.supports(StoreCapability::VersionedKvTtl));
    let key = format!("{prefix}:versioned-ttl");
    let initial = applied(
        p.create_versioned(&key, b"old", Expiry::Preserve)
            .await
            .unwrap(),
    );
    let current = applied(
        p.compare_and_set(&key, initial, b"current", Expiry::Persistent)
            .await
            .unwrap(),
    );
    assert_eq!(
        p.compare_and_set(&key, initial, b"bad", Expiry::After(Duration::from_secs(1)))
            .await
            .unwrap(),
        WriteOutcome::ConditionNotMet
    );
    assert_eq!(
        p.expire_if_version(&key, initial, Duration::from_secs(1))
            .await
            .unwrap(),
        WriteOutcome::ConditionNotMet
    );
    assert_eq!(
        p.ttl(&key).await.unwrap(),
        KeyTtl::Persistent,
        "failed conditions must not change TTL"
    );
    let renewed = applied(
        p.expire_if_version(&key, current, Duration::from_secs(60))
            .await
            .unwrap(),
    );
    assert!(renewed > current);
    assert_eq!(
        p.get_versioned(&key).await.unwrap().unwrap().value,
        b"current"
    );
    assert_eq!(
        p.create_versioned(&key, b"overwrite", Expiry::After(Duration::from_secs(1)))
            .await
            .unwrap(),
        WriteOutcome::ConditionNotMet
    );
    let preserved = applied(
        p.compare_and_set(&key, renewed, b"preserved", Expiry::Preserve)
            .await
            .unwrap(),
    );
    assert!(
        matches!(p.ttl(&key).await.unwrap(), KeyTtl::ExpiresIn(d) if d > Duration::from_secs(1))
    );
    assert_eq!(
        p.compare_and_set(&key, current, b"stale", Expiry::Persistent)
            .await
            .unwrap(),
        WriteOutcome::ConditionNotMet
    );
    assert!(matches!(
        p.expire_if_version(&key, preserved, Duration::ZERO).await,
        Err(StoreError::InvalidInput(_))
    ));
    assert!(matches!(
        p.compare_and_set(&key, preserved, b"bad", Expiry::After(Duration::ZERO))
            .await,
        Err(StoreError::InvalidInput(_))
    ));
    assert_eq!(
        p.get_versioned(&key).await.unwrap(),
        Some(Versioned {
            value: b"preserved".to_vec(),
            version: preserved
        })
    );
    applied(
        p.compare_and_set(&key, preserved, b"persistent", Expiry::Persistent)
            .await
            .unwrap(),
    );
    assert_eq!(p.ttl(&key).await.unwrap(), KeyTtl::Persistent);
    assert!(p.delete(&key).await.unwrap());
    assert_eq!(
        p.expire_if_version(&key, preserved, Duration::from_secs(1))
            .await
            .unwrap(),
        WriteOutcome::ConditionNotMet
    );
    let timed = applied(
        p.create_versioned(&key, b"timed", Expiry::After(Duration::from_secs(60)))
            .await
            .unwrap(),
    );
    let updated = applied(
        p.compare_and_set(
            &key,
            timed,
            b"timed-update",
            Expiry::After(Duration::from_secs(60)),
        )
        .await
        .unwrap(),
    );
    assert!(updated > timed);
    assert!(matches!(p.ttl(&key).await.unwrap(), KeyTtl::ExpiresIn(_)));
}

pub async fn kv_monotonic_contract_tests<P: VersionedKvStorePlugin + ?Sized>(p: &P, prefix: &str) {
    assert!(p.supports(StoreCapability::MonotonicKv));
    let key = format!("{prefix}:monotonic");
    assert_eq!(
        applied(
            p.set_if_newer(&key, Version(100), b"base", Expiry::Persistent)
                .await
                .unwrap()
        ),
        Version(100)
    );
    for version in [Version(99), Version(100)] {
        assert_eq!(
            p.set_if_newer(&key, version, b"stale", Expiry::Persistent)
                .await
                .unwrap(),
            WriteOutcome::ConditionNotMet
        );
    }
    let next = applied(
        p.compare_and_set(&key, Version(100), b"new", Expiry::Persistent)
            .await
            .unwrap(),
    );
    assert!(p.delete(&key).await.unwrap());
    assert_eq!(
        p.set_if_newer(&key, next, b"resurrect", Expiry::Persistent)
            .await
            .unwrap(),
        WriteOutcome::ConditionNotMet,
        "deletion must retain version history"
    );
    let recreated = applied(
        p.create_versioned(&key, b"new incarnation", Expiry::Persistent)
            .await
            .unwrap(),
    );
    assert!(recreated > next);
    assert_eq!(
        p.compare_and_set(&key, next, b"old owner", Expiry::Persistent)
            .await
            .unwrap(),
        WriteOutcome::ConditionNotMet,
        "delete/recreate must not allow ABA"
    );
    if p.supports(StoreCapability::VersionedKvConditionalDelete) {
        assert!(!p.delete_if_version(&key, next).await.unwrap());
        assert!(p.delete_if_version(&key, recreated).await.unwrap());
        assert_eq!(
            p.set_if_newer(&key, recreated, b"resurrect", Expiry::Persistent)
                .await
                .unwrap(),
            WriteOutcome::ConditionNotMet
        );
    }
}

/// advance must move provider time beyond TTL rounding/grace. Without MonotonicKv
/// expiration is supported, but no cross-incarnation version history is asserted.
pub async fn kv_expiry_contract_tests<P, F, Fut>(p: &P, prefix: &str, advance: F)
where
    P: VersionedKvStorePlugin + ?Sized,
    F: FnOnce() -> Fut,
    Fut: Future<Output = ()>,
{
    assert!(p.supports(StoreCapability::VersionedKvTtl));
    let key = format!("{prefix}:expiry");
    let old = applied(
        p.create_versioned(&key, b"old", Expiry::After(Duration::from_secs(1)))
            .await
            .unwrap(),
    );
    advance().await;
    assert_eq!(p.get_versioned(&key).await.unwrap(), None);
    assert_eq!(p.ttl(&key).await.unwrap(), KeyTtl::Missing);
    assert_eq!(
        p.compare_and_set(&key, old, b"stale", Expiry::Persistent)
            .await
            .unwrap(),
        WriteOutcome::ConditionNotMet
    );
    if p.supports(StoreCapability::MonotonicKv) {
        assert_eq!(
            p.set_if_newer(&key, old, b"old", Expiry::Persistent)
                .await
                .unwrap(),
            WriteOutcome::ConditionNotMet,
            "expiry must retain the version high-water mark"
        );
    }
    let new = applied(
        p.create_versioned(&key, b"fresh", Expiry::Persistent)
            .await
            .unwrap(),
    );
    if p.supports(StoreCapability::MonotonicKv) {
        assert!(new > old);
    }
}

/// Arm a provider/proxy to apply the next write and lose its response.
pub async fn kv_unknown_outcome_contract_tests<P, F>(p: &P, prefix: &str, arm: F)
where
    P: VersionedKvStorePlugin + ?Sized,
    F: FnOnce(),
{
    let key = format!("{prefix}:unknown");
    arm();
    assert!(matches!(
        p.create_versioned(&key, b"committed", Expiry::Persistent)
            .await,
        Err(StoreError::OutcomeUnknown(_))
    ));
    let stored = p.get_versioned(&key).await.unwrap().unwrap();
    assert_eq!(stored.value, b"committed");
    assert_eq!(
        p.create_versioned(&key, b"different", Expiry::Persistent)
            .await
            .unwrap(),
        WriteOutcome::ConditionNotMet
    );
    assert_eq!(p.get_versioned(&key).await.unwrap(), Some(stored));
}
