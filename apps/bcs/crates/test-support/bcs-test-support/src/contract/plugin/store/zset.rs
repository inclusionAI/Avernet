use super::applied;
use bcs_store_api::{zset::*, *};
use std::{num::NonZeroU32, ops::Bound};

pub fn all() -> ZSetRange {
    ZSetRange {
        min: Bound::Unbounded,
        max: Bound::Unbounded,
        after: None,
        limit: NonZeroU32::new(100).unwrap(),
    }
}
fn entry(member: &str, score: f64) -> ZSetEntry {
    ZSetEntry {
        member: member.into(),
        score,
    }
}
fn put(member: &str, score: f64) -> ZSetMutation {
    ZSetMutation::Put {
        entry: entry(member, score),
        mode: ZAddMode::Upsert,
    }
}

pub async fn z_set_store_plugin_contract_tests<P: ZSetStorePlugin + ?Sized>(p: &P, prefix: &str) {
    assert!(p.supports(StoreCapability::ZSet));
    let key = format!("{prefix}:zset");
    assert_eq!(
        p.add(
            &key,
            &[
                entry("b", 1.),
                entry("a", 1.),
                entry("later", f64::INFINITY)
            ],
            ZAddMode::Upsert
        )
        .await
        .unwrap(),
        3
    );
    let mut range = all();
    range.limit = NonZeroU32::new(1).unwrap();
    let first = p.range(&key, &range).await.unwrap();
    assert_eq!(first, vec![entry("a", 1.)]);
    range.after = first.last().cloned();
    assert_eq!(p.range(&key, &range).await.unwrap(), vec![entry("b", 1.)]);
    assert_eq!(
        p.add(&key, &[entry("a", 2.)], ZAddMode::Lower)
            .await
            .unwrap(),
        0
    );
    assert_eq!(
        p.add(&key, &[entry("a", 2.)], ZAddMode::UpdateOnly)
            .await
            .unwrap(),
        1
    );
    let before = p.range(&key, &all()).await.unwrap();
    assert!(matches!(
        p.add(
            &key,
            &[entry("valid", 0.), entry("invalid", f64::NAN)],
            ZAddMode::Upsert
        )
        .await,
        Err(StoreError::InvalidInput(_))
    ));
    assert_eq!(p.range(&key, &all()).await.unwrap(), before);
    assert_eq!(
        p.remove(&key, &["a".into(), "absent".into()])
            .await
            .unwrap(),
        1
    );
}

pub async fn versioned_zset_contract_tests<P: ZSetStorePlugin + ?Sized>(p: &P, prefix: &str) {
    assert!(p.supports(StoreCapability::VersionedZSet));
    let key = format!("{prefix}:versioned-zset");
    let v = applied(
        p.update_if_version(&key, None, &[put("a", 10.)])
            .await
            .unwrap(),
    );
    let snapshot = p.snapshot(&key, &all()).await.unwrap();
    assert_eq!(snapshot.version, Some(v));
    let next = applied(
        p.update_if_version(&key, Some(v), &[put("a", 10.)])
            .await
            .unwrap(),
    );
    assert!(
        next > v,
        "same-score activation must advance collection version"
    );
    assert_eq!(
        p.update_if_version(&key, Some(v), &[put("a", 1000.)])
            .await
            .unwrap(),
        WriteOutcome::ConditionNotMet,
        "old settle must not postpone fresh activation"
    );
    assert_eq!(p.range(&key, &all()).await.unwrap(), vec![entry("a", 10.)]);
    let current = applied(
        p.update_if_version(&key, Some(next), &[put("other", 20.)])
            .await
            .unwrap(),
    );
    assert_eq!(
        p.update_if_version(&key, Some(next), &[put("a", 1000.)])
            .await
            .unwrap(),
        WriteOutcome::ConditionNotMet
    );
    p.add(&key, &[entry("a", 10.)], ZAddMode::Lower)
        .await
        .unwrap();
    assert_eq!(
        p.update_if_version(&key, Some(current), &[put("a", 1000.)])
            .await
            .unwrap(),
        WriteOutcome::ConditionNotMet,
        "ordinary no-op add must invalidate a version token"
    );
    p.remove(&key, &["a".into(), "other".into()]).await.unwrap();
    let empty = p.snapshot(&key, &all()).await.unwrap();
    assert!(empty.entries.is_empty());
    assert!(empty.version.unwrap() > current);
    assert_eq!(
        p.update_if_version(&key, None, &[put("a", 0.)])
            .await
            .unwrap(),
        WriteOutcome::ConditionNotMet
    );
    let before = p.snapshot(&key, &all()).await.unwrap();
    assert!(matches!(
        p.update_if_version(&key, before.version, &[put("ok", 0.), put("bad", f64::NAN)])
            .await,
        Err(StoreError::InvalidInput(_))
    ));
    assert_eq!(p.snapshot(&key, &all()).await.unwrap(), before);
    assert!(
        matches!(
            p.update_if_version(&key, Some(Version(0)), &[put("invalid", 0.)])
                .await,
            Err(StoreError::InvalidInput(_))
        ),
        "zero version must be invalid input"
    );

    let race_key = format!("{prefix}:zset-race");
    let initial = applied(
        p.update_if_version(&race_key, None, &[put("a", 0.)])
            .await
            .unwrap(),
    );
    let left_changes = [put("a", 1.)];
    let right_changes = [put("a", 2.)];
    let (left, right) = tokio::join!(
        p.update_if_version(&race_key, Some(initial), &left_changes),
        p.update_if_version(&race_key, Some(initial), &right_changes)
    );
    let left = left.unwrap();
    let right = right.unwrap();
    assert!(
        matches!(
            (&left, &right),
            (WriteOutcome::Applied { .. }, WriteOutcome::ConditionNotMet)
                | (WriteOutcome::ConditionNotMet, WriteOutcome::Applied { .. })
        ),
        "ZSet CAS must have one winner"
    );
    let expected = if matches!(left, WriteOutcome::Applied { .. }) {
        1.
    } else {
        2.
    };
    assert_eq!(
        p.range(&race_key, &all()).await.unwrap(),
        vec![entry("a", expected)]
    );
}
