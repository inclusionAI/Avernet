//! Explicit, fenced migration. Never run this automatically at server startup.
use super::*;
use bcs_db_api::{DbTransactionStep, db_get_column};
use bcs_service_api::bot_provider::BotConnectionMode;
use bcs_service_api::port::repo::bot_provider::BotProviderRepoPort;
use serde::Serialize;

#[derive(Debug, Serialize)]
pub struct ProviderBotBackfillReport {
    pub memberships_to_backfill: usize,
    pub ordinary_modes_to_backfill: usize,
    pub issues: Vec<String>,
    pub applied: bool,
}

fn read<T: bcs_db_api::FromDbColumn>(row: &DbRow, key: &str) -> ServiceResult<T> {
    db_get_column(row, key).map_err(bot_storage::storage_error)
}

fn invalid(message: &str) -> ServiceError {
    ServiceError::InvalidOperation { message: message.into(), request_id: None }
}

impl DbProviderStore {
    /// A dry run is non-mutating. Apply refuses all reported inconsistencies and
    /// requires the operator to fence every writer, including legacy versions.
    /// No Bot token, ownership, lifecycle or legacy row is changed by this tool.
    pub async fn backfill_provider_bots(&self, apply: bool, writers_fenced: bool) -> ServiceResult<ProviderBotBackfillReport> {
        if apply && !writers_fenced { return Err(invalid("backfill requires all writers to be fenced")); }
        let env = resolve_env();
        let providers = self.db.query(DbStatement::with_params("SELECT provider_id FROM bcs_providers WHERE env = ?", vec![env.as_str().into()]))
            .await.map_err(bot_storage::storage_error)?.iter()
            .map(|row| read::<String>(row, "provider_id")).collect::<ServiceResult<HashSet<_>>>()?;
        let rows = self.db.query(DbStatement::with_params("SELECT * FROM bcs_bots WHERE env = ?", vec![env.as_str().into()])).await.map_err(bot_storage::storage_error)?;
        let rows = rows.into_iter().map(|row| Ok((read::<String>(&row, "bot_uuid")?, row))).collect::<ServiceResult<BTreeMap<_, _>>>()?;
        let binding_rows = self.db.query(DbStatement::with_params(format!("SELECT bot_uuid, provider_id, provider_bot_ref, webhook_url, disabled, {} FROM bcs_provider_bot_bindings WHERE env = ?", self.select_timestamp_columns()), vec![env.as_str().into()])).await.map_err(bot_storage::storage_error)?;
        let mut bindings = BTreeMap::new();
        for row in binding_rows {
            let binding = parse_binding(&row).ok_or_else(|| invalid("invalid legacy binding; repair before backfill"))?;
            bindings.insert(binding.bot_uuid.clone(), binding);
        }
        let mut report = ProviderBotBackfillReport { memberships_to_backfill: 0, ordinary_modes_to_backfill: 0, issues: Vec::new(), applied: false };
        for id in bindings.keys().filter(|id| !rows.contains_key(*id)) { report.issues.push(format!("{id}: binding has no Bot")); }
        let mut memberships = Vec::new();
        let mut ordinary = Vec::new();
        let mut seen = BTreeMap::new();
        for (id, row) in &rows {
            let deleted: bool = read(row, "is_deleted")?;
            if row.get_string("provider_id").map_err(bot_storage::storage_error)?.is_none()
                && ["provider_bot_ref", "webhook_url", "provider_registered_at", "provider_updated_at"].iter()
                    .any(|key| row.get(key).is_some_and(|value| !matches!(value, DbValue::Null)))
            {
                report.issues.push(format!("{id}: partial Provider metadata requires manual repair"));
                continue;
            }
            let binding = bindings.get(id);
            let existing = self.get_provider_bot(id).await?;
            let candidate = if let Some(record) = existing {
                match (record.connection_mode, binding) {
                    (BotConnectionMode::Gateway, Some(b)) if record.provider_id == b.provider_id && record.provider_bot_ref == b.provider_bot_ref
                        && record.webhook_url == b.webhook_url && record.is_deleted == b.disabled => {},
                    (BotConnectionMode::Plugin, None) => {},
                    _ => report.issues.push(format!("{id}: Bot metadata and gateway projection disagree")),
                }
                Some((record, false))
            } else if let Some(binding) = binding {
                if deleted != binding.disabled { report.issues.push(format!("{id}: binding.disabled differs from Bot.is_deleted")); }
                Some((binding_projection::metadata(binding.clone()), true))
            } else {
                match row.get_string("connection_mode").map_err(bot_storage::storage_error)?.as_deref() {
                    None => ordinary.push(id.clone()),
                    Some(mode) if mode.parse::<BotConnectionMode>() == Ok(BotConnectionMode::Plugin) => {},
                    _ => report.issues.push(format!("{id}: connection mode has no valid Provider metadata")),
                }
                None
            };
            if let Some((record, pending)) = candidate {
                if !providers.contains(&record.provider_id) {
                    report.issues.push(format!("{id}: Provider does not exist in this environment"));
                }
                if record.provider_id.trim().is_empty() || record.provider_bot_ref.trim().is_empty()
                    || (record.connection_mode == BotConnectionMode::Plugin && record.webhook_url.is_some())
                {
                    report.issues.push(format!("{id}: invalid Provider metadata"));
                }
                // MySQL's existing binding key commonly uses a case-insensitive
                // collation. Conservative ASCII folding prevents cutover clashes.
                let key = if self.flavor == DbSqlFlavor::Mysql {
                    (record.provider_id.to_ascii_lowercase(), record.provider_bot_ref.to_ascii_lowercase())
                } else { (record.provider_id.clone(), record.provider_bot_ref.clone()) };
                if seen.insert(key, id.clone()).is_some() { report.issues.push(format!("{id}: duplicate Provider/ref across modes")); }
                if pending { memberships.push(record); }
            }
        }
        report.memberships_to_backfill = memberships.len();
        report.ordinary_modes_to_backfill = ordinary.len();
        if !apply { return Ok(report); }
        if !report.issues.is_empty() { return Err(invalid("backfill blocked; resolve every dry-run issue first")); }
        let mut steps = Vec::new();
        for record in &memberships {
            let mode = record.connection_mode.as_str();
            steps.push(DbTransactionStep::ExecuteChecked { statement: DbStatement::with_params(
                "UPDATE bcs_bots SET provider_id = ?, provider_bot_ref = ?, connection_mode = ?, webhook_url = ?, provider_registered_at = ?, provider_updated_at = ? \
                 WHERE bot_uuid = ? AND env = ? AND provider_id IS NULL AND provider_bot_ref IS NULL AND is_deleted = ?",
                vec![record.provider_id.as_str().into(), record.provider_bot_ref.as_str().into(), mode.into(), record.webhook_url.clone().into(), record.registered_at.into(), record.updated_at.into(), record.bot_uuid.as_str().into(), env.as_str().into(), record.is_deleted.into()]), expected_affected_rows: 1 });
        }
        for id in ordinary {
            steps.push(DbTransactionStep::ExecuteChecked { statement: DbStatement::with_params(
                "UPDATE bcs_bots SET connection_mode = ? WHERE bot_uuid = ? AND env = ? AND provider_id IS NULL AND connection_mode IS NULL",
                vec![BotConnectionMode::Plugin.as_str().into(), id.into(), env.as_str().into()]), expected_affected_rows: 1 });
        }
        if !steps.is_empty() { self.db.transaction(steps).await.map_err(bot_storage::storage_error)?; }
        report.applied = true;
        Ok(report)
    }
}
