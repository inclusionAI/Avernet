use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

use async_trait::async_trait;
use bcs::lifecycle::LifecycleOrchestrator;
use bcs_service_api::lifecycle::{LifecycleError, ServiceLifecycle};

struct CountingService {
    init_count: AtomicUsize,
    shutdown_count: AtomicUsize,
}

impl CountingService {
    fn new() -> Self {
        Self {
            init_count: AtomicUsize::new(0),
            shutdown_count: AtomicUsize::new(0),
        }
    }
}

#[async_trait]
impl ServiceLifecycle for CountingService {
    async fn initialize(&self) -> Result<(), LifecycleError> {
        self.init_count.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }

    async fn shutdown(&self) -> Result<(), LifecycleError> {
        self.shutdown_count.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
}

struct FailingService {
    initialized: AtomicBool,
}

#[async_trait]
impl ServiceLifecycle for FailingService {
    async fn initialize(&self) -> Result<(), LifecycleError> {
        self.initialized.store(true, Ordering::SeqCst);
        Err(LifecycleError::Precondition("failed for test".to_string()))
    }

    async fn shutdown(&self) -> Result<(), LifecycleError> {
        Ok(())
    }
}

#[tokio::test]
async fn orchestrator_initializes_then_shuts_down_registered_services() {
    let first = Arc::new(CountingService::new());
    let second = Arc::new(CountingService::new());

    let mut orchestrator = LifecycleOrchestrator::new();
    orchestrator.register("first", first.clone());
    orchestrator.register("second", second.clone());

    orchestrator.initialize_all().await.expect("initialize all");
    orchestrator.shutdown_all().await.expect("shutdown all");

    assert_eq!(first.init_count.load(Ordering::SeqCst), 1);
    assert_eq!(second.init_count.load(Ordering::SeqCst), 1);
    assert_eq!(first.shutdown_count.load(Ordering::SeqCst), 1);
    assert_eq!(second.shutdown_count.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn orchestrator_rolls_back_initialized_services_after_failure() {
    let first = Arc::new(CountingService::new());
    let failing = Arc::new(FailingService {
        initialized: AtomicBool::new(false),
    });

    let mut orchestrator = LifecycleOrchestrator::new();
    orchestrator.register("first", first.clone());
    orchestrator.register("failing", failing.clone());

    let result = orchestrator.initialize_all().await;

    assert!(result.is_err());
    assert!(failing.initialized.load(Ordering::SeqCst));
    assert_eq!(first.init_count.load(Ordering::SeqCst), 1);
    assert_eq!(first.shutdown_count.load(Ordering::SeqCst), 1);
}
