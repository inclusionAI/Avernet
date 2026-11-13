//! Real-stack assembly for A2A catalog-sync contract tests.
//!
//! Adapter tests must exercise the production wiring — provider core, card
//! store, catalog-change queue, and Fuse sync worker — without the concrete
//! BCS crates becoming dependencies of the versioned HTTP adapter (its
//! manifest forbids `bcs-bot`). The builders below take the shared
//! [`RegistrationChain`] fixture plus test-provided ports and mirror what
//! production bootstrap composes.

use std::sync::Arc;

use bcs_app_a2a_bot::{A2aBotManagement, v1::A2aApiService};
use bcs_bot::{
    ProviderCore,
    core::{a2a_bot::A2aBotCore, a2a_provider_registration::A2aProviderRegistrationCore},
};
use bcs_bot_store::{MemoryProviderStore, a2a::DbA2aBotStore};
use bcs_config::resolve_env_str;
use bcs_fuse_client::{BcsFuseConfig, FuseClient};
use bcs_fusion::{CardChangeQueue, FuseCardSyncLifecycle};
use bcs_service_api::port::a2a_card::A2aCardPort;

use crate::NoopGroupCoreService;
use crate::contract::bot_registration_chain::RegistrationChain;

/// Assembled V1 A2A facade plus the card-change queue its cores publish to.
pub struct A2aCatalogStack {
    /// Versioned A2A facade, ready for `with_a2a_service`.
    pub api: A2aApiService,
    /// Queue consumed by the Fuse catalog-sync worker.
    pub queue: Arc<CardChangeQueue>,
}

/// Compose the real `A2aBotManagement` stack over a registration chain.
pub fn build_a2a_catalog_stack(
    chain: &RegistrationChain,
    credentials: Arc<MemoryProviderStore>,
    fetcher: Arc<dyn A2aCardPort>,
) -> A2aCatalogStack {
    let queue = Arc::new(CardChangeQueue::default());
    let providers = Arc::new(
        ProviderCore::new(
            chain.provider_repo.clone(),
            credentials,
            chain.provider_repo.clone(),
            chain.registry_core.clone(),
        )
        .with_bot_provider_repo(chain.provider_repo.clone()),
    );
    let cards = Arc::new(
        DbA2aBotStore::sqlite(chain.db.clone(), resolve_env_str()).with_registry(&chain.bot_repo),
    );
    let core = Arc::new(
        A2aBotCore::new(cards, chain.provider_repo.clone())
            .with_catalog_changes(Some(queue.clone())),
    );
    let registration = Arc::new(
        A2aProviderRegistrationCore::new(providers.clone(), chain.provider_repo.clone())
            .with_catalog_changes(Some(queue.clone())),
    );
    let management = Arc::new(
        A2aBotManagement::new(providers, core, fetcher)
            .with_registration(registration, chain.registry_core.clone()),
    );
    A2aCatalogStack {
        api: A2aApiService::new(management),
        queue,
    }
}

/// Build the Fuse catalog-sync worker for a locally bound mock Fuse server.
pub fn fuse_card_sync_worker(
    chain: &RegistrationChain,
    queue: Arc<CardChangeQueue>,
    fuse_listen_addr: String,
) -> FuseCardSyncLifecycle {
    let config = BcsFuseConfig {
        url: format!("http://{fuse_listen_addr}"),
        ..Default::default()
    };
    FuseCardSyncLifecycle::new(
        queue,
        Arc::new(FuseClient::new(&config).unwrap()),
        chain.bot_repo.clone(),
        Arc::new(NoopGroupCoreService),
        resolve_env_str(),
        1,
    )
}
