//! Fanout target projection and durable Delivery materialization.

use std::sync::Arc;

use bcs_service_api::port::repo::{
    ClaimFanoutTargets, EventDeliveryRecord, EventFanoutTargetPurpose, EventFanoutTargetRecord,
    EventRepoError, EventRepoPort, MaterializeFanoutTarget,
};
use bcs_service_api::port::{EventErrorCategory, EventingInstrumentationPort};
use bcs_service_api::types::EventDeliveryStatus;
use sha2::{Digest, Sha256};
use uuid::Uuid;

use crate::{EventCatalog, project_event};

#[derive(Debug, thiserror::Error)]
pub enum EventFanoutError {
    #[error("Event fanout repository operation failed")]
    Repository,
    #[error("Event fanout target references missing immutable state")]
    MissingState,
    #[error("Event fanout payload projection failed")]
    Projection,
}

pub struct EventFanoutWorker {
    repo: Arc<dyn EventRepoPort>,
    metrics: Arc<dyn EventingInstrumentationPort>,
    catalog: Arc<EventCatalog>,
    env: String,
    lease_ms: u64,
    claim_limit: u32,
    max_event_body_bytes: usize,
    now_ms: Arc<dyn Fn() -> u64 + Send + Sync>,
    new_delivery_id: Arc<dyn Fn() -> String + Send + Sync>,
}

impl EventFanoutWorker {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        repo: Arc<dyn EventRepoPort>,
        metrics: Arc<dyn EventingInstrumentationPort>,
        catalog: Arc<EventCatalog>,
        env: impl Into<String>,
        lease_ms: u64,
        claim_limit: u32,
        max_event_body_bytes: usize,
    ) -> Self {
        Self {
            repo,
            metrics,
            catalog,
            env: env.into(),
            lease_ms,
            claim_limit,
            max_event_body_bytes,
            now_ms: Arc::new(super::subscription::system_now_ms_for_workers),
            new_delivery_id: Arc::new(|| format!("del_{}", Uuid::new_v4())),
        }
    }

    pub fn with_runtime(
        mut self,
        now_ms: Arc<dyn Fn() -> u64 + Send + Sync>,
        new_delivery_id: Arc<dyn Fn() -> String + Send + Sync>,
    ) -> Self {
        self.now_ms = now_ms;
        self.new_delivery_id = new_delivery_id;
        self
    }

    pub async fn run_once(&self, worker_id: &str) -> Result<usize, EventFanoutError> {
        let now_ms = (self.now_ms)();
        let targets = self
            .repo
            .claim_fanout_targets(ClaimFanoutTargets {
                worker_id: worker_id.to_string(),
                now_ms,
                lease_until_ms: now_ms.saturating_add(self.lease_ms),
                limit: self.claim_limit,
                env: self.env.clone(),
            })
            .await
            .map_err(|_| EventFanoutError::Repository)?;
        let count = targets.len();
        for target in targets {
            if let Err(error) = self.materialize(target).await {
                let category = match error {
                    EventFanoutError::Projection => EventErrorCategory::Projection,
                    EventFanoutError::MissingState => EventErrorCategory::Validation,
                    EventFanoutError::Repository => EventErrorCategory::Storage,
                };
                self.metrics.fanout_failed(category).await;
            }
        }
        Ok(count)
    }

    async fn materialize(
        &self,
        target: EventFanoutTargetRecord,
    ) -> Result<EventDeliveryRecord, EventFanoutError> {
        let event = self
            .repo
            .get_event(&target.event_id, &self.env)
            .await
            .map_err(map_repo)?
            .ok_or(EventFanoutError::MissingState)?;
        let revision = self
            .repo
            .get_subscription_revision(
                &target.subscription_id,
                target.subscription_revision,
                &self.env,
            )
            .await
            .map_err(map_repo)?
            .ok_or(EventFanoutError::MissingState)?;
        let projection = project_event(
            &event.envelope,
            &self.catalog,
            revision.payload_mode,
            self.max_event_body_bytes,
        );
        let (payload_bytes, status, error_category, error_summary, dead_lettered_at_ms) =
            match projection {
                Ok(payload) => (payload, EventDeliveryStatus::Pending, None, None, None),
                Err(_) => {
                    self.metrics
                        .fanout_failed(EventErrorCategory::Projection)
                        .await;
                    (
                        Vec::new(),
                        EventDeliveryStatus::DeadLettered,
                        Some("projection".to_string()),
                        Some("Event payload projection failed".to_string()),
                        Some((self.now_ms)()),
                    )
                }
            };
        let payload_sha256 = format!("{:x}", Sha256::digest(&payload_bytes));
        let now_ms = (self.now_ms)();
        let delivery_id = if target.purpose == EventFanoutTargetPurpose::ManualReplay {
            target.target_id.clone()
        } else {
            (self.new_delivery_id)()
        };
        self.repo
            .materialize_fanout_target(MaterializeFanoutTarget {
                target_id: target.target_id.clone(),
                expected_lease_owner: target
                    .lease_owner
                    .clone()
                    .ok_or(EventFanoutError::MissingState)?,
                delivery: EventDeliveryRecord {
                    delivery_id,
                    fanout_target_id: target.target_id,
                    event_id: event.envelope.event_id,
                    event_type: event.envelope.event_type,
                    subscription_id: target.subscription_id,
                    subscription_revision: target.subscription_revision,
                    stream_key: event.envelope.stream.key,
                    sequence: event.envelope.stream.sequence,
                    payload_bytes,
                    payload_sha256,
                    status,
                    attempt_count: 0,
                    first_attempt_at_ms: None,
                    last_attempt_at_ms: None,
                    next_attempt_at_ms: None,
                    lease_owner: None,
                    lease_until_ms: None,
                    last_http_status: None,
                    last_error_category: error_category,
                    last_error_summary: error_summary,
                    dead_lettered_at_ms,
                    cancelled_at_ms: None,
                    skipped_at_ms: None,
                    skip_actor: None,
                    skip_reason: None,
                    replay_of_delivery_id: target.replay_of_delivery_id,
                    resolved_by_delivery_id: None,
                    resolved_at_ms: None,
                    created_at_ms: now_ms,
                    succeeded_at_ms: None,
                    env: self.env.clone(),
                },
                materialized_at_ms: now_ms,
            })
            .await
            .map_err(map_repo)
    }
}

fn map_repo(_: EventRepoError) -> EventFanoutError {
    EventFanoutError::Repository
}
