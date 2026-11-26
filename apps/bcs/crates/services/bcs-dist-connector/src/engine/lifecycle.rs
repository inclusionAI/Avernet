//! How a Connector process ends: the exit of `run`, the one shutdown that
//! every `drain` waits for, the heartbeat stop, and stopping the actors.

use super::{ConnectorEngine, Inner, failed, lock};
use bcs_service_api::types::dist_runtime::{DrainOutcome, RuntimeError};
use futures::FutureExt;
use futures::future::{BoxFuture, Shared};
use std::sync::atomic::Ordering;
use std::time::Duration;
use tokio::task::JoinHandle;

/// The shutdown job: its outcome, shared by every `drain` caller.
pub(super) type Shutdown = Shared<BoxFuture<'static, Result<DrainOutcome, RuntimeError>>>;

impl ConnectorEngine {
    /// Starts the shutdown on its own task the first time (with this call's
    /// `grace`), then waits for it. The task is not tied to any caller: a
    /// dropped `drain` future leaves it running to the end (actors stopped
    /// and joined, partitions offered, registration retired), and drains
    /// never overlap.
    pub(super) async fn drain_once(&self, grace: Duration) -> Result<DrainOutcome, RuntimeError> {
        let job = {
            let mut slot = lock(&self.inner.shutdown);
            match slot.as_ref() {
                Some(job) => job.clone(),
                None => {
                    let engine = self.clone();
                    let task = tokio::spawn(async move { engine.shut_down(grace).await });
                    let job: Shutdown = async move { task.await.unwrap_or_else(|_| Err(failed("drain_failed"))) }
                        .boxed()
                        .shared();
                    *slot = Some(job.clone());
                    job
                }
            }
        };
        job.await
    }
}

/// Stops the heartbeat for good: no renewal starts after this returns
/// (`abort` takes effect at the task's next await point, before a renewal).
pub(super) fn silence_heartbeat(inner: &Inner) {
    inner.quiet.store(true, Ordering::SeqCst);
    if let Some(task) = lock(&inner.heartbeat).take() {
        task.abort();
    }
}

/// The end of `run`, the same on every path (return, error, panic, or the
/// `run` future dropped): clears `ready` and `looping`, wakes a waiting
/// drain, and while `armed` silences the heartbeat, unless a drain is
/// already running (it keeps the registration alive until it retires it).
pub(super) struct Exit<'a> {
    pub(super) inner: &'a Inner,
    pub(super) armed: bool,
}

impl Drop for Exit<'_> {
    fn drop(&mut self) {
        if self.armed && !self.inner.draining.load(Ordering::SeqCst) {
            silence_heartbeat(self.inner);
        }
        self.inner.ready.store(false, Ordering::SeqCst);
        self.inner.looping.store(false, Ordering::SeqCst);
        self.inner.wake.notify_waiters();
    }
}

impl ConnectorEngine {
    /// Aborts every actor still running and waits until each has stopped
    /// (`abort` only asks; awaiting the task waits for its next await
    /// point). Returns how many were stopped.
    pub(super) async fn stop_actors(&self) -> usize {
        let leftover: Vec<JoinHandle<()>> = lock(&self.inner.actors)
            .drain()
            .map(|(_, actor)| actor.task)
            .filter(|task| !task.is_finished())
            .collect();
        let stopped = leftover.len();
        for task in &leftover {
            task.abort();
        }
        for task in leftover {
            let _ = task.await;
        }
        stopped
    }
}
