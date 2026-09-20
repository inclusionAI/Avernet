//! Independent registration journal. Credentials stay inside the repository
//! contract; neither stores nor persistence errors expose record contents.

use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_db_api::{DbError, DbPlugin, DbSqlFlavor, DbStatement, DbValue, db_get_column};
use bcs_service_api::port::repo::provider_registration::ProviderRegistrationRepoPort;
use bcs_service_api::types::provider_registration::ProviderRegistrationRecord;
use bcs_service_api::{ServiceError, ServiceResult};
use tokio::sync::RwLock;

/// Each instance is an isolated environment. One lock protects both uniqueness
/// constraints and completion, so competing callers cannot replace a winner.
#[derive(Default)]
pub struct MemoryProviderRegistrationStore {
    records: RwLock<HashMap<(String, String), ProviderRegistrationRecord>>,
}

impl MemoryProviderRegistrationStore {
    pub fn new() -> Self {
        Self::default()
    }
}

#[async_trait]
impl ProviderRegistrationRepoPort for MemoryProviderRegistrationStore {
    async fn get(
        &self,
        provider_id: &str,
        provider_bot_ref: &str,
    ) -> ServiceResult<Option<ProviderRegistrationRecord>> {
        Ok(self
            .records
            .read()
            .await
            .get(&(provider_id.to_owned(), provider_bot_ref.to_owned()))
            .cloned())
    }

    async fn reserve(
        &self,
        record: ProviderRegistrationRecord,
    ) -> ServiceResult<ProviderRegistrationRecord> {
        let key = (record.provider_id.clone(), record.provider_bot_ref.clone());
        let mut records = self.records.write().await;
        if let Some(winner) = records.get(&key) {
            return Ok(winner.clone());
        }
        if records
            .values()
            .any(|existing| existing.bot_uuid == record.bot_uuid)
        {
            return Err(conflicting_bot());
        }
        records.insert(key, record.clone());
        Ok(record)
    }

    async fn complete(&self, provider_id: &str, provider_bot_ref: &str) -> ServiceResult<()> {
        let mut records = self.records.write().await;
        let record = records
            .get_mut(&(provider_id.to_owned(), provider_bot_ref.to_owned()))
            .ok_or_else(missing_reservation)?;
        record.completed = true;
        Ok(())
    }
}

/// Uses database uniqueness for coordination across processes. The composition
/// root supplies the environment explicitly; this store never reads raw env.
pub struct DbProviderRegistrationStore {
    db: Arc<dyn DbPlugin>,
    flavor: DbSqlFlavor,
    env: String,
}

impl DbProviderRegistrationStore {
    pub fn new(db: Arc<dyn DbPlugin>, flavor: DbSqlFlavor, env: String) -> Self {
        Self { db, flavor, env }
    }

    fn identity_params(&self, provider_id: &str, provider_bot_ref: &str) -> Vec<DbValue> {
        vec![
            self.env.as_str().into(),
            provider_id.into(),
            provider_bot_ref.into(),
        ]
    }
}

#[async_trait]
impl ProviderRegistrationRepoPort for DbProviderRegistrationStore {
    async fn get(
        &self,
        provider_id: &str,
        provider_bot_ref: &str,
    ) -> ServiceResult<Option<ProviderRegistrationRecord>> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT record_json, completed FROM bcs_provider_registrations \
             WHERE env = ? AND provider_id = ? AND provider_bot_ref = ?",
                self.identity_params(provider_id, provider_bot_ref),
            ))
            .await
            .map_err(|_| internal("provider registration read failed"))?;
        let Some(row) = rows.first() else {
            return Ok(None);
        };
        if rows.len() != 1 {
            return Err(internal("provider registration identity is not unique"));
        }
        let json: String = db_get_column(row, "record_json")
            .map_err(|_| internal("invalid provider registration record column"))?;
        // Serde errors may quote invalid input, including a credential placed
        // in an unexpected field. Never forward their text to callers/logs.
        let mut record: ProviderRegistrationRecord = serde_json::from_str(&json)
            .map_err(|_| internal("invalid provider registration record JSON"))?;
        if record.provider_id != provider_id || record.provider_bot_ref != provider_bot_ref {
            return Err(internal("provider registration record identity mismatch"));
        }
        record.completed = db_get_column(row, "completed")
            .map_err(|_| internal("invalid provider registration completion column"))?;
        Ok(Some(record))
    }

    async fn reserve(
        &self,
        record: ProviderRegistrationRecord,
    ) -> ServiceResult<ProviderRegistrationRecord> {
        let json = serde_json::to_string(&record)
            .map_err(|_| internal("provider registration serialization failed"))?;
        let result = self
            .db
            .execute(DbStatement::with_params(
                "INSERT INTO bcs_provider_registrations \
             (env, provider_id, provider_bot_ref, bot_uuid, record_json, completed) \
             VALUES (?, ?, ?, ?, ?, ?)",
                vec![
                    self.env.as_str().into(),
                    record.provider_id.as_str().into(),
                    record.provider_bot_ref.as_str().into(),
                    record.bot_uuid.as_str().into(),
                    json.into(),
                    record.completed.into(),
                ],
            ))
            .await;
        match result {
            Ok(result) if result.affected_rows == 1 => Ok(record),
            Err(error) if is_registration_duplicate(&error, self.flavor) => {
                // A successful read is permitted only after a verified unique
                // violation. Timeouts/truncation/other failures must propagate.
                self.get(&record.provider_id, &record.provider_bot_ref)
                    .await?
                    .ok_or_else(conflicting_bot)
            }
            _ => Err(internal("provider registration insert failed")),
        }
    }

    async fn complete(&self, provider_id: &str, provider_bot_ref: &str) -> ServiceResult<()> {
        let result = self
            .db
            .execute(DbStatement::with_params(
                format!(
                    "UPDATE bcs_provider_registrations SET completed = 1, {} \
                WHERE env = ? AND provider_id = ? AND provider_bot_ref = ?",
                    self.flavor.set_modified_now()
                ),
                self.identity_params(provider_id, provider_bot_ref),
            ))
            .await
            .map_err(|_| internal("provider registration completion failed"))?;
        match result.affected_rows {
            1 => Ok(()),
            0 => {
                // MySQL may count changed rows, not matched rows. An already
                // completed row is success; a missing/pending row is not.
                let record = self
                    .get(provider_id, provider_bot_ref)
                    .await?
                    .ok_or_else(missing_reservation)?;
                if record.completed {
                    Ok(())
                } else {
                    Err(internal(
                        "provider registration completion was not persisted",
                    ))
                }
            }
            _ => Err(internal(
                "provider registration completion affected multiple rows",
            )),
        }
    }
}

fn is_registration_duplicate(error: &DbError, flavor: DbSqlFlavor) -> bool {
    // DbError currently erases driver error codes. Match the concrete drivers'
    // unique-error forms and this table's constraints, not arbitrary "1062" text.
    let DbError::Backend(message) = error else {
        return false;
    };
    match flavor {
        DbSqlFlavor::Sqlite => {
            let message = message
                .strip_prefix("execute sqlite statement: ")
                .unwrap_or(message);
            matches!(
                message,
                "UNIQUE constraint failed: bcs_provider_registrations.env, bcs_provider_registrations.provider_id, bcs_provider_registrations.provider_bot_ref"
                    | "UNIQUE constraint failed: bcs_provider_registrations.env, bcs_provider_registrations.bot_uuid"
            )
        }
        DbSqlFlavor::Mysql => {
            let message = message
                .strip_prefix("mysql execute failed: ")
                .or_else(|| message.strip_prefix("mysql prepared execute failed: "))
                .unwrap_or(message);
            let message = message.strip_prefix("Server error: `").unwrap_or(message);
            if !message.starts_with("ERROR 23000 (1062): Duplicate entry ") {
                return false;
            }
            let Some((_, key)) = message.rsplit_once(" for key '") else {
                return false;
            };
            matches!(
                key.trim_end_matches('\'').rsplit('.').next(),
                Some("uk_registration_ref_env" | "uk_registration_bot_env")
            )
        }
    }
}

fn internal(message: &str) -> ServiceError {
    ServiceError::InternalError(message.to_owned())
}

fn missing_reservation() -> ServiceError {
    ServiceError::InvalidOperation {
        message: "provider registration reservation does not exist".into(),
        request_id: None,
    }
}

fn conflicting_bot() -> ServiceError {
    ServiceError::InvalidOperation {
        message: "bot already has a different provider registration".into(),
        request_id: None,
    }
}
