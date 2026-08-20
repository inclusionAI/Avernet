//! Composition-root wiring for the public Event Subscription runtime.

use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use bcs_app_group::{GroupProvisioningLifecycle, GroupProvisioningReconciler};
use bcs_db_api::{DbPlugin, DbSqlFlavor};
use bcs_event_store::{DbEventStore, EventRecorder, MemoryEventStore};
use bcs_eventing::{
    CoreEventSubscriptionAuthorizer, EventCatalog, EventDispatcher, EventFanoutWorker,
    EventRetentionWorker, EventRetryPolicy, EventSubscriptionApplicationService,
    EventSubscriptionAuthorizer, EventSubscriptionPolicy, EventingLifecycle,
};
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::application::v1::{
    EventSubscriptionService, GroupEventSubscriptionProvisioner,
};
use bcs_service_api::lifecycle::ServiceLifecycle;
use bcs_service_api::port::repo::EventRepoPort;
use bcs_service_api::port::{
    EventDeliveryAttemptMetric, EventDeliveryPort, EventErrorCategory, EventProductionMetric,
    EventRecordFactoryPort, EventingInstrumentationPort, WebhookGuardBlockReason,
};
use bcs_service_api::{
    BotRegistryCoreService, CollaborationRuntimeService, GroupCoreService, SessionManagementService,
};
use bcs_webhook_client::{WebhookClient, WebhookEndpointPolicy};

use crate::config::BcsConfig;
use crate::{BcsError, Result};

const EVENTING_CLAIM_LIMIT: u32 = 100;
const EVENTING_RETENTION_BATCH_LIMIT: u32 = 100;
const EVENTING_RETENTION_POLL_INTERVAL: Duration = Duration::from_secs(60 * 60);
const GROUP_PROVISIONING_POLL_INTERVAL: Duration = Duration::from_secs(60);
const GROUP_PROVISIONING_MINIMUM_AGE_MS: u64 = 5 * 60 * 1_000;

pub(crate) struct EventingRuntime {
    pub service: Arc<dyn EventSubscriptionService>,
    pub group_provisioner: Arc<dyn GroupEventSubscriptionProvisioner>,
    pub lifecycle: Option<Arc<dyn ServiceLifecycle>>,
    pub provisioning_lifecycle: Option<Arc<dyn ServiceLifecycle>>,
}

pub(crate) fn memory_event_repo() -> Arc<MemoryEventStore> {
    Arc::new(MemoryEventStore::new())
}

pub(crate) fn db_event_repo(db: Arc<dyn DbPlugin>, flavor: DbSqlFlavor) -> Arc<dyn EventRepoPort> {
    match flavor {
        DbSqlFlavor::Mysql => Arc::new(DbEventStore::mysql(db)),
        DbSqlFlavor::Sqlite => Arc::new(DbEventStore::sqlite(db)),
    }
}

pub(crate) fn event_record_factory(
    config: &BcsConfig,
    repo: Arc<dyn EventRepoPort>,
) -> Arc<dyn EventRecordFactoryPort> {
    event_recorder(config, repo)
}

pub(crate) fn event_recorder(
    config: &BcsConfig,
    repo: Arc<dyn EventRepoPort>,
) -> Arc<EventRecorder> {
    Arc::new(EventRecorder::new(
        repo,
        config.eventing.enabled,
        crate::env::resolve_env(),
        config.eventing.event_retention_days,
        config.eventing.webhook.max_event_body_bytes,
    ))
}

#[allow(clippy::too_many_arguments)]
pub(crate) async fn build_eventing_runtime(
    config: &BcsConfig,
    repo: Arc<dyn EventRepoPort>,
    groups: Arc<dyn GroupCoreService>,
    sessions: Arc<dyn SessionManagementService>,
    collaboration_runtime: Arc<dyn CollaborationRuntimeService>,
    registry: Arc<dyn BotRegistryCoreService>,
    outbound_url_guard: OutboundUrlGuard,
    allow_local_test_endpoints: bool,
) -> Result<EventingRuntime> {
    config
        .eventing
        .validate()
        .map_err(BcsError::InvalidConfig)?;
    validate_production_security(config, allow_local_test_endpoints)?;

    let endpoint_policy = if allow_local_test_endpoints {
        WebhookEndpointPolicy::local(
            config.eventing.webhook.allow_http_loopback,
            config.eventing.webhook.allow_non_standard_ports,
        )
    } else {
        WebhookEndpointPolicy::production()
    };
    let outbound_url_guard = eventing_outbound_url_guard(
        config,
        outbound_url_guard,
        allow_local_test_endpoints,
    )?;
    let delivery: Arc<dyn EventDeliveryPort> = Arc::new(
        WebhookClient::new(outbound_url_guard, endpoint_policy)
            .with_connect_timeout(Duration::from_millis(
                config.eventing.webhook.connect_timeout_ms,
            ))
            .with_response_body_limit(config.eventing.webhook.max_response_body_bytes),
    );
    let catalog = Arc::new(EventCatalog::load_embedded().map_err(|error| {
        BcsError::InvalidConfig(format!("embedded Event Catalog is invalid: {error}"))
    })?);
    let env = crate::env::resolve_env();
    let authorizer: Arc<dyn EventSubscriptionAuthorizer> = Arc::new(
        CoreEventSubscriptionAuthorizer::new(groups.clone(), registry),
    );
    let service_impl = Arc::new(
        EventSubscriptionApplicationService::new(
            repo.clone(),
            delivery.clone(),
            authorizer,
            catalog.clone(),
            EventSubscriptionPolicy::from(&config.eventing),
            env.clone(),
        )
        .with_group_provisioning(groups.clone(), config.eventing.event_retention_days),
    );
    let service: Arc<dyn EventSubscriptionService> = service_impl.clone();
    let group_provisioner: Arc<dyn GroupEventSubscriptionProvisioner> = service_impl;

    if !config.eventing.enabled {
        return Ok(EventingRuntime {
            service,
            group_provisioner,
            lifecycle: None,
            provisioning_lifecycle: None,
        });
    }

    let provisioning_lifecycle: Arc<dyn ServiceLifecycle> = Arc::new(
        GroupProvisioningLifecycle::new(
            Arc::new(GroupProvisioningReconciler::new(
                groups.clone(),
                sessions.clone(),
                Some(collaboration_runtime),
                group_provisioner.clone(),
            )),
            GROUP_PROVISIONING_POLL_INTERVAL,
            GROUP_PROVISIONING_MINIMUM_AGE_MS,
            Duration::from_millis(config.eventing.drain_timeout_ms),
        )
        .map_err(|error| BcsError::InvalidConfig(error.to_string()))?,
    );

    let instrumentation: Arc<dyn EventingInstrumentationPort> =
        Arc::new(BootstrapEventingInstrumentation::new(env.clone()));
    let fanout = Arc::new(EventFanoutWorker::new(
        repo.clone(),
        instrumentation.clone(),
        catalog,
        env.clone(),
        config.eventing.lease_ms,
        EVENTING_CLAIM_LIMIT,
        config.eventing.webhook.max_event_body_bytes,
    ));
    let dispatcher = config.eventing.dispatcher_enabled.then(|| {
        Arc::new(EventDispatcher::new(
            repo.clone(),
            delivery,
            instrumentation,
            EventRetryPolicy::from(&config.eventing.retry),
            env.clone(),
            config.eventing.lease_ms,
            EVENTING_CLAIM_LIMIT,
            config.eventing.worker_concurrency,
            config.eventing.per_host_concurrency,
        ))
    });
    let retention = Arc::new(EventRetentionWorker::new(
        repo,
        env,
        EVENTING_RETENTION_BATCH_LIMIT,
        EVENTING_RETENTION_BATCH_LIMIT,
    ));
    let lifecycle: Arc<dyn ServiceLifecycle> = Arc::new(
        EventingLifecycle::new(
            fanout,
            dispatcher,
            retention,
            Duration::from_millis(config.eventing.fanout_poll_interval_ms),
            Duration::from_millis(config.eventing.delivery_poll_interval_ms),
            EVENTING_RETENTION_POLL_INTERVAL,
            Duration::from_millis(config.eventing.drain_timeout_ms),
        )
        .map_err(|error| BcsError::InvalidConfig(error.to_string()))?,
    );

    Ok(EventingRuntime {
        service,
        group_provisioner,
        lifecycle: Some(lifecycle),
        provisioning_lifecycle: Some(provisioning_lifecycle),
    })
}

fn eventing_outbound_url_guard(
    config: &BcsConfig,
    fallback: OutboundUrlGuard,
    allow_local_test_endpoints: bool,
) -> Result<OutboundUrlGuard> {
    let guard = if allow_local_test_endpoints && config.eventing.webhook.allow_http_loopback {
        OutboundUrlGuard::new(config.security.outbound_url.block_private_networks, true)
    } else {
        fallback
    };
    guard
        .with_private_endpoint_allowlist(
            &config.eventing.webhook.private_endpoint_allowlist,
        )
        .map_err(BcsError::InvalidConfig)
}

fn validate_production_security(
    config: &BcsConfig,
    allow_local_test_endpoints: bool,
) -> Result<()> {
    if !config.eventing.enabled || allow_local_test_endpoints {
        return Ok(());
    }
    if !config.security.outbound_url.block_private_networks
        || config.security.outbound_url.allow_loopback
        || config.eventing.webhook.allow_http_loopback
        || config.eventing.webhook.allow_non_standard_ports
    {
        return Err(BcsError::InvalidConfig(
            "production Eventing requires HTTPS and strict outbound SSRF protection".to_string(),
        ));
    }
    Ok(())
}

struct BootstrapEventingInstrumentation {
    env: String,
}

impl BootstrapEventingInstrumentation {
    fn new(env: String) -> Self {
        Self { env }
    }
}

#[async_trait]
impl EventingInstrumentationPort for BootstrapEventingInstrumentation {
    async fn event_produced(&self, metric: EventProductionMetric) {
        #[cfg(feature = "prometheus-metrics")]
        metrics::counter!(
            "bcs_event_produced_total",
            "env" => self.env.clone(),
            "family" => format!("{:?}", metric.family).to_ascii_lowercase(),
            "result" => format!("{:?}", metric.result).to_ascii_lowercase(),
            "error_category" => metric.error_category.map_or_else(
                || "none".to_string(),
                |value| format!("{value:?}").to_ascii_lowercase(),
            ),
        )
        .increment(1);
        #[cfg(not(feature = "prometheus-metrics"))]
        let _ = (&self.env, metric);
    }

    async fn fanout_failed(&self, error_category: EventErrorCategory) {
        #[cfg(feature = "prometheus-metrics")]
        metrics::counter!(
            "bcs_event_fanout_failed_total",
            "env" => self.env.clone(),
            "error_category" => format!("{error_category:?}").to_ascii_lowercase(),
        )
        .increment(1);
        #[cfg(not(feature = "prometheus-metrics"))]
        let _ = (&self.env, error_category);
    }

    async fn delivery_attempted(&self, metric: EventDeliveryAttemptMetric) {
        #[cfg(feature = "prometheus-metrics")]
        metrics::counter!(
            "bcs_event_delivery_attempt_total",
            "env" => self.env.clone(),
            "family" => format!("{:?}", metric.family).to_ascii_lowercase(),
            "result" => format!("{:?}", metric.result).to_ascii_lowercase(),
            "status_class" => format!("{:?}", metric.status_class).to_ascii_lowercase(),
            "error_category" => metric.error_category.map_or_else(
                || "none".to_string(),
                |value| format!("{value:?}").to_ascii_lowercase(),
            ),
        )
        .increment(1);
        #[cfg(not(feature = "prometheus-metrics"))]
        let _ = (&self.env, metric);
    }

    async fn webhook_guard_blocked(&self, reason: WebhookGuardBlockReason) {
        #[cfg(feature = "prometheus-metrics")]
        metrics::counter!(
            "bcs_event_webhook_guard_blocked_total",
            "env" => self.env.clone(),
            "reason" => format!("{reason:?}").to_ascii_lowercase(),
        )
        .increment(1);
        #[cfg(not(feature = "prometheus-metrics"))]
        let _ = (&self.env, reason);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn production_eventing_rejects_weakened_outbound_security() {
        let mut config = BcsConfig::default();
        config.eventing.enabled = true;
        config.security.outbound_url.block_private_networks = false;

        assert!(matches!(
            validate_production_security(&config, false),
            Err(BcsError::InvalidConfig(message)) if message.contains("strict outbound SSRF")
        ));
        assert!(validate_production_security(&config, true).is_ok());
    }

    #[test]
    fn local_eventing_guard_allows_only_loopback_from_private_address_space() {
        let mut config = BcsConfig::default();
        config.eventing.webhook.allow_http_loopback = true;
        config.security.outbound_url.block_private_networks = true;
        let guard = eventing_outbound_url_guard(&config, OutboundUrlGuard::strict(), true)
            .expect("valid local Eventing guard");

        assert!(
            guard
                .validate_configured_http_url("http://127.0.0.1:28082/events")
                .is_ok()
        );
        assert!(
            guard
                .validate_configured_http_url("http://10.0.0.8:28082/events")
                .is_err()
        );
    }

    #[test]
    fn eventing_guard_compiles_private_endpoint_allowlist() {
        let mut config = BcsConfig::default();
        config.eventing.webhook.private_endpoint_allowlist = vec![
            bcs_config_api::PrivateEndpointAllowlistEntryConfig {
                host: "*.hooks.example.internal".to_string(),
                cidrs: vec!["10.20.0.0/16".to_string()],
                ports: vec![443, 8443],
            },
        ];
        let guard = eventing_outbound_url_guard(&config, OutboundUrlGuard::strict(), false)
            .expect("valid private endpoint allowlist");

        assert!(guard.allows_allowlisted_host_port(
            "https://worker.hooks.example.internal:8443/events"
        ));
        assert!(!guard.allows_allowlisted_host_port(
            "https://hooks.example.internal:8443/events"
        ));
    }

    #[test]
    fn db_event_repo_supports_both_declared_sql_flavors() {
        let db: Arc<dyn DbPlugin> =
            Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().expect("create SQLite test plugin"));
        let _sqlite = db_event_repo(db.clone(), DbSqlFlavor::Sqlite);
        let _mysql = db_event_repo(db, DbSqlFlavor::Mysql);
    }
}
