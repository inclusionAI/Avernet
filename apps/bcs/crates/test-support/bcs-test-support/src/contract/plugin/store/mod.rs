//! Reusable provider tests. Each call needs a fresh isolated prefix. A provider
//! mounts every harness for capabilities it advertises; unsupported capability
//! behavior is checked separately. Passing a test double is NOT backend certification.
#![allow(
    clippy::expect_used,
    clippy::unwrap_used,
    reason = "contract assertions"
)]

pub mod json;
pub mod document;
pub mod kv;
pub mod stream;
pub mod zset;

use bcs_store_api::*;

pub fn store_plugin_contract_tests<P: StorePlugin + ?Sized>(p: &P) {
    for (extension, base) in [
        (StoreCapability::ConditionalKv, StoreCapability::VersionedKv),
        (
            StoreCapability::VersionedKvTtl,
            StoreCapability::ConditionalKv,
        ),
        (
            StoreCapability::VersionedKvConditionalDelete,
            StoreCapability::ConditionalKv,
        ),
        (StoreCapability::MonotonicKv, StoreCapability::ConditionalKv),
        (StoreCapability::StreamRecovery, StoreCapability::Stream),
        (StoreCapability::VersionedZSet, StoreCapability::ZSet),
        (StoreCapability::StreamGroups, StoreCapability::Stream),
    ] {
        assert!(!p.supports(extension) || p.supports(base));
        assert_eq!(p.supports(extension), p.supports(extension));
    }
}

pub(super) fn applied(outcome: WriteOutcome) -> Version {
    match outcome {
        WriteOutcome::Applied { version } => {
            assert!(version.0 > 0);
            version
        }
        other => panic!("expected applied write, got {other:?}"),
    }
}
