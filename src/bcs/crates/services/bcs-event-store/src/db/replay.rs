//! SQL persistence for Event replay.

use super::*;

impl DbEventStore {
    pub(super) async fn sql_create_replay_target(
        &self,
        command: CreateEventReplayTarget,
    ) -> Result<EventFanoutTargetRecord, EventRepoError> {
        validate_replay(&command)?;
        if let Some(existing) = self.replay_target_for_request(&command).await? {
            return Ok(existing);
        }
        let (original, _) = self
            .get_delivery(&command.original_delivery_id, &command.env)
            .await?
            .ok_or_else(|| EventRepoError::NotFound(command.original_delivery_id.clone()))?;
        if original.subscription_id != command.subscription_id
            || original.status != EventDeliveryStatus::DeadLettered
            || original.resolved_by_delivery_id.is_some()
        {
            return Err(EventRepoError::Conflict(
                "only an unresolved dead-lettered Delivery can be replayed".into(),
            ));
        }
        let (subscription, revision) = self
            .get_subscription(&command.subscription_id, &command.env)
            .await?
            .ok_or_else(|| EventRepoError::NotFound(command.subscription_id.clone()))?;
        if subscription.current_revision != command.subscription_revision
            || subscription.status != EventSubscriptionStatus::Active
        {
            return Err(EventRepoError::Conflict(
                "replay revision must be the current active revision".into(),
            ));
        }
        let event = self
            .get_event(&original.event_id, &command.env)
            .await?
            .ok_or_else(|| EventRepoError::NotFound(original.event_id.clone()))?;
        if event.retention_until_ms <= command.created_at_ms {
            return Err(EventRepoError::Conflict(
                "Event payload retention has expired".into(),
            ));
        }

        let mut dependency = None;
        let mut causal_insert = None;
        if let Some(cause_event_id) = event.envelope.causation_event_id.as_deref() {
            let cause = self
                .get_event(cause_event_id, &command.env)
                .await?
                .ok_or_else(|| EventRepoError::CausationViolation(cause_event_id.to_string()))?;
            if revision
                .event_filters
                .iter()
                .any(|filter| event_filter_matches(filter, &cause.envelope.event_type))
            {
                let rows = self
                    .db
                    .query(DbStatement::with_params(
                        self.target_select_sql(
                            "WHERE env = ? AND event_id = ? AND subscription_id = ? \
                             AND subscription_revision = ? AND status <> 'cancelled' \
                             AND purpose IN ('normal', 'causal_prerequisite') \
                             ORDER BY CASE purpose WHEN 'normal' THEN 0 ELSE 1 END LIMIT 1",
                        ),
                        vec![
                            DbValue::from(command.env.as_str()),
                            DbValue::from(cause_event_id),
                            DbValue::from(command.subscription_id.as_str()),
                            DbValue::from(command.subscription_revision),
                        ],
                    ))
                    .await
                    .map_err(storage_error)?;
                if let Some(target) = rows.first().map(target_from_row).transpose()? {
                    dependency = Some(target.target_id);
                } else {
                    let target_id = deterministic_target_id(
                        &command.env,
                        cause_event_id,
                        &command.subscription_id,
                        command.subscription_revision,
                        EventFanoutTargetPurpose::CausalPrerequisite,
                    );
                    dependency = Some(target_id.clone());
                    causal_insert = Some((target_id, cause_event_id.to_string()));
                }
            }
        }

        let created_at = timestamp_value_from_ms(self.flavor, command.created_at_ms)?;
        let mut steps = vec![DbTransactionStep::Query(DbStatement::with_params(
            replay_delivery_lock_sql(self.flavor),
            vec![
                DbValue::from(command.env.as_str()),
                DbValue::from(command.original_delivery_id.as_str()),
            ],
        ))];
        if let Some((target_id, cause_event_id)) = causal_insert {
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(
                causal_replay_insert_sql(self.flavor),
                vec![
                    DbValue::from(target_id),
                    DbValue::from(cause_event_id.as_str()),
                    DbValue::from(command.subscription_id.as_str()),
                    DbValue::from(command.subscription_revision),
                    created_at.clone(),
                    DbValue::from(command.env.as_str()),
                    DbValue::from(command.env.as_str()),
                    DbValue::from(command.original_delivery_id.as_str()),
                ],
            )));
        }
        let replay_insert_step = steps.len();
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            replay_target_insert_sql(self.flavor),
            vec![
                DbValue::from(command.target_id.as_str()),
                DbValue::from(command.subscription_revision),
                DbValue::from(command.replay_request_id.as_str()),
                DbValue::from(command.original_delivery_id.as_str()),
                DbValue::from(dependency),
                created_at.clone(),
                DbValue::from(command.env.as_str()),
                DbValue::from(command.original_delivery_id.as_str()),
                DbValue::from(command.subscription_id.as_str()),
                DbValue::from(command.subscription_revision),
                created_at.clone(),
            ],
        )));
        let replay_audit_id = uuid::Uuid::new_v4().to_string();
        let replay_details = serde_json::json!({
            "original_delivery_id": command.original_delivery_id.as_str(),
            "replay_request_id": command.replay_request_id.as_str(),
        })
        .to_string();
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            sql_with_timestamp_params(self.flavor, "INSERT INTO bcs_event_subscription_audits \
             (audit_id, subscription_id, revision, \
             action, actor_type, actor_id, reason, details_json, created_at, env) \
             SELECT ?, target.subscription_id, target.subscription_revision, \
             'delivery_replayed', ?, ?, ?, ?, __bcs_timestamp_ms__, target.env \
             FROM bcs_event_fanout_targets target WHERE target.env = ? AND target.target_id = ? \
               AND target.purpose = 'manual_replay' AND NOT EXISTS (\
                 SELECT 1 FROM bcs_event_subscription_audits audit \
                 WHERE audit.env = target.env \
                   AND audit.subscription_id = target.subscription_id \
                   AND audit.action = 'delivery_replayed' AND audit.details_json = ?\
               )"),
            vec![
                DbValue::from(replay_audit_id),
                DbValue::from(actor_type_name(command.actor.actor_type)),
                DbValue::from(command.actor.id.as_str()),
                DbValue::from(command.reason.clone()),
                DbValue::from(replay_details.as_str()),
                created_at,
                DbValue::from(command.env.as_str()),
                DbValue::from(command.target_id.as_str()),
                DbValue::from(replay_details),
            ],
        )));
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            "UPDATE bcs_events SET fanout_status = 'pending' WHERE env = ? \
             AND event_id IN (?, ?)",
            vec![
                DbValue::from(command.env.as_str()),
                DbValue::from(original.event_id.as_str()),
                DbValue::from(event.envelope.causation_event_id.clone()),
            ],
        )));
        let target_query_step = steps.len();
        steps.push(DbTransactionStep::Query(DbStatement::with_params(
            self.target_select_sql(
                "WHERE env = ? AND purpose = 'manual_replay' AND replay_of_delivery_id = ? \
                 AND (replay_request_id = ? OR status = 'pending') \
                 ORDER BY CASE WHEN replay_request_id = ? THEN 0 ELSE 1 END, created_at LIMIT 1",
            ),
            vec![
                DbValue::from(command.env.as_str()),
                DbValue::from(command.original_delivery_id.as_str()),
                DbValue::from(command.replay_request_id.as_str()),
                DbValue::from(command.replay_request_id.as_str()),
            ],
        )));
        match self.db.transaction(steps).await {
            Ok(results) => {
                let rows = transaction_rows(&results, target_query_step)?;
                if transaction_affected_rows(&results, replay_insert_step)? == 1 {
                    return rows
                        .first()
                        .map(target_from_row)
                        .transpose()?
                        .ok_or_else(|| {
                            EventRepoError::Storage("created replay target missing".into())
                        });
                }
                rows.first()
                    .map(target_from_row)
                    .transpose()?
                    .ok_or_else(|| {
                        EventRepoError::Conflict(
                            "dead-lettered Delivery is no longer replayable".into(),
                        )
                    })
            }
            Err(error) if error.is_duplicate_key() => self
                .replay_target_for_request(&command)
                .await?
                .ok_or_else(|| EventRepoError::Conflict(error.to_string())),
            Err(error) => Err(storage_error(error)),
        }
    }

    pub(super) async fn sql_skip_dead_lettered_delivery(
        &self,
        command: SkipDeadLetteredEventDelivery,
    ) -> Result<EventDeliveryRecord, EventRepoError> {
        if command.reason.trim().is_empty() || command.reason.len() > 128 {
            return Err(EventRepoError::InvalidInput(
                "skip reason must be between 1 and 128 bytes".into(),
            ));
        }
        let skipped_at = timestamp_value_from_ms(self.flavor, command.skipped_at_ms)?;
        let skip_actor = serde_json::to_string(&command.actor)
            .map_err(|error| EventRepoError::InvalidInput(format!("serialize actor: {error}")))?;
        let audit_id = uuid::Uuid::new_v4().to_string();
        let update_step = 0;
        let steps = vec![
            DbTransactionStep::Execute(DbStatement::with_params(
                sql_with_timestamp_params(self.flavor, "UPDATE bcs_event_deliveries SET \
                 status = 'skipped', skipped_at = __bcs_timestamp_ms__, \
                 skip_actor = ?, skip_reason = ?, lease_owner = NULL, lease_until = NULL \
                 WHERE env = ? AND delivery_id = ? AND status = 'dead_lettered' \
                   AND resolved_by_delivery_id IS NULL"),
                vec![
                    skipped_at.clone(),
                    DbValue::from(skip_actor),
                    DbValue::from(command.reason.as_str()),
                    DbValue::from(command.env.as_str()),
                    DbValue::from(command.delivery_id.as_str()),
                ],
            )),
            DbTransactionStep::Execute(DbStatement::with_params(
                sql_with_timestamp_params(self.flavor, "UPDATE bcs_event_deliveries SET \
                 status = 'cancelled', cancelled_at = __bcs_timestamp_ms__, \
                 lease_owner = NULL, lease_until = NULL WHERE env = ? \
                 AND replay_of_delivery_id = ? AND status IN ('pending', 'retry_wait')"),
                vec![
                    skipped_at.clone(),
                    DbValue::from(command.env.as_str()),
                    DbValue::from(command.delivery_id.as_str()),
                ],
            )),
            DbTransactionStep::Execute(DbStatement::with_params(
                sql_with_timestamp_params(self.flavor, "UPDATE bcs_event_fanout_targets SET \
                 status = 'cancelled', cancelled_at = __bcs_timestamp_ms__, \
                 lease_owner = NULL, lease_until = NULL WHERE env = ? \
                 AND replay_of_delivery_id = ? AND status = 'pending'"),
                vec![
                    skipped_at.clone(),
                    DbValue::from(command.env.as_str()),
                    DbValue::from(command.delivery_id.as_str()),
                ],
            )),
            DbTransactionStep::Execute(DbStatement::with_params(
                sql_with_timestamp_params(self.flavor, "INSERT INTO \
                 bcs_event_subscription_audits (audit_id, subscription_id, \
                 revision, action, actor_type, actor_id, reason, details_json, created_at, env) \
                 SELECT ?, subscription_id, subscription_revision, 'delivery_skipped', ?, ?, ?, \
                 NULL, __bcs_timestamp_ms__, env FROM bcs_event_deliveries \
                 WHERE env = ? AND delivery_id = ? AND status = 'skipped' \
                   AND skipped_at = __bcs_timestamp_ms__"),
                vec![
                    DbValue::from(audit_id),
                    DbValue::from(actor_type_name(command.actor.actor_type)),
                    DbValue::from(command.actor.id.as_str()),
                    DbValue::from(command.reason.as_str()),
                    skipped_at.clone(),
                    DbValue::from(command.env.as_str()),
                    DbValue::from(command.delivery_id.as_str()),
                    skipped_at.clone(),
                ],
            )),
        ];
        let results = self.db.transaction(steps).await.map_err(map_write_error)?;
        if transaction_affected_rows(&results, update_step)? != 1 {
            return Err(EventRepoError::Conflict(
                "only an unresolved dead-lettered Delivery can be skipped".into(),
            ));
        }
        self.get_delivery(&command.delivery_id, &command.env)
            .await?
            .map(|(delivery, _)| delivery)
            .ok_or_else(|| EventRepoError::Storage("skipped Delivery missing".into()))
    }
}
