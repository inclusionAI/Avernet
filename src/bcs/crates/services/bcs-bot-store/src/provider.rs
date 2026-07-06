use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::Arc;

use async_trait::async_trait;
use tokio::sync::RwLock;
use tracing::warn;

use bcs_config::resolve_env_str as resolve_env;
use bcs_db_api::{DbError, DbPlugin, DbRow, DbSqlFlavor, DbStatement, DbValue};
use bcs_service_api::{
    ProviderBotBinding, ProviderBotBindingRepoPort, ProviderBotDiscoveryRecord,
    ProviderBotDiscoverySelector, ProviderCredential, ProviderCredentialRepoPort, ProviderRecord,
    ProviderRepoPort, ServiceError, ServiceResult,
};

pub type ProviderSqlFlavor = DbSqlFlavor;

pub type MysqlProviderStore = DbProviderStore;
pub type SqliteProviderStore = DbProviderStore;

#[derive(Debug, Default)]
pub struct MemoryProviderStore {
    providers: RwLock<BTreeMap<String, ProviderRecord>>,
    credentials_by_kind: RwLock<BTreeMap<(String, String), ProviderCredential>>,
    credential_secret_index: RwLock<HashMap<(String, String), (String, String)>>,
    bindings_by_bot: RwLock<BTreeMap<String, ProviderBotBinding>>,
    binding_ref_index: RwLock<HashMap<(String, String), String>>,
}

impl MemoryProviderStore {
    pub fn new() -> Self {
        Self::default()
    }
}

#[async_trait]
impl ProviderRepoPort for MemoryProviderStore {
    async fn insert_provider(&self, provider: ProviderRecord) -> ServiceResult<()> {
        let mut providers = self.providers.write().await;
        if providers.contains_key(&provider.provider_id) {
            return Err(ServiceError::InvalidOperation {
                message: format!("provider '{}' already exists", provider.provider_id),
                request_id: None,
            });
        }
        providers.insert(provider.provider_id.clone(), provider);
        Ok(())
    }

    async fn get_provider(&self, provider_id: &str) -> ServiceResult<Option<ProviderRecord>> {
        Ok(self.providers.read().await.get(provider_id).cloned())
    }

    async fn list_providers_by_ids(
        &self,
        provider_ids: &[String],
    ) -> ServiceResult<Vec<ProviderRecord>> {
        let provider_ids = provider_ids.iter().map(String::as_str).collect::<HashSet<_>>();
        Ok(self
            .providers
            .read()
            .await
            .values()
            .filter(|provider| provider_ids.contains(provider.provider_id.as_str()))
            .cloned()
            .collect())
    }

    async fn list_providers(&self) -> ServiceResult<Vec<ProviderRecord>> {
        Ok(self.providers.read().await.values().cloned().collect())
    }

    async fn update_provider_metadata(
        &self,
        provider_id: &str,
        name: Option<&str>,
        config: Option<&str>,
        updated_at: u64,
    ) -> ServiceResult<Option<ProviderRecord>> {
        let mut providers = self.providers.write().await;
        let Some(provider) = providers.get_mut(provider_id) else {
            return Ok(None);
        };
        if let Some(name) = name {
            provider.name = name.to_string();
        }
        if let Some(config) = config {
            provider.config = config.to_string();
        }
        provider.updated_at = updated_at;
        Ok(Some(provider.clone()))
    }

    async fn update_provider_disabled(
        &self,
        provider_id: &str,
        disabled: bool,
        updated_at: u64,
    ) -> ServiceResult<Option<ProviderRecord>> {
        let mut providers = self.providers.write().await;
        let Some(provider) = providers.get_mut(provider_id) else {
            return Ok(None);
        };
        provider.disabled = disabled;
        provider.updated_at = updated_at;
        Ok(Some(provider.clone()))
    }
}

#[async_trait]
impl ProviderCredentialRepoPort for MemoryProviderStore {
    async fn insert_credential(&self, credential: ProviderCredential) -> ServiceResult<()> {
        let key = (
            credential.provider_id.clone(),
            credential.credential_kind.clone(),
        );
        let secret_key = (
            credential.credential_kind.clone(),
            credential.secret_value.clone(),
        );

        let mut credentials = self.credentials_by_kind.write().await;
        if credentials.contains_key(&key) {
            return Err(ServiceError::InvalidOperation {
                message: format!(
                    "credential '{}' for provider '{}' already exists",
                    credential.credential_kind, credential.provider_id
                ),
                request_id: None,
            });
        }
        credentials.insert(key.clone(), credential);
        drop(credentials);

        self.credential_secret_index
            .write()
            .await
            .insert(secret_key, key);
        Ok(())
    }

    async fn get_credential_by_kind(
        &self,
        provider_id: &str,
        credential_kind: &str,
    ) -> ServiceResult<Option<ProviderCredential>> {
        Ok(self
            .credentials_by_kind
            .read()
            .await
            .get(&(provider_id.to_string(), credential_kind.to_string()))
            .cloned())
    }

    async fn list_credentials_by_kind_for_providers(
        &self,
        provider_ids: &[String],
        credential_kind: &str,
    ) -> ServiceResult<Vec<ProviderCredential>> {
        let provider_ids = provider_ids.iter().map(String::as_str).collect::<HashSet<_>>();
        Ok(self
            .credentials_by_kind
            .read()
            .await
            .values()
            .filter(|credential| {
                credential.credential_kind == credential_kind
                    && provider_ids.contains(credential.provider_id.as_str())
            })
            .cloned()
            .collect())
    }

    async fn get_credential_by_secret(
        &self,
        credential_kind: &str,
        secret_value: &str,
    ) -> ServiceResult<Option<ProviderCredential>> {
        let key = self
            .credential_secret_index
            .read()
            .await
            .get(&(credential_kind.to_string(), secret_value.to_string()))
            .cloned();
        let Some(key) = key else {
            return Ok(None);
        };
        Ok(self.credentials_by_kind.read().await.get(&key).cloned())
    }

    async fn list_credentials_by_provider(
        &self,
        provider_id: &str,
    ) -> ServiceResult<Vec<ProviderCredential>> {
        Ok(self
            .credentials_by_kind
            .read()
            .await
            .values()
            .filter(|credential| credential.provider_id == provider_id)
            .cloned()
            .collect())
    }

    async fn update_credential_disabled(
        &self,
        provider_id: &str,
        credential_kind: &str,
        disabled: bool,
        updated_at: u64,
    ) -> ServiceResult<Option<ProviderCredential>> {
        let mut credentials = self.credentials_by_kind.write().await;
        let Some(credential) =
            credentials.get_mut(&(provider_id.to_string(), credential_kind.to_string()))
        else {
            return Ok(None);
        };
        credential.disabled = disabled;
        credential.updated_at = updated_at;
        Ok(Some(credential.clone()))
    }
}

#[async_trait]
impl ProviderBotBindingRepoPort for MemoryProviderStore {
    async fn insert_binding(&self, binding: ProviderBotBinding) -> ServiceResult<()> {
        let mut bindings = self.bindings_by_bot.write().await;
        if bindings.contains_key(&binding.bot_uuid) {
            return Err(ServiceError::InvalidOperation {
                message: format!("provider binding for bot '{}' already exists", binding.bot_uuid),
                request_id: None,
            });
        }

        let ref_key = (binding.provider_id.clone(), binding.provider_bot_ref.clone());
        if self.binding_ref_index.read().await.contains_key(&ref_key) {
            return Err(ServiceError::InvalidOperation {
                message: format!(
                    "provider bot ref '{}' for provider '{}' already exists",
                    binding.provider_bot_ref, binding.provider_id
                ),
                request_id: None,
            });
        }
        self.binding_ref_index
            .write()
            .await
            .insert(ref_key, binding.bot_uuid.clone());

        bindings.insert(binding.bot_uuid.clone(), binding);
        Ok(())
    }

    async fn get_binding_by_bot_uuid(
        &self,
        bot_uuid: &str,
    ) -> ServiceResult<Option<ProviderBotBinding>> {
        Ok(self.bindings_by_bot.read().await.get(bot_uuid).cloned())
    }

    async fn list_bindings_by_bot_uuids(
        &self,
        bot_uuids: &[String],
    ) -> ServiceResult<Vec<ProviderBotBinding>> {
        let bot_uuids = bot_uuids.iter().map(String::as_str).collect::<HashSet<_>>();
        Ok(self
            .bindings_by_bot
            .read()
            .await
            .values()
            .filter(|binding| bot_uuids.contains(binding.bot_uuid.as_str()))
            .cloned()
            .collect())
    }

    async fn get_binding_by_provider_ref(
        &self,
        provider_id: &str,
        provider_bot_ref: &str,
    ) -> ServiceResult<Option<ProviderBotBinding>> {
        let bot_uuid = self
            .binding_ref_index
            .read()
            .await
            .get(&(provider_id.to_string(), provider_bot_ref.to_string()))
            .cloned();
        let Some(bot_uuid) = bot_uuid else {
            return Ok(None);
        };
        Ok(self.bindings_by_bot.read().await.get(&bot_uuid).cloned())
    }

    async fn list_bindings_by_provider(
        &self,
        provider_id: &str,
    ) -> ServiceResult<Vec<ProviderBotBinding>> {
        Ok(self
            .bindings_by_bot
            .read()
            .await
            .values()
            .filter(|binding| binding.provider_id == provider_id)
            .cloned()
            .collect())
    }

    async fn list_discoverable_provider_bot_records(
        &self,
        _selector: &ProviderBotDiscoverySelector,
    ) -> ServiceResult<Vec<ProviderBotDiscoveryRecord>> {
        let providers = self.providers.read().await;
        Ok(self
            .bindings_by_bot
            .read()
            .await
            .values()
            .filter(|binding| !binding.disabled)
            .filter_map(|binding| {
                let provider = providers.get(&binding.provider_id)?;
                if provider.disabled {
                    return None;
                }
                Some(ProviderBotDiscoveryRecord {
                    bot_uuid: binding.bot_uuid.clone(),
                    provider_id: binding.provider_id.clone(),
                    provider_name: provider.name.clone(),
                })
            })
            .collect())
    }

    async fn update_binding_disabled(
        &self,
        bot_uuid: &str,
        disabled: bool,
        updated_at: u64,
    ) -> ServiceResult<Option<ProviderBotBinding>> {
        let mut bindings = self.bindings_by_bot.write().await;
        let Some(binding) = bindings.get_mut(bot_uuid) else {
            return Ok(None);
        };
        binding.disabled = disabled;
        binding.updated_at = updated_at;
        Ok(Some(binding.clone()))
    }
}

pub struct DbProviderStore {
    db: Arc<dyn DbPlugin>,
    flavor: ProviderSqlFlavor,
}

impl DbProviderStore {
    pub fn new(db: Arc<dyn DbPlugin>, flavor: ProviderSqlFlavor) -> Self {
        Self { db, flavor }
    }

    pub fn mysql(db: Arc<dyn DbPlugin>) -> Self {
        Self::new(db, ProviderSqlFlavor::Mysql)
    }

    pub fn sqlite(db: Arc<dyn DbPlugin>) -> Self {
        Self::new(db, ProviderSqlFlavor::Sqlite)
    }

    fn insert_provider_sql() -> &'static str {
        "INSERT INTO bcs_providers \
         (provider_id, env, name, config, disabled, created_by, owners) \
         VALUES (?, ?, ?, ?, ?, ?, ?)"
    }

    fn insert_credential_sql() -> &'static str {
        "INSERT INTO bcs_provider_credentials \
         (provider_id, env, credential_kind, secret_value, disabled) \
         VALUES (?, ?, ?, ?, ?)"
    }

    fn insert_binding_sql() -> &'static str {
        "INSERT INTO bcs_provider_bot_bindings \
         (bot_uuid, provider_id, provider_bot_ref, env, disabled) \
         VALUES (?, ?, ?, ?, ?)"
    }

    /// Per-flavor SELECT fragment for `gmt_create` / `gmt_modified` columns.
    /// Always emits Unix-epoch seconds (TZ-correct) under aliases
    /// `gmt_create_ts` / `gmt_modified_ts`.
    fn select_timestamp_columns(&self) -> &'static str {
        match self.flavor {
            ProviderSqlFlavor::Mysql => {
                "UNIX_TIMESTAMP(gmt_create) AS gmt_create_ts, \
                 UNIX_TIMESTAMP(gmt_modified) AS gmt_modified_ts"
            }
            ProviderSqlFlavor::Sqlite => {
                "CAST(strftime('%s', gmt_create) AS INTEGER) AS gmt_create_ts, \
                 CAST(strftime('%s', gmt_modified) AS INTEGER) AS gmt_modified_ts"
            }
        }
    }

    /// Per-flavor SQL fragment for the `gmt_modified = <now>` SET clause.
    /// Lets the engine compute the current instant so writers don't pass a
    /// caller-formatted timestamp string (timezone-correct in both flavors).
    fn now_modified_clause(&self) -> &'static str {
        match self.flavor {
            ProviderSqlFlavor::Mysql => "gmt_modified = NOW()",
            ProviderSqlFlavor::Sqlite => {
                "gmt_modified = strftime('%Y-%m-%d %H:%M:%S','now')"
            }
        }
    }

    async fn execute(&self, operation: &'static str, statement: DbStatement) -> ServiceResult<u64> {
        self.db
            .execute(statement)
            .await
            .map(|result| result.affected_rows)
            .map_err(|err| {
                warn!(operation, error = %err, "db_provider: execute failed");
                service_db_error(operation, err)
            })
    }

    async fn execute_insert(
        &self,
        operation: &'static str,
        statement: DbStatement,
    ) -> ServiceResult<()> {
        let affected_rows = self.execute(operation, statement).await?;
        if affected_rows != 1 {
            return Err(ServiceError::InvalidOperation {
                message: format!(
                    "provider db {} affected {} rows; expected 1",
                    operation, affected_rows
                ),
                request_id: None,
            });
        }
        Ok(())
    }

    async fn query(
        &self,
        operation: &'static str,
        statement: DbStatement,
    ) -> ServiceResult<Vec<DbRow>> {
        self.db.query(statement).await.map_err(|err| {
            warn!(operation, error = %err, "db_provider: query failed");
            service_db_error(operation, err)
        })
    }
}

#[async_trait]
impl ProviderRepoPort for DbProviderStore {
    async fn insert_provider(&self, provider: ProviderRecord) -> ServiceResult<()> {
        let env = resolve_env();
        self.execute_insert(
            "insert_provider",
            DbStatement::with_params(
                Self::insert_provider_sql(),
                vec![
                    DbValue::from(provider.provider_id.as_str()),
                    DbValue::from(env.as_str()),
                    DbValue::from(provider.name.as_str()),
                    DbValue::from(provider.config.as_str()),
                    DbValue::from(provider.disabled),
                    DbValue::from(provider.created_by.as_str()),
                    DbValue::from(provider.owners.as_str()),
                ],
            ),
        )
        .await?;
        Ok(())
    }

    async fn get_provider(&self, provider_id: &str) -> ServiceResult<Option<ProviderRecord>> {
        let env = resolve_env();
        let sql = format!(
            "SELECT provider_id, name, config, disabled, created_by, owners, {ts} \
             FROM bcs_providers WHERE provider_id = ? AND env = ? LIMIT 1",
            ts = self.select_timestamp_columns(),
        );
        let rows = self
            .query(
                "get_provider",
                DbStatement::with_params(
                    sql,
                    vec![DbValue::from(provider_id), DbValue::from(env.as_str())],
                ),
            )
            .await?;
        Ok(rows.first().and_then(parse_provider))
    }

    async fn list_providers_by_ids(
        &self,
        provider_ids: &[String],
    ) -> ServiceResult<Vec<ProviderRecord>> {
        if provider_ids.is_empty() {
            return Ok(Vec::new());
        }

        let env = resolve_env();
        let placeholders = provider_ids.iter().map(|_| "?").collect::<Vec<_>>().join(", ");
        let sql = format!(
            "SELECT provider_id, name, config, disabled, created_by, owners, {ts} \
             FROM bcs_providers WHERE provider_id IN ({placeholders}) AND env = ? ORDER BY provider_id",
            ts = self.select_timestamp_columns(),
        );
        let mut params = provider_ids
            .iter()
            .map(|provider_id| DbValue::from(provider_id.as_str()))
            .collect::<Vec<_>>();
        params.push(DbValue::from(env.as_str()));
        let rows = self
            .query(
                "list_providers_by_ids",
                DbStatement::with_params(sql, params),
            )
            .await?;
        Ok(rows.iter().filter_map(parse_provider).collect())
    }

    async fn list_providers(&self) -> ServiceResult<Vec<ProviderRecord>> {
        let env = resolve_env();
        let sql = format!(
            "SELECT provider_id, name, config, disabled, created_by, owners, {ts} \
             FROM bcs_providers WHERE env = ? ORDER BY provider_id",
            ts = self.select_timestamp_columns(),
        );
        let rows = self
            .query(
                "list_providers",
                DbStatement::with_params(sql, vec![DbValue::from(env.as_str())]),
            )
            .await?;
        Ok(rows.iter().filter_map(parse_provider).collect())
    }

    async fn update_provider_metadata(
        &self,
        provider_id: &str,
        name: Option<&str>,
        config: Option<&str>,
        _updated_at: u64,
    ) -> ServiceResult<Option<ProviderRecord>> {
        let env = resolve_env();
        let now_clause = self.now_modified_clause();
        match (name, config) {
            (None, None) => return self.get_provider(provider_id).await,
            (Some(name), None) => {
                let sql = format!(
                    "UPDATE bcs_providers SET name = ?, {now_clause} \
                     WHERE provider_id = ? AND env = ?"
                );
                self.execute(
                    "update_provider_name",
                    DbStatement::with_params(
                        sql,
                        vec![
                            DbValue::from(name),
                            DbValue::from(provider_id),
                            DbValue::from(env.as_str()),
                        ],
                    ),
                )
                .await?;
            }
            (None, Some(config)) => {
                let sql = format!(
                    "UPDATE bcs_providers SET config = ?, {now_clause} \
                     WHERE provider_id = ? AND env = ?"
                );
                self.execute(
                    "update_provider_config",
                    DbStatement::with_params(
                        sql,
                        vec![
                            DbValue::from(config),
                            DbValue::from(provider_id),
                            DbValue::from(env.as_str()),
                        ],
                    ),
                )
                .await?;
            }
            (Some(name), Some(config)) => {
                let sql = format!(
                    "UPDATE bcs_providers SET name = ?, config = ?, {now_clause} \
                     WHERE provider_id = ? AND env = ?"
                );
                self.execute(
                    "update_provider_metadata",
                    DbStatement::with_params(
                        sql,
                        vec![
                            DbValue::from(name),
                            DbValue::from(config),
                            DbValue::from(provider_id),
                            DbValue::from(env.as_str()),
                        ],
                    ),
                )
                .await?;
            }
        }
        self.get_provider(provider_id).await
    }

    async fn update_provider_disabled(
        &self,
        provider_id: &str,
        disabled: bool,
        _updated_at: u64,
    ) -> ServiceResult<Option<ProviderRecord>> {
        let env = resolve_env();
        let sql = format!(
            "UPDATE bcs_providers SET disabled = ?, {now_clause} \
             WHERE provider_id = ? AND env = ?",
            now_clause = self.now_modified_clause(),
        );
        self.execute(
            "update_provider_disabled",
            DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(disabled),
                    DbValue::from(provider_id),
                    DbValue::from(env.as_str()),
                ],
            ),
        )
        .await?;
        self.get_provider(provider_id).await
    }
}

#[async_trait]
impl ProviderCredentialRepoPort for DbProviderStore {
    async fn insert_credential(&self, credential: ProviderCredential) -> ServiceResult<()> {
        let env = resolve_env();
        self.execute_insert(
            "insert_provider_credential",
            DbStatement::with_params(
                Self::insert_credential_sql(),
                vec![
                    DbValue::from(credential.provider_id.as_str()),
                    DbValue::from(env.as_str()),
                    DbValue::from(credential.credential_kind.as_str()),
                    DbValue::from(credential.secret_value.as_str()),
                    DbValue::from(credential.disabled),
                ],
            ),
        )
        .await?;
        Ok(())
    }

    async fn get_credential_by_kind(
        &self,
        provider_id: &str,
        credential_kind: &str,
    ) -> ServiceResult<Option<ProviderCredential>> {
        let env = resolve_env();
        let sql = format!(
            "SELECT provider_id, credential_kind, secret_value, disabled, {ts} \
             FROM bcs_provider_credentials \
             WHERE provider_id = ? AND credential_kind = ? AND env = ? LIMIT 1",
            ts = self.select_timestamp_columns(),
        );
        let rows = self
            .query(
                "get_provider_credential_by_kind",
                DbStatement::with_params(
                    sql,
                    vec![
                        DbValue::from(provider_id),
                        DbValue::from(credential_kind),
                        DbValue::from(env.as_str()),
                    ],
                ),
            )
            .await?;
        Ok(rows.first().and_then(parse_credential))
    }

    async fn list_credentials_by_kind_for_providers(
        &self,
        provider_ids: &[String],
        credential_kind: &str,
    ) -> ServiceResult<Vec<ProviderCredential>> {
        if provider_ids.is_empty() {
            return Ok(Vec::new());
        }

        let env = resolve_env();
        let placeholders = provider_ids.iter().map(|_| "?").collect::<Vec<_>>().join(", ");
        let sql = format!(
            "SELECT provider_id, credential_kind, secret_value, disabled, {ts} \
             FROM bcs_provider_credentials \
             WHERE provider_id IN ({placeholders}) AND credential_kind = ? AND env = ? ORDER BY provider_id",
            ts = self.select_timestamp_columns(),
        );
        let mut params = provider_ids
            .iter()
            .map(|provider_id| DbValue::from(provider_id.as_str()))
            .collect::<Vec<_>>();
        params.push(DbValue::from(credential_kind));
        params.push(DbValue::from(env.as_str()));
        let rows = self
            .query(
                "list_provider_credentials_by_kind_for_providers",
                DbStatement::with_params(sql, params),
            )
            .await?;
        Ok(rows.iter().filter_map(parse_credential).collect())
    }

    async fn get_credential_by_secret(
        &self,
        credential_kind: &str,
        secret_value: &str,
    ) -> ServiceResult<Option<ProviderCredential>> {
        let env = resolve_env();
        let sql = format!(
            "SELECT provider_id, credential_kind, secret_value, disabled, {ts} \
             FROM bcs_provider_credentials \
             WHERE credential_kind = ? AND secret_value = ? AND env = ? LIMIT 1",
            ts = self.select_timestamp_columns(),
        );
        let rows = self
            .query(
                "get_provider_credential_by_secret",
                DbStatement::with_params(
                    sql,
                    vec![
                        DbValue::from(credential_kind),
                        DbValue::from(secret_value),
                        DbValue::from(env.as_str()),
                    ],
                ),
            )
            .await?;
        Ok(rows.first().and_then(parse_credential))
    }

    async fn list_credentials_by_provider(
        &self,
        provider_id: &str,
    ) -> ServiceResult<Vec<ProviderCredential>> {
        let env = resolve_env();
        let sql = format!(
            "SELECT provider_id, credential_kind, secret_value, disabled, {ts} \
             FROM bcs_provider_credentials \
             WHERE provider_id = ? AND env = ? ORDER BY credential_kind",
            ts = self.select_timestamp_columns(),
        );
        let rows = self
            .query(
                "list_provider_credentials",
                DbStatement::with_params(
                    sql,
                    vec![DbValue::from(provider_id), DbValue::from(env.as_str())],
                ),
            )
            .await?;
        Ok(rows.iter().filter_map(parse_credential).collect())
    }

    async fn update_credential_disabled(
        &self,
        provider_id: &str,
        credential_kind: &str,
        disabled: bool,
        _updated_at: u64,
    ) -> ServiceResult<Option<ProviderCredential>> {
        let env = resolve_env();
        let sql = format!(
            "UPDATE bcs_provider_credentials SET disabled = ?, {now_clause} \
             WHERE provider_id = ? AND credential_kind = ? AND env = ?",
            now_clause = self.now_modified_clause(),
        );
        self.execute(
            "update_provider_credential_disabled",
            DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(disabled),
                    DbValue::from(provider_id),
                    DbValue::from(credential_kind),
                    DbValue::from(env.as_str()),
                ],
            ),
        )
        .await?;
        self.get_credential_by_kind(provider_id, credential_kind).await
    }
}

#[async_trait]
impl ProviderBotBindingRepoPort for DbProviderStore {
    async fn insert_binding(&self, binding: ProviderBotBinding) -> ServiceResult<()> {
        let env = resolve_env();
        self.execute_insert(
            "insert_provider_bot_binding",
            DbStatement::with_params(
                Self::insert_binding_sql(),
                vec![
                    DbValue::from(binding.bot_uuid.as_str()),
                    DbValue::from(binding.provider_id.as_str()),
                    DbValue::from(binding.provider_bot_ref.as_str()),
                    DbValue::from(env.as_str()),
                    DbValue::from(binding.disabled),
                ],
            ),
        )
        .await?;
        Ok(())
    }

    async fn get_binding_by_bot_uuid(
        &self,
        bot_uuid: &str,
    ) -> ServiceResult<Option<ProviderBotBinding>> {
        let env = resolve_env();
        let sql = format!(
            "SELECT bot_uuid, provider_id, provider_bot_ref, disabled, {ts} \
             FROM bcs_provider_bot_bindings \
             WHERE bot_uuid = ? AND env = ? LIMIT 1",
            ts = self.select_timestamp_columns(),
        );
        let rows = self
            .query(
                "get_provider_binding_by_bot_uuid",
                DbStatement::with_params(
                    sql,
                    vec![DbValue::from(bot_uuid), DbValue::from(env.as_str())],
                ),
            )
            .await?;
        Ok(rows.first().and_then(parse_binding))
    }

    async fn list_bindings_by_bot_uuids(
        &self,
        bot_uuids: &[String],
    ) -> ServiceResult<Vec<ProviderBotBinding>> {
        if bot_uuids.is_empty() {
            return Ok(Vec::new());
        }

        let env = resolve_env();
        let placeholders = bot_uuids.iter().map(|_| "?").collect::<Vec<_>>().join(", ");
        let sql = format!(
            "SELECT bot_uuid, provider_id, provider_bot_ref, disabled, {ts} \
             FROM bcs_provider_bot_bindings \
             WHERE bot_uuid IN ({placeholders}) AND env = ? ORDER BY bot_uuid",
            ts = self.select_timestamp_columns(),
        );
        let mut params = bot_uuids
            .iter()
            .map(|bot_uuid| DbValue::from(bot_uuid.as_str()))
            .collect::<Vec<_>>();
        params.push(DbValue::from(env.as_str()));
        let rows = self
            .query(
                "list_provider_bindings_by_bot_uuids",
                DbStatement::with_params(sql, params),
            )
            .await?;
        Ok(rows.iter().filter_map(parse_binding).collect())
    }

    async fn get_binding_by_provider_ref(
        &self,
        provider_id: &str,
        provider_bot_ref: &str,
    ) -> ServiceResult<Option<ProviderBotBinding>> {
        let env = resolve_env();
        let sql = format!(
            "SELECT bot_uuid, provider_id, provider_bot_ref, disabled, {ts} \
             FROM bcs_provider_bot_bindings \
             WHERE provider_id = ? AND provider_bot_ref = ? AND env = ? LIMIT 1",
            ts = self.select_timestamp_columns(),
        );
        let rows = self
            .query(
                "get_provider_binding_by_ref",
                DbStatement::with_params(
                    sql,
                    vec![
                        DbValue::from(provider_id),
                        DbValue::from(provider_bot_ref),
                        DbValue::from(env.as_str()),
                    ],
                ),
            )
            .await?;
        Ok(rows.first().and_then(parse_binding))
    }

    async fn list_bindings_by_provider(
        &self,
        provider_id: &str,
    ) -> ServiceResult<Vec<ProviderBotBinding>> {
        let env = resolve_env();
        let sql = format!(
            "SELECT bot_uuid, provider_id, provider_bot_ref, disabled, {ts} \
             FROM bcs_provider_bot_bindings \
             WHERE provider_id = ? AND env = ? ORDER BY bot_uuid",
            ts = self.select_timestamp_columns(),
        );
        let rows = self
            .query(
                "list_provider_bindings",
                DbStatement::with_params(
                    sql,
                    vec![DbValue::from(provider_id), DbValue::from(env.as_str())],
                ),
            )
            .await?;
        Ok(rows.iter().filter_map(parse_binding).collect())
    }

    async fn list_discoverable_provider_bot_records(
        &self,
        selector: &ProviderBotDiscoverySelector,
    ) -> ServiceResult<Vec<ProviderBotDiscoveryRecord>> {
        let env = resolve_env();
        let mut sql = "\
            SELECT pb.bot_uuid, pb.provider_id, p.name AS provider_name \
            FROM bcs_provider_bot_bindings pb \
            JOIN bcs_providers p \
              ON p.provider_id = pb.provider_id \
             AND p.env = pb.env \
            JOIN bcs_bots b \
              ON b.bot_uuid = pb.bot_uuid \
             AND b.env = pb.env \
            WHERE pb.env = ? \
              AND COALESCE(pb.disabled, 0) = 0 \
              AND COALESCE(p.disabled, 0) = 0 \
              AND COALESCE(b.is_deleted, 0) = 0 \
              AND b.actor_kind = 'bot'"
            .to_string();
        let mut params = vec![DbValue::from(env.as_str())];
        append_provider_discovery_selector_sql(selector, &mut sql, &mut params);
        sql.push_str(" ORDER BY pb.bot_uuid");
        let rows = self
            .query(
                "list_discoverable_provider_bot_records",
                DbStatement::with_params(sql, params),
            )
            .await?;
        Ok(rows.iter().filter_map(parse_provider_bot_record).collect())
    }

    async fn update_binding_disabled(
        &self,
        bot_uuid: &str,
        disabled: bool,
        _updated_at: u64,
    ) -> ServiceResult<Option<ProviderBotBinding>> {
        let env = resolve_env();
        let sql = format!(
            "UPDATE bcs_provider_bot_bindings SET disabled = ?, {now_clause} \
             WHERE bot_uuid = ? AND env = ?",
            now_clause = self.now_modified_clause(),
        );
        self.execute(
            "update_provider_binding_disabled",
            DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(disabled),
                    DbValue::from(bot_uuid),
                    DbValue::from(env.as_str()),
                ],
            ),
        )
        .await?;
        self.get_binding_by_bot_uuid(bot_uuid).await
    }
}

fn parse_provider(row: &DbRow) -> Option<ProviderRecord> {
    Some(ProviderRecord {
        provider_id: optional_string(row, "provider_id")?,
        name: optional_string(row, "name")?,
        config: optional_string(row, "config")?,
        created_by: optional_string(row, "created_by")?,
        owners: optional_string(row, "owners")?,
        disabled: row_bool(row, "disabled"),
        created_at: row_seconds_to_millis(row, "gmt_create_ts"),
        updated_at: row_seconds_to_millis(row, "gmt_modified_ts"),
    })
}

fn parse_credential(row: &DbRow) -> Option<ProviderCredential> {
    Some(ProviderCredential {
        provider_id: optional_string(row, "provider_id")?,
        credential_kind: optional_string(row, "credential_kind")?,
        secret_value: optional_string(row, "secret_value")?,
        disabled: row_bool(row, "disabled"),
        created_at: row_seconds_to_millis(row, "gmt_create_ts"),
        updated_at: row_seconds_to_millis(row, "gmt_modified_ts"),
    })
}

fn parse_binding(row: &DbRow) -> Option<ProviderBotBinding> {
    Some(ProviderBotBinding {
        bot_uuid: optional_string(row, "bot_uuid")?,
        provider_id: optional_string(row, "provider_id")?,
        provider_bot_ref: optional_string(row, "provider_bot_ref").unwrap_or_default(),
        disabled: row_bool(row, "disabled"),
        created_at: row_seconds_to_millis(row, "gmt_create_ts"),
        updated_at: row_seconds_to_millis(row, "gmt_modified_ts"),
    })
}

fn parse_provider_bot_record(row: &DbRow) -> Option<ProviderBotDiscoveryRecord> {
    Some(ProviderBotDiscoveryRecord {
        bot_uuid: optional_string(row, "bot_uuid")?,
        provider_id: optional_string(row, "provider_id")?,
        provider_name: optional_string(row, "provider_name")?,
    })
}

fn append_provider_discovery_selector_sql(
    selector: &ProviderBotDiscoverySelector,
    sql: &mut String,
    params: &mut Vec<DbValue>,
) {
    match selector {
        ProviderBotDiscoverySelector::All => {}
        ProviderBotDiscoverySelector::Query(query) => {
            let pattern = like_pattern(query);
            sql.push_str(
                " AND (LOWER(pb.bot_uuid) LIKE ? \
                   OR LOWER(b.name) LIKE ? \
                   OR LOWER(COALESCE(b.bot_info, '')) LIKE ?)",
            );
            for _ in 0..3 {
                params.push(DbValue::from(pattern.as_str()));
            }
        }
        ProviderBotDiscoverySelector::Name(name) => {
            sql.push_str(" AND LOWER(b.name) LIKE ?");
            let pattern = like_pattern(name);
            params.push(DbValue::from(pattern.as_str()));
        }
        ProviderBotDiscoverySelector::Skills(terms)
        | ProviderBotDiscoverySelector::Domains(terms)
        | ProviderBotDiscoverySelector::Scopes(terms) => {
            for term in terms {
                sql.push_str(" AND LOWER(COALESCE(b.bot_info, '')) LIKE ?");
                let pattern = like_pattern(term);
                params.push(DbValue::from(pattern.as_str()));
            }
        }
    }
}

fn like_pattern(term: &str) -> String {
    format!("%{}%", term.to_lowercase())
}

fn optional_string(row: &DbRow, column: &'static str) -> Option<String> {
    row.get_string(column).ok().flatten()
}

fn row_bool(row: &DbRow, column: &'static str) -> bool {
    row.get_bool(column).ok().flatten().unwrap_or(false)
}

/// Read a Unix-epoch-seconds column (alias `*_ts`) and return milliseconds.
/// Both flavors emit signed/unsigned integers — anything else maps to 0.
fn row_seconds_to_millis(row: &DbRow, column: &'static str) -> u64 {
    match row.get(column) {
        Some(DbValue::I64(value)) if *value >= 0 => (*value as u64).saturating_mul(1000),
        Some(DbValue::U64(value)) => (*value).saturating_mul(1000),
        _ => 0,
    }
}

fn service_db_error(operation: &'static str, err: DbError) -> ServiceError {
    let message = err.to_string();
    let lower = message.to_ascii_lowercase();
    if lower.contains("duplicate") || lower.contains("unique") {
        return ServiceError::InvalidOperation {
            message: format!("provider db {} duplicate key: {}", operation, message),
            request_id: None,
        };
    }
    ServiceError::InternalError(format!("provider db {}: {}", operation, message))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    use bcs_db_api::{
        DbExecuteResult, DbHealth, DbResult, DbTransactionStep, DbTransactionStepResult,
    };

    struct RecordingDbPlugin {
        affected_rows: u64,
        statements: Mutex<Vec<String>>,
    }

    impl RecordingDbPlugin {
        fn new(affected_rows: u64) -> Self {
            Self {
                affected_rows,
                statements: Mutex::new(Vec::new()),
            }
        }

        fn statements(&self) -> Vec<String> {
            self.statements.lock().expect("statements").clone()
        }
    }

    #[async_trait]
    impl DbPlugin for RecordingDbPlugin {
        async fn query(&self, _statement: DbStatement) -> DbResult<Vec<DbRow>> {
            Err(DbError::Unsupported("query not scripted".to_string()))
        }

        async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
            self.statements
                .lock()
                .expect("statements")
                .push(statement.sql().to_string());
            Ok(DbExecuteResult {
                affected_rows: self.affected_rows,
                last_insert_id: None,
            })
        }

        async fn transaction(
            &self,
            _steps: Vec<DbTransactionStep>,
        ) -> DbResult<Vec<DbTransactionStepResult>> {
            Err(DbError::Unsupported("transaction not scripted".to_string()))
        }

        async fn health_check(&self) -> DbResult<DbHealth> {
            Ok(DbHealth::healthy())
        }
    }

    fn provider(provider_id: &str) -> ProviderRecord {
        ProviderRecord {
            provider_id: provider_id.to_string(),
            name: "Provider".to_string(),
            config: r#"{"downlink":{"enabled":true}}"#.to_string(),
            created_by: "11111111".to_string(),
            owners: r#"["11111111"]"#.to_string(),
            disabled: false,
            created_at: 1,
            updated_at: 1,
        }
    }

    #[tokio::test]
    async fn db_provider_insert_uses_plain_insert_and_rejects_zero_affected_rows() {
        let db = Arc::new(RecordingDbPlugin::new(0));
        let store = DbProviderStore::mysql(db.clone());

        let err = store
            .insert_provider(provider("provider-1"))
            .await
            .expect_err("zero affected rows must fail");

        assert!(matches!(err, ServiceError::InvalidOperation { .. }));
        let statements = db.statements();
        assert_eq!(statements.len(), 1);
        assert!(statements[0].starts_with("INSERT INTO bcs_providers"));
        assert!(!statements[0].contains("IGNORE"));
    }

    // -------------------------------------------------------------------
    // SQLite roundtrip tests — exercise the real read path so that any
    // future regression in SELECT / parse_* (e.g. dropping the *_ts
    // aliases, breaking the seconds→millis mapping, or reverting to raw
    // datetime strings) surfaces immediately.
    // -------------------------------------------------------------------

    use bcs_db_local::LocalSqliteDbPlugin;

    fn unix_now_secs() -> u64 {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0)
    }

    async fn sqlite_provider_db() -> Arc<dyn DbPlugin> {
        let db: Arc<dyn DbPlugin> =
            Arc::new(LocalSqliteDbPlugin::new().expect("open sqlite"));
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_providers (
                provider_id TEXT NOT NULL,
                env TEXT NOT NULL,
                name TEXT NOT NULL,
                config TEXT NOT NULL,
                disabled INTEGER NOT NULL DEFAULT 0,
                created_by TEXT NOT NULL,
                owners TEXT NOT NULL,
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (env, provider_id)
            )",
        ))
        .await
        .expect("create bcs_providers");
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_provider_credentials (
                provider_id TEXT NOT NULL,
                env TEXT NOT NULL,
                credential_kind TEXT NOT NULL,
                secret_value TEXT NOT NULL,
                disabled INTEGER NOT NULL DEFAULT 0,
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (env, provider_id, credential_kind)
            )",
        ))
        .await
        .expect("create bcs_provider_credentials");
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_provider_bot_bindings (
                bot_uuid TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                provider_bot_ref TEXT NOT NULL DEFAULT '',
                env TEXT NOT NULL,
                disabled INTEGER NOT NULL DEFAULT 0,
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (env, provider_id, provider_bot_ref),
                UNIQUE (env, bot_uuid)
            )",
        ))
        .await
        .expect("create bcs_provider_bot_bindings");
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_bots (
                bot_uuid TEXT NOT NULL,
                env TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                bot_info TEXT DEFAULT NULL,
                visibility TEXT NOT NULL DEFAULT 'protected',
                actor_kind TEXT NOT NULL DEFAULT 'bot',
                status TEXT NOT NULL DEFAULT 'online',
                agent_code TEXT DEFAULT NULL,
                is_deleted INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (env, bot_uuid)
            )",
        ))
        .await
        .expect("create bcs_bots");
        db
    }

    #[tokio::test]
    async fn db_provider_sqlite_roundtrips_with_nonzero_timestamps() {
        let db = sqlite_provider_db().await;
        let store = DbProviderStore::sqlite(db);
        let baseline_secs = unix_now_secs();

        store
            .insert_provider(provider("provider-rt"))
            .await
            .expect("insert provider");

        let stored = store
            .get_provider("provider-rt")
            .await
            .expect("get provider")
            .expect("provider exists");

        assert!(stored.created_at > 0, "created_at must not be 0");
        assert!(stored.updated_at > 0, "updated_at must not be 0");
        assert_eq!(stored.created_by, "11111111");
        assert_eq!(stored.owners, r#"["11111111"]"#);
        let stored_secs = stored.created_at / 1000;
        // Allow a 5-second window to absorb test latency / clock drift.
        assert!(
            stored_secs + 5 >= baseline_secs && stored_secs <= baseline_secs + 5,
            "created_at {}s should be near baseline {}s",
            stored_secs,
            baseline_secs
        );
    }

    #[tokio::test]
    async fn db_provider_sqlite_update_advances_modified_timestamp() {
        let db = sqlite_provider_db().await;
        let store = DbProviderStore::sqlite(db);

        store
            .insert_provider(provider("provider-upd"))
            .await
            .expect("insert provider");

        let before = store
            .get_provider("provider-upd")
            .await
            .expect("get provider")
            .expect("exists");

        // SQLite CURRENT_TIMESTAMP / strftime('%s','now') resolve at 1-second
        // granularity; sleep just past that boundary so the new value differs.
        tokio::time::sleep(std::time::Duration::from_millis(1_100)).await;

        let after = store
            .update_provider_metadata("provider-upd", Some("Renamed"), None, 0)
            .await
            .expect("update provider")
            .expect("returned record");

        assert_eq!(after.created_at, before.created_at);
        assert!(
            after.updated_at > before.updated_at,
            "updated_at must advance: before={}, after={}",
            before.updated_at,
            after.updated_at
        );
        assert_eq!(after.name, "Renamed");
    }

    #[tokio::test]
    async fn db_provider_sqlite_credential_and_binding_have_nonzero_timestamps() {
        let db = sqlite_provider_db().await;
        let store = DbProviderStore::sqlite(db);

        store
            .insert_provider(provider("provider-cb"))
            .await
            .expect("insert provider");
        store
            .insert_credential(ProviderCredential {
                provider_id: "provider-cb".to_string(),
                credential_kind: "provider_admin".to_string(),
                secret_value: "secret-1".to_string(),
                disabled: false,
                created_at: 0,
                updated_at: 0,
            })
            .await
            .expect("insert credential");
        store
            .insert_binding(ProviderBotBinding {
                bot_uuid: "bot-1".to_string(),
                provider_id: "provider-cb".to_string(),
                provider_bot_ref: "ref-1".to_string(),
                disabled: false,
                created_at: 0,
                updated_at: 0,
            })
            .await
            .expect("insert binding");

        let credential = store
            .get_credential_by_secret("provider_admin", "secret-1")
            .await
            .expect("get credential")
            .expect("credential exists");
        assert!(credential.created_at > 0);
        assert!(credential.updated_at > 0);

        let binding = store
            .get_binding_by_bot_uuid("bot-1")
            .await
            .expect("get binding")
            .expect("binding exists");
        assert!(binding.created_at > 0);
        assert!(binding.updated_at > 0);
    }

    #[tokio::test]
    async fn db_provider_sqlite_lists_discoverable_provider_bot_records_without_credentials() {
        let db = sqlite_provider_db().await;
        let store = DbProviderStore::sqlite(db.clone());
        let env = resolve_env();

        store
            .insert_provider(provider("provider-ok"))
            .await
            .expect("insert provider");
        let mut disabled_provider = provider("provider-disabled");
        disabled_provider.disabled = true;
        store
            .insert_provider(disabled_provider)
            .await
            .expect("insert disabled provider");

        for (bot_uuid, provider_id, disabled, is_deleted, actor_kind) in [
            ("bot-ok", "provider-ok", false, 0, "bot"),
            ("bot-binding-disabled", "provider-ok", true, 0, "bot"),
            ("bot-provider-disabled", "provider-disabled", false, 0, "bot"),
            ("bot-deleted", "provider-ok", false, 1, "bot"),
            ("human-bound", "provider-ok", false, 0, "human"),
        ] {
            db.execute(DbStatement::with_params(
                "INSERT INTO bcs_bots (bot_uuid, env, name, actor_kind, is_deleted) \
                 VALUES (?, ?, ?, ?, ?)",
                vec![
                    DbValue::from(bot_uuid),
                    DbValue::from(env.as_str()),
                    DbValue::from(bot_uuid),
                    DbValue::from(actor_kind),
                    DbValue::from(is_deleted),
                ],
            ))
            .await
            .expect("insert bot row");
            store
                .insert_binding(ProviderBotBinding {
                    bot_uuid: bot_uuid.to_string(),
                    provider_id: provider_id.to_string(),
                    provider_bot_ref: format!("ref-{bot_uuid}"),
                    disabled,
                    created_at: 0,
                    updated_at: 0,
                })
                .await
                .expect("insert binding");
        }

        let records = store
            .list_discoverable_provider_bot_records(&ProviderBotDiscoverySelector::All)
            .await
            .expect("list records");

        assert_eq!(records.len(), 1);
        assert_eq!(records[0].bot_uuid, "bot-ok");
        assert_eq!(records[0].provider_id, "provider-ok");
        assert_eq!(records[0].provider_name, "Provider");
    }

    #[tokio::test]
    async fn db_provider_sqlite_filters_discoverable_provider_bot_records_by_selector() {
        let db = sqlite_provider_db().await;
        let store = DbProviderStore::sqlite(db.clone());
        let env = resolve_env();

        store
            .insert_provider(provider("provider-search"))
            .await
            .expect("insert provider");

        for (bot_uuid, name, bot_info, agent_code) in [
            (
                "bot-query",
                "Needle Runner",
                r#"{"summary":"general helper","domains":["ops"],"skills":[{"name":"deploy"}],"scopes":["logs"]}"#,
                "agent-query",
            ),
            (
                "bot-skill",
                "Skill Runner",
                r#"{"summary":"database helper","domains":["database"],"skills":[{"name":"sql"},{"name":"ops"}],"scopes":["prod"]}"#,
                "agent-skill",
            ),
            (
                "bot-other",
                "Other Runner",
                r#"{"summary":"other helper","domains":["docs"],"skills":[{"name":"writer"}],"scopes":["draft"]}"#,
                "agent-other",
            ),
        ] {
            db.execute(DbStatement::with_params(
                "INSERT INTO bcs_bots (bot_uuid, env, name, bot_info, agent_code) \
                 VALUES (?, ?, ?, ?, ?)",
                vec![
                    DbValue::from(bot_uuid),
                    DbValue::from(env.as_str()),
                    DbValue::from(name),
                    DbValue::from(bot_info),
                    DbValue::from(agent_code),
                ],
            ))
            .await
            .expect("insert bot row");
            store
                .insert_binding(ProviderBotBinding {
                    bot_uuid: bot_uuid.to_string(),
                    provider_id: "provider-search".to_string(),
                    provider_bot_ref: format!("ref-{bot_uuid}"),
                    disabled: false,
                    created_at: 0,
                    updated_at: 0,
                })
                .await
                .expect("insert binding");
        }

        let query_records = store
            .list_discoverable_provider_bot_records(&ProviderBotDiscoverySelector::Query(
                "needle".to_string(),
            ))
            .await
            .expect("query records");
        assert_eq!(query_records.len(), 1);
        assert_eq!(query_records[0].bot_uuid, "bot-query");

        let name_records = store
            .list_discoverable_provider_bot_records(&ProviderBotDiscoverySelector::Name(
                "skill".to_string(),
            ))
            .await
            .expect("name records");
        assert_eq!(name_records.len(), 1);
        assert_eq!(name_records[0].bot_uuid, "bot-skill");

        let skill_records = store
            .list_discoverable_provider_bot_records(&ProviderBotDiscoverySelector::Skills(vec![
                "sql".to_string(),
                "ops".to_string(),
            ]))
            .await
            .expect("skill records");
        assert_eq!(skill_records.len(), 1);
        assert_eq!(skill_records[0].bot_uuid, "bot-skill");

        let agent_code_records = store
            .list_discoverable_provider_bot_records(&ProviderBotDiscoverySelector::Query(
                "agent-other".to_string(),
            ))
            .await
            .expect("agent code query records");
        assert!(
            agent_code_records.is_empty(),
            "provider discover SQL must not treat agent_code as a -q search field"
        );
    }
}
