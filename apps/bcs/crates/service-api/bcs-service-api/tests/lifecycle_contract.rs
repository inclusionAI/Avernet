use bcs_service_api::lifecycle::{LifecycleError, ServiceLifecycle};

struct TestService;

#[async_trait::async_trait]
impl ServiceLifecycle for TestService {}

#[tokio::test]
async fn lifecycle_default_is_no_op() {
    let svc = TestService;

    assert!(svc.initialize().await.is_ok());
    assert!(svc.shutdown().await.is_ok());
}

#[test]
fn lifecycle_error_display_includes_msg() {
    let err = LifecycleError::Precondition("missing X".into());

    assert!(format!("{err}").contains("missing X"));
}
