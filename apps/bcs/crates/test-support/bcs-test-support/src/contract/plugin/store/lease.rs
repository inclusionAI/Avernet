//! Reusable StrictLease conformance. Each provider supplies fresh keys and a
//! callback that advances its test clock or waits for real provider time.
use bcs_store_api::{lease::*, *};
use futures::future::join_all;
use std::{future::Future, time::Duration};

fn active(value: LeaseRegistration) -> LeaseSnapshot {
    match value {
        LeaseRegistration::Active(value) => value,
        other => panic!("expected active lease, got {other:?}"),
    }
}
fn alive(value: LeaseState) -> LeaseSnapshot {
    match value {
        LeaseState::Alive(value) => value,
        other => panic!("expected alive lease, got {other:?}"),
    }
}
fn unchanged(before: &LeaseSnapshot, after: &LeaseSnapshot) {
    assert_eq!(after.value, before.value);
    assert_eq!(after.version, before.version);
    assert!(
        after.remaining <= before.remaining,
        "rejected writes cannot extend validity"
    );
}

pub async fn lease_store_contract_tests<P: LeaseStorePlugin + ?Sized>(p: &P, prefix: &str) {
    assert!(p.supports(StoreCapability::StrictLease));
    let ttl = Duration::from_secs(30);
    let key = format!("{prefix}:live");
    assert_eq!(p.read_lease(&key).await.unwrap(), LeaseState::Absent);
    assert_eq!(
        p.update_lease(&key, Version(1), b"late", Some(ttl))
            .await
            .unwrap(),
        LeaseWrite::ConditionNotMet
    );
    assert_eq!(p.read_lease(&key).await.unwrap(), LeaseState::Absent);
    let first = active(p.register_lease(&key, b"original", ttl).await.unwrap());
    assert!(first.version.0 > 0 && !first.remaining.is_zero() && first.remaining <= ttl);
    assert_eq!(
        p.register_lease(&key, b"conflict", ttl).await.unwrap(),
        LeaseRegistration::ContentConflict
    );
    unchanged(&first, &alive(p.read_lease(&key).await.unwrap()));
    assert!(matches!(
        p.update_lease(&key, first.version, b"invalid", Some(Duration::ZERO))
            .await,
        Err(StoreError::InvalidInput(_))
    ));
    unchanged(&first, &alive(p.read_lease(&key).await.unwrap()));
    let updated = match p
        .update_lease(&key, first.version, b"draining", None)
        .await
        .unwrap()
    {
        LeaseWrite::Applied(value) => value,
        other => panic!("{other:?}"),
    };
    assert!(updated.version > first.version && updated.remaining <= first.remaining);
    assert_eq!(
        p.update_lease(&key, first.version, b"stale", Some(ttl))
            .await
            .unwrap(),
        LeaseWrite::ConditionNotMet
    );
    unchanged(&updated, &alive(p.read_lease(&key).await.unwrap()));
    // Registration identity is the original payload, even after a live edit.
    unchanged(
        &updated,
        &active(p.register_lease(&key, b"original", ttl).await.unwrap()),
    );
    assert_eq!(
        p.register_lease(&key, b"draining", ttl).await.unwrap(),
        LeaseRegistration::ContentConflict
    );
    unchanged(&updated, &alive(p.read_lease(&key).await.unwrap()));
    p.retire_lease(&key).await.unwrap();
    p.retire_lease(&key).await.unwrap();
    assert_eq!(p.read_lease(&key).await.unwrap(), LeaseState::Terminated);
    assert_eq!(
        p.update_lease(&key, updated.version, b"late", Some(ttl))
            .await
            .unwrap(),
        LeaseWrite::Terminated
    );
    assert_eq!(
        p.register_lease(&key, b"original", ttl).await.unwrap(),
        LeaseRegistration::Terminated
    );
    assert_eq!(p.read_lease(&key).await.unwrap(), LeaseState::Terminated);

    let late = format!("{prefix}:retire-before-register");
    p.retire_lease(&late).await.unwrap();
    assert_eq!(p.read_lease(&late).await.unwrap(), LeaseState::Terminated);
    assert_eq!(
        p.register_lease(&late, b"late", ttl).await.unwrap(),
        LeaseRegistration::Terminated
    );
    assert_eq!(p.read_lease(&late).await.unwrap(), LeaseState::Terminated);

    let concurrent = format!("{prefix}:concurrent");
    let first = active(p.register_lease(&concurrent, b"first", ttl).await.unwrap());
    let results = join_all((0u8..8).map(|writer| {
        let key = &concurrent;
        async move {
            p.update_lease(key, first.version, &[writer], None)
                .await
                .unwrap()
        }
    }))
    .await;
    let winners = results
        .iter()
        .filter_map(|result| match result {
            LeaseWrite::Applied(value) => Some(value),
            LeaseWrite::ConditionNotMet => None,
            LeaseWrite::Terminated => panic!("unexpired concurrent lease terminated"),
        })
        .collect::<Vec<_>>();
    assert_eq!(winners.len(), 1);
    unchanged(winners[0], &alive(p.read_lease(&concurrent).await.unwrap()));
}

/// `advance` advances the test clock or waits for provider time. The live check
/// halfway to expiry leaves two seconds of headroom for real-provider RTTs;
/// fixtures with slower I/O should use a controllable provider clock. Checking
/// both before and after the original deadline catches shortening and extension.
pub async fn lease_expiry_contract_tests<P, F, Fut>(p: &P, prefix: &str, mut advance: F)
where
    P: LeaseStorePlugin + ?Sized,
    F: FnMut(Duration) -> Fut,
    Fut: Future<Output = ()>,
{
    let ttl = Duration::from_secs(4);
    let step = Duration::from_secs(1);
    let key = format!("{prefix}:expiry");
    let direct_update = format!("{prefix}:expire-before-update");
    let direct_register = format!("{prefix}:expire-before-register");
    let first = active(p.register_lease(&key, b"original", ttl).await.unwrap());
    let pending_update = active(
        p.register_lease(&direct_update, b"original", ttl)
            .await
            .unwrap(),
    );
    p.register_lease(&direct_register, b"original", ttl)
        .await
        .unwrap();
    advance(step).await;
    let edited = match p
        .update_lease(&key, first.version, b"edited", None)
        .await
        .unwrap()
    {
        LeaseWrite::Applied(value) => value,
        other => panic!("{other:?}"),
    };
    // Retry with a much longer TTL must not postpone the original deadline.
    unchanged(
        &edited,
        &active(
            p.register_lease(&key, b"original", Duration::from_secs(60))
                .await
                .unwrap(),
        ),
    );
    assert_eq!(
        p.update_lease(&key, first.version, b"stale", Some(Duration::from_secs(60)))
            .await
            .unwrap(),
        LeaseWrite::ConditionNotMet
    );
    unchanged(&edited, &alive(p.read_lease(&key).await.unwrap()));
    advance(step).await;
    unchanged(&edited, &alive(p.read_lease(&key).await.unwrap()));
    advance(ttl - step * 2).await;
    // Mutations must themselves check expiry, without requiring a prior read.
    assert_eq!(
        p.update_lease(&direct_update, pending_update.version, b"late", Some(ttl))
            .await
            .unwrap(),
        LeaseWrite::Terminated
    );
    assert_eq!(
        p.register_lease(&direct_register, b"original", ttl)
            .await
            .unwrap(),
        LeaseRegistration::Terminated
    );
    assert_eq!(p.read_lease(&key).await.unwrap(), LeaseState::Terminated);
    assert_eq!(
        p.update_lease(&key, edited.version, b"late", Some(ttl))
            .await
            .unwrap(),
        LeaseWrite::Terminated
    );
    assert_eq!(
        p.register_lease(&key, b"original", ttl).await.unwrap(),
        LeaseRegistration::Terminated
    );
    advance(ttl).await;
    assert_eq!(p.read_lease(&key).await.unwrap(), LeaseState::Terminated);
}

/// Fixture-only setup must seed an unexpired lease at its final usable version.
pub async fn lease_version_exhaustion_contract_tests<P, F, Fut>(p: &P, key: &str, mut advance: F)
where
    P: LeaseStorePlugin + ?Sized,
    F: FnMut(Duration) -> Fut,
    Fut: Future<Output = ()>,
{
    let before = alive(p.read_lease(key).await.unwrap());
    assert_eq!(
        p.update_lease(
            key,
            before.version,
            b"overwrite",
            Some(Duration::from_secs(60))
        )
        .await,
        Err(StoreError::VersionExhausted)
    );
    unchanged(&before, &alive(p.read_lease(key).await.unwrap()));
    assert!(
        before.remaining >= Duration::from_secs(2),
        "seed enough expiry headroom"
    );
    let halfway = before.remaining / 2;
    advance(halfway).await;
    unchanged(&before, &alive(p.read_lease(key).await.unwrap()));
    advance(before.remaining - halfway).await;
    assert_eq!(p.read_lease(key).await.unwrap(), LeaseState::Terminated);
}
