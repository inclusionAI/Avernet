//! Retry persistence only, never transport I/O or an entire event pipeline.
use std::time::Duration;
use bcs_service_api::ManagedDeliveryError;
use bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError;

pub(crate) fn managed_storage(error: &ManagedDeliveryError) -> bool {
    matches!(error, ManagedDeliveryError::Repository(MessageDeliveryRepoError::Storage(_)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn non_storage_errors_are_not_retried() {
        let count = std::sync::atomic::AtomicUsize::new(0);
        let result: Result<(), ManagedDeliveryError> = retry(None, "test", managed_storage, || async {
            count.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            Err(ManagedDeliveryError::Conflict)
        }).await;
        assert!(matches!(result, Err(ManagedDeliveryError::Conflict)));
        assert_eq!(count.load(std::sync::atomic::Ordering::SeqCst), 1);
    }
}

pub(crate) fn shutdown(flow: &crate::BcsMessageFlow) -> Option<tokio::sync::watch::Receiver<bool>> {
    flow.delivery_shutdown.get().map(|(sender, _)| sender.subscribe())
}

/// The caller retains its input/result across attempts. CAS writes must keep
/// their expected version; an ambiguous commit is not permission to send again.
/// Persistent storage failures remain paused until recovery or shutdown.
pub(crate) async fn retry<T, E, F, Fut>(
    mut shutdown: Option<tokio::sync::watch::Receiver<bool>>,
    operation: &'static str,
    retryable: impl Fn(&E) -> bool,
    mut work: F,
) -> Result<T, E>
where
    F: FnMut() -> Fut,
    Fut: Future<Output = Result<T, E>>,
{
    let mut attempts = 0u64;
    let mut delay = Duration::from_millis(100);
    let mut last_warning = tokio::time::Instant::now();
    loop {
        match work().await {
            Ok(value) => {
                if attempts > 0 { tracing::info!(operation, attempts, "queue persistence recovered"); }
                return Ok(value);
            }
            Err(error) if retryable(&error) => {
                attempts = attempts.saturating_add(1);
                if attempts == 1 || last_warning.elapsed() >= Duration::from_secs(30) {
                    tracing::warn!(operation, attempts, retry_ms = delay.as_millis() as u64,
                        "queue persistence unavailable; retaining work and backing off");
                    last_warning = tokio::time::Instant::now();
                }
                if let Some(stop) = shutdown.as_mut() {
                    if *stop.borrow() || stop.has_changed().is_err() { return Err(error); }
                    tokio::select! {
                        _ = stop.changed() => return Err(error),
                        _ = tokio::time::sleep(delay) => {}
                    }
                } else {
                    tokio::time::sleep(delay).await;
                }
                delay = (delay * 2).min(Duration::from_secs(5));
            }
            Err(error) => return Err(error),
        }
    }
}
