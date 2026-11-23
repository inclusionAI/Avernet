#![allow(clippy::unwrap_used, reason = "startup capacity assertions")]
mod processor_support;
mod support;
use bcs_dist_projection::OutputProjectionService;
use bcs_dist_store::inbox::StoreAdmissionControl;
use bcs_dist_worker::{ProcessorConfig, ProcessorPorts, WorkerProcessor};
use processor_support::*;
use std::{sync::Arc, time::Duration};

fn ports(f: &Fixture) -> ProcessorPorts {
    ProcessorPorts {
        scan_budget: f.scan_budget.clone(),
        control: f.control.clone(),
        admission: Arc::new(StoreAdmissionControl::new(
            (*f.inbox).clone(),
            f.feed.clone(),
        )),
        runner: Arc::new(FinishingRunner::default()),
        projection: Arc::new(OutputProjectionService),
        directory: Arc::new(Directory),
        tasks: f.feed.clone(),
        output: f.publisher.clone(),
        blobs: f.blobs.clone(),
        results: f.archive.clone(),
        dispatch: f.dispatch.clone(),
        now: Arc::new(|| bcs_dist_model::TimestampMillis(1000)),
    }
}

#[tokio::test]
async fn retry_delay_cannot_exceed_the_spec_fairness_bound() {
    let f = Fixture::new(Arc::new(FinishingRunner::default())).await;
    for delay in [Duration::from_millis(5), Duration::from_secs(5)] {
        assert!(
            WorkerProcessor::new(
                f.runtime.clone(),
                ports(&f),
                ProcessorConfig {
                    retry_delay: delay,
                    ..ProcessorConfig::default()
                }
            )
            .is_ok()
        );
    }
    assert!(
        WorkerProcessor::new(
            f.runtime.clone(),
            ports(&f),
            ProcessorConfig {
                retry_delay: Duration::from_millis(5001),
                ..ProcessorConfig::default()
            }
        )
        .is_err()
    );
}

#[tokio::test]
async fn startup_accepts_maximum_shapes_at_both_envelope_reserves() {
    let f = Fixture::new(Arc::new(FinishingRunner::default())).await;
    for bytes in [64 * 1024, 128 * 1024] {
        assert!(
            WorkerProcessor::new(
                f.runtime.clone(),
                ports(&f),
                ProcessorConfig {
                    envelope_reserve_bytes: bytes,
                    ..ProcessorConfig::default()
                }
            )
            .is_ok()
        );
    }
}

#[tokio::test]
async fn startup_rejects_undeclared_or_incompatible_provider_frames() {
    use bcs_service_api::port::repo::distributed::session::SessionCheckpointCapacity;
    let f = Fixture::new(Arc::new(FinishingRunner::default())).await;
    for capacity in [
        None,
        Some(SessionCheckpointCapacity {
            max_document_bytes: 960 * 1024,
            max_envelope_bytes: 64 * 1024,
        }),
        Some(SessionCheckpointCapacity {
            max_document_bytes: 1024 * 1024,
            max_envelope_bytes: 128 * 1024,
        }),
    ] {
        *f.sessions.capacity.lock().unwrap() = capacity;
        assert!(
            WorkerProcessor::new(f.runtime.clone(), ports(&f), ProcessorConfig::default()).is_err()
        );
    }
}
