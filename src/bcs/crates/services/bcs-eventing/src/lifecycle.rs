//! Managed Eventing worker lifecycle.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use async_trait::async_trait;
use bcs_service_api::lifecycle::{LifecycleError, ServiceLifecycle};
use tokio::task::JoinHandle;
use tokio_util::sync::CancellationToken;

use crate::{EventDispatcher, EventFanoutWorker, EventRetentionWorker};

struct RunningWorkers {
    cancellation: CancellationToken,
    handles: Vec<JoinHandle<()>>,
}

pub struct EventingLifecycle {
    fanout: Arc<EventFanoutWorker>,
    dispatcher: Option<Arc<EventDispatcher>>,
    retention: Arc<EventRetentionWorker>,
    fanout_poll_interval: Duration,
    delivery_poll_interval: Duration,
    retention_poll_interval: Duration,
    shutdown_timeout: Duration,
    running: Mutex<Option<RunningWorkers>>,
}

impl EventingLifecycle {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        fanout: Arc<EventFanoutWorker>,
        dispatcher: Option<Arc<EventDispatcher>>,
        retention: Arc<EventRetentionWorker>,
        fanout_poll_interval: Duration,
        delivery_poll_interval: Duration,
        retention_poll_interval: Duration,
        shutdown_timeout: Duration,
    ) -> Result<Self, LifecycleError> {
        if fanout_poll_interval.is_zero()
            || delivery_poll_interval.is_zero()
            || retention_poll_interval.is_zero()
            || shutdown_timeout.is_zero()
        {
            return Err(LifecycleError::Precondition(
                "Eventing lifecycle intervals must be non-zero".to_string(),
            ));
        }
        Ok(Self {
            fanout,
            dispatcher,
            retention,
            fanout_poll_interval,
            delivery_poll_interval,
            retention_poll_interval,
            shutdown_timeout,
            running: Mutex::new(None),
        })
    }
}

#[async_trait]
impl ServiceLifecycle for EventingLifecycle {
    async fn initialize(&self) -> Result<(), LifecycleError> {
        let mut running = self
            .running
            .lock()
            .map_err(|_| LifecycleError::Transient("Eventing lifecycle lock poisoned".into()))?;
        if running.is_some() {
            return Ok(());
        }
        let cancellation = CancellationToken::new();
        let mut handles = Vec::with_capacity(3);

        let fanout = self.fanout.clone();
        let fanout_cancel = cancellation.clone();
        let fanout_interval = self.fanout_poll_interval;
        handles.push(tokio::spawn(async move {
            worker_loop(fanout_cancel, fanout_interval, move || {
                let fanout = fanout.clone();
                async move {
                    let _ = fanout.run_once("eventing-fanout").await;
                }
            })
            .await;
        }));

        if let Some(dispatcher) = self.dispatcher.clone() {
            let delivery_cancel = cancellation.clone();
            let delivery_interval = self.delivery_poll_interval;
            handles.push(tokio::spawn(async move {
                worker_loop(delivery_cancel, delivery_interval, move || {
                    let dispatcher = dispatcher.clone();
                    async move {
                        let _ = dispatcher.run_once("eventing-dispatcher").await;
                    }
                })
                .await;
            }));
        }

        let retention = self.retention.clone();
        let retention_cancel = cancellation.clone();
        let retention_interval = self.retention_poll_interval;
        handles.push(tokio::spawn(async move {
            worker_loop(retention_cancel, retention_interval, move || {
                let retention = retention.clone();
                async move {
                    let _ = retention.run_once().await;
                }
            })
            .await;
        }));

        *running = Some(RunningWorkers {
            cancellation,
            handles,
        });
        Ok(())
    }

    async fn shutdown(&self) -> Result<(), LifecycleError> {
        let workers = self
            .running
            .lock()
            .map_err(|_| LifecycleError::ShutdownFailed("Eventing lifecycle lock poisoned".into()))?
            .take();
        let Some(workers) = workers else {
            return Ok(());
        };
        workers.cancellation.cancel();
        let deadline = tokio::time::Instant::now() + self.shutdown_timeout;
        for mut handle in workers.handles {
            let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
            match tokio::time::timeout(remaining, &mut handle).await {
                Ok(result) => result.map_err(|error| {
                    LifecycleError::ShutdownFailed(format!("Eventing worker join failed: {error}"))
                })?,
                Err(_) => {
                    handle.abort();
                    let _ = handle.await;
                    return Err(LifecycleError::ShutdownTimeout(
                        "Eventing workers did not stop in time".into(),
                    ));
                }
            }
        }
        Ok(())
    }
}

async fn worker_loop<F, Fut>(cancellation: CancellationToken, interval: Duration, mut work: F)
where
    F: FnMut() -> Fut,
    Fut: Future<Output = ()>,
{
    loop {
        if cancellation.is_cancelled() {
            return;
        }
        // Finish a claimed batch before stopping so completed Attempts and
        // lease releases remain durable. The lifecycle timeout aborts a truly
        // stuck batch; its leases are then recovered by another worker.
        work().await;
        tokio::select! {
            () = cancellation.cancelled() => return,
            () = tokio::time::sleep(interval) => {}
        }
    }
}
