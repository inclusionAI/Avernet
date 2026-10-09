//! Direct (non-eventful) Group write paths for the MySQL-backed Group store.

use super::*;

impl MySqlGroupStore {

    /// Delete session from MySQL.
    pub(crate) async fn delete_group_from_mysql(&self, group_id: &str) -> ServiceResult<bool> {
        let env = self.env.as_str();
        let results = self
            .db
            .plugin()
            .transaction(vec![
                DbTransactionStep::Execute(DbStatement::with_params(
                    "DELETE FROM bcs_group_participants WHERE group_id = ? AND env = ?",
                    vec![Value::from(group_id), Value::from(env)],
                )),
                DbTransactionStep::Execute(DbStatement::with_params(
                    "DELETE FROM bcs_groups WHERE group_id = ? AND env = ?",
                    vec![Value::from(group_id), Value::from(env)],
                )),
            ])
            .await
            .map_err(|e| {
                warn!(group_id = %group_id, error = %e, "Failed to delete group transaction");
                ServiceError::InternalError(format!("Failed to delete group: {e}"))
            })?;
        Ok(matches!(
            results.get(1),
            Some(DbTransactionStepResult::Executed(result)) if result.affected_rows > 0
        ))
    }

    /// Create or update a session.
    pub(crate) async fn upsert_sql(&self, group: Group) -> ServiceResult<()> {
        let group_id = group.id.clone();
        let env = self.env.clone();
        let _start = std::time::Instant::now();

        let status_str = Self::status_to_str(&group.status);
        let routing_policy_json: Option<String> = group
            .routing_policy
            .as_ref()
            .and_then(|rp| serde_json::to_string(rp).ok());
        let opening_message_json = group
            .opening_message
            .as_ref()
            .map(serde_json::to_string)
            .transpose()
            .map_err(|error| {
                ServiceError::InternalError(format!("serialize opening_message: {error}"))
            })?;
        // Task G.2 / migration 005: persist `group_kind` + `dm_pair_key`.
        // - `group_kind` is always written (defaults to "normal" via the
        //   in-memory enum default, but we still write the explicit value
        //   so DB column reflects intent).
        // - `dm_pair_key` is `NULL` for normal groups; for dm groups, the
        //   `(env, dm_pair_key)` unique index (migration 005) guards
        //   against concurrent duplicate creation.
        // - We DO NOT update `group_kind` / `dm_pair_key` on conflict —
        //   these are immutable per-group identity attributes set at
        //   creation; allowing UPDATE would let a normal group silently
        //   become a dm or change pair, breaking F.7 / G.5 invariants.
        let group_kind_str = Self::group_kind_to_str(group.group_kind);

        // Pre-extract all values from `group` so the closure captures only
        // owned data (no partial moves of `group`).
        let g_id = group.id.clone();
        let g_label: Option<String> = group.label.clone();
        let g_driver_bot = group.driver_bot.clone();
        let g_originator: Option<String> = group.originator.clone();
        let g_context: Option<String> = group.context.clone();
        let g_opening_message_json = opening_message_json;
        let g_dm_pair_key: Option<String> = group.dm_pair_key.clone();
        let g_group_strategy_str = Self::group_strategy_to_str(group.group_strategy);
        let g_service_group_uuid: Option<String> = group.service_group_uuid.clone();
        let g_service_mode: Option<String> = group.service_mode.clone();
        let g_service_spec_json: Option<String> = match group.service_spec {
            Some(ref spec) => Some(
                serde_json::to_string(spec)
                    .map_err(|e| ServiceError::InternalError(format!("service_spec: {e}")))?,
            ),
            None => None,
        };
        let g_version: i64 = group.version as i64;
        let g_record_status = group.record_status.clone();
        // Build participant tuples: (bot_uuid, role_str, actor_kind_str, mode_str, tags_json, scope)
        let g_participants: Vec<(String, &'static str, &'static str, &'static str, String, &'static str)> = group
            .participants
            .iter()
            .map(|p| {
                Ok((
                    p.bot_uuid.clone(),
                    Self::role_to_str(&p.role),
                    Self::actor_kind_to_str(p.actor_kind),
                    Self::mode_to_str(p.effective_mode()),
                    serde_json::to_string(&p.tags).map_err(|error| {
                        ServiceError::InternalError(format!("participant tags: {error}"))
                    })?,
                    Self::message_view_scope_to_str(p.message_view_scope),
                ))
            })
            .collect::<ServiceResult<Vec<_>>>()?;

        let g_visibility = group.visibility.clone();
        let g_notify_mode_str = Self::human_mention_notify_mode_to_str(group.human_mention_notify_mode);

        let upsert_clause = self.flavor.on_conflict_update(
            &["group_id", "env"],
            &[
                "label",
                "status",
                "driver_bot",
                "originator",
                "routing_policy_json",
                "context",
                "opening_message_json",
                "service_spec",
                "version",
                "record_status",
                "visibility",
                "human_mention_notify_mode",
            ],
            &[("gmt_modified", self.flavor.now())],
        );
        let upsert_sql = format!(
            "INSERT INTO bcs_groups (group_id, label, status, driver_bot, originator, env, routing_policy_json, context, opening_message_json, group_kind, dm_pair_key, group_strategy, service_group_uuid, service_mode, service_spec, version, record_status, visibility, human_mention_notify_mode, gmt_create, gmt_modified) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, {now}, {now}) \
             {upsert}",
            now = self.flavor.now(),
            upsert = upsert_clause,
        );

        let mut steps = Vec::with_capacity(2 + g_participants.len());
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            // 1. Upsert session metadata
            &upsert_sql,
            vec![
                Value::from(g_id.as_str()),
                Value::from(g_label.as_deref()),
                Value::from(status_str),
                Value::from(g_driver_bot.as_str()),
                Value::from(g_originator.as_deref()),
                Value::from(env.as_str()),
                Value::from(routing_policy_json.as_deref()),
                Value::from(g_context.as_deref()),
                Value::from(g_opening_message_json.as_deref()),
                Value::from(group_kind_str),
                Value::from(g_dm_pair_key.as_deref()),
                Value::from(g_group_strategy_str),
                Value::from(g_service_group_uuid.as_deref()),
                Value::from(g_service_mode.as_deref()),
                Value::from(g_service_spec_json.as_deref()),
                Value::from(g_version),
                Value::from(g_record_status.as_str()),
                Value::from(g_visibility.as_str()),
                Value::from(g_notify_mode_str),
            ],
        )));

        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            // 2. Delete existing participants
            "DELETE FROM bcs_group_participants WHERE group_id = ? AND env = ?",
            vec![Value::from(g_id.as_str()), Value::from(env.as_str())],
        )));

        // 3. Insert new participants.
        // Always populate actor_kind + mode explicitly per Requirement 3.10#2 / 3.18#6.
        for (bot_uuid, role_str, actor_kind_str, mode_str, tags_json, message_view_scope) in &g_participants {
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(
                    "INSERT INTO bcs_group_participants (group_id, bot_uuid, role, env, actor_kind, mode, tags_json, message_view_scope) \
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    vec![
                        Value::from(g_id.as_str()),
                        Value::from(bot_uuid.as_str()),
                        Value::from(*role_str),
                        Value::from(env.as_str()),
                        Value::from(*actor_kind_str),
                        Value::from(*mode_str),
                        Value::from(tags_json.as_str()),
                        Value::from(*message_view_scope),
                    ],
            )));
        }

        self.db.plugin().transaction(steps).await.map_err(|e| {
            warn!(group_id = %group_id, error = %e, "upsert transaction failed");
            ServiceError::InternalError(e.to_string())
        })?;

        let elapsed = _start.elapsed();
        if elapsed.as_millis() > 100 {
            warn!(group_id = %group_id, duration_ms = %elapsed.as_millis(), "slow upsert");
        } else {
            info!(group_id = %group_id, duration_ms = %elapsed.as_millis(), "upsert");
        }
        // Update cache
        {
            let mut cache = self.cache.write().await;
            cache.insert(group_id, group);
        }
        Ok(())
    }

    pub(crate) async fn patch_mutable_fields_sql(
        &self,
        id: &str,
        patch: GroupMutableFieldsPatch,
    ) -> ServiceResult<()> {
        let routing_policy_snapshot = match patch.default_bot_final_delivery {
            Some(delivery) => Some((
                delivery,
                self.load_raw_routing_policy_json(id)
                    .await?
                    .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?,
            )),
            None => {
                self.try_get(id)
                    .await?
                    .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
                None
            }
        };

        let mut assignments = Vec::new();
        let mut params = Vec::new();
        if let Some(label) = patch.label {
            assignments.push("label = ?".to_string());
            params.push(Value::from(label.as_str()));
        }
        if let Some(context) = patch.context {
            assignments.push("context = ?".to_string());
            params.push(Value::from(context.as_str()));
        }
        if let Some(opening_message) = patch.opening_message {
            let json = opening_message
                .as_ref()
                .map(serde_json::to_string)
                .transpose()
                .map_err(|error| {
                    ServiceError::InternalError(format!("serialize opening_message: {error}"))
                })?;
            assignments.push("opening_message_json = ?".to_string());
            params.push(Value::from(json.as_deref()));
        }
        if let Some(visibility) = patch.visibility {
            assignments.push("visibility = ?".to_string());
            params.push(Value::from(visibility.as_str()));
        }
        if let Some(mode) = patch.human_mention_notify_mode {
            assignments.push("human_mention_notify_mode = ?".to_string());
            params.push(Value::from(Self::human_mention_notify_mode_to_str(mode)));
        }
        let mut guard_routing_policy = false;
        if let Some((delivery, stored_json)) = &routing_policy_snapshot {
            let policy_json = patch_stored_routing_policy_json(stored_json.as_deref(), *delivery)?;
            if stored_json.as_deref() != Some(policy_json.as_str()) {
                assignments.push("routing_policy_json = ?".to_string());
                params.push(Value::from(policy_json));
                guard_routing_policy = true;
            }
        }
        if assignments.is_empty() {
            if routing_policy_snapshot.is_some() {
                self.cache.write().await.remove(id);
            }
            return Ok(());
        }
        assignments.push(self.flavor.set_modified_now().to_string());
        params.push(Value::from(id));
        params.push(Value::from(self.env.as_str()));
        let mut sql = format!(
            "UPDATE bcs_groups SET {} WHERE group_id = ? AND env = ?",
            assignments.join(", ")
        );
        if guard_routing_policy {
            sql.push_str(match self.flavor {
                DbSqlFlavor::Mysql => " AND routing_policy_json <=> ?",
                DbSqlFlavor::Sqlite => " AND routing_policy_json IS ?",
            });
            let stored_json = routing_policy_snapshot
                .as_ref()
                .and_then(|(_, stored_json)| stored_json.as_deref());
            params.push(Value::from(stored_json));
        }
        let affected_rows = self
            .db
            .execute_with(&self.logical_db, &sql, params)
            .await
            .map_err(|error| {
                warn!(group_id = %id, error = %error, "Failed to patch mutable group fields");
                ServiceError::InternalError(error.to_string())
            })?;

        // Invalidate instead of reconstructing the row so hidden routing fields
        // changed concurrently are always reloaded from the authoritative store.
        self.cache.write().await.remove(id);
        if affected_rows == 0 && self.try_get(id).await?.is_none() {
            return Err(ServiceError::GroupNotFound(id.to_string()));
        }
        if affected_rows == 0 && guard_routing_policy {
            return Err(ServiceError::Conflict(format!(
                "Group '{id}' routing policy changed concurrently"
            )));
        }
        Ok(())
    }

    /// Delete a session.
    pub(crate) async fn delete_sql(&self, id: &str) -> ServiceResult<Option<Group>> {
        // Capture a fallible rollback snapshot before deleting. Proceeding
        // after a failed read can delete the persistent Group while returning
        // `None`, which prevents callers from running committed-delete cleanup.
        let group = self.try_get(id).await?;

        // Delete from MySQL
        let deleted = self.delete_group_from_mysql(id).await?;

        // Remove from cache
        self.cache.write().await.remove(id);
        if !deleted {
            return Ok(None);
        }

        debug!(group_id = %id, "Group deleted");
        Ok(group)
    }

    /// Add a participant to a session.
    pub(crate) async fn add_participant_sql(&self, id: &str, participant: Participant) -> ServiceResult<()> {
        // Verify group exists
        if self.get(id).await.is_none() {
            return Err(ServiceError::GroupNotFound(id.to_string()));
        }

        // Check if already exists
        let check_sql =
            "SELECT 1 FROM bcs_group_participants WHERE group_id = ? AND bot_uuid = ? AND env = ?";

        let rows = self
            .db
            .query_with(
                &self.logical_db,
                check_sql,
                vec![
                    Value::from(id),
                    Value::from(participant.bot_uuid.as_str()),
                    Value::from(self.env.as_str()),
                ],
            )
            .await
            .map_err(|e| {
                ServiceError::InternalError(format!("Failed to check participant existence: {}", e))
            })?;

        if !rows.is_empty() {
            return Ok(()); // Already a participant, no-op
        }

        // Add to MySQL. Always populate actor_kind + mode explicitly
        // per Requirement 3.10#2 / 3.18#6.
        let role_str = Self::role_to_str(&participant.role);
        let actor_kind_str = Self::actor_kind_to_str(participant.actor_kind);
        let mode_str = Self::mode_to_str(participant.effective_mode());
        let tags_json = serde_json::to_string(&participant.tags).map_err(|error| {
            ServiceError::InternalError(format!("participant tags: {error}"))
        })?;
        self.db.execute_with(
            &self.logical_db,
            "INSERT INTO bcs_group_participants (group_id, bot_uuid, role, env, actor_kind, mode, tags_json, message_view_scope) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            vec![
                Value::from(id),
                Value::from(participant.bot_uuid.as_str()),
                Value::from(role_str),
                Value::from(self.env.as_str()),
                Value::from(actor_kind_str),
                Value::from(mode_str),
                Value::from(tags_json.as_str()),
                Value::from(Self::message_view_scope_to_str(participant.message_view_scope)),
            ],
        ).await
            .map_err(|e| {
                warn!(group_id = %id, bot_uuid = %participant.bot_uuid, error = %e, "Failed to add participant to MySQL");
                ServiceError::InternalError(e.to_string())
            })?;

        debug!(group_id = %id, bot_uuid = %participant.bot_uuid, "Participant added to group");
        // Invalidate cache
        self.cache.write().await.remove(id);
        Ok(())
    }

    pub(crate) async fn add_participant_with_visibility_guard_sql(
        &self,
        id: &str,
        participant: Participant,
        actor_is_public: bool,
    ) -> ServiceResult<()> {
        let role = Self::role_to_str(&participant.role);
        let actor_kind = Self::actor_kind_to_str(participant.actor_kind);
        let mode = Self::mode_to_str(participant.effective_mode());
        let message_view_scope = Self::message_view_scope_to_str(participant.message_view_scope);
        let update_group = format!(
            "UPDATE bcs_groups \
             SET {} \
             WHERE group_id = ? AND env = ? AND (visibility <> 'public' OR ?) \
               AND NOT EXISTS ( \
                 SELECT 1 FROM bcs_group_participants \
                 WHERE group_id = ? AND bot_uuid = ? AND env = ? \
               )",
            self.flavor.set_modified_now()
        );
        let insert_participant = format!(
            "{} INTO bcs_group_participants \
             (group_id, bot_uuid, role, env, actor_kind, mode, message_view_scope) \
             SELECT ?, ?, ?, ?, ?, ?, ? \
             FROM bcs_groups \
             WHERE group_id = ? AND env = ? AND (visibility <> 'public' OR ?)",
            self.flavor.insert_or_ignore()
        );
        let results = self
            .db
            .plugin()
            .transaction(vec![
                DbTransactionStep::Execute(DbStatement::with_params(
                    update_group,
                    vec![
                        Value::from(id),
                        Value::from(self.env.as_str()),
                        Value::from(actor_is_public),
                        Value::from(id),
                        Value::from(participant.bot_uuid.as_str()),
                        Value::from(self.env.as_str()),
                    ],
                )),
                DbTransactionStep::Execute(DbStatement::with_params(
                    insert_participant,
                    vec![
                        Value::from(id),
                        Value::from(participant.bot_uuid.as_str()),
                        Value::from(role),
                        Value::from(self.env.as_str()),
                        Value::from(actor_kind),
                        Value::from(mode),
                        Value::from(message_view_scope),
                        Value::from(id),
                        Value::from(self.env.as_str()),
                        Value::from(actor_is_public),
                    ],
                )),
            ])
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!(
                    "visibility-guarded participant insert failed: {error}"
                ))
            })?;
        let group_updated = matches!(
            results.first(),
            Some(DbTransactionStepResult::Executed(result)) if result.affected_rows > 0
        );
        self.cache.write().await.remove(id);
        if group_updated {
            return Ok(());
        }

        let group = self
            .try_get(id)
            .await?
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
        if group
            .participants
            .iter()
            .any(|existing| existing.bot_uuid == participant.bot_uuid)
        {
            return Ok(());
        }
        if group.visibility == "public" && !actor_is_public {
            return Err(ServiceError::ExistNonPublicBots {
                bots: vec![(participant.bot_uuid, participant.bot_name)],
            });
        }
        Err(ServiceError::InternalError(format!(
            "visibility-guarded participant insert made no progress for Group '{id}'"
        )))
    }

    pub(crate) async fn remove_participant_sql(&self, group_id: &str, bot_uuid: &str) -> ServiceResult<()> {
        // Verify group exists
        if self.get(group_id).await.is_none() {
            return Err(ServiceError::GroupNotFound(group_id.to_string()));
        }

        let delete_sql =
            "DELETE FROM bcs_group_participants WHERE group_id = ? AND bot_uuid = ? AND env = ?";

        let affected = self
            .db
            .execute_with(
                &self.logical_db,
                delete_sql,
                vec![
                    Value::from(group_id),
                    Value::from(bot_uuid),
                    Value::from(self.env.as_str()),
                ],
            )
            .await
            .map_err(|e| {
                warn!(group_id = %group_id, bot_uuid = %bot_uuid, error = %e, "Failed to remove participant from MySQL");
                ServiceError::InternalError(e.to_string())
            })?;

        if affected == 0 {
            return Err(ServiceError::ParticipantNotFound(bot_uuid.to_string()));
        }

        debug!(group_id = %group_id, bot_uuid = %bot_uuid, "Participant removed from group");
        // Invalidate cache
        self.cache.write().await.remove(group_id);
        Ok(())
    }

    /// Update an existing participant's `mode` (Human Actor V1, Task P.1).
    pub(crate) async fn update_participant_mode_sql(
        &self,
        id: &str,
        actor_id: &str,
        mode: ParticipantMode,
    ) -> ServiceResult<()> {
        // Verify group exists.
        let group = self
            .get(id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;

        // Verify participant exists and capture current mode for idempotency check.
        let current_mode = group
            .participants
            .iter()
            .find(|p| p.bot_uuid == actor_id)
            .map(|p| p.effective_mode())
            .ok_or_else(|| ServiceError::BotNotFound(actor_id.to_string()))?;

        if current_mode == mode {
            debug!(group_id = %id, actor_id = %actor_id, ?mode, "Participant mode unchanged, skipping DB write");
            return Ok(());
        }

        let mode_str = Self::mode_to_str(mode);
        self.db.execute_with(
            &self.logical_db,
            "UPDATE bcs_group_participants SET mode = ? \
             WHERE group_id = ? AND bot_uuid = ? AND env = ?",
            vec![
                Value::from(mode_str),
                Value::from(id),
                Value::from(actor_id),
                Value::from(self.env.as_str()),
            ],
        ).await
            .map_err(|e| {
                warn!(group_id = %id, actor_id = %actor_id, error = %e, "Failed to update participant mode");
                ServiceError::InternalError(e.to_string())
            })?;

        debug!(group_id = %id, actor_id = %actor_id, ?mode, "Participant mode updated");
        // Invalidate cache so the next get() reloads with the new mode.
        self.cache.write().await.remove(id);
        Ok(())
    }

    pub(crate) async fn update_participant_message_view_scope_sql(
        &self,
        id: &str,
        actor_id: &str,
        message_view_scope: MessageViewScope,
    ) -> ServiceResult<()> {
        let group = self
            .get(id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
        let participant = group
            .participants
            .iter()
            .find(|participant| participant.bot_uuid == actor_id)
            .ok_or_else(|| ServiceError::ParticipantNotFound(actor_id.to_string()))?;
        if !message_view_scope.is_valid_for(participant.actor_kind) {
            return Err(ServiceError::InvalidOperation {
                message: "Bot participants must use full message_view_scope".to_string(),
                request_id: None,
            });
        }
        if participant.message_view_scope == message_view_scope {
            return Ok(());
        }
        self.db
            .execute_with(
                &self.logical_db,
                "UPDATE bcs_group_participants SET message_view_scope = ? \
                 WHERE group_id = ? AND bot_uuid = ? AND env = ?",
                vec![
                    Value::from(Self::message_view_scope_to_str(message_view_scope)),
                    Value::from(id),
                    Value::from(actor_id),
                    Value::from(self.env.as_str()),
                ],
            )
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;
        self.cache.write().await.remove(id);
        Ok(())
    }

    /// Update workspace - NOT PERSISTED (memory only, lost on restart).
    pub(crate) async fn update_workspace_sql(&self, id: &str, workspace: Workspace) -> ServiceResult<()> {
        let mut group = self
            .get(id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
        group.workspace = workspace;
        self.cache.write().await.insert(id.to_string(), group);

        debug!(group_id = %id, "Group workspace updated in memory (not persisted)");
        Ok(())
    }

    /// Update session label.
    pub(crate) async fn update_label_sql(&self, id: &str, label: Option<String>) -> ServiceResult<()> {
        // Verify group exists
        if self.get(id).await.is_none() {
            return Err(ServiceError::GroupNotFound(id.to_string()));
        }

        // Persist to MySQL using parameter binding
        self.db
            .execute_with(
                &self.logical_db,
                "UPDATE bcs_groups SET label = ? WHERE group_id = ? AND env = ?",
                vec![
                    Value::from(label.as_deref()),
                    Value::from(id),
                    Value::from(self.env.as_str()),
                ],
            )
            .await
            .map_err(|e| {
                warn!(group_id = %id, error = %e, "Failed to update group label");
                ServiceError::InternalError(e.to_string())
            })?;

        debug!(group_id = %id, "Group label updated");
        // Update cache
        {
            let mut cache = self.cache.write().await;
            if let Some(group) = cache.get_mut(id) {
                group.label = label;
            }
        }
        Ok(())
    }

    /// Update session status.
    pub(crate) async fn update_status_sql(&self, id: &str, status: GroupStatus) -> ServiceResult<()> {
        // Verify group exists
        if self.get(id).await.is_none() {
            return Err(ServiceError::GroupNotFound(id.to_string()));
        }

        // Persist to MySQL using parameter binding
        let status_str = Self::status_to_str(&status);
        self.db.execute_with(
            &self.logical_db,
            "UPDATE bcs_groups SET status = ? WHERE group_id = ? AND env = ?",
            vec![
                Value::from(status_str),
                Value::from(id),
                Value::from(self.env.as_str()),
            ],
        ).await
            .map_err(|e| {
                warn!(group_id = %id, status = ?status, error = %e, "Failed to update group status");
                ServiceError::InternalError(e.to_string())
            })?;

        debug!(group_id = %id, status = ?status, "Group status updated");
        // Update cache
        {
            let mut cache = self.cache.write().await;
            if let Some(group) = cache.get_mut(id) {
                group.status = status;
            }
        }
        Ok(())
    }

    /// Persist a `service_spec` patch to MySQL. `Some(spec)` writes a JSON
    /// blob into the `service_spec` column; `None` clears the column. Caller
    /// is responsible for validation (route-field lock, callback_config
    /// immutability) — this method only writes.
    pub(crate) async fn update_service_spec_sql(
        &self,
        id: &str,
        service_spec: Option<bcs_service_api::ServiceSpec>,
    ) -> ServiceResult<()> {
        // Verify group exists
        if self.get(id).await.is_none() {
            return Err(ServiceError::GroupNotFound(id.to_string()));
        }

        let spec_json = match service_spec.as_ref() {
            Some(s) => {
                serde_json::to_string(s).map_err(|e| ServiceError::InternalError(e.to_string()))?
            }
            None => String::new(),
        };
        let spec_value: Value = if service_spec.is_some() {
            Value::from(spec_json.as_str())
        } else {
            Value::Null
        };

        self.db
            .execute_with(
                &self.logical_db,
                "UPDATE bcs_groups SET service_spec = ? WHERE group_id = ? AND env = ?",
                vec![spec_value, Value::from(id), Value::from(self.env.as_str())],
            )
            .await
            .map_err(|e| {
                warn!(group_id = %id, error = %e, "Failed to update group service_spec");
                ServiceError::InternalError(e.to_string())
            })?;

        debug!(group_id = %id, "Group service_spec updated");
        {
            let mut cache = self.cache.write().await;
            if let Some(group) = cache.get_mut(id) {
                group.service_spec = service_spec;
            }
        }
        Ok(())
    }

    pub(crate) async fn update_visibility_sql(&self, id: &str, visibility: &str) -> ServiceResult<()> {
        // Verify group exists
        if self.get(id).await.is_none() {
            return Err(ServiceError::GroupNotFound(id.to_string()));
        }

        let update_sql = format!(
            "UPDATE bcs_groups SET visibility = ?, {} WHERE group_id = ? AND env = ?",
            self.flavor.set_modified_now(),
        );
        self.db
            .execute_with(
                &self.logical_db,
                &update_sql,
                vec![
                    Value::from(visibility),
                    Value::from(id),
                    Value::from(self.env.as_str()),
                ],
            )
            .await
            .map_err(|e| {
                warn!(group_id = %id, error = %e, "Failed to update group visibility");
                ServiceError::InternalError(e.to_string())
            })?;

        debug!(group_id = %id, visibility = %visibility, "Group visibility updated");
        // Update cache
        {
            let mut cache = self.cache.write().await;
            if let Some(group) = cache.get_mut(id) {
                group.visibility = visibility.to_string();
            }
        }
        Ok(())
    }
}
