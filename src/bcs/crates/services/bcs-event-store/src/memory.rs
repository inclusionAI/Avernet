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
    /// its Group write guard. The callback flips Group availability only after
    /// a complete candidate Event state has been built; both locks remain held
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

#[async_trait]
impl EventRepoPort for MemoryEventStore {
    async fn create_subscription(
        &self,
        record: CreateEventSubscriptionRecord,
    ) -> Result<EventSubscriptionRecord, EventRepoError> {
        validate_create_subscription(&record)?;
        let scope = scope_key(&record.subscription.env, &record.subscription.scope);
        let _scope_guard = self.lock_scope(&scope).await;
        let key = record.subscription.subscription_id.clone();
        let mut state = self.state.write().await;
        if state.subscriptions.contains_key(&key) {
            return Err(EventRepoError::Conflict(format!(
                "subscription {} already exists",
                record.subscription.subscription_id
            )));
        }
        let reserved = state
            .subscriptions
            .values()
            .filter(|stored| {
                stored.record.env == record.subscription.env
                    && stored.record.scope == record.subscription.scope
                    && matches!(
                        stored.record.status,
                        EventSubscriptionStatus::Pending | EventSubscriptionStatus::Active
                    )
            })
            .count();
        if reserved >= record.scope_limit as usize {
            return Err(EventRepoError::LimitReached(
                "Scope has reached its Event Subscription limit".to_string(),
            ));
        }
        *state.scope_epochs.entry(scope).or_default() += 1;
        let result = record.subscription.clone();
        state.subscriptions.insert(
            key,
            StoredSubscription {
                record: record.subscription,
                revisions: BTreeMap::from([(record.revision.revision, record.revision)]),
            },
        );
        Ok(result)
    }

    async fn cancel_pending_subscriptions(
        &self,
        command: CancelPendingEventSubscriptions,
    ) -> Result<u64, EventRepoError> {
        if command.reason.trim().is_empty() {
            return Err(EventRepoError::InvalidInput(
                "pending Subscription cancellation reason must not be empty".to_string(),
            ));
        }
        let mut scopes = {
            let state = self.state.read().await;
            let mut scopes = Vec::new();
            for subscription_id in &command.subscription_ids {
                let Some(stored) = state.subscriptions.get(subscription_id) else {
                    continue;
                };
                if stored.record.env != command.env {
                    continue;
                }
                if stored.record.status != EventSubscriptionStatus::Pending
                    && stored.record.status != EventSubscriptionStatus::Deleted
                {
                    return Err(EventRepoError::Conflict(format!(
                        "subscription {subscription_id} is no longer pending"
                    )));
                }
                scopes.push(scope_key(&command.env, &stored.record.scope));
            }
            scopes
        };
        scopes.sort_by(|left, right| left.scope_id.cmp(&right.scope_id));
        scopes.dedup();
        let mut guards = Vec::with_capacity(scopes.len());
        for scope in &scopes {
            guards.push(self.lock_scope(scope).await);
        }

        let mut state = self.state.write().await;
        let mut cancelled = 0;
        let mut changed_scopes = Vec::new();
        for subscription_id in &command.subscription_ids {
            let Some(stored) = state.subscriptions.get_mut(subscription_id) else {
                continue;
            };
            if stored.record.env != command.env
                || stored.record.status == EventSubscriptionStatus::Deleted
            {
                continue;
            }
            if stored.record.status != EventSubscriptionStatus::Pending {
                return Err(EventRepoError::Conflict(format!(
                    "subscription {subscription_id} is no longer pending"
                )));
            }
            stored.record.status = EventSubscriptionStatus::Deleted;
            stored.record.updated_at_ms = command.cancelled_at_ms;
            stored.record.deleted_at_ms = Some(command.cancelled_at_ms);
            changed_scopes.push(scope_key(&command.env, &stored.record.scope));
            cancelled += 1;
        }
        changed_scopes.sort_by(|left, right| left.scope_id.cmp(&right.scope_id));
        changed_scopes.dedup();
        for scope in changed_scopes {
            *state.scope_epochs.entry(scope).or_default() += 1;
        }
        drop(guards);
        Ok(cancelled)
    }

    async fn get_subscription(
        &self,
        subscription_id: &str,
        env: &str,
    ) -> Result<Option<(EventSubscriptionRecord, EventSubscriptionRevisionRecord)>, EventRepoError>
    {
        let state = self.state.read().await;
        let Some(stored) = state.subscriptions.get(subscription_id) else {
            return Ok(None);
        };
        if stored.record.env != env {
            return Ok(None);
        }
        let revision = stored
            .revisions
            .get(&stored.record.current_revision)
            .ok_or_else(|| {
                EventRepoError::Storage("current subscription revision missing".to_string())
            })?;
        Ok(Some((stored.record.clone(), revision.clone())))
    }

    async fn get_subscription_revision(
        &self,
        subscription_id: &str,
        revision: u64,
        env: &str,
    ) -> Result<Option<EventSubscriptionRevisionRecord>, EventRepoError> {
        Ok(self
            .state
            .read()
            .await
            .subscriptions
            .get(subscription_id)
            .filter(|stored| stored.record.env == env)
            .and_then(|stored| stored.revisions.get(&revision))
            .cloned())
    }

    async fn list_subscriptions(
        &self,
        query: ListEventSubscriptionRecords,
    ) -> Result<Vec<EventSubscriptionRecord>, EventRepoError> {
        if query.limit == 0 || query.limit > 100 {
            return Err(EventRepoError::InvalidInput(
                "subscription list limit must be between 1 and 100".to_string(),
            ));
        }
        let mut records = self
            .state
            .read()
            .await
            .subscriptions
            .values()
            .filter(|stored| stored.record.env == query.env)
            .filter(|stored| {
                query
                    .status
                    .is_none_or(|status| stored.record.status == status)
            })
            .filter(|stored| {
                query
                    .scope
                    .as_ref()
                    .is_none_or(|scope| stored.record.scope == *scope)
            })
            .filter(|stored| {
                query
                    .after_subscription_id
                    .as_ref()
                    .is_none_or(|after| stored.record.subscription_id > *after)
            })
            .map(|stored| stored.record.clone())
            .collect::<Vec<_>>();
        records.sort_by(|left, right| left.subscription_id.cmp(&right.subscription_id));
        records.truncate(query.limit as usize);
        Ok(records)
    }

    async fn replace_subscription_revision(
        &self,
        command: ReplaceEventSubscriptionRevision,
    ) -> Result<EventSubscriptionRecord, EventRepoError> {
        validate_replace_subscription(&command)?;
        let lookup = command.subscription_id.clone();
        let existing_scope = self
            .state
            .read()
            .await
            .subscriptions
            .get(&lookup)
            .filter(|stored| stored.record.env == command.env)
            .map(|stored| stored.record.scope.clone())
            .ok_or_else(|| EventRepoError::NotFound(command.subscription_id.clone()))?;
        let scope = scope_key(&command.env, &existing_scope);
        let _scope_guard = self.lock_scope(&scope).await;
        let mut state = self.state.write().await;
        let stored = state
            .subscriptions
            .get_mut(&lookup)
            .filter(|stored| stored.record.env == command.env)
            .ok_or_else(|| EventRepoError::NotFound(command.subscription_id.clone()))?;
        if stored.record.current_revision != command.expected_revision {
            return Err(EventRepoError::Conflict(format!(
                "expected revision {}, found {}",
                command.expected_revision, stored.record.current_revision
            )));
        }
        if stored.record.status != command.status
            && !stored.record.status.can_transition_to(command.status)
        {
            return Err(EventRepoError::Conflict(format!(
                "invalid subscription status transition {:?} -> {:?}",
                stored.record.status, command.status
            )));
        }
        if let Some(previous) = stored.revisions.get_mut(&command.expected_revision) {
            previous.retired_at_ms = Some(command.updated_at_ms);
        }
        stored.record.name = command.name;
        stored.record.status = command.status;
        stored.record.current_revision = command.revision.revision;
        stored.record.updated_at_ms = command.updated_at_ms;
        if command.status == EventSubscriptionStatus::Deleted {
            stored.record.deleted_at_ms = Some(command.updated_at_ms);
        }
        stored
            .revisions
            .insert(command.revision.revision, command.revision);
        let result = stored.record.clone();

        if command.cancel_retired_pending_deliveries {
            let cancel_all_revisions = matches!(
                command.status,
                EventSubscriptionStatus::Disabled | EventSubscriptionStatus::Deleted
            );
            for delivery in state.deliveries.values_mut().filter(|delivery| {
                delivery.env == command.env
                    && delivery.subscription_id == command.subscription_id
                    && (cancel_all_revisions
                        || delivery.subscription_revision == command.expected_revision)
                    && matches!(
                        delivery.status,
                        EventDeliveryStatus::Pending | EventDeliveryStatus::RetryWait
                    )
            }) {
                delivery.status = EventDeliveryStatus::Cancelled;
                delivery.cancelled_at_ms = Some(command.updated_at_ms);
                delivery.lease_owner = None;
                delivery.lease_until_ms = None;
            }
            for target in state.targets.values_mut().filter(|target| {
                target.env == command.env
                    && target.subscription_id == command.subscription_id
                    && (cancel_all_revisions
                        || target.subscription_revision == command.expected_revision)
                    && target.status == EventFanoutTargetStatus::Pending
            }) {
                target.status = EventFanoutTargetStatus::Cancelled;
                target.cancelled_at_ms = Some(command.updated_at_ms);
            }
            let affected_event_ids = state
                .targets
                .values()
                .filter(|target| {
                    target.env == command.env
                        && target.subscription_id == command.subscription_id
                        && (cancel_all_revisions
                            || target.subscription_revision == command.expected_revision)
                })
                .map(|target| target.event_id.clone())
                .collect::<Vec<_>>();
            for event_id in affected_event_ids {
                complete_event_fanout_if_settled(&mut state, &event_id, &command.env);
            }
        }
        *state.scope_epochs.entry(scope).or_default() += 1;
        Ok(result)
    }

    async fn append_event(
        &self,
        command: AppendEventRecord,
    ) -> Result<AppendEventRecordResult, EventRepoError> {
        validate_append(&command)?;
        let producer_key = (
            command.env.clone(),
            command.event.producer.clone(),
            command.event.producer_key.clone(),
            command.event.event_type.clone(),
        );
        let _producer_guard = self
            .lock_named(format!(
                "producer:{}:{}:{}:{}",
                producer_key.0, producer_key.1, producer_key.2, producer_key.3
            ))
            .await;

        {
            let state = self.state.read().await;
            if let Some(existing) = existing_by_producer(&state, &producer_key) {
                return Ok(AppendEventRecordResult {
                    fanout_target_ids: target_ids_for_event(
                        &state,
                        &command.env,
                        &existing.envelope.event_id,
                    ),
                    event: existing,
                    deduplicated: true,
                });
            }
        }

        let stream_key = (command.env.clone(), command.event.stream_key.clone());
        let _stream_guard = self
            .lock_named(format!("stream:{}:{}", stream_key.0, stream_key.1))
            .await;
        let _scope_guards = self
            .lock_event_scope_chain(&command.env, &command.event.scope)
            .await;
        let recorded_at_ms = parse_timestamp_ms(&command.recorded_at)?;
        let mut state = self.state.write().await;

        if let Some(existing) = existing_by_producer(&state, &producer_key) {
            return Ok(AppendEventRecordResult {
                fanout_target_ids: target_ids_for_event(
                    &state,
                    &command.env,
                    &existing.envelope.event_id,
                ),
                event: existing,
                deduplicated: true,
            });
        }
        if state.events.contains_key(&command.event.event_id) {
            return Err(EventRepoError::Conflict(format!(
                "event id {} already exists",
                command.event.event_id
            )));
        }
        validate_causation(&state, &command)?;

        let sequence = state
            .stream_sequences
            .get(&stream_key)
            .copied()
            .unwrap_or(0)
            + 1;
        let envelope = EventEnvelope {
            spec_version: EVENT_SPEC_VERSION.to_string(),
            event_id: command.event.event_id.clone(),
            event_type: command.event.event_type.clone(),
            schema_version: command.event.schema_version.clone(),
            source: EVENT_SOURCE.to_string(),
            occurred_at: command.event.occurred_at.clone(),
            recorded_at: command.recorded_at.clone(),
            subject: command.event.subject.clone(),
            scope: command.event.scope.clone(),
            stream: EventStream {
                key: command.event.stream_key.clone(),
                sequence,
            },
            actor: command.event.actor.clone(),
            correlation_id: command.event.correlation_id.clone(),
            causation_event_id: command.event.causation_event_id.clone(),
            trace_id: command.event.trace_id.clone(),
            data: command.event.data.clone(),
        };

        let matching = state
            .subscriptions
            .values()
            .filter_map(|stored| {
                let revision = stored.revisions.get(&stored.record.current_revision)?;
                (stored.record.env == command.env
                    && stored.record.status == EventSubscriptionStatus::Active
                    && subscription_scope_matches(&stored.record.scope, &command.event.scope)
                    && revision
                        .event_filters
                        .iter()
                        .any(|filter| event_filter_matches(filter, &command.event.event_type)))
                .then(|| (stored.record.subscription_id.clone(), revision.revision))
            })
            .collect::<Vec<_>>();

        let event = EventRecord {
            envelope,
            producer: command.event.producer.clone(),
            producer_key: command.event.producer_key.clone(),
            fanout_status: if matching.is_empty() {
                EventFanoutStatus::Completed
            } else {
                EventFanoutStatus::Pending
            },
            retention_until_ms: command.retention_until_ms,
            env: command.env.clone(),
        };
        let mut fanout_target_ids = Vec::with_capacity(matching.len());
        for (subscription_id, subscription_revision) in matching {
            let depends_on_target_id = resolve_causal_target(
                &mut state,
                &command.env,
                command.event.causation_event_id.as_deref(),
                &subscription_id,
                subscription_revision,
                recorded_at_ms,
            )?;
            let target_id = target_id(
                &command.env,
                &event.envelope.event_id,
                &subscription_id,
                subscription_revision,
                EventFanoutTargetPurpose::Normal,
            );
            fanout_target_ids.push(target_id.clone());
            state.targets.insert(
                target_id.clone(),
                EventFanoutTargetRecord {
                    target_id,
                    event_id: event.envelope.event_id.clone(),
                    subscription_id,
                    subscription_revision,
                    purpose: EventFanoutTargetPurpose::Normal,
                    replay_request_id: None,
                    replay_of_delivery_id: None,
                    depends_on_target_id,
                    status: EventFanoutTargetStatus::Pending,
                    created_at_ms: recorded_at_ms,
                    materialized_at_ms: None,
                    cancelled_at_ms: None,
                    lease_owner: None,
                    lease_until_ms: None,
                    env: command.env.clone(),
                },
            );
        }
        state.stream_sequences.insert(stream_key, sequence);
        state
            .producers
            .insert(producer_key, event.envelope.event_id.clone());
        state
            .events
            .insert(event.envelope.event_id.clone(), event.clone());
        Ok(AppendEventRecordResult {
            event,
            fanout_target_ids,
            deduplicated: false,
        })
    }

    async fn get_event(
        &self,
        event_id: &str,
        env: &str,
    ) -> Result<Option<EventRecord>, EventRepoError> {
        Ok(self
            .state
            .read()
            .await
            .events
            .get(event_id)
            .filter(|event| event.env == env)
            .cloned())
    }

    async fn claim_fanout_targets(
        &self,
        command: ClaimFanoutTargets,
    ) -> Result<Vec<EventFanoutTargetRecord>, EventRepoError> {
        validate_claim(
            &command.worker_id,
            command.now_ms,
            command.lease_until_ms,
            command.limit,
            &command.env,
        )?;
        let _claim_guard = self
            .lock_named(format!("fanout-claim:{}", command.env))
            .await;
        let lease_owner = claim_owner(&command.worker_id);
        let mut state = self.state.write().await;
        let mut candidate_ids = state
            .targets
            .values()
            .filter(|target| {
                target.env == command.env
                    && target.status == EventFanoutTargetStatus::Pending
                    && target
                        .lease_until_ms
                        .is_none_or(|lease_until| lease_until <= command.now_ms)
            })
            .map(|target| (target.created_at_ms, target.target_id.clone()))
            .collect::<Vec<_>>();
        candidate_ids.sort();
        candidate_ids.truncate(command.limit as usize);

        let mut claimed = Vec::with_capacity(candidate_ids.len());
        for (_, target_id) in candidate_ids {
            let target = state.targets.get_mut(&target_id).ok_or_else(|| {
                EventRepoError::Storage(
                    "candidate target disappeared while state was write locked".into(),
                )
            })?;
            target.lease_owner = Some(lease_owner.clone());
            target.lease_until_ms = Some(command.lease_until_ms);
            claimed.push(target.clone());
        }
        Ok(claimed)
    }

    async fn renew_delivery_lease(
        &self,
        command: RenewEventDeliveryLease,
    ) -> Result<EventDeliveryRecord, EventRepoError> {
        validate_lease_renewal(&command)?;
        let _delivery_guard = self
            .lock_named(format!("delivery:{}", command.delivery_id))
            .await;
        let mut state = self.state.write().await;
        let delivery = state
            .deliveries
            .get_mut(&command.delivery_id)
            .ok_or_else(|| EventRepoError::NotFound(command.delivery_id.clone()))?;
        if delivery.env != command.env
            || delivery.status != EventDeliveryStatus::InFlight
            || delivery.lease_owner.as_deref() != Some(command.expected_lease_owner.as_str())
            || delivery.attempt_count != command.attempt_no
            || delivery
                .lease_until_ms
                .is_none_or(|lease_until| lease_until <= command.now_ms)
        {
            return Err(EventRepoError::LeaseLost(command.delivery_id));
        }
        delivery.lease_until_ms = Some(command.lease_until_ms);
        Ok(delivery.clone())
    }

    async fn materialize_fanout_target(
        &self,
        command: MaterializeFanoutTarget,
    ) -> Result<EventDeliveryRecord, EventRepoError> {
        validate_materialization(&command)?;
        let _target_guard = self
            .lock_named(format!("fanout-target:{}", command.target_id))
            .await;
        let mut state = self.state.write().await;
        if let Some(delivery_id) = state.delivery_by_target.get(&command.target_id) {
            return state
                .deliveries
                .get(delivery_id)
                .cloned()
                .ok_or_else(|| EventRepoError::Storage("delivery target index is corrupt".into()));
        }
        if state.deliveries.contains_key(&command.delivery.delivery_id) {
            return Err(EventRepoError::Conflict(format!(
                "delivery id {} already exists",
                command.delivery.delivery_id
            )));
        }
        let target = state
            .targets
            .get(&command.target_id)
            .filter(|target| target.env == command.delivery.env)
            .cloned()
            .ok_or_else(|| EventRepoError::NotFound(command.target_id.clone()))?;
        if target.status != EventFanoutTargetStatus::Pending
            || target.lease_owner.as_deref() != Some(command.expected_lease_owner.as_str())
            || target
                .lease_until_ms
                .is_none_or(|lease_until| lease_until <= command.materialized_at_ms)
        {
            return Err(EventRepoError::LeaseLost(command.target_id));
        }
        let event = state
            .events
            .get(&target.event_id)
            .ok_or_else(|| EventRepoError::Storage("target Event is missing".into()))?;
        if command.delivery.fanout_target_id != target.target_id
            || command.delivery.event_id != target.event_id
            || command.delivery.event_type != event.envelope.event_type
            || command.delivery.subscription_id != target.subscription_id
            || command.delivery.subscription_revision != target.subscription_revision
            || command.delivery.stream_key != event.envelope.stream.key
            || command.delivery.sequence != event.envelope.stream.sequence
            || command.delivery.replay_of_delivery_id != target.replay_of_delivery_id
        {
            return Err(EventRepoError::InvalidInput(
                "Delivery does not match its immutable fanout target and Event".into(),
            ));
        }

        let result = command.delivery;
        state
            .delivery_by_target
            .insert(target.target_id.clone(), result.delivery_id.clone());
        state
            .deliveries
            .insert(result.delivery_id.clone(), result.clone());
        let stored_target = state.targets.get_mut(&target.target_id).ok_or_else(|| {
            EventRepoError::Storage("target disappeared while state was write locked".into())
        })?;
        stored_target.status = EventFanoutTargetStatus::Materialized;
        stored_target.materialized_at_ms = Some(command.materialized_at_ms);
        stored_target.lease_owner = None;
        stored_target.lease_until_ms = None;
        complete_event_fanout_if_settled(&mut state, &target.event_id, &target.env);
        Ok(result)
    }

    async fn claim_deliveries(
        &self,
        command: ClaimEventDeliveries,
    ) -> Result<Vec<EventDeliveryRecord>, EventRepoError> {
        validate_claim(
            &command.worker_id,
            command.now_ms,
            command.lease_until_ms,
            command.limit,
            &command.env,
        )?;
        let _claim_guard = self
            .lock_named(format!("delivery-claim:{}", command.env))
            .await;
        let lease_owner = claim_owner(&command.worker_id);
        let mut state = self.state.write().await;
        let mut candidate_ids = state
            .deliveries
            .values()
            .filter(|delivery| delivery.env == command.env)
            .filter(|delivery| delivery_is_due(delivery, command.now_ms))
            .map(|delivery| {
                (
                    delivery.created_at_ms,
                    delivery.sequence,
                    delivery.delivery_id.clone(),
                )
            })
            .collect::<Vec<_>>();
        candidate_ids.sort();

        let mut claimed = Vec::new();
        for (_, _, delivery_id) in candidate_ids {
            if claimed.len() == command.limit as usize {
                break;
            }
            if !delivery_is_eligible(&state, &delivery_id, command.now_ms)? {
                continue;
            }
            let next_attempt_no = state
                .deliveries
                .get(&delivery_id)
                .ok_or_else(|| {
                    EventRepoError::Storage(
                        "candidate Delivery disappeared while state was write locked".into(),
                    )
                })?
                .attempt_count
                .checked_add(1)
                .ok_or_else(|| {
                    EventRepoError::Conflict("Delivery attempt counter overflow".into())
                })?;
            if state
                .attempts
                .get(&delivery_id)
                .is_some_and(|attempts| attempts.contains_key(&next_attempt_no))
            {
                return Err(EventRepoError::Conflict(format!(
                    "attempt {next_attempt_no} already exists for Delivery {delivery_id}"
                )));
            }
            let (abandoned_attempt_no, claimed_delivery) = {
                let delivery = state.deliveries.get_mut(&delivery_id).ok_or_else(|| {
                    EventRepoError::Storage(
                        "candidate Delivery disappeared while state was write locked".into(),
                    )
                })?;
                let abandoned_attempt_no = (delivery.status == EventDeliveryStatus::InFlight)
                    .then_some(delivery.attempt_count);
                delivery.status = EventDeliveryStatus::InFlight;
                delivery.attempt_count = next_attempt_no;
                delivery.first_attempt_at_ms.get_or_insert(command.now_ms);
                delivery.last_attempt_at_ms = Some(command.now_ms);
                delivery.next_attempt_at_ms = None;
                delivery.lease_owner = Some(lease_owner.clone());
                delivery.lease_until_ms = Some(command.lease_until_ms);
                (abandoned_attempt_no, delivery.clone())
            };
            let attempts = state.attempts.entry(delivery_id.clone()).or_default();
            if let Some(attempt_no) = abandoned_attempt_no
                && let Some(attempt) = attempts.get_mut(&attempt_no)
                && attempt.completed_at_ms.is_none()
            {
                attempt.completed_at_ms = Some(command.now_ms);
                attempt.result = Some(EventDeliveryAttemptRecordResult::Retryable);
                attempt.error_category = Some("lease_expired".to_string());
                attempt.error_summary = Some(
                    "Delivery lease expired before completion; remote outcome is unknown"
                        .to_string(),
                );
                attempt.response_bytes_observed = Some(0);
            }
            attempts.insert(
                claimed_delivery.attempt_count,
                EventDeliveryAttemptRecord {
                    delivery_id: delivery_id.clone(),
                    attempt_no: claimed_delivery.attempt_count,
                    started_at_ms: command.now_ms,
                    completed_at_ms: None,
                    latency_ms: None,
                    result: None,
                    http_status: None,
                    error_category: None,
                    error_summary: None,
                    response_bytes_observed: None,
                    worker_id: command.worker_id.clone(),
                },
            );
            claimed.push(claimed_delivery);
        }
        Ok(claimed)
    }

    async fn complete_delivery_attempt(
        &self,
        command: CompleteEventDeliveryAttempt,
    ) -> Result<EventDeliveryRecord, EventRepoError> {
        validate_completion(&command)?;
        let _delivery_guard = self
            .lock_named(format!("delivery:{}", command.delivery_id))
            .await;
        let mut state = self.state.write().await;
        let existing = state
            .deliveries
            .get(&command.delivery_id)
            .cloned()
            .ok_or_else(|| EventRepoError::NotFound(command.delivery_id.clone()))?;
        if existing.status != EventDeliveryStatus::InFlight
            || existing.lease_owner.as_deref() != Some(command.expected_lease_owner.as_str())
            || existing
                .lease_until_ms
                .is_none_or(|lease_until| lease_until <= command.completed_at_ms)
            || existing.attempt_count != command.attempt_no
        {
            return Err(EventRepoError::LeaseLost(command.delivery_id));
        }
        let attempts = state
            .attempts
            .get_mut(&command.delivery_id)
            .ok_or_else(|| {
                EventRepoError::Storage(format!(
                    "active attempt {} is missing for Delivery {}",
                    command.attempt_no, command.delivery_id
                ))
            })?;
        let attempt = attempts.get_mut(&command.attempt_no).ok_or_else(|| {
            EventRepoError::Storage(format!(
                "active attempt {} is missing for Delivery {}",
                command.attempt_no, command.delivery_id
            ))
        })?;
        if attempt.completed_at_ms.is_some() {
            return Err(EventRepoError::Conflict(format!(
                "attempt {} is already complete for Delivery {}",
                command.attempt_no, command.delivery_id
            )));
        }
        attempt.started_at_ms = command.started_at_ms;
        attempt.completed_at_ms = Some(command.completed_at_ms);
        attempt.latency_ms = Some(command.completed_at_ms - command.started_at_ms);
        attempt.result = Some(command.result);
        attempt.http_status = command.http_status;
        attempt.error_category = command.error_category.clone();
        attempt.error_summary = command.error_summary.clone();
        attempt.response_bytes_observed = Some(command.response_bytes_observed);
        let result = {
            let delivery = state
                .deliveries
                .get_mut(&command.delivery_id)
                .ok_or_else(|| {
                    EventRepoError::Storage(
                        "Delivery disappeared while state was write locked".into(),
                    )
                })?;
            delivery.status = command.next_status;
            delivery.last_attempt_at_ms = Some(command.completed_at_ms);
            delivery.next_attempt_at_ms = command.next_attempt_at_ms;
            delivery.lease_owner = None;
            delivery.lease_until_ms = None;
            delivery.last_http_status = command.http_status;
            delivery.last_error_category = command.error_category;
            delivery.last_error_summary = command.error_summary;
            if command.next_status == EventDeliveryStatus::Succeeded {
                delivery.succeeded_at_ms = Some(command.completed_at_ms);
            }
            if command.next_status == EventDeliveryStatus::DeadLettered {
                delivery.dead_lettered_at_ms = Some(command.completed_at_ms);
            }
            delivery.clone()
        };
        if command.next_status == EventDeliveryStatus::Succeeded
            && let Some(original_id) = result.replay_of_delivery_id.as_ref()
            && let Some(original) = state.deliveries.get_mut(original_id)
            && original.status == EventDeliveryStatus::DeadLettered
            && original.resolved_by_delivery_id.is_none()
        {
            original.resolved_by_delivery_id = Some(result.delivery_id.clone());
            original.resolved_at_ms = Some(command.completed_at_ms);
        }
        Ok(result)
    }

    async fn get_delivery(
        &self,
        delivery_id: &str,
        env: &str,
    ) -> Result<Option<(EventDeliveryRecord, Vec<EventDeliveryAttemptRecord>)>, EventRepoError>
    {
        let state = self.state.read().await;
        let Some(delivery) = state
            .deliveries
            .get(delivery_id)
            .filter(|delivery| delivery.env == env)
            .cloned()
        else {
            return Ok(None);
        };
        let attempts = state
            .attempts
            .get(delivery_id)
            .map(|attempts| attempts.values().cloned().collect())
            .unwrap_or_default();
        Ok(Some((delivery, attempts)))
    }

    async fn list_deliveries(
        &self,
        query: ListEventDeliveryRecords,
    ) -> Result<Vec<EventDeliveryRecord>, EventRepoError> {
        validate_list_limit(query.limit, "Delivery")?;
        let mut deliveries = self
            .state
            .read()
            .await
            .deliveries
            .values()
            .filter(|delivery| delivery.env == query.env)
            .filter(|delivery| {
                query
                    .subscription_id
                    .as_ref()
                    .is_none_or(|value| &delivery.subscription_id == value)
            })
            .filter(|delivery| {
                query
                    .event_id
                    .as_ref()
                    .is_none_or(|value| &delivery.event_id == value)
            })
            .filter(|delivery| query.status.is_none_or(|value| delivery.status == value))
            .filter(|delivery| {
                query
                    .after_delivery_id
                    .as_ref()
                    .is_none_or(|value| &delivery.delivery_id > value)
            })
            .cloned()
            .collect::<Vec<_>>();
        deliveries.sort_by(|left, right| left.delivery_id.cmp(&right.delivery_id));
        deliveries.truncate(query.limit as usize);
        Ok(deliveries)
    }

    async fn create_replay_target(
        &self,
        command: CreateEventReplayTarget,
    ) -> Result<EventFanoutTargetRecord, EventRepoError> {
        validate_replay(&command)?;
        let _delivery_guard = self
            .lock_named(format!("delivery:{}", command.original_delivery_id))
            .await;
        let mut state = self.state.write().await;
        if let Some(existing) = replay_target(&state, &command) {
            return Ok(existing);
        }
        let original = state
            .deliveries
            .get(&command.original_delivery_id)
            .filter(|delivery| delivery.env == command.env)
            .cloned()
            .ok_or_else(|| EventRepoError::NotFound(command.original_delivery_id.clone()))?;
        if original.subscription_id != command.subscription_id
            || original.status != EventDeliveryStatus::DeadLettered
            || original.resolved_by_delivery_id.is_some()
        {
            return Err(EventRepoError::Conflict(
                "only an unresolved dead-lettered Delivery can be replayed".into(),
            ));
        }
        if let Some(existing) = unresolved_replay_target(&state, &command.original_delivery_id) {
            return Ok(existing);
        }
        let subscription = state
            .subscriptions
            .get(&command.subscription_id)
            .filter(|stored| stored.record.env == command.env)
            .ok_or_else(|| EventRepoError::NotFound(command.subscription_id.clone()))?;
        if subscription.record.current_revision != command.subscription_revision
            || subscription.record.status != EventSubscriptionStatus::Active
        {
            return Err(EventRepoError::Conflict(
                "replay revision must be the current active revision".into(),
            ));
        }
        let event = state
            .events
            .get(&original.event_id)
            .cloned()
            .ok_or_else(|| EventRepoError::NotFound(original.event_id.clone()))?;
        if event.retention_until_ms <= command.created_at_ms {
            return Err(EventRepoError::Conflict(
                "Event payload retention has expired".into(),
            ));
        }
        let depends_on_target_id = resolve_causal_target(
            &mut state,
            &command.env,
            event.envelope.causation_event_id.as_deref(),
            &command.subscription_id,
            command.subscription_revision,
            command.created_at_ms,
        )?;
        if state.targets.contains_key(&command.target_id) {
            return Err(EventRepoError::Conflict(format!(
                "fanout target id {} already exists",
                command.target_id
            )));
        }
        let target = EventFanoutTargetRecord {
            target_id: command.target_id,
            event_id: original.event_id.clone(),
            subscription_id: command.subscription_id,
            subscription_revision: command.subscription_revision,
            purpose: EventFanoutTargetPurpose::ManualReplay,
            replay_request_id: Some(command.replay_request_id),
            replay_of_delivery_id: Some(original.delivery_id),
            depends_on_target_id,
            status: EventFanoutTargetStatus::Pending,
            created_at_ms: command.created_at_ms,
            materialized_at_ms: None,
            cancelled_at_ms: None,
            lease_owner: None,
            lease_until_ms: None,
            env: command.env,
        };
        state
            .targets
            .insert(target.target_id.clone(), target.clone());
        if let Some(event) = state.events.get_mut(&target.event_id) {
            event.fanout_status = EventFanoutStatus::Pending;
        }
        Ok(target)
    }

    async fn skip_dead_lettered_delivery(
        &self,
        command: SkipDeadLetteredEventDelivery,
    ) -> Result<EventDeliveryRecord, EventRepoError> {
        if command.reason.trim().is_empty() || command.reason.len() > 128 {
            return Err(EventRepoError::InvalidInput(
                "skip reason must be between 1 and 128 bytes".into(),
            ));
        }
        let _delivery_guard = self
            .lock_named(format!("delivery:{}", command.delivery_id))
            .await;
        let mut state = self.state.write().await;
        let result = {
            let delivery = state
                .deliveries
                .get_mut(&command.delivery_id)
                .filter(|delivery| delivery.env == command.env)
                .ok_or_else(|| EventRepoError::NotFound(command.delivery_id.clone()))?;
            if delivery.status != EventDeliveryStatus::DeadLettered
                || delivery.resolved_by_delivery_id.is_some()
            {
                return Err(EventRepoError::Conflict(
                    "only an unresolved dead-lettered Delivery can be skipped".into(),
                ));
            }
            delivery.status = EventDeliveryStatus::Skipped;
            delivery.skipped_at_ms = Some(command.skipped_at_ms);
            delivery.skip_actor = Some(command.actor);
            delivery.skip_reason = Some(command.reason);
            delivery.lease_owner = None;
            delivery.lease_until_ms = None;
            delivery.clone()
        };
        for replay in state.deliveries.values_mut().filter(|delivery| {
            delivery.env == command.env
                && delivery.replay_of_delivery_id.as_deref() == Some(command.delivery_id.as_str())
                && matches!(
                    delivery.status,
                    EventDeliveryStatus::Pending | EventDeliveryStatus::RetryWait
                )
        }) {
            replay.status = EventDeliveryStatus::Cancelled;
            replay.cancelled_at_ms = Some(command.skipped_at_ms);
            replay.lease_owner = None;
            replay.lease_until_ms = None;
        }
        for target in state.targets.values_mut().filter(|target| {
            target.env == command.env
                && target.replay_of_delivery_id.as_deref() == Some(command.delivery_id.as_str())
                && target.status == EventFanoutTargetStatus::Pending
        }) {
            target.status = EventFanoutTargetStatus::Cancelled;
            target.cancelled_at_ms = Some(command.skipped_at_ms);
            target.lease_owner = None;
            target.lease_until_ms = None;
        }
        Ok(result)
    }

    async fn purge_expired(
        &self,
        command: EventRetentionRequest,
    ) -> Result<EventRetentionResult, EventRepoError> {
        if command.event_limit == 0 || command.audit_limit == 0 || command.env.is_empty() {
            return Err(EventRepoError::InvalidInput(
                "retention limits and env must be non-empty".into(),
            ));
        }
        let _retention_guard = self.lock_named(format!("retention:{}", command.env)).await;
        let mut state = self.state.write().await;
        let mut event_ids = state
            .events
            .values()
            .filter(|event| event.env == command.env && event.retention_until_ms <= command.now_ms)
            .filter(|event| event_can_be_purged(&state, &event.envelope.event_id))
            .map(|event| event.envelope.event_id.clone())
            .collect::<Vec<_>>();
        event_ids.sort();
        event_ids.truncate(command.event_limit as usize);

        let mut result = EventRetentionResult::default();
        for event_id in event_ids {
            let target_ids = state
                .targets
                .values()
                .filter(|target| target.env == command.env && target.event_id == event_id)
                .map(|target| target.target_id.clone())
                .collect::<Vec<_>>();
            for target_id in target_ids {
                if let Some(delivery_id) = state.delivery_by_target.remove(&target_id) {
                    if state.deliveries.remove(&delivery_id).is_some() {
                        result.deliveries_deleted += 1;
                    }
                    result.attempts_deleted += state
                        .attempts
                        .remove(&delivery_id)
                        .map_or(0, |attempts| attempts.len() as u64);
                }
                state.targets.remove(&target_id);
            }
            if let Some(event) = state.events.remove(&event_id) {
                state.producers.remove(&(
                    event.env,
                    event.producer,
                    event.producer_key,
                    event.envelope.event_type,
                ));
                result.events_deleted += 1;
            }
        }
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

fn append_event_to_state(
    state: &mut MemoryState,
    command: &AppendEventRecord,
) -> Result<AppendEventRecordResult, EventRepoError> {
    let producer_key = (
        command.env.clone(),
        command.event.producer.clone(),
        command.event.producer_key.clone(),
        command.event.event_type.clone(),
    );
    if let Some(existing) = existing_by_producer(state, &producer_key) {
        return Ok(AppendEventRecordResult {
            fanout_target_ids: target_ids_for_event(
                state,
                &command.env,
                &existing.envelope.event_id,
            ),
            event: existing,
            deduplicated: true,
        });
    }
    if state.events.contains_key(&command.event.event_id) {
        return Err(EventRepoError::Conflict(format!(
            "event id {} already exists",
            command.event.event_id
        )));
    }
    validate_causation(state, command)?;
    let recorded_at_ms = parse_timestamp_ms(&command.recorded_at)?;
    let stream_key = (command.env.clone(), command.event.stream_key.clone());
    let sequence = state
        .stream_sequences
        .get(&stream_key)
        .copied()
        .unwrap_or(0)
        + 1;
    let envelope = EventEnvelope {
        spec_version: EVENT_SPEC_VERSION.to_string(),
        event_id: command.event.event_id.clone(),
        event_type: command.event.event_type.clone(),
        schema_version: command.event.schema_version.clone(),
        source: EVENT_SOURCE.to_string(),
        occurred_at: command.event.occurred_at.clone(),
        recorded_at: command.recorded_at.clone(),
        subject: command.event.subject.clone(),
        scope: command.event.scope.clone(),
        stream: EventStream {
            key: command.event.stream_key.clone(),
            sequence,
        },
        actor: command.event.actor.clone(),
        correlation_id: command.event.correlation_id.clone(),
        causation_event_id: command.event.causation_event_id.clone(),
        trace_id: command.event.trace_id.clone(),
        data: command.event.data.clone(),
    };
    let matching = state
        .subscriptions
        .values()
        .filter_map(|stored| {
            let revision = stored.revisions.get(&stored.record.current_revision)?;
            (stored.record.env == command.env
                && stored.record.status == EventSubscriptionStatus::Active
                && subscription_scope_matches(&stored.record.scope, &command.event.scope)
                && revision
                    .event_filters
                    .iter()
                    .any(|filter| event_filter_matches(filter, &command.event.event_type)))
            .then(|| (stored.record.subscription_id.clone(), revision.revision))
        })
        .collect::<Vec<_>>();
    let event = EventRecord {
        envelope,
        producer: command.event.producer.clone(),
        producer_key: command.event.producer_key.clone(),
        fanout_status: if matching.is_empty() {
            EventFanoutStatus::Completed
        } else {
            EventFanoutStatus::Pending
        },
        retention_until_ms: command.retention_until_ms,
        env: command.env.clone(),
    };
    let mut fanout_target_ids = Vec::with_capacity(matching.len());
    for (subscription_id, subscription_revision) in matching {
        let depends_on_target_id = resolve_causal_target(
            state,
            &command.env,
            command.event.causation_event_id.as_deref(),
            &subscription_id,
            subscription_revision,
            recorded_at_ms,
        )?;
        let target_id = target_id(
            &command.env,
            &event.envelope.event_id,
            &subscription_id,
            subscription_revision,
            EventFanoutTargetPurpose::Normal,
        );
        fanout_target_ids.push(target_id.clone());
        state.targets.insert(
            target_id.clone(),
            EventFanoutTargetRecord {
                target_id,
                event_id: event.envelope.event_id.clone(),
                subscription_id,
                subscription_revision,
                purpose: EventFanoutTargetPurpose::Normal,
                replay_request_id: None,
                replay_of_delivery_id: None,
                depends_on_target_id,
                status: EventFanoutTargetStatus::Pending,
                created_at_ms: recorded_at_ms,
                materialized_at_ms: None,
                cancelled_at_ms: None,
                lease_owner: None,
                lease_until_ms: None,
                env: command.env.clone(),
            },
        );
    }
    state.stream_sequences.insert(stream_key, sequence);
    state
        .producers
        .insert(producer_key, event.envelope.event_id.clone());
    state
        .events
        .insert(event.envelope.event_id.clone(), event.clone());
    Ok(AppendEventRecordResult {
        event,
        fanout_target_ids,
        deduplicated: false,
    })
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

fn validate_causation(
    state: &MemoryState,
    command: &AppendEventRecord,
) -> Result<(), EventRepoError> {
    let Some(cause_id) = command.event.causation_event_id.as_ref() else {
        return Ok(());
    };
    if cause_id == &command.event.event_id {
        return Err(EventRepoError::CausationViolation(
            "event cannot cause itself".to_string(),
        ));
    }
    if state
        .events
        .get(cause_id)
        .is_none_or(|cause| cause.env != command.env)
    {
        return Err(EventRepoError::CausationViolation(format!(
            "causation event {cause_id} must already exist in the same environment"
        )));
    }
    Ok(())
}

fn existing_by_producer(state: &MemoryState, key: &ProducerKey) -> Option<EventRecord> {
    let event_id = state.producers.get(key)?;
    state
        .events
        .get(event_id)
        .filter(|event| event.env == key.0)
        .cloned()
}

fn target_ids_for_event(state: &MemoryState, env: &str, event_id: &str) -> Vec<String> {
    let mut ids = state
        .targets
        .values()
        .filter(|target| target.env == env && target.event_id == event_id)
        .map(|target| target.target_id.clone())
        .collect::<Vec<_>>();
    ids.sort();
    ids
}

fn target_id(
    env: &str,
    event_id: &str,
    subscription_id: &str,
    revision: u64,
    purpose: EventFanoutTargetPurpose,
) -> String {
    let digest = Sha256::digest(
        format!("{env}\0{event_id}\0{subscription_id}\0{revision}\0{purpose:?}").as_bytes(),
    );
    format!("evtgt_{digest:x}")
}

fn resolve_causal_target(
    state: &mut MemoryState,
    env: &str,
    cause_event_id: Option<&str>,
    subscription_id: &str,
    subscription_revision: u64,
    created_at_ms: u64,
) -> Result<Option<String>, EventRepoError> {
    let Some(cause_event_id) = cause_event_id else {
        return Ok(None);
    };
    let cause = state
        .events
        .get(cause_event_id)
        .filter(|event| event.env == env)
        .cloned()
        .ok_or_else(|| {
            EventRepoError::CausationViolation(format!(
                "causation event {cause_event_id} must exist in the same environment"
            ))
        })?;
    let revision = state
        .subscriptions
        .get(subscription_id)
        .and_then(|subscription| subscription.revisions.get(&subscription_revision))
        .ok_or_else(|| EventRepoError::Storage("causal Subscription revision is missing".into()))?;
    if !revision
        .event_filters
        .iter()
        .any(|filter| event_filter_matches(filter, &cause.envelope.event_type))
    {
        return Ok(None);
    }

    let mut existing = state
        .targets
        .values()
        .filter(|target| {
            target.env == env
                && target.event_id == cause_event_id
                && target.subscription_id == subscription_id
                && target.subscription_revision == subscription_revision
                && target.status != EventFanoutTargetStatus::Cancelled
        })
        .cloned()
        .collect::<Vec<_>>();
    existing.sort_by_key(|target| match target.purpose {
        EventFanoutTargetPurpose::Normal => 0,
        EventFanoutTargetPurpose::CausalPrerequisite => 1,
        EventFanoutTargetPurpose::ManualReplay => 2,
    });
    if let Some(target) = existing.first() {
        return Ok(Some(target.target_id.clone()));
    }

    let target_id = target_id(
        env,
        cause_event_id,
        subscription_id,
        subscription_revision,
        EventFanoutTargetPurpose::CausalPrerequisite,
    );
    if state.targets.contains_key(&target_id) {
        return Err(EventRepoError::Conflict(
            "causal prerequisite was previously cancelled for this revision".into(),
        ));
    }
    state.targets.insert(
        target_id.clone(),
        EventFanoutTargetRecord {
            target_id: target_id.clone(),
            event_id: cause_event_id.to_string(),
            subscription_id: subscription_id.to_string(),
            subscription_revision,
            purpose: EventFanoutTargetPurpose::CausalPrerequisite,
            replay_request_id: None,
            replay_of_delivery_id: None,
            depends_on_target_id: None,
            status: EventFanoutTargetStatus::Pending,
            created_at_ms,
            materialized_at_ms: None,
            cancelled_at_ms: None,
            lease_owner: None,
            lease_until_ms: None,
            env: env.to_string(),
        },
    );
    if let Some(cause) = state.events.get_mut(cause_event_id) {
        cause.fanout_status = EventFanoutStatus::Pending;
    }
    Ok(Some(target_id))
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

fn complete_event_fanout_if_settled(state: &mut MemoryState, event_id: &str, env: &str) {
    let settled = state.targets.values().all(|target| {
        target.env != env
            || target.event_id != event_id
            || target.status != EventFanoutTargetStatus::Pending
    });
    if settled
        && let Some(event) = state.events.get_mut(event_id)
        && event.env == env
    {
        event.fanout_status = EventFanoutStatus::Completed;
    }
}

fn delivery_is_due(delivery: &EventDeliveryRecord, now_ms: u64) -> bool {
    let lease_expired = delivery
        .lease_until_ms
        .is_none_or(|lease_until| lease_until <= now_ms);
    match delivery.status {
        EventDeliveryStatus::Pending => lease_expired,
        EventDeliveryStatus::RetryWait => {
            lease_expired
                && delivery
                    .next_attempt_at_ms
                    .is_some_and(|next_attempt| next_attempt <= now_ms)
        }
        EventDeliveryStatus::InFlight => lease_expired,
        EventDeliveryStatus::Succeeded
        | EventDeliveryStatus::DeadLettered
        | EventDeliveryStatus::Cancelled
        | EventDeliveryStatus::Skipped => false,
    }
}

fn delivery_is_eligible(
    state: &MemoryState,
    delivery_id: &str,
    now_ms: u64,
) -> Result<bool, EventRepoError> {
    let delivery = state
        .deliveries
        .get(delivery_id)
        .ok_or_else(|| EventRepoError::Storage("candidate Delivery disappeared".into()))?;
    if !delivery_is_due(delivery, now_ms) || !delivery_dependency_satisfied(state, delivery) {
        return Ok(false);
    }
    let revision_exists = state
        .subscriptions
        .get(&delivery.subscription_id)
        .and_then(|subscription| subscription.revisions.get(&delivery.subscription_revision))
        .is_some();
    if !revision_exists {
        return Err(EventRepoError::Storage(
            "Delivery Subscription revision is missing".into(),
        ));
    }
    Ok(!state.deliveries.values().any(|previous| {
        previous.env == delivery.env
            && previous.subscription_id == delivery.subscription_id
            && previous.stream_key == delivery.stream_key
            && previous.sequence < delivery.sequence
            && !delivery_unblocks_lane(previous)
    }))
}

fn delivery_dependency_satisfied(state: &MemoryState, delivery: &EventDeliveryRecord) -> bool {
    let Some(dependency_id) = state
        .targets
        .get(&delivery.fanout_target_id)
        .and_then(|target| target.depends_on_target_id.as_ref())
    else {
        return true;
    };
    let Some(dependency_delivery) = state
        .delivery_by_target
        .get(dependency_id)
        .and_then(|delivery_id| state.deliveries.get(delivery_id))
    else {
        return false;
    };
    matches!(
        dependency_delivery.status,
        EventDeliveryStatus::Succeeded | EventDeliveryStatus::Skipped
    ) || (dependency_delivery.status == EventDeliveryStatus::DeadLettered
        && dependency_delivery.resolved_by_delivery_id.is_some())
}

fn delivery_unblocks_lane(delivery: &EventDeliveryRecord) -> bool {
    delivery.status.unblocks_strict_lane()
        || (delivery.status == EventDeliveryStatus::DeadLettered
            && delivery.resolved_by_delivery_id.is_some())
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

fn replay_target(
    state: &MemoryState,
    command: &CreateEventReplayTarget,
) -> Option<EventFanoutTargetRecord> {
    state
        .targets
        .values()
        .find(|target| {
            target.env == command.env
                && target.subscription_id == command.subscription_id
                && target.subscription_revision == command.subscription_revision
                && target.purpose == EventFanoutTargetPurpose::ManualReplay
                && target.replay_request_id.as_deref() == Some(&command.replay_request_id)
                && target.replay_of_delivery_id.as_deref() == Some(&command.original_delivery_id)
        })
        .cloned()
}

fn unresolved_replay_target(
    state: &MemoryState,
    original_delivery_id: &str,
) -> Option<EventFanoutTargetRecord> {
    state
        .targets
        .values()
        .find(|target| {
            if target.purpose != EventFanoutTargetPurpose::ManualReplay
                || target.replay_of_delivery_id.as_deref() != Some(original_delivery_id)
            {
                return false;
            }
            match target.status {
                EventFanoutTargetStatus::Pending => true,
                EventFanoutTargetStatus::Materialized => state
                    .delivery_by_target
                    .get(&target.target_id)
                    .and_then(|delivery_id| state.deliveries.get(delivery_id))
                    .is_some_and(|delivery| !delivery.status.is_attempt_terminal()),
                EventFanoutTargetStatus::Cancelled | EventFanoutTargetStatus::Failed => false,
            }
        })
        .cloned()
}

fn event_can_be_purged(state: &MemoryState, event_id: &str) -> bool {
    let targets = state
        .targets
        .values()
        .filter(|target| target.event_id == event_id)
        .collect::<Vec<_>>();
    if targets.is_empty() {
        return true;
    }
    if targets.iter().any(|target| {
        target.status == EventFanoutTargetStatus::Pending
            || state.targets.values().any(|dependent| {
                dependent.depends_on_target_id.as_deref() == Some(target.target_id.as_str())
            })
    }) {
        return false;
    }
    targets.iter().all(|target| {
        state
            .delivery_by_target
            .get(&target.target_id)
            .and_then(|delivery_id| state.deliveries.get(delivery_id))
            .is_none_or(delivery_unblocks_lane)
    })
}

fn parse_timestamp_ms(timestamp: &str) -> Result<u64, EventRepoError> {
    let parsed = DateTime::parse_from_rfc3339(timestamp).map_err(|error| {
        EventRepoError::InvalidInput(format!("invalid RFC3339 timestamp {timestamp:?}: {error}"))
    })?;
    u64::try_from(parsed.timestamp_millis()).map_err(|_| {
        EventRepoError::InvalidInput(format!("timestamp predates Unix epoch: {timestamp}"))
    })
}
