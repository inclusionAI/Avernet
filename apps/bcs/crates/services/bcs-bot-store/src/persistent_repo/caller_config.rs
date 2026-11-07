//! Per-Bot caller configuration persistence (spec §10.7.2/§15.96).
//!
//! The authoritative snapshot lives inside the `bcs_bots.bot_info` JSON
//! column under the `$.caller` sub-object — no dedicated table. Reads use a
//! projected `$.caller`-only SELECT so the admission-time member scan never
//! parses whole `bot_info` blobs; writes replace exactly that sub-object
//! under a binary CAS on the original text so concurrent sibling-field
//! updates are never overwritten. The process-local read-through cache
//! (`crate::caller_config_cache`) bounds repeat scans; the sync path
//! invalidates on success.
//!
//! Ported from the `dev_caller_rebase` consolidation line where this storage
//! was originally written, adapted to the split-repository layout.

use bcs_db_api::{DbRow, DbSqlFlavor, db_get_column_opt, DbValue as Value};
use bcs_service_api::port::repo::bot_control_plane::{
    BotCallerConfiguration, CallerConfigurationSync, CallerConfigRevisionDecision,
    CallerIdentitySnapshot,
};
use bcs_service_api::ServiceResult;
use bcs_service_api::ServiceError;

use super::PersistentBotRepo;

/// Closed, sanitized storage failure for caller configuration data. Never
/// carries raw JSON or parser diagnostics.
const CALLER_CONFIG_STORAGE_ERROR: &str = "caller configuration storage is unreadable";

impl PersistentBotRepo {
    /// Read the raw `bot_info` text of one non-deleted row. The outer `None`
    /// means the target is missing out of scope; the inner `None` is a SQL
    /// NULL legacy column. With `provider`, the row must carry a canonical,
    /// enabled binding to exactly that Provider.
    async fn read_caller_bot_info_raw(
        &self,
        bot_id: &str,
        env: &str,
        provider: Option<&str>,
    ) -> ServiceResult<Option<Option<String>>> {
        let (join, mut params): (&str, Vec<Value>) = match provider {
            Some(provider_id) => (
                "JOIN bcs_provider_bot_bindings g \
                  ON g.bot_uuid = b.bot_uuid AND g.env = b.env AND g.provider_id = ? \
                  AND COALESCE(g.disabled, 0) = 0",
                vec![Value::from(provider_id.to_string())],
            ),
            None => ("", Vec::new()),
        };
        let sql = format!(
            "SELECT b.bot_info FROM bcs_bots b {join} \
             WHERE b.bot_uuid = ? AND b.env = ? AND COALESCE(b.is_deleted, 0) = 0 LIMIT 1",
            join = join.trim_end(),
        );
        params.push(Value::from(bot_id.to_string()));
        params.push(Value::from(env.to_string()));
        let rows = self
            .db_query(&sql, params)
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;
        match rows.first() {
            None => Ok(None),
            Some(row) => Ok(Some(
                db_get_column_opt::<String>(row, "bot_info")
                    .map_err(|error| ServiceError::InternalError(error.to_string()))?,
            )),
        }
    }

    /// Scope-resolved read-back shared by the unscoped cache loader and the
    /// provider-scoped port method. Projected read (§15.96): selects only
    /// `$.caller` plus a validity flag instead of the whole `bot_info`
    /// blob. A NULL `bot_info` (legal legacy column) reads as "never
    /// configured"; invalid JSON fails closed with the closed storage
    /// error. Only realization drift vs the old full-parse: a
    /// valid-but-non-object root (a shape no writer of this table
    /// produces) reads as unconfigured instead of erroring.
    pub(crate) async fn read_caller_configuration_scoped(
        &self,
        bot_id: &str,
        env: &str,
        provider: Option<&str>,
    ) -> ServiceResult<Option<BotCallerConfiguration>> {
        let (join, mut params): (&str, Vec<Value>) = match provider {
            Some(provider_id) => (
                "JOIN bcs_provider_bot_bindings g \
                  ON g.bot_uuid = b.bot_uuid AND g.env = b.env AND g.provider_id = ? \
                  AND COALESCE(g.disabled, 0) = 0",
                vec![Value::from(provider_id.to_string())],
            ),
            None => ("", Vec::new()),
        };
        let sql = format!(
            "SELECT {projection} FROM bcs_bots b {join} \
             WHERE b.bot_uuid = ? AND b.env = ? AND COALESCE(b.is_deleted, 0) = 0 LIMIT 1",
            projection = caller_config_projection(self.flavor),
            join = join.trim_end(),
        );
        params.push(Value::from(bot_id.to_string()));
        params.push(Value::from(env.to_string()));
        let rows = self
            .db_query(&sql, params)
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;
        match rows.first() {
            None => Ok(None),
            Some(row) => Ok(Some(caller_configuration_from_projected_row(row)?)),
        }
    }

    /// Batch read-back of the unscoped caller configurations of several
    /// targets in ONE environment (§10.7.2 per-target semantics; absent
    /// targets omitted). Answers in a bounded number of round trips
    /// (chunks of 500 ids) so the admission-time member scan stays off
    /// the N+1 path regardless of group size.
    pub(crate) async fn read_caller_configurations_projected(
        &self,
        env: &str,
        bot_ids: &[String],
    ) -> ServiceResult<Vec<(String, BotCallerConfiguration)>> {
        let mut results = Vec::new();
        const CHUNK: usize = 500;
        for chunk in bot_ids.chunks(CHUNK) {
            let mut params: Vec<Value> = Vec::with_capacity(chunk.len() + 1);
            params.push(Value::from(env.to_string()));
            let placeholders = chunk
                .iter()
                .map(|id| {
                    params.push(Value::from(id.clone()));
                    "?"
                })
                .collect::<Vec<_>>()
                .join(", ");
            let sql = format!(
                "SELECT b.bot_uuid, {projection} FROM bcs_bots b \
                 WHERE b.env = ? AND b.bot_uuid IN ({placeholders}) \
                 AND COALESCE(b.is_deleted, 0) = 0",
                projection = caller_config_projection(self.flavor),
            );
            let rows = self
                .db_query(&sql, params)
                .await
                .map_err(|error| ServiceError::InternalError(error.to_string()))?;
            for row in rows {
                let bot_uuid = db_get_column_opt::<String>(&row, "bot_uuid")
                    .map_err(|error| ServiceError::InternalError(error.to_string()))?
                    .ok_or_else(|| {
                        ServiceError::InternalError(CALLER_CONFIG_STORAGE_ERROR.to_string())
                    })?;
                results.push((bot_uuid, caller_configuration_from_projected_row(&row)?));
            }
        }
        Ok(results)
    }

    /// Read-decide-write with a binary CAS on the original bot_info text:
    /// never overwrites concurrent sibling-field updates, and retries the
    /// decision exactly once after a lost race.
    pub(crate) async fn sync_caller_configuration_scoped(
        &self,
        bot_id: &str,
        env: &str,
        provider: Option<&str>,
        incoming: CallerIdentitySnapshot,
    ) -> ServiceResult<Option<CallerConfigurationSync>> {
        let raw = match self.read_caller_bot_info_raw(bot_id, env, provider).await? {
            Some(raw) => raw,
            None => return Ok(None),
        };
        let current = caller_snapshot_from_bot_info(raw.as_deref())?;
        if let Some(outcome) = caller_sync_decision(&incoming, current.clone()) {
            return Ok(Some(outcome));
        }
        if let Some(outcome) = self
            .write_caller_snapshot(bot_id, env, raw.as_deref(), &incoming, provider)
            .await?
        {
            return Ok(Some(outcome));
        }
        // CAS lost the race: exactly one re-read, then re-decide. Still
        // writable -> Conflict (the caller retries); never a blind overwrite.
        let raw = match self.read_caller_bot_info_raw(bot_id, env, provider).await? {
            Some(raw) => raw,
            None => return Ok(None),
        };
        let current = caller_snapshot_from_bot_info(raw.as_deref())?;
        Ok(Some(caller_sync_decision(&incoming, current).unwrap_or(
            CallerConfigurationSync::Conflict {
                source_revision: incoming.source_revision,
            },
        )))
    }

    /// Replace the `$.caller` sub-object while preserving every sibling
    /// field, guarded by a binary CAS on the original `bot_info` text.
    /// Returns `Ok(None)` when the race is lost and the caller re-decides.
    async fn write_caller_snapshot(
        &self,
        bot_id: &str,
        env: &str,
        original: Option<&str>,
        incoming: &CallerIdentitySnapshot,
        provider: Option<&str>,
    ) -> ServiceResult<Option<CallerConfigurationSync>> {
        let mut root: serde_json::Value = match original {
            Some(text) => serde_json::from_str(text)
                .map_err(|_| ServiceError::InternalError(CALLER_CONFIG_STORAGE_ERROR.to_string()))?,
            None => serde_json::json!({}),
        };
        if !root.is_object() {
            return Err(ServiceError::InternalError(
                CALLER_CONFIG_STORAGE_ERROR.to_string(),
            ));
        }
        root.as_object_mut()
            .expect("checked object root")
            .insert(
                "caller".to_string(),
                serde_json::to_value(incoming).map_err(|_| {
                    ServiceError::InternalError(CALLER_CONFIG_STORAGE_ERROR.to_string())
                })?,
            );
        let new_text = root.to_string();
        // Binary comparison avoids case-insensitive collations weakening the
        // CAS on MySQL; SQLite TEXT comparison is already binary.
        let (cas, cas_params): (&str, Vec<Value>) = match original {
            Some(text) => {
                let params = vec![Value::from(text.to_string())];
                if matches!(self.flavor, DbSqlFlavor::Mysql) {
                    ("CAST(bot_info AS BINARY) = CAST(? AS BINARY)", params)
                } else {
                    ("bot_info = ?", params)
                }
            }
            None => ("bot_info IS NULL", Vec::new()),
        };
        // Ownership is re-asserted inside the same conditional write, so a
        // concurrent ownership change can never be overwritten by a stale
        // provider authority.
        let (ownership, ownership_params): (&str, Vec<Value>) = match provider {
            Some(provider_id) => (
                " AND EXISTS (SELECT 1 FROM bcs_provider_bot_bindings g \
                   WHERE g.bot_uuid = bcs_bots.bot_uuid AND g.env = bcs_bots.env \
                   AND g.provider_id = ? AND COALESCE(g.disabled, 0) = 0)",
                vec![Value::from(provider_id.to_string())],
            ),
            None => ("", Vec::new()),
        };
        let sql = format!(
            "UPDATE bcs_bots SET bot_info = ? \
             WHERE bot_uuid = ? AND env = ? AND COALESCE(is_deleted, 0) = 0{ownership} AND {cas}"
        );
        let mut params = {
            let mut head = vec![
                Value::from(new_text),
                Value::from(bot_id.to_string()),
                Value::from(env.to_string()),
            ];
            head.extend(ownership_params);
            head
        };
        params.extend(cas_params);
        let affected = self
            .db_execute_affected(&sql, params)
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;
        if affected > 0 {
            Ok(Some(CallerConfigurationSync::Applied {
                snapshot: incoming.clone(),
            }))
        } else {
            Ok(None)
        }
    }
}

/// Resolve the §10.7.2 revision matrix into a terminal sync outcome. `None`
/// means "still needs to write" (Apply) and defers to the CAS path.
pub(crate) fn caller_sync_decision(
    incoming: &CallerIdentitySnapshot,
    current: Option<CallerIdentitySnapshot>,
) -> Option<CallerConfigurationSync> {
    match incoming.compare_revision(current.as_ref()) {
        Ok(CallerConfigRevisionDecision::Apply) => None,
        Ok(CallerConfigRevisionDecision::Unchanged) => Some(CallerConfigurationSync::Unchanged {
            snapshot: current.expect("unchanged requires a stored snapshot"),
        }),
        Ok(CallerConfigRevisionDecision::Stale) => Some(CallerConfigurationSync::Stale {
            snapshot: current.expect("stale requires a stored snapshot"),
        }),
        Err(conflict) => Some(CallerConfigurationSync::Conflict {
            source_revision: conflict.source_revision,
        }),
    }
}

/// Parse the authoritative caller snapshot out of a raw `bot_info` value.
/// A missing `caller` sub-object is `Ok(None)` ("never configured"); a SQL
/// NULL column is a legal legacy row; `caller: null`, a non-object root, or
/// a malformed snapshot are explicit errors — never a fabricated disable.
fn caller_snapshot_from_bot_info(
    raw: Option<&str>,
) -> ServiceResult<Option<CallerIdentitySnapshot>> {
    let Some(text) = raw else {
        return Ok(None);
    };
    let root: serde_json::Value = serde_json::from_str(text).map_err(|_| {
        ServiceError::InternalError(CALLER_CONFIG_STORAGE_ERROR.to_string())
    })?;
    if !root.is_object() {
        return Err(ServiceError::InternalError(
            CALLER_CONFIG_STORAGE_ERROR.to_string(),
        ));
    }
    match root.get("caller") {
        None => Ok(None),
        Some(serde_json::Value::Null) => Err(ServiceError::InternalError(
            CALLER_CONFIG_STORAGE_ERROR.to_string(),
        )),
        Some(value) => serde_json::from_value::<CallerIdentitySnapshot>(value.clone())
            .map(Some)
            .map_err(|_| ServiceError::InternalError(CALLER_CONFIG_STORAGE_ERROR.to_string())),
    }
}

/// Projected caller columns (§15.96). `$.caller` only, never the whole
/// `bot_info` blob. A NULL `bot_info` is a legal legacy row and reads as
/// "never configured"; invalid JSON carries the same closed storage error
/// as the full parse. Both SQLite (`json_extract` → TEXT) and MySQL
/// (JSON_EXTRACT → driver-delivered text) map the projection to the
/// plugin's string value. `caller_type` distinguishes a literal
/// `caller: null` (a frozen storage ERROR — never a fabricated disable)
/// from a genuinely missing path: SQLite spells that type lower-case,
/// MySQL upper-case, so the parser compares case-insensitively.
fn caller_config_projection(flavor: DbSqlFlavor) -> String {
    let caller_type = match flavor {
        DbSqlFlavor::Mysql => "JSON_TYPE(JSON_EXTRACT(b.bot_info, '$.caller'))",
        DbSqlFlavor::Sqlite => "json_type(b.bot_info, '$.caller')",
    };
    // The type subselect is guarded the same way as the value: the json_*
    // helpers raise on malformed input, and one corrupt row must surface as
    // THIS row's closed storage error, never abort the whole query.
    format!(
        "CASE WHEN b.bot_info IS NULL THEN NULL \
         ELSE CASE WHEN COALESCE(JSON_VALID(b.bot_info), 0) = 1 \
              THEN json_extract(b.bot_info, '$.caller') ELSE NULL END END AS caller_config, \
         CASE WHEN b.bot_info IS NULL THEN 1 \
              ELSE COALESCE(JSON_VALID(b.bot_info), 0) END AS bot_info_valid, \
         CASE WHEN b.bot_info IS NULL OR COALESCE(JSON_VALID(b.bot_info), 0) != 1 THEN NULL \
              ELSE {caller_type} END AS caller_type",
    )
}

/// Parse one projected reader row. Invalid storage fails closed; a missing
/// `$.caller` is "existing Bot, never configured"; a present-but-null
/// sub-object keeps the frozen full-parse semantics — an explicit error,
/// never a fabricated disable.
fn caller_configuration_from_projected_row(
    row: &DbRow,
) -> ServiceResult<BotCallerConfiguration> {
    let valid = db_get_column_opt::<i64>(row, "bot_info_valid")
        .map_err(|error| ServiceError::InternalError(error.to_string()))?
        .unwrap_or(0);
    if valid == 0 {
        return Err(ServiceError::InternalError(
            CALLER_CONFIG_STORAGE_ERROR.to_string(),
        ));
    }
    if db_get_column_opt::<String>(row, "caller_type")
        .map_err(|error| ServiceError::InternalError(error.to_string()))?
        .is_some_and(|kind| kind.eq_ignore_ascii_case("null"))
    {
        return Err(ServiceError::InternalError(
            CALLER_CONFIG_STORAGE_ERROR.to_string(),
        ));
    }
    let raw = db_get_column_opt::<String>(row, "caller_config")
        .map_err(|error| ServiceError::InternalError(error.to_string()))?;
    let snapshot = caller_snapshot_from_extracted(raw.as_deref())?;
    Ok(BotCallerConfiguration { snapshot })
}

/// Parse the already-extracted `$.caller` value (NOT the whole bot_info):
/// SQL NULL / a missing path is "never configured"; a malformed snapshot
/// object fails closed. A literal `caller: null` never reaches here — the
/// reader catches it via the caller_type guard first.
fn caller_snapshot_from_extracted(
    raw: Option<&str>,
) -> ServiceResult<Option<CallerIdentitySnapshot>> {
    let Some(text) = raw else {
        return Ok(None);
    };
    serde_json::from_str::<CallerIdentitySnapshot>(text)
        .map(Some)
        .map_err(|_| ServiceError::InternalError(CALLER_CONFIG_STORAGE_ERROR.to_string()))
}

/// Cache key for the (env, bot) caller configuration entry.
pub(crate) fn caller_cache_key(bot_id: &str, env: &str) -> String {
    format!("{env}\u{1}{bot_id}")
}

