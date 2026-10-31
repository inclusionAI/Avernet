#[path = "provider_db.rs"]
mod db;
use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::Arc;

#[path = "bot_provider_storage.rs"]
mod bot_storage;
#[path = "collaboration_actor_storage.rs"]
mod collaboration_actor_storage;
#[path = "affiliation_scope.rs"]
mod affiliation_scope;
#[path = "memory_bot_provider_storage.rs"]
mod memory_bot_storage;
pub use memory_bot_storage::MemoryBotProviderStore;
#[path = "provider_binding_projection.rs"]
mod binding_projection;
pub use binding_projection::ProviderBindingProjection;

use async_trait::async_trait;
use tokio::sync::RwLock;
use tracing::warn;

use bcs_config::resolve_env_str as resolve_env;
use bcs_db_api::{DbError, DbPlugin, DbRow, DbSqlFlavor, DbStatement, DbValue};
use bcs_service_api::{
    BotCapabilities, OrganizationCandidateReadPage, OrganizationCandidateReadPort,
    OrganizationCandidateReadQuery, ProviderBotBinding, ProviderBotBindingRepoPort,
    ProviderBotDiscoveryRecord, ProviderBotDiscoverySelector, ProviderCallerCapabilities,
    ProviderCredential, ProviderCredentialRepoPort, ProviderRecord, ProviderRepoPort, ServiceError,
    ServiceResult,
};

pub type ProviderSqlFlavor = DbSqlFlavor;

pub type MysqlProviderStore = DbProviderStore;
pub type SqliteProviderStore = DbProviderStore;

#[derive(Default)]
pub struct MemoryProviderStore {
    policy_gate: Arc<RwLock<()>>,
    group_scope_source: std::sync::RwLock<Option<(Arc<dyn bcs_service_api::port::repo::GroupRepoPort>, std::sync::Weak<dyn bcs_service_api::port::repo::bot_provider::BotProviderRepoPort>)>>,
    providers: RwLock<BTreeMap<String, ProviderRecord>>,
    credentials_by_kind: RwLock<BTreeMap<(String, String), ProviderCredential>>,
    credential_secret_index: RwLock<HashMap<(String, String), (String, String)>>,
    bindings_by_bot: RwLock<BTreeMap<String, ProviderBotBinding>>,
    binding_ref_index: RwLock<HashMap<(String, String), String>>,
}

impl MemoryProviderStore {
    pub fn policy_gate(&self) -> Arc<RwLock<()>> { self.policy_gate.clone() }
    pub fn bind_group_scope_source(&self, groups: Arc<dyn bcs_service_api::port::repo::GroupRepoPort>, bots: std::sync::Weak<dyn bcs_service_api::port::repo::bot_provider::BotProviderRepoPort>) {
        *self.group_scope_source.write().expect("group scope source lock") = Some((groups, bots));
    }
    pub fn new() -> Self {
        Self::default()
    }
}

#[async_trait]
impl OrganizationCandidateReadPort for MemoryProviderStore {}

#[async_trait]
impl ProviderRepoPort for MemoryProviderStore {
    async fn get_caller_capabilities(
        &self,
        provider_id: &str,
    ) -> ServiceResult<Option<ProviderCallerCapabilities>> {
        // Same closed semantics as the SQL store: missing providers are
        // ProviderNotFound, disabled providers are unavailable, undeclared is
        // Ok(None), and broken declarations never echo raw config.
        let provider = self
            .providers
            .read()
            .await
            .get(provider_id)
            .cloned()
            .ok_or_else(|| ServiceError::ProviderNotFound(provider_id.to_string()))?;
        if provider.disabled {
            return Err(caller_capabilities_unavailable());
        }
        let root: serde_json::Value = serde_json::from_str(&provider.config)
            .map_err(|_| invalid_provider_caller_capabilities())?;
        if !root.is_object() {
            return Err(invalid_provider_caller_capabilities());
        }
        match root.get("caller_capabilities") {
            None => Ok(None),
            Some(serde_json::Value::Null) => Err(invalid_provider_caller_capabilities()),
            Some(value) => serde_json::from_value::<ProviderCallerCapabilities>(value.clone())
                .map(Some)
                .map_err(|_| invalid_provider_caller_capabilities()),
        }
    }

    async fn insert_provider(&self, provider: ProviderRecord) -> ServiceResult<()> {
        let mut providers = self.providers.write().await;
        if providers.contains_key(&provider.provider_id) {
            return Err(ServiceError::InvalidOperation {
                message: format!("provider '{}' already exists", provider.provider_id),
                request_id: None,
            });
        }
        if let Some(slug) = provider.slug.as_deref() {
            if providers.values().any(|existing| existing.slug.as_deref() == Some(slug)) {
                return Err(ServiceError::Conflict(format!("provider slug '{slug}' already exists")));
            }
        }
        providers.insert(provider.provider_id.clone(), provider);
        Ok(())
    }

    async fn get_provider(&self, provider_id: &str) -> ServiceResult<Option<ProviderRecord>> {
        Ok(self.providers.read().await.get(provider_id).cloned())
    }

    async fn get_provider_by_slug(&self, slug: &str) -> ServiceResult<Option<ProviderRecord>> {
        Ok(self.providers.read().await.values()
            .find(|provider| provider.slug.as_deref() == Some(slug)).cloned())
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
        slug: Option<&str>,
        updated_at: u64,
    ) -> ServiceResult<Option<ProviderRecord>> {
        let _policy_write = self.policy_gate.write().await;
        let mut providers = self.providers.write().await;
        if !providers.contains_key(provider_id) { return Ok(None); }
        if let Some(slug) = slug {
            if providers.values().any(|existing| existing.provider_id != provider_id
                && existing.slug.as_deref() == Some(slug)) {
                return Err(ServiceError::Conflict(format!("provider slug '{slug}' already exists")));
            }
        }
        let Some(provider) = providers.get_mut(provider_id) else {
            return Ok(None);
        };
        if let Some(config) = config {
            reject_policy_relaxation(&provider.config, config)?;
            if bcs_service_api::ProviderCollaborationScope::from_config(&provider.config)? == bcs_service_api::ProviderCollaborationScope::Standard
                && bcs_service_api::ProviderCollaborationScope::from_config(config)? == bcs_service_api::ProviderCollaborationScope::SameOwner {
                let source = self.group_scope_source.read().map_err(|_| ServiceError::InternalError("group scope source lock failed".into()))?.clone();
                let (groups, metadata) = source.ok_or_else(|| ServiceError::InternalError("public group scope lookup is not configured".into()))?;
                {
                    let metadata = metadata.upgrade().ok_or_else(|| ServiceError::InternalError("Bot Provider metadata is unavailable".into()))?;
                    let ids = metadata.list_provider_bot_metadata(Some(provider_id)).await?.into_iter().filter(|b| !b.is_deleted).map(|b| b.bot_uuid).collect::<Vec<_>>();
                    if groups.has_public_group_with_members(&ids).await? {
                        return Err(ServiceError::Conflict("Provider has public groups; change them to private before enabling same_owner".into()));
                    }
                }
            }
        }
        if let Some(name) = name {
            provider.name = name.to_string();
        }
        if let Some(config) = config {
            provider.config = config.to_string();
        }
        if let Some(slug) = slug {
            provider.slug = Some(slug.to_string());
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
    async fn update_binding_webhook_url(
        &self, provider_id: &str, bot_uuid: &str, webhook_url: Option<&str>, updated_at: u64,
    ) -> ServiceResult<Option<ProviderBotBinding>> {
        let mut bindings = self.bindings_by_bot.write().await;
        let Some(binding) = bindings.get_mut(bot_uuid).filter(|b| b.provider_id == provider_id) else {
            return Ok(None);
        };
        binding.webhook_url = webhook_url.map(str::to_string);
        binding.updated_at = updated_at;
        Ok(Some(binding.clone()))
    }

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
                    capabilities: None,
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
    providers: crate::provider_cache::ProviderCache<ProviderRecord>,
    bindings: crate::provider_cache::ProviderCache<ProviderBotBinding>,
    credentials: crate::provider_cache::ProviderCache<ProviderCredential>,
}

impl DbProviderStore {
    pub fn new(db: Arc<dyn DbPlugin>, flavor: ProviderSqlFlavor) -> Self {
        Self { db, flavor,
            providers: crate::provider_cache::ProviderCache::new(1024, std::time::Duration::from_secs(30)),
            bindings: crate::provider_cache::ProviderCache::new(4096, std::time::Duration::from_secs(30)),
            credentials: crate::provider_cache::ProviderCache::new(1024, std::time::Duration::from_secs(30)),
        }
    }

    pub fn mysql(db: Arc<dyn DbPlugin>) -> Self {
        Self::new(db, ProviderSqlFlavor::Mysql)
    }

    pub fn sqlite(db: Arc<dyn DbPlugin>) -> Self {
        Self::new(db, ProviderSqlFlavor::Sqlite)
    }

    fn insert_provider_sql() -> &'static str {
        "INSERT INTO bcs_providers \
         (provider_id, env, name, config, disabled, created_by, owners, slug) \
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    }

    fn insert_credential_sql() -> &'static str {
        "INSERT INTO bcs_provider_credentials \
         (provider_id, env, credential_kind, secret_value, disabled) \
         VALUES (?, ?, ?, ?, ?)"
    }

    fn insert_binding_sql() -> &'static str {
        "INSERT INTO bcs_provider_bot_bindings \
         (bot_uuid, provider_id, provider_bot_ref, env, disabled, webhook_url) \
         VALUES (?, ?, ?, ?, ?, ?)"
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
                warn!(request_id = %bcs_observability::CurrentRequestId, operation, error = %err, "db_provider: execute failed");
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
            warn!(request_id = %bcs_observability::CurrentRequestId, operation, error = %err, "db_provider: query failed");
            service_db_error(operation, err)
        })
    }
}

#[async_trait]
impl ProviderBotBindingRepoPort for DbProviderStore {
    async fn update_binding_webhook_url(
        &self, provider_id: &str, bot_uuid: &str, webhook_url: Option<&str>, _updated_at: u64,
    ) -> ServiceResult<Option<ProviderBotBinding>> {
        let env = resolve_env();
        let sql = format!(
            "UPDATE bcs_provider_bot_bindings SET webhook_url = ?, {now_clause} \
             WHERE provider_id = ? AND bot_uuid = ? AND env = ?",
            now_clause = self.now_modified_clause(),
        );
        self.execute("update_provider_binding_webhook", DbStatement::with_params(sql, vec![
            webhook_url.map(DbValue::from).unwrap_or(DbValue::Null),
            DbValue::from(provider_id), DbValue::from(bot_uuid), DbValue::from(env.as_str()),
        ])).await?;
        self.bindings.invalidate(&format!("{env}:{bot_uuid}"));
        let binding = self.get_binding_by_bot_uuid(bot_uuid).await?;
        Ok(binding.filter(|b| b.provider_id == provider_id))
    }

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
                    binding.webhook_url.as_deref().map(DbValue::from).unwrap_or(DbValue::Null),
                ],
            ),
        )
        .await?;
        self.bindings.invalidate(&format!("{}:{}", env, binding.bot_uuid));
        Ok(())
    }

    async fn get_binding_by_bot_uuid(
        &self,
        bot_uuid: &str,
    ) -> ServiceResult<Option<ProviderBotBinding>> {
        let env = resolve_env();
        self.bindings.get(&format!("{env}:{bot_uuid}"), || async {
        let sql = format!(
            "SELECT bot_uuid, provider_id, provider_bot_ref, webhook_url, disabled, {ts} \
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
        rows.first().map(|row| parse_binding(row).ok_or_else(|| ServiceError::InternalError("invalid provider binding".into()))).transpose()
        }).await
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
            "SELECT bot_uuid, provider_id, provider_bot_ref, webhook_url, disabled, {ts} \
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
            "SELECT bot_uuid, provider_id, provider_bot_ref, webhook_url, disabled, {ts} \
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
            "SELECT bot_uuid, provider_id, provider_bot_ref, webhook_url, disabled, {ts} \
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
            SELECT pb.bot_uuid, pb.provider_id, p.name AS provider_name, b.name AS bot_name, b.bot_info \
            FROM bcs_provider_bot_bindings pb \
            JOIN bcs_providers p \
              ON p.provider_id = pb.provider_id \
             AND p.env = pb.env \
            JOIN bcs_bots b \
              ON b.bot_uuid = pb.bot_uuid \
             AND b.env = pb.env \
            WHERE pb.env = ? \
              AND pb.disabled = 0 \
              AND p.disabled = 0 \
              AND b.is_deleted = 0 \
              AND b.actor_kind = 'bot'"
            .to_string();
        let mut params = vec![DbValue::from(env.as_str())];
        append_provider_discovery_selector_sql(self.flavor, selector, &mut sql, &mut params);
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
        self.bindings.invalidate(&format!("{env}:{bot_uuid}"));
        self.get_binding_by_bot_uuid(bot_uuid).await
    }
}

fn parse_provider(row: &DbRow) -> ServiceResult<ProviderRecord> {
    let required = |column| optional_string(row, column)
        .ok_or_else(|| ServiceError::InternalError("invalid provider record".into()));
    Ok(ProviderRecord {
        provider_id: required("provider_id")?,
        slug: row.get_string("slug").map_err(|err| service_db_error("read_provider_slug", err))?,
        name: required("name")?,
        config: required("config")?,
        created_by: required("created_by")?,
        owners: required("owners")?,
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
        webhook_url: optional_string(row, "webhook_url"),
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
        capabilities: parse_provider_bot_capabilities(row),
    })
}

fn parse_provider_bot_capabilities(row: &DbRow) -> Option<BotCapabilities> {
    let mut capabilities = optional_string(row, "bot_info")
        .and_then(|bot_info| serde_json::from_str::<BotCapabilities>(&bot_info).ok())
        .unwrap_or_default();
    if let Some(name) = optional_string(row, "bot_name") {
        capabilities.name = Some(name);
    }
    Some(capabilities)
}

fn append_provider_discovery_selector_sql(
    flavor: DbSqlFlavor,
    selector: &ProviderBotDiscoverySelector,
    sql: &mut String,
    params: &mut Vec<DbValue>,
) {
    match selector {
        ProviderBotDiscoverySelector::All => {}
        ProviderBotDiscoverySelector::ProviderIds(provider_ids) => {
            if provider_ids.is_empty() {
                sql.push_str(" AND 1 = 0");
            } else {
                sql.push_str(" AND pb.provider_id IN (");
                sql.push_str(&provider_ids.iter().map(|_| "?").collect::<Vec<_>>().join(", "));
                sql.push(')');
                params.extend(provider_ids.iter().map(|provider_id| DbValue::from(provider_id.as_str())));
            }
        }
        ProviderBotDiscoverySelector::Query(query) => {
            let pattern = like_pattern(query);
            let (card_sql, card_params) = crate::a2a_search::card_text_match(flavor, "b.agent_card", query);
            sql.push_str(&format!(" AND (LOWER(pb.bot_uuid) LIKE ? OR \
                (b.agent_card IS NULL AND (LOWER(b.name) LIKE ? OR LOWER(COALESCE(b.bot_info, '')) LIKE ?)) OR \
                (b.agent_card IS NOT NULL AND ({card_sql})))"));
            for _ in 0..3 {
                params.push(DbValue::from(pattern.as_str()));
            }
            params.extend(card_params);
        }
        ProviderBotDiscoverySelector::RequiredSkills(skills) => {
            for skill in skills {
                let (card_sql, card_params) = crate::a2a_search::card_skill_match(flavor, "b.agent_card", skill);
                sql.push_str(&format!(" AND ((b.agent_card IS NULL AND LOWER(COALESCE(b.bot_info, '')) LIKE ?) OR \
                    (b.agent_card IS NOT NULL AND ({card_sql})))"));
                let pattern = like_pattern(skill);
                params.push(DbValue::from(pattern.as_str()));
                params.extend(card_params);
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
    if err.is_duplicate_key() && matches!(operation, "insert_provider" | "update_provider_metadata") {
        return ServiceError::Conflict("provider slug or identity already exists".to_string());
    }
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
#[path = "provider_tests.rs"]
mod tests;

fn reject_policy_relaxation(current: &str, incoming: &str) -> ServiceResult<()> {
    use bcs_service_api::ProviderCollaborationScope;
    if ProviderCollaborationScope::from_config(current)? == ProviderCollaborationScope::SameOwner
        && ProviderCollaborationScope::from_config(incoming)? != ProviderCollaborationScope::SameOwner {
        return Err(ServiceError::Forbidden("same_owner Provider policy cannot be relaxed".into()));
    }
    Ok(())
}

impl std::fmt::Debug for MemoryProviderStore {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result { f.debug_struct("MemoryProviderStore").finish_non_exhaustive() }
}

/// Closed error for a caller-capability projection that exists but is
/// unusable. The message is fixed: raw config never leaks into errors.
fn invalid_provider_caller_capabilities() -> ServiceError {
    ServiceError::InvalidOperation {
        message: "invalid provider caller capabilities".to_string(),
        request_id: None,
    }
}

/// Closed error for a provider that exists but is disabled; distinct from
/// "never declared" at the storage seam.
fn caller_capabilities_unavailable() -> ServiceError {
    ServiceError::InvalidOperation {
        message: "caller capabilities unavailable: provider is disabled".to_string(),
        request_id: None,
    }
}
