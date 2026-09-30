//! Transactional versioned (eventful) Group mutations for the MySQL-backed Group store.

use super::*;

impl MySqlGroupStore {

    pub(crate) async fn finalize_provisioning_sql(&self, command: FinalizeGroupProvisioning) -> ServiceResult<()> {
        if command.env != self.env || command.events.iter().any(|event| event.env != self.env) {
            return Err(ServiceError::InvalidOperation {
                message: "Group provisioning Event environment mismatch".to_string(),
                request_id: None,
            });
        }
        let group_query_sql = match self.flavor {
            DbSqlFlavor::Mysql => {
                "SELECT group_id FROM bcs_groups WHERE env = ? AND group_id = ? \
                 AND record_status = 'provisioning' FOR UPDATE"
            }
            DbSqlFlavor::Sqlite => {
                "SELECT group_id FROM bcs_groups WHERE env = ? AND group_id = ? \
                 AND record_status = 'provisioning'"
            }
        };
        let mut steps = vec![DbTransactionStep::Query(DbStatement::with_params(
            group_query_sql,
            vec![
                Value::from(self.env.as_str()),
                Value::from(command.group_id.as_str()),
            ],
        ))];
        steps.push(DbTransactionStep::Execute(
            DbStatement::with_transaction_params(
                "UPDATE bcs_groups SET record_status = 'active', gmt_modified = CURRENT_TIMESTAMP \
                 WHERE env = ? AND group_id = ? AND record_status = 'provisioning'",
                vec![
                    DbTransactionParam::value(self.env.as_str()),
                    DbTransactionParam::query_result(0, 0, "group_id"),
                ],
            ),
        ));
        let event_plan = GroupProvisioningEventTransactionPlan::build(
            &command.group_id,
            &command.subscription_ids,
            &command.actor,
            command.finalized_at_ms,
            &self.env,
            &command.events,
            self.flavor,
            steps.len(),
        )
        .map_err(|error| ServiceError::InternalError(error.to_string()))?;
        steps.extend(event_plan.steps);
        self.db.plugin().transaction(steps).await.map_err(|error| {
            ServiceError::InternalError(format!("Group provisioning finalization failed: {error}"))
        })?;
        if let Some(group) = self.cache.write().await.get_mut(&command.group_id) {
            group.record_status = "active".to_string();
        }
        Ok(())
    }

    pub(crate) async fn commit_eventful_mutation_sql(
        &self,
        command: CommitGroupEventfulMutation,
    ) -> ServiceResult<Group> {
        if let Some(event) = command.event.as_ref()
            && (event.env != self.env
                || event.event.scope.group_id.as_deref() != Some(command.group_id.as_str()))
        {
            return Err(ServiceError::InvalidOperation {
                message: "Group mutation Event environment or scope mismatch".to_string(),
                request_id: None,
            });
        }
        let routing_policy_snapshot = match &command.mutation {
            GroupEventfulMutation::PatchMutableFields(patch)
                if patch.default_bot_final_delivery.is_some() =>
            {
                Some(
                    self.load_raw_routing_policy_json(&command.group_id)
                        .await?
                        .ok_or_else(|| ServiceError::GroupNotFound(command.group_id.clone()))?,
                )
            }
            _ => None,
        };
        let authoritative_routing_policy = routing_policy_snapshot
            .as_ref()
            .map(|stored_json| parse_stored_routing_policy_json(stored_json.as_deref()))
            .transpose()?;
        let current = self
            .try_get(&command.group_id)
            .await?
            .ok_or_else(|| ServiceError::GroupNotFound(command.group_id.clone()))?;
        if current.version != command.expected_version {
            return Err(ServiceError::Conflict(format!(
                "Group '{}' expected version {}, found {}",
                command.group_id, command.expected_version, current.version
            )));
        }
        let deleting = matches!(&command.mutation, GroupEventfulMutation::Delete);
        if deleting && command.event.is_some() {
            return Err(ServiceError::InvalidOperation {
                message: "Group deletion is not part of the public Event Catalog".to_string(),
                request_id: None,
            });
        }
        let mut terminal = current;
        if let Some(policy) = authoritative_routing_policy {
            terminal.routing_policy = policy;
        }
        apply_db_group_mutation_candidate(&mut terminal, &command.mutation, command.mutated_at_ms)?;
        let mutated_at = db_timestamp_from_millis(command.mutated_at_ms)?;
        let routing_policy_guard = match self.flavor {
            DbSqlFlavor::Mysql if routing_policy_snapshot.is_some() => {
                " AND routing_policy_json <=> ?"
            }
            DbSqlFlavor::Sqlite if routing_policy_snapshot.is_some() => {
                " AND routing_policy_json IS ?"
            }
            _ => "",
        };
        let lock_sql = match self.flavor {
            DbSqlFlavor::Mysql => format!(
                "SELECT group_id FROM bcs_groups WHERE env = ? AND group_id = ? \
                 AND version = ? AND record_status = 'active'{routing_policy_guard} FOR UPDATE"
            ),
            DbSqlFlavor::Sqlite => format!(
                "SELECT group_id FROM bcs_groups WHERE env = ? AND group_id = ? \
                 AND version = ? AND record_status = 'active'{routing_policy_guard}"
            ),
        };
        let mut lock_params = vec![
            Value::from(self.env.as_str()),
            Value::from(command.group_id.as_str()),
            Value::from(command.expected_version),
        ];
        if let Some(stored_json) = &routing_policy_snapshot {
            lock_params.push(Value::from(stored_json.as_deref()));
        }
        let mut steps = vec![DbTransactionStep::Query(DbStatement::with_params(
            lock_sql,
            lock_params,
        ))];
        let group_id = DbTransactionParam::query_result(0, 0, "group_id");

        match &command.mutation {
            GroupEventfulMutation::PatchMutableFields(patch) => {
                let mut assignments = Vec::new();
                let mut params = Vec::new();
                if let Some(label) = &patch.label {
                    assignments.push("label = ?".to_string());
                    params.push(DbTransactionParam::value(label.as_str()));
                }
                if let Some(context) = &patch.context {
                    assignments.push("context = ?".to_string());
                    params.push(DbTransactionParam::value(context.as_str()));
                }
                if let Some(opening_message) = &patch.opening_message {
                    let json = opening_message
                        .as_ref()
                        .map(serde_json::to_string)
                        .transpose()
                        .map_err(|error| {
                            ServiceError::InternalError(format!(
                                "serialize opening_message: {error}"
                            ))
                        })?;
                    assignments.push("opening_message_json = ?".to_string());
                    params.push(DbTransactionParam::value(json));
                }
                if let Some(visibility) = &patch.visibility {
                    assignments.push("visibility = ?".to_string());
                    params.push(DbTransactionParam::value(visibility.as_str()));
                }
                if let Some(delivery) = patch.default_bot_final_delivery {
                    let policy_json =
                        routing_policy_json(terminal.routing_policy.as_ref(), delivery);
                    assignments.push("routing_policy_json = ?".to_string());
                    params.push(DbTransactionParam::value(policy_json));
                }
                if let Some(mode) = patch.human_mention_notify_mode {
                    assignments.push("human_mention_notify_mode = ?".to_string());
                    params.push(DbTransactionParam::value(
                        Self::human_mention_notify_mode_to_str(mode),
                    ));
                }
                if assignments.is_empty() {
                    return Err(ServiceError::Conflict(
                        "Group mutation contains no changed fields".to_string(),
                    ));
                }
                assignments.push("version = version + 1".to_string());
                assignments.push("gmt_modified = ?".to_string());
                params.push(DbTransactionParam::value(mutated_at.as_str()));
                params.push(DbTransactionParam::value(self.env.as_str()));
                params.push(group_id.clone());
                steps.push(DbTransactionStep::Execute(
                    DbStatement::with_transaction_params(
                        format!(
                            "UPDATE bcs_groups SET {} WHERE env = ? AND group_id = ?",
                            assignments.join(", ")
                        ),
                        params,
                    ),
                ));
            }
            GroupEventfulMutation::UpdateStatus(status) => {
                steps.push(DbTransactionStep::Execute(
                    DbStatement::with_transaction_params(
                        "UPDATE bcs_groups SET status = ?, version = version + 1, \
                         gmt_modified = ? WHERE env = ? AND group_id = ?",
                        vec![
                            DbTransactionParam::value(Self::status_to_str(status)),
                            DbTransactionParam::value(mutated_at.as_str()),
                            DbTransactionParam::value(self.env.as_str()),
                            group_id.clone(),
                        ],
                    ),
                ));
            }
            GroupEventfulMutation::AddParticipant {
                participant,
                actor_is_public,
            } => {
                if participant.is_bot() && terminal.visibility == "public" && !actor_is_public {
                    return Err(ServiceError::ExistNonPublicBots {
                        bots: vec![(participant.bot_uuid.clone(), participant.bot_name.clone())],
                    });
                }
                steps.push(DbTransactionStep::Execute(
                    DbStatement::with_transaction_params(
                        "INSERT INTO bcs_group_participants \
                         (group_id, bot_uuid, role, env, actor_kind, mode, message_view_scope) \
                         VALUES (?, ?, ?, ?, ?, ?, ?)",
                        vec![
                            group_id.clone(),
                            DbTransactionParam::value(participant.bot_uuid.as_str()),
                            DbTransactionParam::value(Self::role_to_str(&participant.role)),
                            DbTransactionParam::value(self.env.as_str()),
                            DbTransactionParam::value(Self::actor_kind_to_str(
                                participant.actor_kind,
                            )),
                            DbTransactionParam::value(Self::mode_to_str(
                                participant.effective_mode(),
                            )),
                            DbTransactionParam::value(Self::message_view_scope_to_str(
                                participant.message_view_scope,
                            )),
                        ],
                    ),
                ));
                steps.push(group_version_update_step(
                    &self.env,
                    group_id.clone(),
                    &mutated_at,
                ));
            }
            GroupEventfulMutation::RemoveParticipant { actor_id } => {
                let participant_query_step = steps.len();
                let participant_lock = match self.flavor {
                    DbSqlFlavor::Mysql => {
                        "SELECT bot_uuid FROM bcs_group_participants \
                         WHERE env = ? AND group_id = ? AND bot_uuid = ? FOR UPDATE"
                    }
                    DbSqlFlavor::Sqlite => {
                        "SELECT bot_uuid FROM bcs_group_participants \
                         WHERE env = ? AND group_id = ? AND bot_uuid = ?"
                    }
                };
                steps.push(DbTransactionStep::Query(
                    DbStatement::with_transaction_params(
                        participant_lock,
                        vec![
                            DbTransactionParam::value(self.env.as_str()),
                            group_id.clone(),
                            DbTransactionParam::value(actor_id.as_str()),
                        ],
                    ),
                ));
                steps.push(DbTransactionStep::Execute(
                    DbStatement::with_transaction_params(
                        "DELETE FROM bcs_group_participants \
                         WHERE env = ? AND group_id = ? AND bot_uuid = ?",
                        vec![
                            DbTransactionParam::value(self.env.as_str()),
                            group_id.clone(),
                            DbTransactionParam::query_result(participant_query_step, 0, "bot_uuid"),
                        ],
                    ),
                ));
                steps.push(group_version_update_step(
                    &self.env,
                    group_id.clone(),
                    &mutated_at,
                ));
            }
            GroupEventfulMutation::UpdateParticipantMode { actor_id, mode } => {
                let participant_query_step = steps.len();
                let participant_lock = match self.flavor {
                    DbSqlFlavor::Mysql => {
                        "SELECT bot_uuid FROM bcs_group_participants \
                         WHERE env = ? AND group_id = ? AND bot_uuid = ? FOR UPDATE"
                    }
                    DbSqlFlavor::Sqlite => {
                        "SELECT bot_uuid FROM bcs_group_participants \
                         WHERE env = ? AND group_id = ? AND bot_uuid = ?"
                    }
                };
                steps.push(DbTransactionStep::Query(
                    DbStatement::with_transaction_params(
                        participant_lock,
                        vec![
                            DbTransactionParam::value(self.env.as_str()),
                            group_id.clone(),
                            DbTransactionParam::value(actor_id.as_str()),
                        ],
                    ),
                ));
                steps.push(DbTransactionStep::Execute(
                    DbStatement::with_transaction_params(
                        "UPDATE bcs_group_participants SET mode = ? \
                         WHERE env = ? AND group_id = ? AND bot_uuid = ?",
                        vec![
                            DbTransactionParam::value(Self::mode_to_str(*mode)),
                            DbTransactionParam::value(self.env.as_str()),
                            group_id.clone(),
                            DbTransactionParam::query_result(participant_query_step, 0, "bot_uuid"),
                        ],
                    ),
                ));
                steps.push(group_version_update_step(
                    &self.env,
                    group_id.clone(),
                    &mutated_at,
                ));
            }
            GroupEventfulMutation::UpdateParticipantMessageViewScope {
                actor_id,
                message_view_scope,
                mode,
            } => {
                let participant_query_step = steps.len();
                let participant_lock = match self.flavor {
                    DbSqlFlavor::Mysql => {
                        "SELECT bot_uuid FROM bcs_group_participants \
                         WHERE env = ? AND group_id = ? AND bot_uuid = ? FOR UPDATE"
                    }
                    DbSqlFlavor::Sqlite => {
                        "SELECT bot_uuid FROM bcs_group_participants \
                         WHERE env = ? AND group_id = ? AND bot_uuid = ?"
                    }
                };
                steps.push(DbTransactionStep::Query(
                    DbStatement::with_transaction_params(
                        participant_lock,
                        vec![
                            DbTransactionParam::value(self.env.as_str()),
                            group_id.clone(),
                            DbTransactionParam::value(actor_id.as_str()),
                        ],
                    ),
                ));
                let (update_sql, update_params) = if let Some(mode) = mode {
                    (
                        "UPDATE bcs_group_participants \
                         SET message_view_scope = ?, mode = ? \
                         WHERE env = ? AND group_id = ? AND bot_uuid = ?",
                        vec![
                            DbTransactionParam::value(Self::message_view_scope_to_str(
                                *message_view_scope,
                            )),
                            DbTransactionParam::value(Self::mode_to_str(*mode)),
                            DbTransactionParam::value(self.env.as_str()),
                            group_id.clone(),
                            DbTransactionParam::query_result(participant_query_step, 0, "bot_uuid"),
                        ],
                    )
                } else {
                    (
                        "UPDATE bcs_group_participants SET message_view_scope = ? \
                         WHERE env = ? AND group_id = ? AND bot_uuid = ?",
                        vec![
                            DbTransactionParam::value(Self::message_view_scope_to_str(
                                *message_view_scope,
                            )),
                            DbTransactionParam::value(self.env.as_str()),
                            group_id.clone(),
                            DbTransactionParam::query_result(participant_query_step, 0, "bot_uuid"),
                        ],
                    )
                };
                steps.push(DbTransactionStep::Execute(
                    DbStatement::with_transaction_params(update_sql, update_params),
                ));
                steps.push(group_version_update_step(
                    &self.env,
                    group_id.clone(),
                    &mutated_at,
                ));
            }
            GroupEventfulMutation::UpdateRoutingPolicy(policy) => {
                let policy_json = serde_json::to_string(policy)
                    .map_err(|error| ServiceError::InternalError(error.to_string()))?;
                steps.push(DbTransactionStep::Execute(
                    DbStatement::with_transaction_params(
                        "UPDATE bcs_groups SET routing_policy_json = ?, version = version + 1, \
                         gmt_modified = ? WHERE env = ? AND group_id = ?",
                        vec![
                            DbTransactionParam::value(policy_json),
                            DbTransactionParam::value(mutated_at.as_str()),
                            DbTransactionParam::value(self.env.as_str()),
                            group_id.clone(),
                        ],
                    ),
                ));
            }
            GroupEventfulMutation::UpdateServiceSpec(service_spec) => {
                let spec_json = service_spec
                    .as_ref()
                    .map(serde_json::to_string)
                    .transpose()
                    .map_err(|error| ServiceError::InternalError(error.to_string()))?;
                steps.push(DbTransactionStep::Execute(
                    DbStatement::with_transaction_params(
                        "UPDATE bcs_groups SET service_spec = ?, version = version + 1, \
                         gmt_modified = ? WHERE env = ? AND group_id = ?",
                        vec![
                            DbTransactionParam::value(
                                spec_json.as_deref().map(Value::from).unwrap_or(Value::Null),
                            ),
                            DbTransactionParam::value(mutated_at.as_str()),
                            DbTransactionParam::value(self.env.as_str()),
                            group_id.clone(),
                        ],
                    ),
                ));
            }
            GroupEventfulMutation::Delete => {}
        }

        if let Some(event) = command.event.as_ref() {
            let event_plan = EventAppendTransactionPlan::build(event, self.flavor, steps.len())
                .map_err(|error| ServiceError::InternalError(error.to_string()))?;
            steps.extend(event_plan.steps);
        }
        if deleting {
            let cleanup_plan = GroupDeletionEventTransactionPlan::build(
                &command.group_id,
                &self.env,
                command.mutated_at_ms,
                self.flavor,
            )
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;
            steps.extend(cleanup_plan.steps);
            steps.push(DbTransactionStep::Execute(
                DbStatement::with_transaction_params(
                    "DELETE FROM bcs_group_participants WHERE env = ? AND group_id = ?",
                    vec![
                        DbTransactionParam::value(self.env.as_str()),
                        group_id.clone(),
                    ],
                ),
            ));
            steps.push(DbTransactionStep::Execute(
                DbStatement::with_transaction_params(
                    "DELETE FROM bcs_groups WHERE env = ? AND group_id = ?",
                    vec![DbTransactionParam::value(self.env.as_str()), group_id],
                ),
            ));
        }
        if let Err(error) = self.db.plugin().transaction(steps).await {
            if routing_policy_snapshot.is_some() && transaction_lock_row_is_missing(&error) {
                self.cache.write().await.remove(&command.group_id);
                return Err(ServiceError::Conflict(format!(
                    "Group '{}' routing policy changed concurrently",
                    command.group_id
                )));
            }
            return Err(ServiceError::InternalError(format!(
                "Eventful Group mutation failed: {error}"
            )));
        }
        self.cache.write().await.remove(&command.group_id);
        if deleting {
            return Ok(terminal);
        }
        self.try_get(&command.group_id)
            .await?
            .ok_or_else(|| ServiceError::GroupNotFound(command.group_id))
    }
}
