//! Lifecycle contract harnesses.

use bcs_service_api::lifecycle::ServiceLifecycle;

pub async fn service_lifecycle_contract_tests<T: ServiceLifecycle + ?Sized>(svc: &T) {
    svc.shutdown()
        .await
        .expect("shutdown without initialize must be idempotent");
    svc.initialize().await.expect("first initialize");
    svc.initialize()
        .await
        .expect("second initialize must be idempotent");
    svc.shutdown().await.expect("first shutdown");
    svc.shutdown()
        .await
        .expect("second shutdown must be idempotent");
}
