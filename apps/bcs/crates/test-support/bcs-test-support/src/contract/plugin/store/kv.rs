use super::applied;
use bcs_store_api::{kv::KvStorePlugin, *};
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

pub async fn kv_monotonic_contract_tests<P: KvStorePlugin + ?Sized>(p: &P, prefix: &str) {
    assert!(p.supports(StoreCapability::MonotonicKv));
    let key = format!("{prefix}:cas");
    applied(
        p.set_if_newer(&key, Version(100), b"base", Expiry::Persistent)
            .await
            .unwrap(),
    );
    let base = p.get_versioned(&key).await.unwrap().unwrap();
    assert_eq!(base.version, Version(100));
    for v in [Version(99), Version(100)] {
        assert_eq!(
            p.set_if_newer(&key, v, b"stale", Expiry::After(Duration::from_secs(1)))
                .await
                .unwrap(),
            WriteOutcome::ConditionNotMet
        );
    }
    assert_eq!(p.ttl(&key).await.unwrap(), KeyTtl::Persistent);
    let (a, b) = tokio::join!(
        p.compare_and_set(&key, base.version, b"a"),
        p.compare_and_set(&key, base.version, b"b")
    );
    let a = a.unwrap();
    let b = b.unwrap();
    assert!(
        matches!(
            (&a, &b),
            (WriteOutcome::Applied { .. }, WriteOutcome::ConditionNotMet)
                | (WriteOutcome::ConditionNotMet, WriteOutcome::Applied { .. })
        ),
        "CAS must have one winner"
    );
    let latest = p.get_versioned(&key).await.unwrap().unwrap();
    assert!(latest.version > base.version);
    assert_eq!(
        latest.value,
        if matches!(a, WriteOutcome::Applied { .. }) {
            b"a"
        } else {
            b"b"
        }
    );
    assert_eq!(
        p.delete_if_version(&key, base.version).await.unwrap(),
        WriteOutcome::ConditionNotMet
    );
    applied(p.delete_if_version(&key, latest.version).await.unwrap());
    assert_eq!(
        p.set_if_newer(&key, base.version, b"resurrect", Expiry::Persistent)
            .await
            .unwrap(),
        WriteOutcome::ConditionNotMet
    );
    assert!(
        p.put_if_absent(&key, b"new incarnation", Expiry::Persistent)
            .await
            .unwrap()
    );
    assert_eq!(
        p.compare_and_set(&key, latest.version, b"old owner")
            .await
            .unwrap(),
        WriteOutcome::ConditionNotMet,
        "delete/recreate must not allow ABA"
    );
    let before_plain = p.get_versioned(&key).await.unwrap().unwrap();
    p.set(&key, &before_plain.value, Expiry::Preserve)
        .await
        .unwrap();
    assert_eq!(
        p.compare_and_set(&key, before_plain.version, b"stale")
            .await
            .unwrap(),
        WriteOutcome::ConditionNotMet,
        "plain writes must invalidate CAS tokens"
    );
}

/// advance must move provider time beyond its documented TTL rounding/grace;
/// real providers may await wall time, test fixtures may advance a manual clock.
pub async fn kv_expiry_contract_tests<P, F, Fut>(p: &P, prefix: &str, advance: F)
where
    P: KvStorePlugin + ?Sized,
    F: FnOnce() -> Fut,
    Fut: Future<Output = ()>,
{
    let key = format!("{prefix}:expiry");
    applied(
        p.set_if_newer(
            &key,
            Version(50),
            b"old",
            Expiry::After(Duration::from_secs(1)),
        )
        .await
        .unwrap(),
    );
    advance().await;
    assert_eq!(p.get(&key).await.unwrap(), None);
    assert_eq!(p.ttl(&key).await.unwrap(), KeyTtl::Missing);
    assert_eq!(
        p.set_if_newer(&key, Version(50), b"old", Expiry::Persistent)
            .await
            .unwrap(),
        WriteOutcome::ConditionNotMet,
        "expiry must retain the version high-water mark"
    );
    assert!(
        p.put_if_absent(&key, b"fresh", Expiry::Persistent)
            .await
            .unwrap()
    );
    assert!(p.get_versioned(&key).await.unwrap().unwrap().version > Version(50));
}

/// Arm a provider/proxy to apply the next write and lose its response.
pub async fn kv_unknown_outcome_contract_tests<P, F>(p: &P, prefix: &str, arm: F)
where
    P: KvStorePlugin + ?Sized,
    F: FnOnce(),
{
    let key = format!("{prefix}:unknown");
    arm();
    assert!(matches!(
        p.set_if_newer(&key, Version(10), b"committed", Expiry::Persistent)
            .await,
        Err(StoreError::OutcomeUnknown(_))
    ));
    let stored = p.get_versioned(&key).await.unwrap().unwrap();
    assert_eq!(stored.value, b"committed");
    assert_eq!(stored.version, Version(10));
    assert_eq!(
        p.set_if_newer(&key, Version(10), b"different", Expiry::Preserve)
            .await
            .unwrap(),
        WriteOutcome::ConditionNotMet
    );
    assert_eq!(p.get(&key).await.unwrap(), Some(b"committed".to_vec()));
}

/// Basic CAS needs no caller-assigned logical version or cross-incarnation history.
pub async fn kv_conditional_contract_tests<P: KvStorePlugin + ?Sized>(p: &P, prefix: &str) {
    assert!(p.supports(StoreCapability::ConditionalKv));
    let key = format!("{prefix}:cas");
    assert_eq!(p.get_versioned(&key).await.unwrap(), None);
    let initial = applied(p.create_versioned(&key, &[0, 255, 1]).await.unwrap());
    assert_eq!(
        p.create_versioned(&key, b"duplicate").await.unwrap(),
        WriteOutcome::ConditionNotMet
    );
    let base = p.get_versioned(&key).await.unwrap().unwrap();
    assert_eq!(base.value, vec![0, 255, 1]);
    assert_eq!(base.version, initial);
    let (a, b) = tokio::join!(
        p.compare_and_set(&key, initial, b"a"),
        p.compare_and_set(&key, initial, b"b")
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
        p.compare_and_set(&key, initial, b"stale").await.unwrap(),
        WriteOutcome::ConditionNotMet
    );
    let next = applied(
        p.compare_and_set(&key, latest.version, &latest.value)
            .await
            .unwrap(),
    );
    assert!(next > latest.version);
    assert!(matches!(
        p.compare_and_set(&key, Version(0), b"bad").await,
        Err(StoreError::InvalidInput(_))
    ));
}
