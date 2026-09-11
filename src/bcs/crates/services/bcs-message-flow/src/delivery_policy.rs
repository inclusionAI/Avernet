//! Live policy publication. The write guard spans DB CAS and snapshot publication;
//! readers never observe an acknowledged policy before its durable commit.
use bcs_config_api::message_delivery::{DeliveryPolicy, DeliveryPolicyRecord};
use bcs_service_api::{CallerContext, ServiceError, ServiceResult};
use bcs_service_api::port::repo::message_delivery::{MessageDeliveryRepoPort, MessageDeliveryRepoError};
use std::sync::{Arc, atomic::{AtomicBool, Ordering}};
use tracing::instrument::WithSubscriber;

#[derive(Clone)]
pub struct LiveDeliveryPolicy {
    pub snapshot: Arc<tokio::sync::RwLock<DeliveryPolicyRecord>>,
    repository: Arc<dyn MessageDeliveryRepoPort>,
    updates: Arc<tokio::sync::Semaphore>,
    pub scheduler_available: Arc<AtomicBool>,
    pub provider_headers_configured: bool,
}

impl LiveDeliveryPolicy {
    pub fn new(repository: Arc<dyn MessageDeliveryRepoPort>, initial: DeliveryPolicyRecord, provider_headers_configured: bool) -> Self {
        Self { snapshot: Arc::new(tokio::sync::RwLock::new(initial)), repository, updates: Arc::new(tokio::sync::Semaphore::new(8)),
            scheduler_available: Arc::new(AtomicBool::new(false)), provider_headers_configured }
    }

    fn human(caller: &CallerContext) -> ServiceResult<&str> {
        match caller {
            CallerContext::Public => Err(ServiceError::Unauthorized("authenticated Human required".into())),
            CallerContext::Human(human) if !human.staff_no.trim().is_empty()
                && human.actor_id == format!("human_{}", human.staff_no) => Ok(&human.actor_id),
            _ => Err(ServiceError::Forbidden("authenticated Human required".into())),
        }
    }

    pub async fn get(&self, caller: CallerContext) -> ServiceResult<DeliveryPolicyRecord> {
        Self::human(&caller)?;
        // A failed durable read is never presented as a healthy cached config.
        let current = self.snapshot.read().await;
        let stored = self.repository.load_policy().await.map_err(|_| ServiceError::InternalError("delivery policy read failed".into()))?;
        if stored != *current { return Err(ServiceError::InternalError("delivery policy changed outside management API; restart required".into())); }
        Ok(current.clone())
    }

    pub async fn replace(&self, caller: CallerContext, expected: u64, policy: DeliveryPolicy) -> ServiceResult<DeliveryPolicyRecord> {
        if let Err(error) = Self::human(&caller) {
            tracing::warn!(actor_id = "non_human_or_unauthenticated", attempted_at_ms = chrono::Utc::now().timestamp_millis(),
                expected_version = expected, before_version = "unknown", after_version = "unchanged", outcome = "denied", changed_fields = "unknown", "delivery policy update audited");
            return Err(error);
        }
        // Once accepted, finishing commit + publication is independent of the
        // HTTP request lifetime. Disconnecting must not leave DB and runtime
        // on different policies after a successful SQL commit.
        let actor = Self::human(&caller)?.to_owned();
        let permit = self.updates.clone().try_acquire_owned().map_err(|_| {
            tracing::warn!(actor_id = %actor, attempted_at_ms = chrono::Utc::now().timestamp_millis(), expected_version = expected,
                before_version = "unknown", after_version = "unchanged", changed_fields = "unknown", outcome = "busy", "delivery policy update audited");
            ServiceError::InternalError("delivery policy update busy".into())
        })?;
        let owned = self.clone();
        tokio::spawn(async move { let _permit = permit; owned.replace_inner(caller, expected, policy).await }.with_current_subscriber()).await
            .map_err(|_| {
                tracing::error!(actor_id = %actor, attempted_at_ms = chrono::Utc::now().timestamp_millis(), expected_version = expected,
                    before_version = "unknown", after_version = "unknown", changed_fields = "unknown", outcome = "task_failed", "delivery policy update audited");
                ServiceError::InternalError("delivery policy update task failed".into())
            })?
    }

    async fn replace_inner(&self, caller: CallerContext, expected: u64, policy: DeliveryPolicy) -> ServiceResult<DeliveryPolicyRecord> {
        let actor = Self::human(&caller)?;
        let attempted_at_ms = chrono::Utc::now().timestamp_millis();
        let mut current = self.snapshot.write().await;
        let before_version = current.version;
        let changed_fields = changed_fields(&current.policy, &policy).join(",");
        let outcome = async {
            policy.validate().map_err(|e| invalid(&e.to_string()))?;
            if current.version != expected { return Err(invalid("delivery_policy_version_conflict")); }
            if policy.needs_scheduler() && !self.scheduler_available.load(Ordering::SeqCst) {
                return Err(invalid("delivery_scheduler_unavailable: durable storage and a healthy scheduler are required"));
            }
            if self.provider_headers_configured && policy.needs_scheduler() {
                return Err(invalid("delivery_provider_headers_unsupported"));
            }
            let version = expected.checked_add(1).filter(|v| *v <= i64::MAX as u64).ok_or_else(|| invalid("delivery policy version exhausted"))?;
            let record = DeliveryPolicyRecord { version, policy, updated_by: actor.into(), updated_at_ms: chrono::Utc::now().timestamp_millis() };
            self.repository.replace_policy(expected, record.clone()).await.map_err(|error| match error {
                MessageDeliveryRepoError::Conflict => invalid("delivery_policy_version_conflict"),
                _ => ServiceError::InternalError("delivery policy persistence failed".into()),
            })?;
            *current = record.clone();
            if record.policy.needs_scheduler() && !self.scheduler_available.load(Ordering::SeqCst) {
                return Err(ServiceError::InternalError("policy persisted but scheduler stopped; restart required".into()));
            }
            Ok(record)
        }.await;
        tracing::info!(actor_id = actor, attempted_at_ms, finished_at_ms = chrono::Utc::now().timestamp_millis(),
            expected_version = expected, before_version, after_version = current.version, changed_fields,
            outcome = if outcome.is_ok() { "success" } else { "failure" },
            "delivery policy update audited");
        outcome
    }
}

fn invalid(message: &str) -> ServiceError {
    ServiceError::InvalidOperation { message: message.into(), request_id: None }
}

/// Log field names only, never entire policies, credentials or arbitrary values.
fn changed_fields(before: &DeliveryPolicy, after: &DeliveryPolicy) -> Vec<&'static str> {
    let mut fields = Vec::new();
    macro_rules! changed { ($field:expr, $left:expr, $right:expr) => { if $left != $right { fields.push($field); } }; }
    changed!("flow_enabled.group", before.flow_enabled.group, after.flow_enabled.group);
    changed!("flow_enabled.direct_a2a", before.flow_enabled.direct_a2a, after.flow_enabled.direct_a2a);
    changed!("flow_enabled.task", before.flow_enabled.task, after.flow_enabled.task);
    changed!("flow_enabled.system", before.flow_enabled.system, after.flow_enabled.system);
    changed!("flow_enabled.state_machine", before.flow_enabled.state_machine, after.flow_enabled.state_machine);
    changed!("defaults.mode", before.defaults.mode, after.defaults.mode);
    changed!("defaults.max_running", before.defaults.max_running, after.defaults.max_running);
    changed!("defaults.max_queued", before.defaults.max_queued, after.defaults.max_queued);
    changed!("defaults.min_send_interval_ms", before.defaults.min_send_interval_ms, after.defaults.min_send_interval_ms);
    changed!("bots", before.bots, after.bots);
    changed!("queue_ttl_ms", before.queue_ttl_ms, after.queue_ttl_ms);
    changed!("safe_retry", before.safe_retry, after.safe_retry);
    changed!("pause_dispatch", before.pause_dispatch, after.pause_dispatch);
    changed!("max_context_messages", before.max_context_messages, after.max_context_messages);
    changed!("max_context_bytes", before.max_context_bytes, after.max_context_bytes);
    fields
}
