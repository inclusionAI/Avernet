//! How a Connector process ends: the exit of `run` and of a cancelled
//! `drain`, the heartbeat stop, and stopping the actors.

use super::{ConnectorEngine, Inner, lock};
use std::sync::atomic::Ordering;
use tokio::task::JoinHandle;

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

/// Finishes a drain whose future was dropped before it retired: stops the
/// actors first, then retires (which silences the heartbeat), in that order,
/// so this instance reads `Dead` only once none of its calls runs. Done on a
/// spawned task, since `Drop` cannot wait; outside a runtime it only asks the
/// actors to stop and silences the heartbeat.
pub(super) struct DrainExit {
    pub(super) engine: ConnectorEngine,
    pub(super) done: bool,
}

impl Drop for DrainExit {
    fn drop(&mut self) {
        if self.done {
            return;
        }
        let engine = self.engine.clone();
        match tokio::runtime::Handle::try_current() {
            Ok(runtime) => {
                runtime.spawn(async move {
                    tracing::warn!("drain cancelled; stopping the calls and retiring");
                    engine.stop_actors().await;
                    engine.retire().await;
                });
            }
            Err(_) => {
                for actor in lock(&engine.inner.actors).values() {
                    actor.task.abort();
                }
                silence_heartbeat(&engine.inner);
            }
        }
    }
}
