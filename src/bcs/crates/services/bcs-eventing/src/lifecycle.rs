//! Managed Eventing worker lifecycle.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use async_trait::async_trait;
use bcs_service_api::lifecycle::{LifecycleError, ServiceLifecycle};
use tokio::task::JoinHandle;
use tokio_util::sync::CancellationToken;

use crate::{EventDispatcher, EventFanoutWorker, EventRetentionWorker};

mod polling;
use polling::IdleBackoff;

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
    fanout_idle_poll_max_interval: Duration,
    delivery_idle_poll_max_interval: Duration,
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
            fanout_idle_poll_max_interval: fanout_poll_interval,
            delivery_idle_poll_max_interval: delivery_poll_interval,
            retention_poll_interval,
            shutdown_timeout,
            running: Mutex::new(None),
        })
    }

    /// Opt into bounded idle backoff. Ceilings equal to the base intervals
    /// preserve fixed polling; no change to lease or delivery retry policy.
    pub fn with_idle_poll_limits(
        mut self,
        fanout_max: Duration,
        delivery_max: Duration,
    ) -> Result<Self, LifecycleError> {
        if fanout_max < self.fanout_poll_interval || delivery_max < self.delivery_poll_interval {
            return Err(LifecycleError::Precondition(
                "Eventing idle polling ceilings must not be below base intervals".to_string(),
            ));
        }
        self.fanout_idle_poll_max_interval = fanout_max;
        self.delivery_idle_poll_max_interval = delivery_max;
        Ok(self)
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
        let fanout_max = self.fanout_idle_poll_max_interval;
        handles.push(tokio::spawn(async move {
            worker_loop(fanout_cancel, fanout_interval, fanout_max, "fanout", move || {
                let fanout = fanout.clone();
                async move {
                    fanout.run_once("eventing-fanout").await
                }
            })
            .await;
        }));

        if let Some(dispatcher) = self.dispatcher.clone() {
            let delivery_cancel = cancellation.clone();
            let delivery_interval = self.delivery_poll_interval;
            let delivery_max = self.delivery_idle_poll_max_interval;
            handles.push(tokio::spawn(async move {
                worker_loop(delivery_cancel, delivery_interval, delivery_max, "delivery", move || {
                    let dispatcher = dispatcher.clone();
                    async move {
                        dispatcher.run_once("eventing-dispatcher").await
                    }
                })
                .await;
            }));
        }

        let retention = self.retention.clone();
        let retention_cancel = cancellation.clone();
        let retention_interval = self.retention_poll_interval;
        handles.push(tokio::spawn(async move {
            worker_loop(retention_cancel, retention_interval, retention_interval, "retention", move || {
                let retention = retention.clone();
                async move {
                    retention.run_once().await.map(|_| 1)
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

async fn worker_loop<F, Fut, E>(
    cancellation: CancellationToken,
    interval: Duration,
    idle_max: Duration,
    worker: &'static str,
    mut work: F,
)
where
    F: FnMut() -> Fut,
    Fut: Future<Output = Result<usize, E>>,
    E: std::fmt::Display,
{
    let mut backoff = IdleBackoff::new(interval, idle_max);
    loop {
        if cancellation.is_cancelled() {
            return;
        }
        // Finish a claimed batch before stopping so completed Attempts and
        // lease releases remain durable. The lifecycle timeout aborts a truly
        // stuck batch; its leases are then recovered by another worker.
        let empty = match work().await {
            Ok(count) => count == 0,
            Err(error) => {
                tracing::warn!(worker, error = %error, "Eventing worker iteration failed");
                false
            }
        };
        let delay = backoff.next_delay(empty, rand::random());
        tokio::select! {
            () = cancellation.cancelled() => return,
            () = tokio::time::sleep(delay) => {}
        }
    }
}

#[cfg(test)]
mod tests;
