//! SQL Bot membership writes and gateway compatibility projection share a transaction.
use super::*;
use bcs_db_api::{DbTransactionParam, DbTransactionStep};
use bcs_service_api::bot_provider::{BotConnectionMode, BotProviderRecord};
use bcs_service_api::core::provider::{ProviderRetire, ProviderRetireBranch, ProviderRetirement};
use bcs_service_api::port::repo::bot_provider::BotProviderRepoPort;

use bcs_edge_permission_store::lifecycle;

use crate::retirement::{retire_failed, retire_uncertain};

/// Column/decode failures of the pre-read never expose SQL;
/// the public error stays generic.
fn decode_error<T>(_: DbError) -> ServiceResult<T> {
    Err(retire_failed())
}

/// Source of the retirement: which durable row routed
/// the command. Used only for the local re-verification.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum RetireSource {
    /// Bot metadata row carries this provider/ref.
    Metadata,
    /// A compatibility binding carries this provider/ref;
    /// the Bot has no metadata.
    BindingFallback,
    /// No metadata and no binding.
    LegacyUnbound,
}

impl RetireSource {
    fn branch(self) -> ProviderRetireBranch {
        match self {
            RetireSource::Metadata => ProviderRetireBranch::Metadata,
            RetireSource::BindingFallback => ProviderRetireBranch::BindingFallback,
            RetireSource::LegacyUnbound => ProviderRetireBranch::LegacyUnbound,
        }
    }
}

/// The derived retirement branch tells the caller which
/// store state matched; mismatched expectations are
/// errors, never silent reinterpretations (spec §7.1).
fn retirement_branch_mismatch(expected: ProviderRetireBranch) -> ServiceError {
    ServiceError::Conflict(format!(
        "retirement branch does not match the stored identity \
         state (expected {expected:?})"
    ))
}

pub(super) fn validate_record(record: &BotProviderRecord) -> ServiceResult<()> {
    if record.bot_uuid.trim().is_empty() || record.provider_id.trim().is_empty()
        || record.provider_bot_ref.trim().is_empty() || record.is_deleted
        || (record.connection_mode == BotConnectionMode::Plugin && record.webhook_url.is_some())
    {
        return Err(ServiceError::InvalidOperation { message: "invalid Bot Provider metadata".into(), request_id: None });
    }
    Ok(())
}

pub(super) fn storage_error(error: DbError) -> ServiceError {
    if error.is_duplicate_key() || matches!(error, DbError::ConditionFailed { .. }) {
        ServiceError::Conflict("Bot or Provider/ref is already registered or changed".into())
    } else {
        // Driver SQL can include the newly generated runtime credential.
        ServiceError::InternalError("Bot Provider persistence failed".into())
    }
}

fn decode(row: &DbRow) -> ServiceResult<BotProviderRecord> {
    let read = || -> Result<BotProviderRecord, DbError> {
        let mode: String = bcs_db_api::db_get_column(row, "connection_mode")?;
        let mode = mode.parse::<BotConnectionMode>().map_err(|message| DbError::Conversion(message.into()))?;
        Ok(BotProviderRecord {
            bot_uuid: bcs_db_api::db_get_column(row, "bot_uuid")?,
            provider_id: bcs_db_api::db_get_column(row, "provider_id")?,
            provider_bot_ref: bcs_db_api::db_get_column(row, "provider_bot_ref")?,
            connection_mode: mode, webhook_url: row.get_string("webhook_url")?,
            is_deleted: bcs_db_api::db_get_column(row, "is_deleted")?,
        })
    };
    read().map_err(|_| ServiceError::InternalError("invalid or unmigrated Bot Provider metadata".into()))
}

impl DbProviderStore {
    fn lock_gateway_binding(&self, record: &BotProviderRecord, env: &str) -> DbTransactionStep {
        let lock = if self.flavor == DbSqlFlavor::Mysql { " FOR UPDATE" } else { "" };
        DbTransactionStep::Query(DbStatement::with_params(format!(
            "SELECT bot_uuid FROM bcs_provider_bot_bindings WHERE bot_uuid = ? AND env = ? \
             AND provider_id = ? AND provider_bot_ref = ? AND disabled = 0{lock}"),
            vec![record.bot_uuid.as_str().into(), env.into(), record.provider_id.as_str().into(), record.provider_bot_ref.as_str().into()]))
    }

    /// Trusted Provider registration (bot-authority plan Task 4,
    /// spec §10.2): the Bot row (ownership_version = 1, owner as
    /// first created_by), its gateway projection when gateway-mode,
    /// the Human ACTOR row, the Human identity guard, the approved
    /// owner edge, the default runtime profile and the
    /// `initialize` audit commit in ONE transaction — the same
    /// pieces the Human registration composite writes. Duplicate
    /// Provider/ref (including tombstones) conflicts and writes
    /// nothing; a reused Bot ID never resets a committed owner.
    pub(crate) async fn create_provider_bot_composite(
        &self,
        record: BotProviderRecord,
        capabilities: BotCapabilities,
        owner: &str,
        token: &str,
        initialization: &bcs_service_api::types::bot_authority::OwnershipInitialization,
    ) -> ServiceResult<()> {
        validate_record(&record)?;
        if owner.trim().is_empty() || token.is_empty() {
            return Err(ServiceError::InvalidOperation { message: "Bot owner and runtime credential are required".into(), request_id: None });
        }
        if owner.trim() != initialization.owner_user_id {
            return Err(crate::ownership::initialization_invalid_public(
                "registration owner must be the verified registration owner",
            ));
        }
        let env = resolve_env();
        let info = serde_json::to_string(&capabilities)
            .map_err(|_| ServiceError::InternalError("Bot capability serialization failed".into()))?;
        let mode = record.connection_mode.as_str();
        let mut steps = vec![DbTransactionStep::ExecuteChecked {
            statement: DbStatement::with_params(
                "INSERT INTO bcs_bots (bot_uuid, env, name, bot_info, session_token, created_by, \
                 visibility, status, actor_kind, is_deleted, registered_at, updated_at, agent_code, \
                 provider_id, provider_bot_ref, connection_mode, webhook_url, ownership_version) \
                 SELECT ?, ?, ?, ?, ?, ?, ?, 'online', 'bot', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, 1 \
                 WHERE NOT EXISTS (SELECT 1 FROM bcs_provider_bot_bindings WHERE env = ? AND provider_id = ? AND provider_bot_ref = ?)",
                vec![record.bot_uuid.as_str().into(), env.as_str().into(), capabilities.name.as_deref().unwrap_or(&record.bot_uuid).into(),
                    info.into(), token.into(), owner.into(), capabilities.visibility.as_str().into(),
                    capabilities.agent_code.clone().into(),
                    record.provider_id.as_str().into(), record.provider_bot_ref.as_str().into(), mode.into(), record.webhook_url.clone().into(),
                    env.as_str().into(), record.provider_id.as_str().into(), record.provider_bot_ref.as_str().into()]),
            expected_affected_rows: 1,
        }];
        if record.connection_mode == BotConnectionMode::Gateway {
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(
                Self::insert_binding_sql(), vec![record.bot_uuid.as_str().into(), record.provider_id.as_str().into(), record.provider_bot_ref.as_str().into(),
                    env.as_str().into(), false.into(), record.webhook_url.clone().into()])));
        }
        // Ownership pieces share the same Bot-row-first lock order
        // (Human ACTOR row is a separate bcs_bots row after the
        // Bot row).
        let human_session_token = uuid::Uuid::new_v4().to_string();
        let ownership_steps = crate::ownership::ownership_authorization_steps(
            self.flavor,
            &env,
            &record.bot_uuid,
            initialization,
            &human_session_token,
            steps.len(),
        )?;
        steps.extend(ownership_steps.steps);
        steps.push(ownership_steps.final_guard);

        match self
            .db
            .transaction_with_options(
                steps,
                crate::ownership::transaction_options(),
            )
            .await
        {
            Ok(_) => {}
            Err(error) => match error {
                DbError::Timeout => {
                    return Err(ServiceError::InternalError(format!(
                        "{}: the trusted Provider registration transaction \
                         exceeded the deadline; per contract it rolled back \
                         and the caller must not auto-retry",
                        bcs_service_api::application::v1::bot_authority::
                            ERROR_BOT_TX_OUTCOME_UNCERTAIN
                    )));
                }
                other => return Err(storage_error(other)),
            },
        }
        // Post-commit cache publish (rebuildable object state only).
        self.bindings.invalidate(&format!("{env}:{}", record.bot_uuid));
        Ok(())
    }

    /// Atomic Provider retirement (bot-authority spec §7.1,
    /// plan §3.1 frozen contract): the Bot tombstone,
    /// the Provider/ref tombstone, the necessary binding
    /// disable, the owner/manager edge revocation, the
    /// valid-pending transfer closure (fixed system
    /// marker and reason) and the `retire` audit commit
    /// in ONE transaction — never followed by a second
    /// ordinary delete.
    ///
    /// Lock order: binding row → physical Bot row →
    /// transfer rows (spec §7). The expected branch is
    /// re-derived under the locks from the SAME durable
    /// rows the read/update paths use; a mismatch is an
    /// error. A repeated command on an already-tombstoned
    /// Bot is idempotent and returns `changed = false`
    /// with the identity still read from the retained
    /// tombstone/metadata/binding — no duplicate audit,
    /// nothing resurrected.
    pub(crate) async fn retire_with_authority_composite(
        &self,
        retire: ProviderRetire,
    ) -> ServiceResult<ProviderRetirement> {
        crate::ownership::validate_mutation(&retire.mutation)?;
        let env = resolve_env();
        let bot_id = retire.bot_id.as_str();
        // Pre-read is parameter preparation only: derive the
        // retirement branch from the durable rows and
        // refuse any mismatch with the expected branch.
        let pre = read_retire_rows(
            self.db.as_ref(),
            &env,
            bot_id,
            &retire,
        )
        .await?;
        let source = pre.source;
        if source.branch() != retire.expected_branch {
            return Err(retirement_branch_mismatch(
                retire.expected_branch,
            ));
        }
        for _ in 0..crate::retirement::RETIRE_MAX_RETRIES {
            // Pre-reads are parameter preparation only; a
            // deterministic rollback (e.g. the pending
            // count guard behind a concurrent decision)
            // starts the NEXT attempt from a FRESH
            // pre-read of the branch state AND the
            // pending set — no attempt fights a stale
            // snapshot.
            let pre = read_retire_rows(
                self.db.as_ref(),
                &env,
                bot_id,
                &retire,
            )
            .await?;
            if pre.source != source {
                return Err(retirement_branch_mismatch(
                    retire.expected_branch,
                ));
            }
            let pendings = crate::retirement::read_pending_transfers(
                self.db.as_ref(),
                &env,
                bot_id,
            )
            .await
            .map_err(crate::retirement::map_retire_tx_error)?;
            let changed = !pre.pre_tombstoned;
            let mut steps: Vec<DbTransactionStep> = Vec::new();
            let mut guard_indexes: Vec<(usize, &'static str)> =
                Vec::new();

            // 0: binding row lock (WHEN one exists — the
            // unified binding → Bot lock order even for a
            // Bot the metadata already routes). MySQL takes
            // a real row lock; SQLite is single-writer, so
            // the suffix carries no semantics there.
            let lock_suffix = match self.flavor {
                DbSqlFlavor::Mysql => " FOR UPDATE",
                DbSqlFlavor::Sqlite => "",
            };
            steps.push(DbTransactionStep::Query(
                DbStatement::with_transaction_params(
                    format!(
                        "SELECT 1 AS ok FROM bcs_provider_bot_bindings \
                         WHERE bot_uuid = ? AND env = ?{lock_suffix}"
                    )
                    .as_str(),
                    vec![
                        DbTransactionParam::value(DbValue::from(bot_id)),
                        DbTransactionParam::value(DbValue::from(env.as_str())),
                    ],
                ),
            ));

            // 1: physical Bot row lock + membership
            // guard (the guard carries the same MySQL
            // FOR UPDATE suffix as the Task-3 style
            // count guards).
            steps.push(DbTransactionStep::Query(
                self.retire_member_guard_step(source, &env, bot_id, &retire),
            ));
            guard_indexes.push((
                steps.len() - 1,
                crate::retirement::GUARD_COLUMN,
            ));

            // 2: the tombstone + Provider/ref tombstone.
            if changed {
                steps.push(DbTransactionStep::ExecuteChecked {
                    statement: lifecycle::tombstone_bot_statement(
                        &env, bot_id,
                    ),
                    expected_affected_rows: 1,
                });
            }

            // 3: the necessary binding disable (a gateway
            // projection of THIS provider/ref, or the
            // BindingFallback row).
            if pre.disable_binding {
                steps.push(DbTransactionStep::Execute(
                    DbStatement::with_params(
                        "UPDATE bcs_provider_bot_bindings \
                         SET disabled = 1, \
                             gmt_modified = CURRENT_TIMESTAMP \
                         WHERE bot_uuid = ? AND env = ? \
                           AND disabled = 0",
                        vec![
                            DbValue::from(bot_id),
                            DbValue::from(env.as_str()),
                        ],
                    ),
                ));
            }

            // 4: owner/manager revocation + pending closure
            // (fixed system marker, `bot_retired` reason).
            steps.push(DbTransactionStep::Execute(
                lifecycle::revoke_authority_edges_statement(
                    &env, bot_id,
                ),
            ));
            crate::retirement::push_pending_closure_steps(
                &mut steps,
                self.flavor,
                &mut guard_indexes,
                &env,
                bot_id,
                &retire.mutation.mutation_id,
                &pendings,
            );

            // 5: the single `retire` audit row carries the
            // verified Provider actor; the token never does.
            if changed {
                crate::retirement::push_retire_audit_steps(
                    &mut steps,
                    &mut guard_indexes,
                    &env,
                    bot_id,
                    &retire.mutation,
                );
            }

            steps.push(crate::retirement::final_guard_consume(
                &guard_indexes,
            ));

            match self
                .db
                .transaction_with_options(
                    steps,
                    crate::ownership::transaction_options(),
                )
                .await
            {
                Ok(_) => {
                    // Post-commit buildable-cache publish only.
                    self.bindings.invalidate(&format!("{env}:{bot_id}"));
                    return Ok(ProviderRetirement {
                        bot_id: bot_id.to_string(),
                        provider_id: retire.provider_id.clone(),
                        provider_bot_ref: retire.provider_bot_ref.clone(),
                        changed,
                        branch: source.branch(),
                    });
                }
                Err(error)
                    if crate::retirement::deterministic_rollback(&error) =>
                {
                    continue;
                }
                Err(DbError::Timeout) => return Err(retire_uncertain()),
                Err(_) => return Err(retire_failed()),
            }
        }
        Err(ServiceError::InternalError(
            "Provider retirement kept racing; retry budget \
             exhausted"
                .into(),
        ))
    }

    /// The membership re-verification guard: ONE row must
    /// still match the derived durable source under the
    /// row locks (an attached/replaced membership rolls
    /// the retirement back for a fresh pre-read).
    fn retire_member_guard_step(
        &self,
        source: RetireSource,
        env: &str,
        bot_id: &str,
        retire: &ProviderRetire,
    ) -> DbStatement {
        let (count_sql, count_params): (&str, Vec<DbValue>) =
            match source {
                RetireSource::Metadata => (
                    "SELECT COUNT(*) AS c FROM bcs_bots \
                     WHERE bot_uuid = ? AND env = ? \
                       AND provider_id = ? AND provider_bot_ref = ?",
                    vec![
                        DbValue::from(bot_id),
                        DbValue::from(env),
                        DbValue::from(retire.provider_id.as_str()),
                        DbValue::from(retire.provider_bot_ref.as_str()),
                    ],
                ),
                RetireSource::BindingFallback
                | RetireSource::LegacyUnbound => (
                    "SELECT COUNT(*) AS c FROM bcs_bots \
                     WHERE bot_uuid = ? AND env = ? \
                       AND provider_id IS NULL",
                    vec![
                        DbValue::from(bot_id),
                        DbValue::from(env),
                    ],
                ),
            };
        let mut params: Vec<DbTransactionParam> = count_params
            .into_iter()
            .map(DbTransactionParam::value)
            .collect();
        params.push(DbTransactionParam::value(DbValue::from(1_i64)));
        let lock_suffix = match self.flavor {
            DbSqlFlavor::Mysql => " FOR UPDATE",
            DbSqlFlavor::Sqlite => "",
        };
        DbStatement::with_transaction_params(
            format!(
                "SELECT t.c AS ok FROM ({}) t WHERE t.c = ?{lock_suffix}",
                count_sql
            )
            .as_str(),
            params,
        )
    }

    async fn require_provider_bot(&self, provider_id: &str, bot_uuid: &str) -> ServiceResult<BotProviderRecord> {
        let record = self.get_provider_bot(bot_uuid).await?
            .ok_or_else(|| ServiceError::BotNotFound(bot_uuid.into()))?;
        if record.provider_id != provider_id {
            return Err(ServiceError::Forbidden("provider_id_mismatch".into()));
        }
        Ok(record)
    }
}

#[async_trait]
impl BotProviderRepoPort for DbProviderStore {
    async fn validate_provider_affiliation(&self, provider_id: &str, bot_uuid: &str) -> ServiceResult<()> {
        self.check_affiliation_scope(provider_id, bot_uuid).await
    }
    async fn collaboration_actors(&self, ids: &[String]) -> ServiceResult<Vec<bcs_service_api::port::repo::collaboration_actor::CollaborationActor>> {
        self.collaboration_actor_rows(ids).await
    }
    async fn get_provider_bot_by_ref(&self, provider_id: &str, provider_bot_ref: &str) -> ServiceResult<Option<BotProviderRecord>> {
        let rows = self.db.query(DbStatement::with_params(
            "SELECT * FROM bcs_bots WHERE env = ? AND provider_id = ? AND provider_bot_ref = ?",
            vec![resolve_env().into(), provider_id.into(), provider_bot_ref.into()])).await.map_err(storage_error)?;
        rows.first().map(decode).transpose()
    }

    async fn list_provider_bot_metadata(&self, provider_id: Option<&str>) -> ServiceResult<Vec<BotProviderRecord>> {
        let mut sql = "SELECT * FROM bcs_bots WHERE env = ? AND provider_id IS NOT NULL".to_string();
        let mut params = vec![resolve_env().into()];
        if let Some(id) = provider_id { sql.push_str(" AND provider_id = ?"); params.push(id.into()); }
        sql.push_str(" ORDER BY bot_uuid");
        self.db.query(DbStatement::with_params(sql, params)).await.map_err(storage_error)?
            .iter().map(decode).collect()
    }

    async fn get_connection_mode(&self, bot_uuid: &str) -> ServiceResult<Option<BotConnectionMode>> {
        let rows = self.db.query(DbStatement::with_params("SELECT connection_mode FROM bcs_bots WHERE bot_uuid = ? AND env = ?",
            vec![bot_uuid.into(), resolve_env().into()])).await.map_err(storage_error)?;
        match rows.first().map(|row| row.get_string("connection_mode")).transpose().map_err(storage_error)?.flatten().as_deref() {
            None if rows.is_empty() => Ok(None),
            None => Err(ServiceError::InternalError("Bot connection mode has not been migrated".into())),
            Some(mode) => mode.parse::<BotConnectionMode>().map(Some)
                .map_err(|message| ServiceError::InternalError(message.into())),
        }
    }

    async fn attach_provider_bot(&self, record: BotProviderRecord) -> ServiceResult<()> {
        validate_record(&record)?;
        let previous = self.get_provider_bot(&record.bot_uuid).await?;
        if let Some(previous) = &previous {
            if previous.is_deleted || previous.provider_id != record.provider_id || previous.provider_bot_ref != record.provider_bot_ref
                || (previous.connection_mode == BotConnectionMode::Gateway && (record.connection_mode != BotConnectionMode::Gateway || previous.webhook_url != record.webhook_url))
            { return Err(ServiceError::Conflict("Bot Provider membership cannot be replaced".into())); }
        }
        let binding = self.get_binding_by_bot_uuid(&record.bot_uuid).await?;
        if let Some(binding) = &binding {
            if record.connection_mode != BotConnectionMode::Gateway || binding.disabled
                || binding.provider_id != record.provider_id || binding.provider_bot_ref != record.provider_bot_ref
                || binding.webhook_url != record.webhook_url
            { return Err(ServiceError::Conflict("gateway projection differs from Bot membership".into())); }
        }
        // Reattachment is not a webhook update. In particular, never write a
        // previously read callback over a concurrent explicit webhook change.
        if previous.as_ref() == Some(&record) {
            self.validate_provider_affiliation(&record.provider_id, &record.bot_uuid).await?;
            if record.connection_mode == BotConnectionMode::Gateway && binding.is_none() {
                return Err(ServiceError::Conflict("gateway projection is missing".into()));
            }
            return Ok(());
        }
        let env = resolve_env();
        let mode = record.connection_mode.as_str();
        // Only a first affiliation or plugin-to-gateway transition writes.
        // Both necessarily change a value, including on MySQL. Recheck the
        // business constraints in the UPDATE, without a timestamp/version CAS.
        let mut steps = self.affiliation_scope_steps(&record, &env);
        let guard_step = steps.len() - 1;
        let mut statement = DbStatement::with_params(
                "UPDATE bcs_bots SET provider_id = ?, provider_bot_ref = ?, connection_mode = ?, webhook_url = ?, updated_at = CURRENT_TIMESTAMP \
                 WHERE bot_uuid = ? AND env = ? AND is_deleted = 0 \
                 AND (provider_id IS NULL OR (provider_id = ? AND provider_bot_ref = ? AND connection_mode = 'plugin' AND ? = 'gateway')) \
                 AND NOT EXISTS (SELECT 1 FROM bcs_provider_bot_bindings WHERE env = ? AND provider_id = ? AND provider_bot_ref = ? AND bot_uuid <> ?)",
                vec![record.provider_id.as_str().into(), record.provider_bot_ref.as_str().into(), mode.into(), record.webhook_url.clone().into(),
                    record.bot_uuid.as_str().into(), env.as_str().into(), record.provider_id.as_str().into(), record.provider_bot_ref.as_str().into(), mode.into(),
                    env.as_str().into(), record.provider_id.as_str().into(), record.provider_bot_ref.as_str().into(), record.bot_uuid.as_str().into()]);
        let mut params = statement.params().iter().cloned().map(DbTransactionParam::Value).collect::<Vec<_>>();
        params[0] = DbTransactionParam::query_result(guard_step, 0, "provider_id");
        statement = DbStatement::with_transaction_params(statement.sql(), params);
        steps.push(DbTransactionStep::ExecuteChecked { statement, expected_affected_rows: 1 });
        if record.connection_mode == BotConnectionMode::Gateway && binding.is_none() {
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(Self::insert_binding_sql(),
                vec![record.bot_uuid.as_str().into(), record.provider_id.as_str().into(), record.provider_bot_ref.as_str().into(), env.as_str().into(), false.into(), record.webhook_url.clone().into()])));
        }
        self.db.transaction(steps).await.map_err(|error| {
            if matches!(&error, DbError::InvalidInput(message) if message.contains(&format!("references missing row 0 from step {guard_step}"))) {
                return affiliation_scope::public_group_conflict();
            }
            storage_error(error)
        })?;
        self.bindings.invalidate(&format!("{env}:{}", record.bot_uuid));
        Ok(())
    }

    async fn get_provider_bot(&self, bot_uuid: &str) -> ServiceResult<Option<BotProviderRecord>> {
        let rows = self.db.query(DbStatement::with_params(
            "SELECT bot_uuid, provider_id, provider_bot_ref, connection_mode, webhook_url, \
             is_deleted FROM bcs_bots \
             WHERE bot_uuid = ? AND env = ? AND provider_id IS NOT NULL",
            vec![bot_uuid.into(), resolve_env().into()])).await.map_err(storage_error)?;
        rows.first().map(decode).transpose()
    }

    async fn create_provider_bot(&self, record: BotProviderRecord, capabilities: BotCapabilities, owner: &str, token: &str) -> ServiceResult<()> {
        validate_record(&record)?;
        if owner.trim().is_empty() || token.is_empty() {
            return Err(ServiceError::InvalidOperation { message: "Bot owner and runtime credential are required".into(), request_id: None });
        }
        let env = resolve_env();
        let info = serde_json::to_string(&capabilities)
            .map_err(|_| ServiceError::InternalError("Bot capability serialization failed".into()))?;
        let mode = record.connection_mode.as_str();
        let mut steps = vec![DbTransactionStep::ExecuteChecked {
            statement: DbStatement::with_params(
                "INSERT INTO bcs_bots (bot_uuid, env, name, bot_info, session_token, created_by, \
                 visibility, status, actor_kind, is_deleted, registered_at, updated_at, agent_code, \
                 provider_id, provider_bot_ref, connection_mode, webhook_url) \
                 SELECT ?, ?, ?, ?, ?, ?, ?, 'online', 'bot', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, ?, ?, ?, ? \
                 WHERE NOT EXISTS (SELECT 1 FROM bcs_provider_bot_bindings WHERE env = ? AND provider_id = ? AND provider_bot_ref = ?)",
                vec![record.bot_uuid.as_str().into(), env.as_str().into(), capabilities.name.as_deref().unwrap_or(&record.bot_uuid).into(),
                    info.into(), token.into(), owner.into(), capabilities.visibility.as_str().into(),
                    capabilities.agent_code.clone().into(),
                    record.provider_id.as_str().into(), record.provider_bot_ref.as_str().into(), mode.into(), record.webhook_url.clone().into(),
                    env.as_str().into(), record.provider_id.as_str().into(), record.provider_bot_ref.as_str().into()]),
            expected_affected_rows: 1,
        }];
        if record.connection_mode == BotConnectionMode::Gateway {
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(
                Self::insert_binding_sql(), vec![record.bot_uuid.as_str().into(), record.provider_id.as_str().into(), record.provider_bot_ref.as_str().into(),
                    env.as_str().into(), false.into(), record.webhook_url.clone().into()])));
        }
        self.db.transaction(steps).await.map_err(storage_error)?;
        self.bindings.invalidate(&format!("{env}:{}", record.bot_uuid));
        Ok(())
    }

    async fn create_provider_bot_with_ownership(
        &self,
        record: BotProviderRecord,
        capabilities: BotCapabilities,
        owner: &str,
        token: &str,
        initialization: bcs_service_api::types::bot_authority::OwnershipInitialization,
    ) -> ServiceResult<()> {
        self.create_provider_bot_composite(record, capabilities, owner, token, &initialization).await
    }

    async fn retire_with_authority(
        &self,
        retire: ProviderRetire,
    ) -> ServiceResult<ProviderRetirement> {
        self.retire_with_authority_composite(retire).await
    }

    async fn update_provider_webhook(&self, provider_id: &str, bot_uuid: &str, webhook_url: Option<String>, _updated_at: u64) -> ServiceResult<BotProviderRecord> {
        let mut record = self.require_provider_bot(provider_id, bot_uuid).await?;
        if record.is_deleted || record.connection_mode != BotConnectionMode::Gateway {
            return Err(ServiceError::Conflict("webhook updates require an active gateway Bot".into()));
        }
        let env = resolve_env();
        let steps = vec![
            self.lock_gateway_binding(&record, &env),
            DbTransactionStep::Execute(DbStatement::with_params(
                "UPDATE bcs_bots SET webhook_url = ?, updated_at = CURRENT_TIMESTAMP WHERE bot_uuid = ? AND env = ? \
                 AND provider_id = ? AND provider_bot_ref = ? AND connection_mode = 'gateway' AND is_deleted = 0",
                vec![webhook_url.clone().into(), bot_uuid.into(), env.as_str().into(), provider_id.into(), record.provider_bot_ref.as_str().into()])),
            // A same-value webhook UPDATE may report zero changed rows on
            // MySQL. Check business state, not affected rows or old timestamps;
            // the UPDATE holds its normal write lock until transaction end.
            DbTransactionStep::Query(DbStatement::with_params(
                "SELECT bot_uuid FROM bcs_bots WHERE bot_uuid = ? AND env = ? AND provider_id = ? AND provider_bot_ref = ? AND connection_mode = 'gateway' AND is_deleted = 0",
                vec![bot_uuid.into(), env.as_str().into(), provider_id.into(), record.provider_bot_ref.as_str().into()])),
            DbTransactionStep::Execute(DbStatement::with_transaction_params(
                format!("UPDATE bcs_provider_bot_bindings SET webhook_url = ?, {} WHERE bot_uuid = ? AND env = ? AND bot_uuid = ?", self.now_modified_clause()),
                vec![DbTransactionParam::value(webhook_url.clone()), DbTransactionParam::query_result(0, 0, "bot_uuid"), DbTransactionParam::value(env.as_str()), DbTransactionParam::query_result(2, 0, "bot_uuid")])),
        ];
        self.db.transaction(steps).await.map_err(storage_error)?;
        self.bindings.invalidate(&format!("{env}:{bot_uuid}"));
        record.webhook_url = webhook_url;
        Ok(record)
    }

    async fn delete_provider_bot(&self, provider_id: &str, bot_uuid: &str, _updated_at: u64) -> ServiceResult<bool> {
        let record = self.require_provider_bot(provider_id, bot_uuid).await?;
        if record.is_deleted { return Ok(false); }
        let env = resolve_env();
        let mut steps = Vec::new();
        if record.connection_mode == BotConnectionMode::Gateway {
            steps.push(self.lock_gateway_binding(&record, &env));
        }
        steps.push(DbTransactionStep::ExecuteChecked { statement: DbStatement::with_params(
            "UPDATE bcs_bots SET is_deleted = 1, updated_at = CURRENT_TIMESTAMP WHERE bot_uuid = ? AND env = ? AND provider_id = ? AND provider_bot_ref = ? AND connection_mode = ? AND is_deleted = 0",
            vec![bot_uuid.into(), env.as_str().into(), provider_id.into(), record.provider_bot_ref.as_str().into(), record.connection_mode.as_str().into()]), expected_affected_rows: 1 });
        if record.connection_mode == BotConnectionMode::Gateway {
            steps.push(DbTransactionStep::Execute(DbStatement::with_transaction_params(
                format!("UPDATE bcs_provider_bot_bindings SET disabled = 1, {} WHERE bot_uuid = ? AND env = ?", self.now_modified_clause()),
                vec![DbTransactionParam::query_result(0, 0, "bot_uuid"), DbTransactionParam::value(env.as_str())])));
        }
        self.db.transaction(steps).await.map_err(storage_error)?;
        self.bindings.invalidate(&format!("{env}:{bot_uuid}"));
        Ok(true)
    }
}

/// Durable rows snapshot (pre-read; parameter preparation
/// only per spec §7) plus the derived retirement source.
struct RetireRows {
    source: RetireSource,
    pre_tombstoned: bool,
    disable_binding: bool,
}

/// Pre-read the durable rows of the retiring Bot and
/// derive the retirement branch with the SAME 归属规则
/// the read and update paths enforce — a foreign
/// membership is `provider_id_mismatch` / a replace
/// conflict, never a silent reinterpretation.
async fn read_retire_rows(
    db: &dyn DbPlugin,
    env: &str,
    bot_id: &str,
    retire: &ProviderRetire,
) -> ServiceResult<RetireRows> {
    let rows = |error: DbError| {
        crate::retirement::map_retire_tx_error(error)
    };
    let bot_rows = db
        .query(DbStatement::with_params(
            "SELECT is_deleted, provider_id, provider_bot_ref \
             FROM bcs_bots WHERE bot_uuid = ? AND env = ?",
            vec![DbValue::from(bot_id), DbValue::from(env)],
        ))
        .await
        .map_err(rows)?;
    let bot_row = match bot_rows.first() {
        Some(row) => row,
        None => return Err(ServiceError::BotNotFound(bot_id.into())),
    };
    let is_deleted: i64 =
        bcs_db_api::db_get_column(bot_row, "is_deleted")
            .or_else(decode_error)?;
    let bot_provider_id: Option<String> =
        bot_row.get_string("provider_id")
            .or_else(decode_error)?;
    let bot_provider_bot_ref: Option<String> =
        bot_row.get_string("provider_bot_ref")
            .or_else(decode_error)?;

    let binding_rows = db
        .query(DbStatement::with_params(
            "SELECT provider_id, provider_bot_ref, disabled \
             FROM bcs_provider_bot_bindings \
             WHERE bot_uuid = ? AND env = ?",
            vec![DbValue::from(bot_id), DbValue::from(env)],
        ))
        .await
        .map_err(rows)?;
    let binding: Option<(String, String, bool)> =
        match binding_rows.first() {
            Some(row) => Some((
                bcs_db_api::db_get_column(row, "provider_id")
                    .or_else(decode_error)?,
                bcs_db_api::db_get_column(row, "provider_bot_ref")
                    .or_else(decode_error)?,
                bcs_db_api::db_get_column::<i64>(row, "disabled")
                    .or_else(decode_error)?
                    != 0,
            )),
            None => None,
        };

    let source = if let Some(provider_id) = &bot_provider_id {
        if provider_id.as_str() != retire.provider_id.as_str() {
            return Err(ServiceError::Forbidden(
                "provider_id_mismatch".into(),
            ));
        }
        if bot_provider_bot_ref.as_deref()
            != Some(retire.provider_bot_ref.as_str())
        {
            return Err(ServiceError::Conflict(
                "Bot Provider membership cannot be replaced".into(),
            ));
        }
        RetireSource::Metadata
    } else {
        match &binding {
            Some((binding_provider, binding_ref, _)) => {
                if binding_provider.as_str()
                    != retire.provider_id.as_str()
                    || binding_ref.as_str()
                        != retire.provider_bot_ref.as_str()
                {
                    return Err(ServiceError::Forbidden(
                        "provider_id_mismatch".into(),
                    ));
                }
                RetireSource::BindingFallback
            }
            None => {
                // Existing LegacyUnbound admission preserved:
                // the owner-suffix shape; the flag only
                // relaxed the LOOKUP in the Core.
                if crate::retirement::is_owner_suffixed_bot_id(bot_id)
                    && retire.expected_branch
                        == ProviderRetireBranch::LegacyUnbound
                {
                    RetireSource::LegacyUnbound
                } else {
                    return Err(retirement_branch_mismatch(
                        retire.expected_branch,
                    ));
                }
            }
        }
    };
    Ok(RetireRows {
        pre_tombstoned: is_deleted != 0,
        disable_binding: binding
            .as_ref()
            .map(|(_, _, disabled)| !*disabled)
            .unwrap_or(false),
        source,
    })
}
