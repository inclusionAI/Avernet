#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "contract assertions"
)]
use async_trait::async_trait;
use bcs_store_api::{lease::*, *};
use bcs_test_support::{
    contract::plugin::store::lease::lease_expiry_contract_tests, store_model::Model,
};
use std::time::Duration;

/// Deliberately incorrect adapters prove the portable suite detects the two
/// TTL implementations that must never advertise StrictLease.
#[derive(Default)]
struct IncorrectLease {
    model: Model,
    forget_expired: bool,
    refresh_registration: bool,
    shorten_rejected: bool,
}
impl StorePlugin for IncorrectLease {
    fn supports(&self, capability: StoreCapability) -> bool {
        self.model.supports(capability)
    }
}
#[async_trait]
impl LeaseStorePlugin for IncorrectLease {
    async fn register_lease(
        &self,
        key: &str,
        value: &[u8],
        ttl: Duration,
    ) -> StoreResult<LeaseRegistration> {
        if self.refresh_registration
            && let LeaseState::Alive(snapshot) = self.model.read_lease(key).await?
            && let LeaseWrite::Applied(snapshot) = self.model
                .update_lease(key, snapshot.version, &snapshot.value, Some(ttl)).await?
        {
            return Ok(LeaseRegistration::Active(snapshot));
        }
        self.model.register_lease(key, value, ttl).await
    }
    async fn read_lease(&self, key: &str) -> StoreResult<LeaseState> {
        let state = self.model.read_lease(key).await?;
        Ok(if self.forget_expired && state == LeaseState::Terminated {
            LeaseState::Absent
        } else {
            state
        })
    }
    async fn update_lease(
        &self,
        key: &str,
        expected: Version,
        value: &[u8],
        ttl: Option<Duration>,
    ) -> StoreResult<LeaseWrite> {
        let result = self.model.update_lease(key, expected, value, ttl).await?;
        if self.shorten_rejected && result == LeaseWrite::ConditionNotMet {
            self.model
                .shorten_lease_deadline(key, Duration::from_millis(100));
        }
        Ok(result)
    }
    async fn retire_lease(&self, key: &str) -> StoreResult<()> {
        self.model.retire_lease(key).await
    }
}
#[tokio::test]
#[should_panic]
async fn strict_lease_suite_rejects_disappearing_expiry_evidence() {
    let provider = IncorrectLease {
        forget_expired: true,
        ..Default::default()
    };
    lease_expiry_contract_tests(&provider, "forget", |duration| {
        provider.model.advance(duration);
        std::future::ready(())
    })
    .await;
}
#[tokio::test]
#[should_panic]
async fn strict_lease_suite_rejects_registration_that_refreshes_the_deadline() {
    let provider = IncorrectLease {
        refresh_registration: true,
        ..Default::default()
    };
    lease_expiry_contract_tests(&provider, "refresh", |duration| {
        provider.model.advance(duration);
        std::future::ready(())
    })
    .await;
}

#[tokio::test]
#[should_panic(expected = "expected alive lease")]
async fn strict_lease_suite_rejects_shortened_deadline_after_condition_failure() {
    let provider = IncorrectLease {
        shorten_rejected: true,
        ..Default::default()
    };
    lease_expiry_contract_tests(&provider, "shorten", |duration| {
        provider.model.advance(duration);
        std::future::ready(())
    })
    .await;
}
