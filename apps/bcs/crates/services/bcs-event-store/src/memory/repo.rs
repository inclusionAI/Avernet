//! In-memory Event repository port implementation.

use super::*;

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
            .map(|target| {
                let (stream_key, sequence) = state
                    .events
                    .get(&target.event_id)
                    .map(|event| {
                        (
                            event.envelope.stream.key.clone(),
                            event.envelope.stream.sequence,
                        )
                    })
                    .unwrap_or_default();
                (
                    target.created_at_ms,
                    stream_key,
                    sequence,
                    target.target_id.clone(),
                )
            })
            .collect::<Vec<_>>();
        candidate_ids.sort();
        candidate_ids.truncate(command.limit as usize);

        let mut claimed = Vec::with_capacity(candidate_ids.len());
        for (_, _, _, target_id) in candidate_ids {
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
        // Body lives in `memory/delivery.rs` as an inherent method.
        self.claim_deliveries(command).await
    }

    async fn complete_delivery_attempt(
        &self,
        command: CompleteEventDeliveryAttempt,
    ) -> Result<EventDeliveryRecord, EventRepoError> {
        // Body lives in `memory/delivery.rs` as an inherent method.
        self.complete_delivery_attempt(command).await
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
        // Body lives in `memory/delivery.rs` as an inherent method.
        self.create_replay_target(command).await
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
        // Body lives in `memory/retention.rs` as an inherent method.
        self.purge_expired(command).await
    }
}
