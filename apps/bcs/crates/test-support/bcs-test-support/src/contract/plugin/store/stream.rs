use bcs_store_api::{stream::*, *};
use std::{collections::BTreeMap, num::NonZeroU32, ops::Bound, time::Duration};

fn id(major: u64) -> StreamId {
    StreamId { major, minor: 0 }
}
fn fields(value: u8) -> BTreeMap<String, Vec<u8>> {
    BTreeMap::from([("body".into(), vec![0, 255, value])])
}
fn options(n: u32) -> ReadOptions {
    ReadOptions {
        max_entries: NonZeroU32::new(n).unwrap(),
        block: None,
    }
}

pub async fn stream_store_plugin_contract_tests<P: StreamStorePlugin + ?Sized>(
    p: &P,
    prefix: &str,
) {
    assert!(p.supports(StoreCapability::Stream));
    let a = format!("{prefix}:stream-a");
    let b = format!("{prefix}:stream-b");
    for n in [10, 20, 30] {
        assert_eq!(
            p.append(&a, AppendId::Explicit(id(n)), &fields(n as u8))
                .await
                .unwrap(),
            id(n)
        );
    }
    for n in [10, 20, 30] {
        assert_eq!(
            p.append(&a, AppendId::Explicit(id(n)), &fields(99)).await,
            Err(StoreError::IdNotIncreasing)
        );
    }
    let b1 = p.append(&b, AppendId::Auto, &fields(1)).await.unwrap();
    let b2 = p.append(&b, AppendId::Auto, &fields(2)).await.unwrap();
    assert!(b2 > b1);
    let reads = [
        StreamRead {
            key: a.clone(),
            after: Some(id(10)),
        },
        StreamRead {
            key: b.clone(),
            after: None,
        },
    ];
    let batches = p.read(&reads, options(10)).await.unwrap();
    assert_eq!(batches.len(), 2);
    assert_eq!(batches[0].key, a);
    assert_eq!(batches[1].key, b);
    assert_eq!(
        batches[0]
            .page
            .entries
            .iter()
            .map(|x| x.id)
            .collect::<Vec<_>>(),
        vec![id(20), id(30)]
    );
    assert_eq!(batches[1].page.entries[0].id, b1);
    assert!(
        p.read(&reads, options(1))
            .await
            .unwrap()
            .iter()
            .map(|x| x.page.entries.len())
            .sum::<usize>()
            <= 1
    );
    let reverse = p
        .range(
            &a,
            Bound::Unbounded,
            Bound::Included(id(30)),
            NonZeroU32::new(1).unwrap(),
            true,
        )
        .await
        .unwrap();
    assert_eq!(reverse.entries[0].id, id(30));
    let trimmed = p.trim(&a, TrimPolicy::Before(id(20))).await.unwrap();
    assert_eq!(trimmed.removed, 1);
    if p.supports(StoreCapability::StreamRecovery) {
        assert_eq!(
            trimmed.recovery.as_ref().unwrap().removed_through,
            Some(id(10))
        );
    } else {
        assert!(trimmed.recovery.is_none());
    }
    let page = p
        .range(
            &a,
            Bound::Unbounded,
            Bound::Unbounded,
            NonZeroU32::new(10).unwrap(),
            false,
        )
        .await
        .unwrap();
    assert_eq!(page.entries[0].id, id(20));
    if p.supports(StoreCapability::StreamRecovery) {
        assert_eq!(
            page.recovery.as_ref().unwrap().removed_through,
            Some(id(10))
        );
    } else {
        assert!(page.recovery.is_none());
    }
    p.trim(&a, TrimPolicy::MaxLen(0)).await.unwrap();
    let empty = p
        .range(
            &a,
            Bound::Unbounded,
            Bound::Unbounded,
            NonZeroU32::new(10).unwrap(),
            false,
        )
        .await
        .unwrap();
    assert!(empty.entries.is_empty());
    if p.supports(StoreCapability::StreamRecovery) {
        let evidence = empty.recovery.as_ref().unwrap();
        assert_eq!(evidence.last_id, Some(id(30)));
        assert_eq!(evidence.removed_through, Some(id(30)));
    } else {
        assert!(empty.recovery.is_none());
    }
    assert_eq!(
        p.append(&a, AppendId::Explicit(id(30)), &fields(30)).await,
        Err(StoreError::IdNotIncreasing)
    );
    assert!(p.append(&a, AppendId::Auto, &fields(31)).await.unwrap() > id(30));
    assert!(matches!(
        p.append(&a, AppendId::Auto, &BTreeMap::new()).await,
        Err(StoreError::InvalidInput(_))
    ));
}

pub async fn stream_group_contract_tests<P: StreamStorePlugin + ?Sized>(p: &P, prefix: &str) {
    assert!(p.supports(StoreCapability::StreamGroups));
    let key = format!("{prefix}:group");
    assert_eq!(
        p.create_group(&key, "g", GroupStart::Beginning)
            .await
            .unwrap(),
        GroupCreateOutcome::Created
    );
    for n in [10, 20] {
        p.append(&key, AppendId::Explicit(id(n)), &fields(n as u8))
            .await
            .unwrap();
    }
    let delivered = p
        .read_group(&key, "g", "old-inc", options(10))
        .await
        .unwrap();
    assert_eq!(delivered.len(), 2);
    assert_eq!(
        p.create_group(&key, "g", GroupStart::Beginning)
            .await
            .unwrap(),
        GroupCreateOutcome::AlreadyExists
    );
    assert!(
        p.read_group(&key, "g", "new-inc", options(10))
            .await
            .unwrap()
            .is_empty()
    );
    assert!(
        p.claim_idle(
            &key,
            "g",
            "new-inc",
            Duration::from_secs(3600),
            None,
            NonZeroU32::new(10).unwrap()
        )
        .await
        .unwrap()
        .entries
        .is_empty()
    );
    let claimed = p
        .claim_idle(
            &key,
            "g",
            "new-inc",
            Duration::ZERO,
            None,
            NonZeroU32::new(1).unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(claimed.entries.len(), 1);
    assert!(claimed.next.is_some());
    let second = p
        .claim_idle(
            &key,
            "g",
            "new-inc",
            Duration::ZERO,
            claimed.next,
            NonZeroU32::new(10).unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(
        second.entries.iter().map(|x| x.id).collect::<Vec<_>>(),
        vec![id(20)]
    );
    assert_eq!(p.ack(&key, "g", &[id(10), id(10)]).await.unwrap(), 1);
    assert_eq!(p.ack(&key, "g", &[id(10)]).await.unwrap(), 0);
    p.trim(&key, TrimPolicy::MaxLen(0)).await.unwrap();
    let deleted = p
        .claim_idle(
            &key,
            "g",
            "new-inc",
            Duration::ZERO,
            None,
            NonZeroU32::new(10).unwrap(),
        )
        .await
        .unwrap();
    assert!(deleted.entries.is_empty());
    assert_eq!(deleted.deleted, vec![id(20)]);
    assert_eq!(p.ack(&key, "missing", &[id(10)]).await, Ok(0));
}

/// Plain-read cancellation must not consume input; a subsequent reader still
/// observes the same entry. Real connection-pool exhaustion needs provider tests.
pub async fn stream_blocking_contract_tests<P: StreamStorePlugin + ?Sized>(p: &P, prefix: &str) {
    let key = format!("{prefix}:blocking");
    let read = [StreamRead {
        key: key.clone(),
        after: None,
    }];
    let options = ReadOptions {
        max_entries: NonZeroU32::new(10).unwrap(),
        block: Some(Duration::from_secs(1)),
    };
    assert!(
        tokio::time::timeout(Duration::from_millis(5), p.read(&read, options))
            .await
            .is_err()
    );
    let writer = async {
        tokio::time::sleep(Duration::from_millis(5)).await;
        p.append(
            &key,
            AppendId::Auto,
            &BTreeMap::from([("body".into(), b"payload".to_vec())]),
        )
        .await
        .unwrap()
    };
    let (result, id) = tokio::join!(p.read(&read, options), writer);
    assert_eq!(result.unwrap()[0].page.entries[0].id, id);
    assert_eq!(
        p.read(
            &read,
            ReadOptions {
                block: None,
                ..options
            }
        )
        .await
        .unwrap()[0]
            .page
            .entries[0]
            .id,
        id
    );
}
