//! Scope-linearized in-memory Event repository for tests and local runtimes.

use std::collections::{BTreeMap, HashMap};
use std::sync::{Arc, Mutex as StdMutex};

use async_trait::async_trait;
use bcs_service_api::port::repo::{
    AppendEventRecord, AppendEventRecordResult, CancelPendingEventSubscriptions,
    ClaimEventDeliveries, ClaimFanoutTargets, CompleteEventDeliveryAttempt,
    CreateEventReplayTarget, CreateEventSubscriptionRecord, EventDeliveryAttemptRecord,
    EventDeliveryAttemptRecordResult, EventDeliveryRecord, EventFanoutStatus,
    EventFanoutTargetPurpose, EventFanoutTargetRecord, EventFanoutTargetStatus, EventRecord,
    EventRepoError, EventRepoPort, EventRetentionRequest, EventRetentionResult,
    EventSubscriptionRecord, EventSubscriptionRevisionRecord, ListEventDeliveryRecords,
    ListEventSubscriptionRecords, MaterializeFanoutTarget, RenewEventDeliveryLease,
    ReplaceEventSubscriptionRevision, SkipDeadLetteredEventDelivery,
};
use bcs_service_api::types::{
    EVENT_SOURCE, EVENT_SPEC_VERSION, EventDeliveryStatus, EventEnvelope, EventStream,
    EventSubscriptionScope, EventSubscriptionScopeType, EventSubscriptionStatus,
};
use chrono::DateTime;
use sha2::{Digest, Sha256};
use tokio::sync::{Mutex, OwnedMutexGuard, RwLock};

use crate::{event_filter_matches, subscription_scope_matches, validate_scope};

mod delivery;
mod fanout;
mod repo;
mod retention;

use fanout::*;

type SubscriptionKey = String;
type EventKey = String;
type ProducerKey = (String, String, String, String);
type StreamKey = (String, String);

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
struct ScopeKey {
    env: String,
    scope_type: EventSubscriptionScopeType,
    scope_id: String,
}

#[derive(Debug, Clone)]
struct StoredSubscription {
    record: EventSubscriptionRecord,
    revisions: BTreeMap<u64, EventSubscriptionRevisionRecord>,
}

#[derive(Debug, Clone, Default)]
struct MemoryState {
    subscriptions: HashMap<SubscriptionKey, StoredSubscription>,
    scope_epochs: HashMap<ScopeKey, u64>,
    events: HashMap<EventKey, EventRecord>,
    producers: HashMap<ProducerKey, String>,
    stream_sequences: HashMap<StreamKey, u64>,
    targets: HashMap<String, EventFanoutTargetRecord>,
    deliveries: HashMap<String, EventDeliveryRecord>,
    delivery_by_target: HashMap<String, String>,
    attempts: HashMap<String, BTreeMap<u32, EventDeliveryAttemptRecord>>,
}

/// The registry mutex only creates/retrieves keyed locks. Event and
/// Subscription critical sections use their scope/producer/stream locks, so
/// unrelated groups do not share one environment-wide serialization point.
#[derive(Debug, Default)]
struct LockRegistry {
    locks: StdMutex<HashMap<String, Arc<Mutex<()>>>>,
}

impl LockRegistry {
    fn get(&self, key: String) -> Arc<Mutex<()>> {
        let mut locks = self
            .locks
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        locks.entry(key).or_default().clone()
    }
}

#[derive(Debug, Default)]
pub struct MemoryEventStore {
    state: RwLock<MemoryState>,
    locks: LockRegistry,
}

impl MemoryEventStore {
    pub fn new() -> Self {
        Self::default()
    }

    async fn lock_scope(&self, key: &ScopeKey) -> OwnedMutexGuard<()> {
        self.locks
            .get(format!(
                "scope:{}:{:?}:{}",
                key.env, key.scope_type, key.scope_id
            ))
            .lock_owned()
            .await
    }

    async fn lock_named(&self, key: String) -> OwnedMutexGuard<()> {
        self.locks.get(key).lock_owned().await
    }

    async fn lock_event_scope_chain(
        &self,
        env: &str,
        scope: &bcs_service_api::types::EventScope,
    ) -> Vec<OwnedMutexGuard<()>> {
        let mut keys = event_scope_keys(env, scope);
        keys.sort_by(|left, right| left.scope_id.cmp(&right.scope_id));
        keys.dedup();

        let mut guards = Vec::with_capacity(keys.len());
        for key in keys {
            guards.push(self.lock_scope(&key).await);
        }
        guards
    }

    /// Commit the in-memory half of Group provisioning while the caller holds
    /// its Group write guard. Matching Events produced while the inline
    /// subscriptions were pending are snapshotted before the creation Events
    /// are appended. The callback flips Group availability only after a
    /// complete candidate Event state has been built; both locks remain held
    /// until the Event state is published.
    pub async fn finalize_group_provisioning<F>(
        &self,
        group_id: &str,
        subscription_ids: &[String],
        events: &[AppendEventRecord],
        finalized_at_ms: u64,
        env: &str,
        commit_group: F,
    ) -> Result<Vec<AppendEventRecordResult>, EventRepoError>
    where
        F: FnOnce() -> Result<(), EventRepoError>,
    {
        if events.iter().any(|event| {
            event.env != env || event.event.scope.group_id.as_deref() != Some(group_id)
        }) {
            return Err(EventRepoError::InvalidInput(
                "Group provisioning Events must use the finalized Group scope and environment"
                    .to_string(),
            ));
        }
        for event in events {
            validate_append(event)?;
        }

        let mut producer_locks = events
            .iter()
            .map(|event| {
                format!(
                    "producer:{}:{}:{}:{}",
                    event.env,
                    event.event.producer,
                    event.event.producer_key,
                    event.event.event_type
                )
            })
            .collect::<Vec<_>>();
        producer_locks.sort();
        producer_locks.dedup();
        let mut producer_guards = Vec::with_capacity(producer_locks.len());
        for key in producer_locks {
            producer_guards.push(self.lock_named(key).await);
        }

        let mut stream_locks = events
            .iter()
            .map(|event| format!("stream:{}:{}", event.env, event.event.stream_key))
            .collect::<Vec<_>>();
        stream_locks.sort();
        stream_locks.dedup();
        let mut stream_guards = Vec::with_capacity(stream_locks.len());
        for key in stream_locks {
            stream_guards.push(self.lock_named(key).await);
        }

        let mut scope_keys = events
            .iter()
            .flat_map(|event| event_scope_keys(env, &event.event.scope))
            .collect::<Vec<_>>();
        scope_keys.push(scope_key(
            env,
            &EventSubscriptionScope {
                scope_type: EventSubscriptionScopeType::Group,
                id: group_id.to_string(),
            },
        ));
        scope_keys.sort_by(|left, right| left.scope_id.cmp(&right.scope_id));
        scope_keys.dedup();
        let mut scope_guards = Vec::with_capacity(scope_keys.len());
        for key in &scope_keys {
            scope_guards.push(self.lock_scope(key).await);
        }

        let mut state = self.state.write().await;
        let mut candidate = state.clone();
        for subscription_id in subscription_ids {
            let stored = candidate
                .subscriptions
                .get_mut(subscription_id)
                .ok_or_else(|| {
                    EventRepoError::NotFound(format!(
                        "pending subscription {subscription_id} was not found"
                    ))
                })?;
            if stored.record.env != env
                || stored.record.scope.scope_type != EventSubscriptionScopeType::Group
                || stored.record.scope.id != group_id
                || stored.record.status != EventSubscriptionStatus::Pending
            {
                return Err(EventRepoError::Conflict(format!(
                    "subscription {subscription_id} is not pending for Group {group_id}"
                )));
            }
            stored.record.status = EventSubscriptionStatus::Active;
            stored.record.updated_at_ms = finalized_at_ms;
            let revision = stored
                .revisions
                .get_mut(&stored.record.current_revision)
                .ok_or_else(|| {
                    EventRepoError::Storage(format!(
                        "subscription {subscription_id} current revision is missing"
                    ))
                })?;
            revision.activated_at_ms = finalized_at_ms;
        }
        if !subscription_ids.is_empty() {
            *candidate
                .scope_epochs
                .entry(scope_key(
                    env,
                    &EventSubscriptionScope {
                        scope_type: EventSubscriptionScopeType::Group,
                        id: group_id.to_string(),
                    },
                ))
                .or_default() += 1;
        }
        backfill_group_provisioning_targets(
            &mut candidate,
            group_id,
            subscription_ids,
            finalized_at_ms,
            env,
        )?;
        let mut results = Vec::with_capacity(events.len());
        for event in events {
            results.push(append_event_to_state(&mut candidate, event)?);
        }
        commit_group()?;
        *state = candidate;
        drop(scope_guards);
        drop(stream_guards);
        drop(producer_guards);
        Ok(results)
    }

    /// Atomically publish one Group mutation Event while the owning memory
    /// repository commits its candidate Group state.
    pub async fn commit_group_mutation<F>(
        &self,
        group_id: &str,
        event: &AppendEventRecord,
        commit_group: F,
    ) -> Result<AppendEventRecordResult, EventRepoError>
    where
        F: FnOnce() -> Result<(), EventRepoError>,
    {
        if event.event.scope.group_id.as_deref() != Some(group_id) {
            return Err(EventRepoError::InvalidInput(
                "Group mutation Event must use the mutated Group scope".to_string(),
            ));
        }
        self.commit_mutation(event, commit_group).await
    }

    /// Atomically delete one Group and disable its active subscriptions.
    ///
    /// Pending fanout work and Deliveries that have not started are cancelled;
    /// an already in-flight HTTP request retains its lease and may complete.
    pub async fn commit_group_deletion<F>(
        &self,
        group_id: &str,
        env: &str,
        deleted_at_ms: u64,
        commit_group: F,
    ) -> Result<(), EventRepoError>
    where
        F: FnOnce() -> Result<(), EventRepoError>,
    {
        if group_id.is_empty() || env.is_empty() {
            return Err(EventRepoError::InvalidInput(
                "Group deletion group id and env must be non-empty".to_string(),
            ));
        }
        let scope = scope_key(
            env,
            &EventSubscriptionScope {
                scope_type: EventSubscriptionScopeType::Group,
                id: group_id.to_string(),
            },
        );
        let _scope_guard = self.lock_scope(&scope).await;
        let mut state = self.state.write().await;
        let mut candidate = state.clone();
        let subscription_ids = candidate
            .subscriptions
            .values()
            .filter(|stored| {
                stored.record.env == env
                    && stored.record.scope.scope_type == EventSubscriptionScopeType::Group
                    && stored.record.scope.id == group_id
                    && stored.record.status == EventSubscriptionStatus::Active
            })
            .map(|stored| stored.record.subscription_id.clone())
            .collect::<Vec<_>>();

        for subscription_id in &subscription_ids {
            if let Some(stored) = candidate.subscriptions.get_mut(subscription_id) {
                stored.record.status = EventSubscriptionStatus::Disabled;
                stored.record.updated_at_ms = deleted_at_ms;
            }
            for delivery in candidate.deliveries.values_mut().filter(|delivery| {
                delivery.env == env
                    && delivery.subscription_id == *subscription_id
                    && matches!(
                        delivery.status,
                        EventDeliveryStatus::Pending | EventDeliveryStatus::RetryWait
                    )
            }) {
                delivery.status = EventDeliveryStatus::Cancelled;
                delivery.cancelled_at_ms = Some(deleted_at_ms);
                delivery.next_attempt_at_ms = None;
                delivery.lease_owner = None;
                delivery.lease_until_ms = None;
            }
            for target in candidate.targets.values_mut().filter(|target| {
                target.env == env
                    && target.subscription_id == *subscription_id
                    && target.status == EventFanoutTargetStatus::Pending
            }) {
                target.status = EventFanoutTargetStatus::Cancelled;
                target.cancelled_at_ms = Some(deleted_at_ms);
                target.lease_owner = None;
                target.lease_until_ms = None;
            }
        }
        let affected_event_ids = candidate
            .targets
            .values()
            .filter(|target| {
                target.env == env && subscription_ids.contains(&target.subscription_id)
            })
            .map(|target| target.event_id.clone())
            .collect::<Vec<_>>();
        for event_id in affected_event_ids {
            complete_event_fanout_if_settled(&mut candidate, &event_id, env);
        }
        if !subscription_ids.is_empty() {
            *candidate.scope_epochs.entry(scope).or_default() += 1;
        }
        commit_group()?;
        *state = candidate;
        Ok(())
    }

    /// Atomically publish one Event and an in-memory business mutation. The
    /// business store must hold its own write guard for the duration of this
    /// call so neither side can become visible independently.
    pub async fn commit_business_mutation<F>(
        &self,
        event: &AppendEventRecord,
        commit_business: F,
    ) -> Result<AppendEventRecordResult, EventRepoError>
    where
        F: FnOnce() -> Result<(), EventRepoError>,
    {
        self.commit_mutation(event, commit_business).await
    }

    /// Atomically publish an ordered batch of Events and one in-memory
    /// business mutation. This is used when one transition intentionally
    /// exposes more than one public fact, such as run.created/run.started.
    pub async fn commit_business_mutations<F>(
        &self,
        events: &[AppendEventRecord],
        commit_business: F,
    ) -> Result<Vec<AppendEventRecordResult>, EventRepoError>
    where
        F: FnOnce() -> Result<(), EventRepoError>,
    {
        if events.is_empty() {
            return Err(EventRepoError::InvalidInput(
                "Eventful business mutation requires at least one Event".to_string(),
            ));
        }
        for event in events {
            validate_append(event)?;
        }

        let mut producer_keys = events
            .iter()
            .map(|event| {
                format!(
                    "producer:{}:{}:{}:{}",
                    event.env,
                    event.event.producer,
                    event.event.producer_key,
                    event.event.event_type
                )
            })
            .collect::<Vec<_>>();
        producer_keys.sort();
        producer_keys.dedup();
        let mut producer_guards = Vec::with_capacity(producer_keys.len());
        for key in producer_keys {
            producer_guards.push(self.lock_named(key).await);
        }

        let mut stream_keys = events
            .iter()
            .map(|event| format!("stream:{}:{}", event.env, event.event.stream_key))
            .collect::<Vec<_>>();
        stream_keys.sort();
        stream_keys.dedup();
        let mut stream_guards = Vec::with_capacity(stream_keys.len());
        for key in stream_keys {
            stream_guards.push(self.lock_named(key).await);
        }

        let mut scope_keys = events
            .iter()
            .flat_map(|event| event_scope_keys(&event.env, &event.event.scope))
            .collect::<Vec<_>>();
        scope_keys.sort_by(|left, right| {
            left.env
                .cmp(&right.env)
                .then_with(|| left.scope_id.cmp(&right.scope_id))
        });
        scope_keys.dedup();
        let mut scope_guards = Vec::with_capacity(scope_keys.len());
        for key in &scope_keys {
            scope_guards.push(self.lock_scope(key).await);
        }

        let mut state = self.state.write().await;
        let mut candidate = state.clone();
        let mut results = Vec::with_capacity(events.len());
        for event in events {
            results.push(append_event_to_state(&mut candidate, event)?);
        }
        commit_business()?;
        *state = candidate;
        drop(scope_guards);
        drop(stream_guards);
        drop(producer_guards);
        Ok(results)
    }

    async fn commit_mutation<F>(
        &self,
        event: &AppendEventRecord,
        commit_business: F,
    ) -> Result<AppendEventRecordResult, EventRepoError>
    where
        F: FnOnce() -> Result<(), EventRepoError>,
    {
        validate_append(event)?;
        let _producer_guard = self
            .lock_named(format!(
                "producer:{}:{}:{}:{}",
                event.env, event.event.producer, event.event.producer_key, event.event.event_type
            ))
            .await;
        let _stream_guard = self
            .lock_named(format!("stream:{}:{}", event.env, event.event.stream_key))
            .await;
        let mut scope_keys = event_scope_keys(&event.env, &event.event.scope);
        scope_keys.sort_by(|left, right| left.scope_id.cmp(&right.scope_id));
        scope_keys.dedup();
        let mut scope_guards = Vec::with_capacity(scope_keys.len());
        for key in &scope_keys {
            scope_guards.push(self.lock_scope(key).await);
        }

        let mut state = self.state.write().await;
        let mut candidate = state.clone();
        let result = append_event_to_state(&mut candidate, event)?;
        commit_business()?;
        *state = candidate;
        drop(scope_guards);
        Ok(result)
    }
}

fn scope_key(env: &str, scope: &EventSubscriptionScope) -> ScopeKey {
    ScopeKey {
        env: env.to_string(),
        scope_type: scope.scope_type,
        scope_id: scope.id.clone(),
    }
}

fn event_scope_keys(env: &str, scope: &bcs_service_api::types::EventScope) -> Vec<ScopeKey> {
    scope
        .group_id
        .as_ref()
        .map(|id| {
            vec![ScopeKey {
                env: env.to_string(),
                scope_type: EventSubscriptionScopeType::Group,
                scope_id: id.clone(),
            }]
        })
        .unwrap_or_default()
}

fn validate_create_subscription(
    record: &CreateEventSubscriptionRecord,
) -> Result<(), EventRepoError> {
    validate_scope(&record.subscription.scope).map_err(EventRepoError::InvalidInput)?;
    if record.subscription.subscription_id.is_empty() || record.subscription.env.is_empty() {
        return Err(EventRepoError::InvalidInput(
            "subscription id and env must be non-empty".to_string(),
        ));
    }
    if record.subscription.current_revision != 1
        || record.revision.revision != 1
        || record.revision.subscription_id != record.subscription.subscription_id
    {
        return Err(EventRepoError::InvalidInput(
            "new subscription must contain matching immutable revision 1".to_string(),
        ));
    }
    if record.scope_limit == 0 {
        return Err(EventRepoError::InvalidInput(
            "subscription scope limit must be non-zero".to_string(),
        ));
    }
    Ok(())
}

fn validate_lease_renewal(command: &RenewEventDeliveryLease) -> Result<(), EventRepoError> {
    if command.delivery_id.is_empty()
        || command.expected_lease_owner.is_empty()
        || command.attempt_no == 0
        || command.env.is_empty()
        || command.lease_until_ms <= command.now_ms
    {
        return Err(EventRepoError::InvalidInput(
            "Delivery lease renewal is invalid".to_string(),
        ));
    }
    Ok(())
}

fn validate_replace_subscription(
    command: &ReplaceEventSubscriptionRevision,
) -> Result<(), EventRepoError> {
    if command.subscription_id.is_empty() || command.env.is_empty() {
        return Err(EventRepoError::InvalidInput(
            "subscription id and env must be non-empty".to_string(),
        ));
    }
    if command.expected_revision == u64::MAX
        || command.revision.revision != command.expected_revision + 1
        || command.revision.subscription_id != command.subscription_id
    {
        return Err(EventRepoError::InvalidInput(
            "replacement must contain the next immutable revision".to_string(),
        ));
    }
    Ok(())
}

fn validate_append(command: &AppendEventRecord) -> Result<(), EventRepoError> {
    for (name, value) in [
        ("env", command.env.as_str()),
        ("event_id", command.event.event_id.as_str()),
        ("event_type", command.event.event_type.as_str()),
        ("producer", command.event.producer.as_str()),
        ("producer_key", command.event.producer_key.as_str()),
        ("stream_key", command.event.stream_key.as_str()),
    ] {
        if value.is_empty() {
            return Err(EventRepoError::InvalidInput(format!(
                "{name} must be non-empty"
            )));
        }
    }
    parse_timestamp_ms(&command.recorded_at)?;
    parse_timestamp_ms(&command.event.occurred_at)?;
    Ok(())
}

fn validate_claim(
    worker_id: &str,
    now_ms: u64,
    lease_until_ms: u64,
    limit: u32,
    env: &str,
) -> Result<(), EventRepoError> {
    if worker_id.is_empty() || worker_id.len() > 200 || env.is_empty() {
        return Err(EventRepoError::InvalidInput(
            "claim worker id and env must be non-empty".into(),
        ));
    }
    if limit == 0 || limit > 1_000 {
        return Err(EventRepoError::InvalidInput(
            "claim limit must be between 1 and 1000".into(),
        ));
    }
    if lease_until_ms <= now_ms {
        return Err(EventRepoError::InvalidInput(
            "claim lease must expire after now".into(),
        ));
    }
    Ok(())
}

fn claim_owner(worker_id: &str) -> String {
    format!("{worker_id}#{}", uuid::Uuid::new_v4())
}

fn validate_materialization(command: &MaterializeFanoutTarget) -> Result<(), EventRepoError> {
    let delivery = &command.delivery;
    if command.target_id.is_empty()
        || command.expected_lease_owner.is_empty()
        || delivery.delivery_id.is_empty()
        || delivery.env.is_empty()
    {
        return Err(EventRepoError::InvalidInput(
            "materialization identifiers and env must be non-empty".into(),
        ));
    }
    let valid_initial_status = delivery.status == EventDeliveryStatus::Pending
        || (delivery.status == EventDeliveryStatus::DeadLettered
            && delivery.dead_lettered_at_ms.is_some()
            && delivery.last_error_category.is_some());
    if !valid_initial_status
        || delivery.attempt_count != 0
        || delivery.lease_owner.is_some()
        || delivery.lease_until_ms.is_some()
    {
        return Err(EventRepoError::InvalidInput(
            "new Delivery must be pending or a projection dead letter, unattempted, and unleased"
                .into(),
        ));
    }
    let actual_sha = format!("{:x}", Sha256::digest(&delivery.payload_bytes));
    if delivery.payload_sha256 != actual_sha {
        return Err(EventRepoError::InvalidInput(
            "Delivery payload SHA-256 does not match payload bytes".into(),
        ));
    }
    Ok(())
}

fn validate_completion(command: &CompleteEventDeliveryAttempt) -> Result<(), EventRepoError> {
    if command.delivery_id.is_empty()
        || command.expected_lease_owner.is_empty()
        || command.attempt_no == 0
    {
        return Err(EventRepoError::InvalidInput(
            "completion identifiers and attempt number must be non-empty".into(),
        ));
    }
    if command.completed_at_ms < command.started_at_ms {
        return Err(EventRepoError::InvalidInput(
            "attempt completion cannot precede its start".into(),
        ));
    }
    if command
        .error_summary
        .as_ref()
        .is_some_and(|summary| summary.len() > 2_048)
    {
        return Err(EventRepoError::InvalidInput(
            "error summary exceeds 2048 bytes".into(),
        ));
    }
    if command
        .error_category
        .as_ref()
        .is_some_and(|category| category.len() > 128)
    {
        return Err(EventRepoError::InvalidInput(
            "error category exceeds 128 bytes".into(),
        ));
    }
    let valid = match command.result {
        EventDeliveryAttemptRecordResult::Success => {
            command.next_status == EventDeliveryStatus::Succeeded
                && command.next_attempt_at_ms.is_none()
        }
        EventDeliveryAttemptRecordResult::Retryable => {
            (command.next_status == EventDeliveryStatus::RetryWait
                && command
                    .next_attempt_at_ms
                    .is_some_and(|next| next > command.completed_at_ms))
                || (command.next_status == EventDeliveryStatus::DeadLettered
                    && command.next_attempt_at_ms.is_none())
        }
        EventDeliveryAttemptRecordResult::Terminal => {
            command.next_status == EventDeliveryStatus::DeadLettered
                && command.next_attempt_at_ms.is_none()
        }
    };
    if !valid {
        return Err(EventRepoError::InvalidInput(
            "Attempt result and next Delivery state are inconsistent".into(),
        ));
    }
    Ok(())
}

fn validate_list_limit(limit: u32, kind: &str) -> Result<(), EventRepoError> {
    if limit == 0 || limit > 100 {
        return Err(EventRepoError::InvalidInput(format!(
            "{kind} list limit must be between 1 and 100"
        )));
    }
    Ok(())
}

fn validate_replay(command: &CreateEventReplayTarget) -> Result<(), EventRepoError> {
    if command.original_delivery_id.is_empty()
        || command.subscription_id.is_empty()
        || command.replay_request_id.is_empty()
        || command.target_id.is_empty()
        || command.actor.id.is_empty()
        || command.env.is_empty()
        || command.subscription_revision == 0
    {
        return Err(EventRepoError::InvalidInput(
            "replay identifiers, revision, and env must be non-empty".into(),
        ));
    }
    if command
        .reason
        .as_ref()
        .is_some_and(|reason| reason.len() > 128)
    {
        return Err(EventRepoError::InvalidInput(
            "replay reason exceeds 128 bytes".into(),
        ));
    }
    Ok(())
}

fn parse_timestamp_ms(timestamp: &str) -> Result<u64, EventRepoError> {
    let parsed = DateTime::parse_from_rfc3339(timestamp).map_err(|error| {
        EventRepoError::InvalidInput(format!("invalid RFC3339 timestamp {timestamp:?}: {error}"))
    })?;
    u64::try_from(parsed.timestamp_millis()).map_err(|_| {
        EventRepoError::InvalidInput(format!("timestamp predates Unix epoch: {timestamp}"))
    })
}
