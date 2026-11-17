use bcs_store_api::{
    json::JsonStorePlugin,
    kv::{KvStorePlugin, VersionedKvStorePlugin},
    stream::*,
    zset::ZSetStorePlugin,
    *,
};

#[test]
fn plugin_traits_are_object_safe() {
    fn accepts(
        _: &dyn StorePlugin,
        _: &dyn KvStorePlugin,
        _: &dyn VersionedKvStorePlugin,
        _: &dyn JsonStorePlugin,
        _: &dyn ZSetStorePlugin,
        _: &dyn StreamStorePlugin,
    ) {
    }
    let _ = accepts;
}

#[test]
fn stream_ids_order_by_components_not_text() {
    assert!(
        StreamId {
            major: 10,
            minor: 0
        } > StreamId {
            major: 9,
            minor: 99
        }
    );
    assert!(
        StreamId {
            major: 10,
            minor: 1
        } > StreamId {
            major: 10,
            minor: 0
        }
    );
}

#[test]
fn condition_rejection_is_not_an_unknown_write() {
    assert_ne!(
        WriteOutcome::ConditionNotMet,
        WriteOutcome::Applied {
            version: Version(7)
        }
    );
    assert!(matches!(
        StoreError::OutcomeUnknown("response lost".into()),
        StoreError::OutcomeUnknown(_)
    ));
}
