//! Direct-message Group lookup and race-safe creation for the MySQL-backed Group store.

use super::*;

impl MySqlGroupStore {

    /// Task G.2: precise indexed lookup for dm groups by canonical pair key.
    ///
    /// Backed by the `(env, dm_pair_key)` UNIQUE index on `bcs_groups`
    /// (migration 005). This overrides the default trait impl which would
    /// otherwise scan `list()` — a non-starter at production scale.
    ///
    /// Returns `None` on:
    /// - no row matching the key in this env
    /// - row exists but participants fail to load (we don't want to surface a
    ///   half-loaded group to the caller; logged via `warn!` upstream)
    pub(crate) async fn find_dm_by_pair_key_sql(&self, dm_pair_key: &str) -> Option<Group> {
        // Hit cache first to avoid repeated DB round-trips for hot dm pairs.
        // Cache key is `group_id`, not `dm_pair_key`, so we still need the
        // initial DB lookup to translate pair_key → group_id; afterwards
        // `load_group_from_mysql` (which `get` uses) benefits from the cache.
        let sql = "SELECT group_id FROM bcs_groups \
                   WHERE env = ? AND group_kind = 'dm' AND dm_pair_key = ? LIMIT 1";

        let rows = match self
            .db
            .query_with(
                &self.logical_db,
                sql,
                vec![Value::from(self.env.as_str()), Value::from(dm_pair_key)],
            )
            .await
        {
            Ok(r) => r,
            Err(e) => {
                warn!(
                    dm_pair_key = %dm_pair_key,
                    env = %self.env,
                    error = %e,
                    "find_dm_by_pair_key: query failed"
                );
                return None;
            }
        };

        let row = rows.first()?;
        let group_id: String = db_get_column(row, "group_id").ok()?;
        // Reuse the standard load path (cache + participants normalization).
        self.get(&group_id).await
    }

    /// Try to insert a DM group without mutating an existing row with the same pair key.
    ///
    /// Returns `true` only when this call created the canonical group row.
    /// Returns `false` when the `(env, dm_pair_key)` unique index already has
    /// a winner; the caller owns refetching that row via `find_dm_by_pair_key`.
    ///
    /// Participant inserts are guarded by the newly inserted group row. This
    /// matters because `bcs_group_participants` has no FK to `bcs_groups`: a
    /// loser in a DM pair-key race must not create participant rows for its
    /// caller-supplied, non-canonical `group_id`.
    pub(crate) async fn insert_dm_group_if_absent_sql(&self, group: Group) -> ServiceResult<bool> {
        let pair_key = group.dm_pair_key.clone().ok_or_else(|| {
            ServiceError::InternalError(
                "insert_dm_group_if_absent requires group.dm_pair_key".to_string(),
            )
        })?;

        // ------------------------------------------------------------------
        // Step 1: Pre-flight find. The unique index makes this fast.
        //         If hit -> report no insert, NEVER mutate.
        // ------------------------------------------------------------------
        if self.find_dm_by_pair_key(&pair_key).await.is_some() {
            debug!(
                pair_key = %pair_key,
                requested_id = %group.id,
                "insert_dm_group_if_absent: reuse via pre-flight pair_key lookup"
            );
            return Ok(false);
        }

        // ------------------------------------------------------------------
        // Step 2: Race-safe create via the database plugin transaction API.
        //
        // The first statement reports whether the INSERT happened (we won the
        // race) or the unique key already existed (affected_rows == 0).
        // Participant inserts are idempotent for the winner and guarded for
        // the loser: when the caller's `group_id` is not the canonical row,
        // the INSERT ... SELECT matches zero rows and writes no participants.
        // ------------------------------------------------------------------
        let group_kind_str = Self::group_kind_to_str(group.group_kind);
        let status_str = Self::status_to_str(&group.status);
        let env = self.env.clone();

        // Pre-extract values so the closure captures only owned data and
        // `group` remains available after the closure for cache/logging.
        let g_id = group.id.clone();
        let g_label = group.label.clone();
        let g_driver_bot = group.driver_bot.clone();
        let g_originator: Option<String> = group.originator.clone();
        let g_context = group.context.clone();
        let g_dm_pair_key = group.dm_pair_key.clone();
        let g_group_strategy_str = Self::group_strategy_to_str(group.group_strategy);
        let g_notify_mode_str = Self::human_mention_notify_mode_to_str(group.human_mention_notify_mode);
        // Build participant tuples: (bot_uuid, role_str, actor_kind_str, mode_str, scope)
        let g_participants: Vec<(String, &'static str, &'static str, &'static str, &'static str)> = group
            .participants
            .iter()
            .map(|p| {
                (
                    p.bot_uuid.clone(),
                    Self::role_to_str(&p.role),
                    Self::actor_kind_to_str(p.actor_kind),
                    Self::mode_to_str(p.effective_mode()),
                    Self::message_view_scope_to_str(p.message_view_scope),
                )
            })
            .collect();

        let on_conflict_nothing = self.flavor.on_conflict_nothing(&["group_id", "env"]);
        let dm_insert_sql = format!(
            "INSERT INTO bcs_groups \
                     (group_id, label, status, driver_bot, originator, env, \
                      routing_policy_json, context, group_kind, dm_pair_key, group_strategy, \
                      human_mention_notify_mode, gmt_create, gmt_modified) \
                 VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, {now}, {now}) \
                 {on_conflict}",
            now = self.flavor.now(),
            on_conflict = on_conflict_nothing,
        );

        let mut steps = Vec::with_capacity(1 + g_participants.len());
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            // 3.1 Race-safe insert into `bcs_groups`.
            //
            // The on-conflict-nothing clause converts the unique-index
            // violation into a clean 0-affected-rows signal. The first
            // transaction result decides whether this caller created the row.
            &dm_insert_sql,
            vec![
                Value::from(g_id.as_str()),
                Value::from(g_label.as_deref()),
                Value::from(status_str),
                Value::from(g_driver_bot.as_str()),
                Value::from(g_originator.as_deref()),
                Value::from(env.as_str()),
                Value::from(g_context.as_deref()),
                Value::from(group_kind_str),
                Value::from(g_dm_pair_key.as_deref()),
                Value::from(g_group_strategy_str),
                Value::from(g_notify_mode_str),
            ],
        )));

        // 3.2 Insert the two Bot participants in the same transaction, but
        // only if this caller's group row exists with the requested pair key.
        let insert_ignore_prefix = self.flavor.insert_or_ignore();
        let participant_insert_sql = format!(
            "{} INTO bcs_group_participants \
                         (group_id, bot_uuid, role, env, actor_kind, mode, message_view_scope) \
                     SELECT ?, ?, ?, ?, ?, ?, ? \
                     FROM bcs_groups \
                     WHERE group_id = ? AND env = ? AND dm_pair_key = ?",
            insert_ignore_prefix,
        );
        for (bot_uuid, role_str, actor_kind_str, mode_str, message_view_scope) in &g_participants {
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(
                &participant_insert_sql,
                vec![
                    Value::from(g_id.as_str()),
                    Value::from(bot_uuid.as_str()),
                    Value::from(*role_str),
                    Value::from(env.as_str()),
                    Value::from(*actor_kind_str),
                    Value::from(*mode_str),
                    Value::from(*message_view_scope),
                    Value::from(g_id.as_str()),
                    Value::from(env.as_str()),
                    Value::from(pair_key.as_str()),
                ],
            )));
        }

        let tx_result = self.db.plugin().transaction(steps).await;

        match tx_result {
            Ok(results) => {
                let group_insert_affected_rows = match results.first() {
                    Some(DbTransactionStepResult::Executed(result)) => result.affected_rows,
                    _ => {
                        return Err(ServiceError::InternalError(
                            "insert_dm_group_if_absent: transaction did not return insert result"
                                .to_string(),
                        ));
                    }
                };
                let mut participant_inserted_rows = 0;
                for result in results.iter().skip(1) {
                    match result {
                        DbTransactionStepResult::Executed(result) => {
                            participant_inserted_rows += result.affected_rows;
                        }
                        DbTransactionStepResult::Rows(_) => {
                            return Err(ServiceError::InternalError(
                                "insert_dm_group_if_absent: transaction returned query rows for participant insert"
                                    .to_string(),
                            ));
                        }
                    }
                }

                let expected_participant_rows = g_participants.len() as u64;
                if participant_inserted_rows > 0
                    && participant_inserted_rows != expected_participant_rows
                {
                    return Err(ServiceError::InternalError(format!(
                        "insert_dm_group_if_absent: inserted {} participant rows, expected {}",
                        participant_inserted_rows, expected_participant_rows
                    )));
                }

                let created = group_insert_affected_rows == 1
                    && participant_inserted_rows == expected_participant_rows;

                if created {
                    info!(
                        group_id = %group.id,
                        pair_key = %pair_key,
                        driver_bot = %group.driver_bot,
                        "insert_dm_group_if_absent: created new dm group"
                    );

                    // Populate the cache so the next `get(group_id)` skips a roundtrip.
                    {
                        let mut cache = self.cache.write().await;
                        cache.insert(group.id.clone(), group.clone());
                    }

                    return Ok(true);
                }

                // Lost the race — the no-op upsert committed without changing
                // group business columns.
                warn!(
                    pair_key = %pair_key,
                    requested_id = %group.id,
                    "insert_dm_group_if_absent: lost race on dm_pair_key unique index"
                );
                Ok(false)
            }
            Err(e) => {
                if e.is_duplicate_key() {
                    warn!(
                        pair_key = %pair_key,
                        requested_id = %group.id,
                        error = %e,
                        "insert_dm_group_if_absent: lost race on unique key"
                    );
                    return Ok(false);
                }
                // Genuine transaction failure.
                warn!(
                    pair_key = %pair_key,
                    requested_id = %group.id,
                    error = %e,
                    "insert_dm_group_if_absent: transaction failed"
                );
                Err(ServiceError::InternalError(e.to_string()))
            }
        }
    }
}
