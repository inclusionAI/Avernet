//! Durable Delivery dispatcher for one or more claimed attempts.

use std::collections::HashMap;
use std::sync::Arc;

use bcs_service_api::port::repo::{
    ClaimEventDeliveries, CompleteEventDeliveryAttempt, EventDeliveryAttemptRecordResult,
    EventDeliveryRecord, EventRepoPort, EventSubscriptionRevisionRecord, RenewEventDeliveryLease,
    ReplaceEventSubscriptionRevision,
};
use bcs_service_api::port::{
    EventDeliveryAttemptMetric, EventDeliveryDisposition, EventDeliveryMetricResult,
    EventDeliveryPort, EventDeliveryRequest, EventDeliveryResponse, EventErrorCategory,
    EventHttpStatusClass, EventMetricFamily, EventingInstrumentationPort,
};
use bcs_service_api::types::{
    EventActor, EventActorType, EventDeliveryStatus, EventSubscriptionStatus,
};
use futures::{StreamExt, stream};
use rand::RngCore;
use sha2::Digest;
use tokio::sync::{Mutex, Semaphore};
use tokio::time::{MissedTickBehavior, interval};
use tracing::{debug, info, warn};
use url::Url;

use crate::retry::EventRetryPolicy;
use crate::subscription::system_now_ms_for_workers;

#[derive(Debug, thiserror::Error)]
pub enum EventDispatcherError {
    #[error("Event dispatcher repository operation failed")]
    Repository,
    #[error("Event dispatcher immutable state is unavailable")]
    MissingState,
    #[error("Event dispatcher endpoint is invalid")]
    InvalidEndpoint,
}

pub struct EventDispatcher {
    repo: Arc<dyn EventRepoPort>,
    delivery: Arc<dyn EventDeliveryPort>,
    metrics: Arc<dyn EventingInstrumentationPort>,
    retry: EventRetryPolicy,
    env: String,
    lease_ms: u64,
    claim_limit: u32,
    worker_concurrency: usize,
    per_host_concurrency: usize,
    host_semaphores: Mutex<HashMap<String, Arc<Semaphore>>>,
    now_ms: Arc<dyn Fn() -> u64 + Send + Sync>,
    random_sample: Arc<dyn Fn() -> u64 + Send + Sync>,
}

impl EventDispatcher {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        repo: Arc<dyn EventRepoPort>,
        delivery: Arc<dyn EventDeliveryPort>,
        metrics: Arc<dyn EventingInstrumentationPort>,
        retry: EventRetryPolicy,
        env: impl Into<String>,
        lease_ms: u64,
        claim_limit: u32,
        worker_concurrency: usize,
        per_host_concurrency: usize,
    ) -> Self {
        Self {
            repo,
            delivery,
            metrics,
            retry,
            env: env.into(),
            lease_ms: lease_ms.max(1),
            claim_limit,
            worker_concurrency: worker_concurrency.max(1),
            per_host_concurrency: per_host_concurrency.max(1),
            host_semaphores: Mutex::new(HashMap::new()),
            now_ms: Arc::new(system_now_ms_for_workers),
            random_sample: Arc::new(|| rand::thread_rng().next_u64()),
        }
    }

    pub fn with_runtime(
        mut self,
        now_ms: Arc<dyn Fn() -> u64 + Send + Sync>,
        random_sample: Arc<dyn Fn() -> u64 + Send + Sync>,
    ) -> Self {
        self.now_ms = now_ms;
        self.random_sample = random_sample;
        self
    }

    pub async fn run_once(&self, worker_id: &str) -> Result<usize, EventDispatcherError> {
        let now_ms = (self.now_ms)();
        let deliveries = self
            .repo
            .claim_deliveries(ClaimEventDeliveries {
                worker_id: worker_id.to_string(),
                now_ms,
                lease_until_ms: now_ms.saturating_add(self.lease_ms),
                // A claimed Delivery owns a live lease immediately. Do not
                // claim more work than this process can start heartbeating.
                limit: self
                    .claim_limit
                    .min(u32::try_from(self.worker_concurrency).unwrap_or(u32::MAX)),
                env: self.env.clone(),
            })
            .await
            .map_err(|error| {
                warn!(
                    target: "bcs_event_webhook",
                    component = "delivery",
                    worker_id,
                    env = %self.env,
                    error = %error,
                    "failed to claim webhook deliveries"
                );
                EventDispatcherError::Repository
            })?;
        let count = deliveries.len();
        debug!(
            target: "bcs_event_webhook",
            component = "delivery",
            worker_id,
            env = %self.env,
            count,
            "claimed webhook deliveries"
        );
        for delivery in &deliveries {
            info!(
                target: "bcs_event_webhook",
                component = "delivery",
                worker_id,
                env = %self.env,
                delivery_id = %delivery.delivery_id,
                attempt_no = delivery.attempt_count,
                lease_until_ms = ?delivery.lease_until_ms,
                "claimed webhook delivery attempt"
            );
        }
        stream::iter(deliveries)
            .for_each_concurrent(self.worker_concurrency, |delivery| async move {
                let delivery_id = delivery.delivery_id.clone();
                let attempt_no = delivery.attempt_count;
                if let Err(error) = self.dispatch(delivery).await {
                    warn!(
                        target: "bcs_event_webhook",
                        component = "delivery",
                        delivery_id,
                        attempt_no,
                        error = %error,
                        "webhook delivery dispatch did not complete"
                    );
                }
            })
            .await;
        self.host_semaphores
            .lock()
            .await
            .retain(|_, semaphore| Arc::strong_count(semaphore) > 1);
        Ok(count)
    }

    async fn dispatch(
        &self,
        delivery_record: EventDeliveryRecord,
    ) -> Result<(), EventDispatcherError> {
        let expected_lease_owner = delivery_record
            .lease_owner
            .clone()
            .ok_or(EventDispatcherError::MissingState)?;
        let delivery_id = delivery_record.delivery_id.clone();
        let attempt_no = delivery_record.attempt_count;
        let env = delivery_record.env.clone();
        let dispatch = self.dispatch_claimed(delivery_record);
        tokio::pin!(dispatch);
        let mut heartbeat = interval(std::time::Duration::from_millis((self.lease_ms / 3).max(1)));
        heartbeat.set_missed_tick_behavior(MissedTickBehavior::Delay);
        heartbeat.tick().await;
        loop {
            tokio::select! {
                biased;
                result = &mut dispatch => return result,
                _ = heartbeat.tick() => {
                    let now_ms = (self.now_ms)();
                    self.repo
                        .renew_delivery_lease(RenewEventDeliveryLease {
                            delivery_id: delivery_id.clone(),
                            expected_lease_owner: expected_lease_owner.clone(),
                            attempt_no,
                            now_ms,
                            lease_until_ms: now_ms.saturating_add(self.lease_ms),
                            env: env.clone(),
                        })
                        .await
                        .map_err(|error| {
                            warn!(
                                target: "bcs_event_webhook",
                                component = "delivery",
                                delivery_id,
                                attempt_no,
                                error = %error,
                                "failed to renew webhook delivery lease"
                            );
                            EventDispatcherError::Repository
                        })?;
                }
            }
        }
    }

    async fn dispatch_claimed(
        &self,
        delivery_record: EventDeliveryRecord,
    ) -> Result<(), EventDispatcherError> {
        let preflight_started_at_ms = (self.now_ms)();
        let revision = match self
            .repo
            .get_subscription_revision(
                &delivery_record.subscription_id,
                delivery_record.subscription_revision,
                &self.env,
            )
            .await
        {
            Ok(Some(revision)) => revision,
            Ok(None) => {
                warn!(
                    target: "bcs_event_webhook",
                    component = "delivery",
                    delivery_id = %delivery_record.delivery_id,
                    attempt_no = delivery_record.attempt_count,
                    subscription_id = %delivery_record.subscription_id,
                    revision = delivery_record.subscription_revision,
                    "webhook subscription revision is missing"
                );
                return self
                    .complete_preflight_failure(
                        &delivery_record,
                        preflight_started_at_ms,
                        EventDeliveryDisposition::Terminal,
                        "subscription_revision_missing",
                        "Webhook subscription revision is missing",
                    )
                    .await;
            }
            Err(error) => {
                warn!(
                    target: "bcs_event_webhook",
                    component = "delivery",
                    delivery_id = %delivery_record.delivery_id,
                    attempt_no = delivery_record.attempt_count,
                    subscription_id = %delivery_record.subscription_id,
                    error = %error,
                    "failed to load webhook subscription revision"
                );
                return self
                    .complete_preflight_failure(
                        &delivery_record,
                        preflight_started_at_ms,
                        EventDeliveryDisposition::Retryable,
                        "subscription_revision_lookup",
                        "Failed to load webhook subscription revision",
                    )
                    .await;
            }
        };
        let host_key = match endpoint_host_key(&revision.endpoint_url) {
            Ok(host_key) => host_key,
            Err(error) => {
                warn!(
                    target: "bcs_event_webhook",
                    component = "delivery",
                    delivery_id = %delivery_record.delivery_id,
                    attempt_no = delivery_record.attempt_count,
                    subscription_id = %delivery_record.subscription_id,
                    error = %error,
                    "webhook endpoint is invalid"
                );
                return self
                    .complete_preflight_failure(
                        &delivery_record,
                        preflight_started_at_ms,
                        EventDeliveryDisposition::Terminal,
                        "invalid_endpoint",
                        "Webhook endpoint configuration is invalid",
                    )
                    .await;
            }
        };
        let semaphore = {
            let mut semaphores = self.host_semaphores.lock().await;
            semaphores
                .entry(host_key)
                .or_insert_with(|| Arc::new(Semaphore::new(self.per_host_concurrency)))
                .clone()
        };
        let _permit = match bcs_observability::observe_result("event.delivery.permit_wait", semaphore.acquire_owned()).await {
            Ok(permit) => permit,
            Err(error) => {
                warn!(
                    target: "bcs_event_webhook",
                    component = "delivery",
                    delivery_id = %delivery_record.delivery_id,
                    attempt_no = delivery_record.attempt_count,
                    error = %error,
                    "webhook host concurrency semaphore closed"
                );
                return self
                    .complete_preflight_failure(
                        &delivery_record,
                        preflight_started_at_ms,
                        EventDeliveryDisposition::Retryable,
                        "dispatcher_concurrency",
                        "Webhook dispatcher concurrency control is unavailable",
                    )
                    .await;
            }
        };
        let started_at_ms = (self.now_ms)();
        let attempt_no = delivery_record.attempt_count;
        let mut response = match self
            .delivery
            .deliver(EventDeliveryRequest {
                endpoint_url: revision.endpoint_url,
                body: delivery_record.payload_bytes.clone(),
                request_timeout_ms: revision.request_timeout_ms,
            })
            .await
        {
            Ok(response) => response,
            Err(error) => {
                warn!(
                    target: "bcs_event_webhook",
                    component = "delivery",
                    delivery_id = %delivery_record.delivery_id,
                    attempt_no,
                    subscription_id = %delivery_record.subscription_id,
                    error = %error,
                    "webhook delivery adapter failed"
                );
                EventDeliveryResponse {
                    disposition: EventDeliveryDisposition::Retryable,
                    http_status: None,
                    retry_after_ms: None,
                    response_bytes_observed: 0,
                    error_category: Some("delivery_adapter".to_string()),
                    error_summary: Some("Webhook delivery adapter failed".to_string()),
                }
            }
        };
        if response.disposition == EventDeliveryDisposition::DisableSubscription {
            if let Err(error) = self
                .disable_subscription(&delivery_record.subscription_id)
                .await
            {
                warn!(
                    target: "bcs_event_webhook",
                    component = "delivery",
                    delivery_id = %delivery_record.delivery_id,
                    attempt_no,
                    subscription_id = %delivery_record.subscription_id,
                    error = %error,
                    "failed to disable webhook subscription after terminal response"
                );
                // Do not permanently consume the 410 until the required durable
                // Subscription disable/cancellation transaction has committed.
                response = EventDeliveryResponse {
                    disposition: EventDeliveryDisposition::Retryable,
                    http_status: response.http_status,
                    retry_after_ms: None,
                    response_bytes_observed: response.response_bytes_observed,
                    error_category: Some("subscription_disable".to_string()),
                    error_summary: Some("Subscription disable will be retried".to_string()),
                };
            }
        }
        let completed_at_ms = (self.now_ms)();
        self.complete_attempt(
            &delivery_record,
            attempt_no,
            started_at_ms,
            completed_at_ms,
            &response,
        )
        .await?;
        self.metrics
            .delivery_attempted(attempt_metric(&delivery_record.event_type, &response))
            .await;
        Ok(())
    }

    async fn complete_preflight_failure(
        &self,
        delivery_record: &EventDeliveryRecord,
        started_at_ms: u64,
        disposition: EventDeliveryDisposition,
        error_category: &str,
        error_summary: &str,
    ) -> Result<(), EventDispatcherError> {
        let response = EventDeliveryResponse {
            disposition,
            http_status: None,
            retry_after_ms: None,
            response_bytes_observed: 0,
            error_category: Some(error_category.to_string()),
            error_summary: Some(error_summary.to_string()),
        };
        self.complete_attempt(
            delivery_record,
            delivery_record.attempt_count,
            started_at_ms,
            (self.now_ms)(),
            &response,
        )
        .await?;
        self.metrics
            .delivery_attempted(attempt_metric(&delivery_record.event_type, &response))
            .await;
        Ok(())
    }

    async fn complete_attempt(
        &self,
        delivery: &EventDeliveryRecord,
        attempt_no: u32,
        started_at_ms: u64,
        completed_at_ms: u64,
        response: &EventDeliveryResponse,
    ) -> Result<(), EventDispatcherError> {
        let first_attempt_at = delivery.first_attempt_at_ms.unwrap_or(started_at_ms);
        let (result, next_status, next_attempt_at_ms) = match response.disposition {
            EventDeliveryDisposition::Succeeded => (
                EventDeliveryAttemptRecordResult::Success,
                EventDeliveryStatus::Succeeded,
                None,
            ),
            EventDeliveryDisposition::Retryable => {
                let retry_at = self.retry.retry_at_ms(
                    attempt_no,
                    first_attempt_at,
                    completed_at_ms,
                    response.retry_after_ms,
                    (self.random_sample)(),
                );
                (
                    EventDeliveryAttemptRecordResult::Retryable,
                    if retry_at.is_some() {
                        EventDeliveryStatus::RetryWait
                    } else {
                        EventDeliveryStatus::DeadLettered
                    },
                    retry_at,
                )
            }
            EventDeliveryDisposition::Terminal | EventDeliveryDisposition::DisableSubscription => (
                EventDeliveryAttemptRecordResult::Terminal,
                EventDeliveryStatus::DeadLettered,
                None,
            ),
        };
        self.repo
            .complete_delivery_attempt(CompleteEventDeliveryAttempt {
                delivery_id: delivery.delivery_id.clone(),
                expected_lease_owner: delivery
                    .lease_owner
                    .clone()
                    .ok_or(EventDispatcherError::MissingState)?,
                attempt_no,
                started_at_ms,
                completed_at_ms,
                result,
                next_status,
                next_attempt_at_ms,
                http_status: response.http_status,
                error_category: response.error_category.clone(),
                error_summary: response.error_summary.clone(),
                response_bytes_observed: response.response_bytes_observed,
            })
            .await
            .map_err(|error| {
                warn!(
                    target: "bcs_event_webhook",
                    component = "delivery",
                    delivery_id = %delivery.delivery_id,
                    attempt_no,
                    next_status = ?next_status,
                    http_status = ?response.http_status,
                    error_category = ?response.error_category,
                    error_summary = ?response.error_summary,
                    error = %error,
                    "failed to persist webhook delivery attempt completion"
                );
                EventDispatcherError::Repository
            })?;
        if next_status == EventDeliveryStatus::Succeeded {
            info!(
                target: "bcs_event_webhook",
                component = "delivery",
                delivery_id = %delivery.delivery_id,
                attempt_no,
                http_status = ?response.http_status,
                "webhook delivery attempt succeeded"
            );
        } else {
            warn!(
                target: "bcs_event_webhook",
                component = "delivery",
                delivery_id = %delivery.delivery_id,
                attempt_no,
                next_status = ?next_status,
                http_status = ?response.http_status,
                error_category = ?response.error_category,
                error_summary = ?response.error_summary,
                "webhook delivery attempt failed"
            );
        }
        Ok(())
    }

    async fn disable_subscription(
        &self,
        subscription_id: &str,
    ) -> Result<(), EventDispatcherError> {
        let Some((subscription, revision)) = self
            .repo
            .get_subscription(subscription_id, &self.env)
            .await
            .map_err(|error| {
                warn!(
                    target: "bcs_event_webhook",
                    component = "delivery",
                    subscription_id,
                    error = %error,
                    "failed to load webhook subscription for disable"
                );
                EventDispatcherError::Repository
            })?
        else {
            return Err(EventDispatcherError::MissingState);
        };
        if matches!(
            subscription.status,
            EventSubscriptionStatus::Disabled | EventSubscriptionStatus::Deleted
        ) {
            return Ok(());
        }
        if !subscription
            .status
            .can_transition_to(EventSubscriptionStatus::Disabled)
        {
            return Err(EventDispatcherError::MissingState);
        }
        let next_revision = subscription.current_revision.saturating_add(1);
        let now_ms = (self.now_ms)();
        let next = EventSubscriptionRevisionRecord {
            subscription_id: subscription_id.to_string(),
            revision: next_revision,
            event_filters: revision.event_filters,
            payload_mode: revision.payload_mode,
            endpoint_url: revision.endpoint_url,
            request_timeout_ms: revision.request_timeout_ms,
            activated_at_ms: now_ms,
            retired_at_ms: None,
        };
        self.repo
            .replace_subscription_revision(ReplaceEventSubscriptionRevision {
                subscription_id: subscription_id.to_string(),
                expected_revision: subscription.current_revision,
                name: subscription.name,
                status: EventSubscriptionStatus::Disabled,
                revision: next,
                cancel_retired_pending_deliveries: true,
                actor: EventActor {
                    actor_type: EventActorType::System,
                    id: "bcs-event-dispatcher".to_string(),
                    display_name: None,
                },
                reason: Some("webhook_gone".to_string()),
                updated_at_ms: now_ms,
                env: self.env.clone(),
            })
            .await
            .map_err(|error| {
                warn!(
                    target: "bcs_event_webhook",
                    component = "delivery",
                    subscription_id,
                    error = %error,
                    "failed to persist disabled webhook subscription"
                );
                EventDispatcherError::Repository
            })?;
        Ok(())
    }
}

fn endpoint_host_key(endpoint: &str) -> Result<String, EventDispatcherError> {
    let url = Url::parse(endpoint).map_err(|_| EventDispatcherError::InvalidEndpoint)?;
    let host = url
        .host_str()
        .ok_or(EventDispatcherError::InvalidEndpoint)?;
    Ok(format!("{:x}", sha2::Sha256::digest(host.as_bytes())))
}

fn attempt_metric(
    event_type: &str,
    response: &EventDeliveryResponse,
) -> EventDeliveryAttemptMetric {
    EventDeliveryAttemptMetric {
        family: event_family(event_type),
        result: match response.disposition {
            EventDeliveryDisposition::Succeeded => EventDeliveryMetricResult::Success,
            EventDeliveryDisposition::Retryable => EventDeliveryMetricResult::Retryable,
            EventDeliveryDisposition::Terminal | EventDeliveryDisposition::DisableSubscription => {
                EventDeliveryMetricResult::Terminal
            }
        },
        status_class: match response.http_status {
            None => EventHttpStatusClass::None,
            Some(100..=199) => EventHttpStatusClass::Informational,
            Some(200..=299) => EventHttpStatusClass::Success,
            Some(300..=399) => EventHttpStatusClass::Redirection,
            Some(400..=499) => EventHttpStatusClass::ClientError,
            Some(_) => EventHttpStatusClass::ServerError,
        },
        error_category: response.error_category.as_deref().map(map_error_category),
    }
}

fn event_family(event_type: &str) -> EventMetricFamily {
    match event_type.split('.').next() {
        Some("group") => EventMetricFamily::Group,
        Some("session") => EventMetricFamily::Session,
        Some("message") => EventMetricFamily::Message,
        Some("task") => EventMetricFamily::Task,
        Some("state_machine") => EventMetricFamily::StateMachine,
        Some("event_subscription") => EventMetricFamily::EventSubscription,
        _ => EventMetricFamily::Other,
    }
}

fn map_error_category(category: &str) -> EventErrorCategory {
    match category {
        "dns_resolution" => EventErrorCategory::Dns,
        "network" => EventErrorCategory::Connect,
        "timeout" => EventErrorCategory::Timeout,
        "http_status" | "response_body" => EventErrorCategory::Http,
        _ => EventErrorCategory::Internal,
    }
}
