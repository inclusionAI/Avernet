//! A2A composition: official SDK/network adapters stay outside Bot core.
use std::sync::Arc;
use bcs_a2a_http::card_fetch::HttpA2aCardFetcher;
use bcs_app_a2a_bot::A2aBotManagement;
use bcs_bot::core::a2a_bot::A2aBotCore;
use bcs_bot_store::a2a::DbA2aBotStore;
use bcs_db_api::DbPlugin;
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::{ProviderCoreService, application::a2a_bot::A2aBotManagementService};
use bcs_service_api::port::repo::bot_provider::BotProviderRepoPort;

pub(super) fn build_card_management(
    db: Arc<dyn DbPlugin>, flavor:bcs_db_api::DbSqlFlavor, bots: Arc<dyn BotProviderRepoPort>,
    providers: Arc<dyn ProviderCoreService>, guard: OutboundUrlGuard, grpc: Option<&bcs_a2a_http::grpc_mesh::GrpcMeshConfig>,
) -> crate::Result<(Arc<dyn A2aBotManagementService>, Arc<dyn bcs_service_api::core::a2a_bot::A2aBotCoreService>)> {
    let cards = Arc::new(DbA2aBotStore::new(db, crate::env::resolve_env(), flavor));
    let registration = Arc::new(bcs_bot::core::a2a_provider_registration::A2aProviderRegistrationCore::new(providers.clone(), bots.clone()));
    let core = Arc::new(A2aBotCore::new(cards, bots));
    let mut fetcher = HttpA2aCardFetcher::new(guard, vec![bcs_a2a_http::tools::EXTENSION.into(),
        bcs_a2a_http::delivery::DEDUP_EXTENSION.into(),bcs_a2a_http::structured_exec::EXTENSION.into()]);
    if let Some(grpc)=grpc {fetcher=fetcher.with_grpc_mesh(grpc.clone())?;}
    Ok((Arc::new(A2aBotManagement::new(providers, core.clone(), Arc::new(fetcher)).with_registration(registration)), core))
}

pub(super) fn build_delivery(
    db: Arc<dyn DbPlugin>, flavor: bcs_db_api::DbSqlFlavor,
    credentials: Arc<dyn bcs_service_api::ProviderCredentialRepoPort>, guard: OutboundUrlGuard, grpc: Option<&bcs_a2a_http::grpc_mesh::GrpcMeshConfig>,
) -> crate::Result<Arc<bcs_a2a_http::delivery::A2aHttpTransport>> {
    let runs = Arc::new(bcs_chat_run_store::a2a::DbA2aRunStore::new(db, crate::env::resolve_env(), flavor));
    let mut transport=bcs_a2a_http::delivery::A2aHttpTransport::new(guard, runs, credentials);
    if let Some(grpc)=grpc {transport=transport.with_grpc_mesh(grpc.clone())?;}
    Ok(Arc::new(transport))
}

pub(super) fn bind_input(
    transport:&bcs_a2a_http::delivery::A2aHttpTransport,service:Arc<dyn bcs_service_api::InteractionService>,
    deliveries:Arc<dyn bcs_service_api::ManagedMessageDeliveryService>,contexts:Arc<dyn bcs_service_api::BotRunContextPort>,config:&crate::BcsConfig,
) -> crate::Result<()> {
    config.a2a_input.validate().map_err(crate::BcsError::InvalidConfig)?;
    let budgets=Arc::new(bcs_message_flow::run_budget_management::RunBudgetManagement::new(
        Arc::new(bcs_message_flow::run_budget::RunBudgetCore),deliveries.clone(),contexts));
    transport.set_input_runtime(bcs_a2a_http::delivery::InputRuntime::new(service,deliveries,
        budgets,bcs_service_api::types::run_budget::RunBudgetLimits {
            input_wait_ms:config.a2a_input.wait_timeout_ms,total_run_ms:config.a2a_input.total_timeout_ms,
        }))?;Ok(())
}

pub(super) struct CardRefreshLoop(tokio::task::JoinHandle<()>);
impl Drop for CardRefreshLoop {
    fn drop(&mut self) { self.0.abort(); }
}

/// Kept alive by the serving future; bind failures and graceful shutdown drop it.
pub(super) fn start_card_refresh(service: Arc<dyn A2aBotManagementService>) -> CardRefreshLoop {
    CardRefreshLoop(tokio::spawn(async move {
        let mut ticker = tokio::time::interval(std::time::Duration::from_secs(10));
        ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        loop {
            ticker.tick().await;
            let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default().as_millis() as i64;
            match service.refresh_due(now).await {
                Ok(count) if count > 0 => tracing::info!(count, "A2A due Cards refreshed"),
                Ok(_) => {}
                // Never print remote response bodies, source URLs or credentials.
                Err(_) => tracing::warn!("A2A Card maintenance failed; previous validated snapshot retained"),
            }
        }
    }))
}
