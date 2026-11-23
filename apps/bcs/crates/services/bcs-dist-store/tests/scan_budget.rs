#![allow(clippy::unwrap_used, reason = "shared scan resource assertions")]
use bcs_dist_store::budget::{ScanBudgetConfig, SharedScanBudget};
use bcs_service_api::port::repo::distributed::budget::*;
#[test]
fn shared_io_cannot_exceed_eight_or_one_quarter_of_the_pool() {
    let budget = SharedScanBudget::new(ScanBudgetConfig::default()).unwrap();
    let permits = (0..8).map(|_| budget.try_io().unwrap()).collect::<Vec<_>>();
    assert!(budget.try_io().is_none());
    drop(permits);
    assert!(budget.try_io().is_some());
    assert!(
        SharedScanBudget::new(ScanBudgetConfig {
            pool_size: 16,
            ..Default::default()
        })
        .is_err()
    );
}
#[tokio::test(start_paused = true)]
async fn busy_repair_cannot_spend_discovery_or_dispatch_reservations_or_exceed_the_burst() {
    let budget = SharedScanBudget::new(ScanBudgetConfig::default()).unwrap();
    assert!(budget.claim(ScanClass::Repair, 64, 64).unwrap());
    assert!(!budget.claim(ScanClass::Repair, 1, 1).unwrap());
    assert!(budget.claim(ScanClass::Discovery, 32, 32).unwrap());
    assert!(budget.claim(ScanClass::Dispatch, 32, 32).unwrap());
    assert!(!budget.claim(ScanClass::Dispatch, 1, 1).unwrap());
    tokio::time::advance(std::time::Duration::from_millis(20)).await;
    assert!(budget.claim(ScanClass::Dispatch, 1, 1).unwrap());
    assert!(budget.claim(ScanClass::Discovery, 1, 1).unwrap());
}
#[test]
fn failed_dual_budget_reservation_does_not_spend_the_other_dimension() {
    let budget = SharedScanBudget::new(ScanBudgetConfig::default()).unwrap();
    assert!(!budget.claim(ScanClass::Repair, 1, 65).unwrap());
    assert!(budget.claim(ScanClass::Repair, 64, 0).unwrap());
    assert!(!budget.claim(ScanClass::Repair, 1, 0).unwrap());
}
