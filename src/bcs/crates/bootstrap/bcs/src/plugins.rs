//! Infrastructure plugin selection for the public BCS composition root.
//!
//! Public builds wire the configured cache and database adapters.

use std::fmt;
use std::sync::Arc;

use bcs_cache_api::CachePlugin;
use bcs_cache_local::InMemoryCachePlugin;
use bcs_cache_redis::RedisCachePlugin;
use bcs_channel_api::ChannelProvider;
use bcs_config_api::{LeaderElectionProviderConfig, RedisCacheConfig};
use bcs_db_api::DbPlugin;
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_db_mysql::{MysqlDbManager, MysqlDbPlugin};
use bcs_llm_api::LlmChatCompletionPort;
use bcs_security_gateway_api::SecurityGatewayPort;
use bcs_service_api::LeaderElectionPort;
use bcs_service_api::lifecycle::ServiceLifecycle;
use bcs_service_api::port::repo::ChannelBindingRepoPort;
use bcs_user_directory_api::UserDirectoryPlugin;
use futures::future::BoxFuture;

use crate::config::{
    BcsConfig, DatabaseType, SecurityGatewayProviderConfig, UserDirectoryProviderConfig,
};

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CachePluginKind {
    LocalMemory,
    Redis,
    External(String),
}

impl fmt::Display for CachePluginKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::LocalMemory => f.write_str("memory"),
            Self::Redis => f.write_str("redis"),
            Self::External(provider) => f.write_str(provider),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DbPluginKind {
    LocalSqlite,
    Mysql,
    External(String),
}

impl fmt::Display for DbPluginKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::LocalSqlite => f.write_str("local-sqlite"),
            Self::Mysql => f.write_str("mysql"),
            Self::External(provider) => f.write_str(provider),
        }
    }
}

#[derive(Clone)]
pub struct CachePluginRegistration {
    pub kind: CachePluginKind,
    pub plugin: Arc<dyn CachePlugin>,
}

pub type CachePluginBuild =
    fn(BcsConfig) -> BoxFuture<'static, crate::Result<CachePluginRegistration>>;

pub struct CachePluginFactory {
    pub name: &'static str,
    pub build: CachePluginBuild,
}

inventory::collect!(CachePluginFactory);

#[derive(Clone)]
pub struct DbPluginRegistration {
    pub kind: DbPluginKind,
    pub plugin: Arc<dyn DbPlugin>,
}

pub type DbPluginBuild = fn(BcsConfig) -> BoxFuture<'static, crate::Result<DbPluginRegistration>>;

pub struct DbPluginFactory {
    pub name: &'static str,
    pub build: DbPluginBuild,
}

inventory::collect!(DbPluginFactory);

pub type LlmProviderBuild = fn(BcsConfig) -> crate::Result<Arc<dyn LlmChatCompletionPort>>;

pub struct LlmProviderFactory {
    pub name: &'static str,
    pub build: LlmProviderBuild,
}

inventory::collect!(LlmProviderFactory);

#[derive(Clone)]
pub struct LeaderElectionRegistration {
    pub leader: Arc<dyn LeaderElectionPort>,
    pub lifecycle: Option<Arc<dyn ServiceLifecycle>>,
}

pub type LeaderElectionBuild = fn(
    BcsConfig,
    LeaderElectionProviderConfig,
) -> BoxFuture<'static, crate::Result<LeaderElectionRegistration>>;

pub struct LeaderElectionFactory {
    pub name: &'static str,
    pub build: LeaderElectionBuild,
}

inventory::collect!(LeaderElectionFactory);

#[derive(Clone)]
pub struct SecurityGatewayRegistration {
    pub gateway: Arc<dyn SecurityGatewayPort>,
}

pub type SecurityGatewayBuild =
    fn(BcsConfig, SecurityGatewayProviderConfig) -> crate::Result<SecurityGatewayRegistration>;

pub struct SecurityGatewayFactory {
    pub name: &'static str,
    pub build: SecurityGatewayBuild,
}

inventory::collect!(SecurityGatewayFactory);

#[derive(Clone)]
pub struct UserDirectoryRegistration {
    pub plugin: Arc<dyn UserDirectoryPlugin>,
}

pub type UserDirectoryBuild =
    fn(BcsConfig, UserDirectoryProviderConfig) -> crate::Result<UserDirectoryRegistration>;

pub struct UserDirectoryFactory {
    pub name: &'static str,
    pub build: UserDirectoryBuild,
}

inventory::collect!(UserDirectoryFactory);

#[derive(Clone)]
pub struct ChannelProviderBuildContext {
    pub config: BcsConfig,
    pub provider_name: String,
    pub provider_config: bcs_config_api::ChannelProviderConfig,
    pub channel_bindings: Arc<dyn ChannelBindingRepoPort>,
    pub now_ms: Arc<dyn Fn() -> u64 + Send + Sync>,
}

pub type ChannelProviderBuild =
    fn(ChannelProviderBuildContext) -> crate::Result<Arc<dyn ChannelProvider>>;

pub struct ChannelProviderFactory {
    pub name: &'static str,
    pub build: ChannelProviderBuild,
}

inventory::collect!(ChannelProviderFactory);

async fn build_registered_cache_plugin(
    config: &BcsConfig,
    provider: &str,
) -> crate::Result<Option<CachePluginRegistration>> {
    for factory in inventory::iter::<CachePluginFactory> {
        if factory.name == provider {
            return Ok(Some((factory.build)(config.clone()).await?));
        }
    }
    Ok(None)
}

async fn build_registered_db_plugin(
    config: &BcsConfig,
    provider: &str,
) -> crate::Result<Option<DbPluginRegistration>> {
    for factory in inventory::iter::<DbPluginFactory> {
        if factory.name == provider {
            return Ok(Some((factory.build)(config.clone()).await?));
        }
    }
    Ok(None)
}

pub fn build_registered_llm_provider(
    config: &BcsConfig,
    provider: &str,
) -> crate::Result<Option<Arc<dyn LlmChatCompletionPort>>> {
    for factory in inventory::iter::<LlmProviderFactory> {
        if factory.name == provider {
            return Ok(Some((factory.build)(config.clone())?));
        }
    }
    Ok(None)
}

pub async fn build_registered_leader_election(
    config: &BcsConfig,
    provider: &str,
    provider_config: LeaderElectionProviderConfig,
) -> crate::Result<Option<LeaderElectionRegistration>> {
    for factory in inventory::iter::<LeaderElectionFactory> {
        if factory.name == provider {
            return Ok(Some((factory.build)(config.clone(), provider_config).await?));
        }
    }
    Ok(None)
}

pub fn build_registered_security_gateway(
    config: &BcsConfig,
    provider: &str,
    provider_config: SecurityGatewayProviderConfig,
) -> crate::Result<Option<SecurityGatewayRegistration>> {
    for factory in inventory::iter::<SecurityGatewayFactory> {
        if factory.name == provider {
            return Ok(Some((factory.build)(config.clone(), provider_config)?));
        }
    }
    Ok(None)
}

pub fn build_registered_user_directory(
    config: &BcsConfig,
    provider: &str,
    provider_config: UserDirectoryProviderConfig,
) -> crate::Result<Option<UserDirectoryRegistration>> {
    for factory in inventory::iter::<UserDirectoryFactory> {
        if factory.name == provider {
            return Ok(Some((factory.build)(config.clone(), provider_config)?));
        }
    }
    Ok(None)
}

pub fn build_registered_channel_provider(
    config: &BcsConfig,
    provider: &str,
    provider_config: bcs_config_api::ChannelProviderConfig,
    channel_bindings: Arc<dyn ChannelBindingRepoPort>,
    now_ms: Arc<dyn Fn() -> u64 + Send + Sync>,
) -> crate::Result<Option<Arc<dyn ChannelProvider>>> {
    for factory in inventory::iter::<ChannelProviderFactory> {
        if factory.name == provider {
            return Ok(Some((factory.build)(ChannelProviderBuildContext {
                config: config.clone(),
                provider_name: provider.to_string(),
                provider_config,
                channel_bindings,
                now_ms,
            })?));
        }
    }
    Ok(None)
}

fn resolve_sqlite_path(config: &BcsConfig) -> String {
    let raw = config.database.sqlite.path.as_str();
    let p = std::path::Path::new(raw);
    if p.is_relative() {
        if let Ok(data_dir) = std::env::var("BCS_DATA_DIR") {
            return std::path::Path::new(&data_dir).join(raw).to_string_lossy().to_string();
        }
    }
    raw.to_string()
}

fn resolve_cache_type(config: &BcsConfig) -> &str {
    config.cache.cache_type.trim()
}

fn resolve_redis_config(config: &BcsConfig) -> crate::Result<RedisCacheConfig> {
    Ok(config.cache.redis.to_runtime_redis_config())
}

#[derive(Clone)]
pub struct InfrastructurePlugins {
    cache_kind: CachePluginKind,
    db_kind: DbPluginKind,
    cache: Option<Arc<dyn CachePlugin>>,
    db: Option<Arc<dyn DbPlugin>>,
}

impl fmt::Debug for InfrastructurePlugins {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("InfrastructurePlugins")
            .field("cache_kind", &self.cache_kind)
            .field("db_kind", &self.db_kind)
            .field("cache_ready", &self.cache.is_some())
            .field("db_ready", &self.db.is_some())
            .finish()
    }
}

impl InfrastructurePlugins {
    pub fn from_parts(
        cache_kind: CachePluginKind,
        db_kind: DbPluginKind,
        cache: Arc<dyn CachePlugin>,
        db: Arc<dyn DbPlugin>,
    ) -> Self {
        Self {
            cache_kind,
            db_kind,
            cache: Some(cache),
            db: Some(db),
        }
    }

    pub async fn from_config(config: &BcsConfig) -> crate::Result<Self> {
        let (cache_kind, cache): (CachePluginKind, Arc<dyn CachePlugin>) =
            if config.cache.is_configured() {
                let cache_type = resolve_cache_type(config);
                match cache_type {
                    "memory" => (
                        CachePluginKind::LocalMemory,
                        Arc::new(InMemoryCachePlugin::new()),
                    ),
                    "redis" => {
                        let redis = &config.cache.redis;
                        if redis.connection.connection_type == "direct" {
                            let redis_config = resolve_redis_config(config)?;
                            let redis = RedisCachePlugin::connect(redis_config)
                                .await
                                .map_err(|err| {
                                    crate::BcsError::StorageInitError(format!(
                                        "init redis cache plugin: {}",
                                        err
                                    ))
                                })?;
                            (CachePluginKind::Redis, Arc::new(redis))
                        } else if let Some(registration) = build_registered_cache_plugin(
                            config,
                            &redis.connection.connection_type,
                        )
                        .await?
                        {
                            (registration.kind, registration.plugin)
                        } else {
                            return Err(crate::BcsError::StorageInitError(format!(
                                "cache.redis.connection.type = '{}' is not available in this binary",
                                redis.connection.connection_type
                            )));
                        }
                    }
                    other => {
                        if let Some(registration) = build_registered_cache_plugin(config, other).await?
                        {
                            (registration.kind, registration.plugin)
                        } else {
                            return Err(crate::BcsError::StorageInitError(format!(
                                "cache.type = '{}' is not available in this binary",
                                other
                            )));
                        }
                    }
                }
            } else {
                (
                    CachePluginKind::LocalMemory,
                    Arc::new(InMemoryCachePlugin::new()),
                )
            };

        if cache_kind == CachePluginKind::Redis {
            tracing::info!(
                "Redis-compatible cache enabled; bot status and cache-backed features use Redis"
            );
        }

        let (db_kind, db): (DbPluginKind, Arc<dyn DbPlugin>) = match &config.database.database_type {
            DatabaseType::Sqlite => {
                let path = resolve_sqlite_path(config);
                let db = LocalSqliteDbPlugin::new_file(&path).map_err(|err| {
                    crate::BcsError::StorageInitError(format!(
                        "init local db plugin at '{}': {}",
                        path, err
                    ))
                })?;
                (DbPluginKind::LocalSqlite, Arc::new(db))
            }
            DatabaseType::Mysql => {
                let mysql = &config.database.mysql;
                let datasource_name = mysql.datasource_name();
                if mysql.connection.connection_type != "direct" {
                    if let Some(registration) = build_registered_db_plugin(
                        config,
                        &mysql.connection.connection_type,
                    )
                    .await?
                    {
                        (registration.kind, registration.plugin)
                    } else {
                        return Err(crate::BcsError::StorageInitError(format!(
                            "database.mysql.connection.type = '{}' is not available in this binary",
                            mysql.connection.connection_type
                        )));
                    }
                } else {
                    let manager = MysqlDbManager::new(mysql.clone()).await.map_err(|err| {
                        crate::BcsError::StorageInitError(format!(
                            "init mysql db plugin for datasource '{}': {}",
                            datasource_name, err
                        ))
                    })?;
                    if !manager.is_enabled() {
                        return Err(crate::BcsError::StorageInitError(
                            "database.type = 'mysql' selected but mysql manager is disabled"
                                .to_string(),
                        ));
                    }
                    (
                        DbPluginKind::Mysql,
                        Arc::new(MysqlDbPlugin::new(manager, datasource_name)),
                    )
                }
            }
            DatabaseType::Other(provider) => {
                if let Some(registration) = build_registered_db_plugin(config, provider).await? {
                    (registration.kind, registration.plugin)
                } else {
                    return Err(crate::BcsError::StorageInitError(format!(
                        "database.type = '{}' is not available in this binary",
                        provider
                    )));
                }
            }
        };

        Ok(Self {
            cache_kind,
            db_kind,
            cache: Some(cache),
            db: Some(db),
        })
    }

    pub fn local_for_tests() -> Self {
        let db = match LocalSqliteDbPlugin::new() {
            Ok(db) => db,
            Err(err) => panic!("init local db plugin for tests: {}", err),
        };
        Self {
            cache_kind: CachePluginKind::LocalMemory,
            db_kind: DbPluginKind::LocalSqlite,
            cache: Some(Arc::new(InMemoryCachePlugin::new())),
            db: Some(Arc::new(db)),
        }
    }

    pub fn cache_kind(&self) -> CachePluginKind {
        self.cache_kind.clone()
    }

    pub fn db_kind(&self) -> DbPluginKind {
        self.db_kind.clone()
    }

    pub fn cache(&self) -> Option<Arc<dyn CachePlugin>> {
        self.cache.clone()
    }

    pub fn db(&self) -> Option<Arc<dyn DbPlugin>> {
        self.db.clone()
    }
}

pub fn select_cache_plugin_kind(config: &BcsConfig) -> CachePluginKind {
    if config.cache.is_configured() {
        match resolve_cache_type(config) {
            "memory" => CachePluginKind::LocalMemory,
            "redis" => CachePluginKind::Redis,
            other => CachePluginKind::External(other.to_string()),
        }
    } else {
        CachePluginKind::LocalMemory
    }
}

pub fn select_db_plugin_kind(config: &BcsConfig) -> DbPluginKind {
    match &config.database.database_type {
        DatabaseType::Sqlite => DbPluginKind::LocalSqlite,
        DatabaseType::Mysql => DbPluginKind::Mysql,
        DatabaseType::Other(provider) => DbPluginKind::External(provider.clone()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use async_trait::async_trait;
    use bcs_channel_api::{
        ChannelHttpIngressPort, ChannelInboundSink, ChannelProvider, ChannelProviderResult,
    };
    use bcs_channel_store::MemoryChannelBindingRepo;
    use bcs_service_api::ServiceResult;
    use bcs_service_api::lifecycle::ServiceLifecycle;
    use bcs_service_api::port::channel_delivery::{
        ChannelBindingRef, ChannelDeliveryPort, ChannelDeliveryResult, ChannelOutboundEvent,
    };
    use bcs_service_api::port::repo::ChannelBindingRepoPort;

    fn test_cache_factory(
        _config: BcsConfig,
    ) -> BoxFuture<'static, crate::Result<CachePluginRegistration>> {
        Box::pin(async move {
            Ok(CachePluginRegistration {
                kind: CachePluginKind::External("test-external-cache".to_string()),
                plugin: Arc::new(InMemoryCachePlugin::new()),
            })
        })
    }

    inventory::submit! {
        CachePluginFactory {
            name: "test-external-cache",
            build: test_cache_factory,
        }
    }

    fn test_leader_factory(
        _config: BcsConfig,
        provider_config: LeaderElectionProviderConfig,
    ) -> BoxFuture<'static, crate::Result<LeaderElectionRegistration>> {
        Box::pin(async move {
            if provider_config.get("zone").and_then(|value| value.as_str()) != Some("blue") {
                return Err(crate::BcsError::InvalidConfig(
                    "expected leader provider option zone = blue".to_string(),
                ));
            }

            Ok(LeaderElectionRegistration {
                leader: Arc::new(bcs_leader_election::StandaloneLeaderElection::local()),
                lifecycle: None,
            })
        })
    }

    inventory::submit! {
        LeaderElectionFactory {
            name: "test-leader-options",
            build: test_leader_factory,
        }
    }

    fn test_security_gateway_factory(
        _config: BcsConfig,
        provider_config: SecurityGatewayProviderConfig,
    ) -> crate::Result<SecurityGatewayRegistration> {
        if provider_config.get("mode").and_then(|value| value.as_str()) != Some("allow") {
            return Err(crate::BcsError::InvalidConfig(
                "expected security gateway provider option mode = allow".to_string(),
            ));
        }

        Ok(SecurityGatewayRegistration {
            gateway: Arc::new(bcs_security_gateway_local::NoopSecurityGateway),
        })
    }

    inventory::submit! {
        SecurityGatewayFactory {
            name: "test-security-options",
            build: test_security_gateway_factory,
        }
    }

    struct TestUserDirectoryPlugin;

    #[async_trait::async_trait]
    impl UserDirectoryPlugin for TestUserDirectoryPlugin {
        async fn lookup_by_staff_no(
            &self,
            staff_no: &str,
        ) -> Result<
            Option<bcs_user_directory_api::UserDirectoryProfile>,
            bcs_user_directory_api::UserDirectoryError,
        > {
            Ok(Some(bcs_user_directory_api::UserDirectoryProfile {
                staff_no: staff_no.to_string(),
                nick_name: Some("test-user".to_string()),
            }))
        }
    }

    fn test_user_directory_factory(
        _config: BcsConfig,
        provider_config: UserDirectoryProviderConfig,
    ) -> crate::Result<UserDirectoryRegistration> {
        if provider_config.get("source").and_then(|value| value.as_str()) != Some("unit") {
            return Err(crate::BcsError::InvalidConfig(
                "expected user directory provider option source = unit".to_string(),
            ));
        }

        Ok(UserDirectoryRegistration {
            plugin: Arc::new(TestUserDirectoryPlugin),
        })
    }

    inventory::submit! {
        UserDirectoryFactory {
            name: "test-user-directory-options",
            build: test_user_directory_factory,
        }
    }

    fn test_channel_provider_factory(
        ctx: ChannelProviderBuildContext,
    ) -> crate::Result<Arc<dyn ChannelProvider>> {
        if ctx.provider_name != "test-channel-provider" {
            return Err(crate::BcsError::InvalidConfig(
                "unexpected channel provider name".to_string(),
            ));
        }
        if ctx
            .provider_config
            .options
            .get("mode")
            .and_then(|value| value.as_str())
            != Some("lab")
        {
            return Err(crate::BcsError::InvalidConfig(
                "expected provider option mode = lab".to_string(),
            ));
        }
        Ok(Arc::new(TestChannelProvider))
    }

    inventory::submit! {
        ChannelProviderFactory {
            name: "test-channel-provider",
            build: test_channel_provider_factory,
        }
    }

    struct TestChannelProvider;

    #[async_trait]
    impl ChannelProvider for TestChannelProvider {
        fn channel_type(&self) -> &'static str {
            "test-channel"
        }

        fn validate_config(&self, _config: &serde_json::Value) -> ChannelProviderResult<()> {
            Ok(())
        }

        fn redact_config(&self, config: &serde_json::Value) -> serde_json::Value {
            config.clone()
        }

        fn delivery(&self) -> Arc<dyn ChannelDeliveryPort> {
            Arc::new(TestChannelDelivery)
        }

        fn http_ingress(&self) -> Option<Arc<dyn ChannelHttpIngressPort>> {
            None
        }

        fn stream_lifecycle(
            &self,
            _sink: Arc<dyn ChannelInboundSink>,
        ) -> Option<Arc<dyn ServiceLifecycle>> {
            None
        }
    }

    struct TestChannelDelivery;

    #[async_trait]
    impl ChannelDeliveryPort for TestChannelDelivery {
        async fn is_available(&self, _binding: &ChannelBindingRef) -> bool {
            true
        }

        async fn deliver_event(
            &self,
            _event: ChannelOutboundEvent,
        ) -> ServiceResult<ChannelDeliveryResult> {
            Ok(ChannelDeliveryResult {
                delivered: true,
                provider_message_ref: None,
                error: None,
            })
        }
    }

    #[test]
    fn default_config_selects_memory_cache_and_local_db_plugins() {
        let config = BcsConfig::default();

        assert_eq!(
            select_cache_plugin_kind(&config),
            CachePluginKind::LocalMemory
        );
        assert_eq!(select_db_plugin_kind(&config), DbPluginKind::LocalSqlite);
    }

    #[test]
    fn mysql_database_type_selects_mysql_kind() {
        let mut config = BcsConfig::default();
        config.database.database_type = DatabaseType::Mysql;

        assert_eq!(select_db_plugin_kind(&config), DbPluginKind::Mysql);
    }

    #[test]
    fn redis_cache_type_selects_redis_cache_kind() {
        let mut config = BcsConfig::default();
        config.cache.cache_type = "redis".to_string();

        assert_eq!(select_cache_plugin_kind(&config), CachePluginKind::Redis);
    }

    #[tokio::test]
    async fn registered_cache_factory_handles_non_direct_redis_connection() {
        let mut config = BcsConfig::default();
        config.cache.cache_type = "redis".to_string();
        config.cache.redis.connection.connection_type = "test-external-cache".to_string();

        let infrastructure = InfrastructurePlugins::from_config(&config)
            .await
            .expect("registered cache factory should build");

        assert_eq!(
            infrastructure.cache_kind(),
            CachePluginKind::External("test-external-cache".to_string())
        );
    }

    #[tokio::test]
    async fn memory_cache_ignores_redis_subconfig() {
        let mut config = BcsConfig::default();
        config.cache.cache_type = "memory".to_string();
        config.cache.redis.connection.host = Some("127.0.0.1".to_string());

        let infrastructure = InfrastructurePlugins::from_config(&config)
            .await
            .expect("inactive redis subconfig should not block memory cache");

        assert_eq!(infrastructure.cache_kind(), CachePluginKind::LocalMemory);
    }

    #[tokio::test]
    async fn registered_leader_factory_receives_provider_options() {
        let mut provider_config = LeaderElectionProviderConfig::new();
        provider_config.insert(
            "zone".to_string(),
            serde_json::Value::String("blue".to_string()),
        );

        let registration = build_registered_leader_election(
            &BcsConfig::default(),
            "test-leader-options",
            provider_config,
        )
        .await
        .expect("leader factory should build")
        .expect("leader factory should be registered");

        assert!(registration.leader.is_leader().await.expect("is leader"));
    }

    #[tokio::test]
    async fn registered_security_gateway_factory_receives_provider_options() {
        let mut provider_config = SecurityGatewayProviderConfig::new();
        provider_config.insert(
            "mode".to_string(),
            serde_json::Value::String("allow".to_string()),
        );

        let registration = build_registered_security_gateway(
            &BcsConfig::default(),
            "test-security-options",
            provider_config,
        )
        .expect("security gateway factory should build")
        .expect("security gateway factory should be registered");

        let request = bcs_security_gateway_api::SecurityCheckRequest {
            sender_bot_id: "sender".to_string(),
            receiver_bot_id: "receiver".to_string(),
            sender_agent_code: None,
            receiver_agent_code: None,
            agent_token: None,
            message_content: "hello".to_string(),
            message_id: "msg-1".to_string(),
        };
        assert!(matches!(
            registration.gateway.check(request).await,
            bcs_security_gateway_api::SecurityVerdict::Allow { task_id: None }
        ));
    }

    #[tokio::test]
    async fn registered_user_directory_factory_receives_provider_options() {
        let mut provider_config = UserDirectoryProviderConfig::new();
        provider_config.insert(
            "source".to_string(),
            serde_json::Value::String("unit".to_string()),
        );

        let registration = build_registered_user_directory(
            &BcsConfig::default(),
            "test-user-directory-options",
            provider_config,
        )
        .expect("user directory factory should build")
        .expect("user directory factory should be registered");

        let profile = registration
            .plugin
            .lookup_by_staff_no("197262")
            .await
            .expect("lookup should succeed")
            .expect("profile should be found");
        assert_eq!(profile.staff_no, "197262");
        assert_eq!(profile.nick_name.as_deref(), Some("test-user"));
    }

    #[test]
    fn registered_channel_provider_factory_receives_provider_context() {
        let channel_bindings: Arc<dyn ChannelBindingRepoPort> =
            Arc::new(MemoryChannelBindingRepo::new("test"));
        let mut provider_config = bcs_config_api::ChannelProviderConfig {
            enabled: true,
            ..Default::default()
        };
        provider_config.options.insert(
            "mode".to_string(),
            serde_json::Value::String("lab".to_string()),
        );

        let provider = build_registered_channel_provider(
            &BcsConfig::default(),
            "test-channel-provider",
            provider_config,
            channel_bindings,
            Arc::new(|| 42),
        )
        .expect("channel provider factory should build")
        .expect("channel provider factory should be registered");

        assert_eq!(provider.channel_type(), "test-channel");
    }

    #[test]
    fn from_parts_uses_external_plugin_handles() {
        let db = LocalSqliteDbPlugin::new().expect("local sqlite plugin");
        let cache: Arc<dyn CachePlugin> = Arc::new(InMemoryCachePlugin::new());
        let db: Arc<dyn DbPlugin> = Arc::new(db);

        let plugins = InfrastructurePlugins::from_parts(
            CachePluginKind::Redis,
            DbPluginKind::Mysql,
            cache.clone(),
            db.clone(),
        );

        assert_eq!(plugins.cache_kind(), CachePluginKind::Redis);
        assert_eq!(plugins.db_kind(), DbPluginKind::Mysql);
        assert!(Arc::ptr_eq(&plugins.cache().expect("cache handle"), &cache));
        assert!(Arc::ptr_eq(&plugins.db().expect("db handle"), &db));
    }

}
