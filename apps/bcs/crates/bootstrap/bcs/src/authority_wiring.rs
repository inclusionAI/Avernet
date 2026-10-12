//! Composition-root helpers for the bot authority model (plan Task 18,
//! spec §13 composition order):
//!
//! ```text
//! composition root
//!   -> authority Repo + lifecycle Repo (same DB / shared Memory state)
//!   -> authority Core
//!   -> Human/Service application + registered Hook
//!   -> v1/legacy HTTP, Session connection service, WS protected writer
//! ```
//!
//! The datasources are selected here and ONLY here: the memory lane shares
//! the one `MemoryBotRepo` (the bot lifecycle store IS the authority repo —
//! one critical section), the durable lane resolves the same selected
//! DbPlugin into the SQL authority store. A datasource with no authority
//! store wiring is a STARTUP ERROR, never a panic and never an unmounted
//! authority. Everything downstream (the centralized hook, the manager /
//! transfer / team application facades, the WS protected-delivery
//! authorization service) is assembled from this one lane, so every
//! entrypoint answers through the SAME store.
//!
//! `service-api` exposes only application/Core/port contracts here — no
//! DbPlugin type, no concrete store type, and no secret material leaks
//! into Debug output (the resolved team key is a `Secret` from the first
//! resolution step and never recurs in an error message).

use std::sync::Arc;

use bcs_service_api::application::v1::{
    BotAuthorityHook, BotManagerService, OwnershipTransferService, TeamManagerSyncService,
};
use bcs_service_api::application::v1::delivery_authorization::DeliveryAuthorizationService;
use bcs_service_api::core::BotAuthorityCoreService;
use bcs_service_api::port::repo::BotAuthorityRepoPort;
use bcs_service_api::port::SecretAccessPort;

use crate::plugins::DbPluginKind;

/// The full assembled authority lane plus the application facades built on
/// top of it (the exact bundle the server constructors mount).
pub struct BotManagementWiring {
    /// The strict authority Core over the selected datasource.
    pub core: Arc<dyn BotAuthorityCoreService>,
    /// The centralized authority Hook every consumer consults.
    pub hook: Arc<dyn BotAuthorityHook>,
    /// Task 13 Human-only manager facade over the same Core.
    pub bot_manager: Arc<dyn BotManagerService>,
    /// Task 14 Human-only ownership-transfer facade over the same Core.
    pub ownership_transfer: Arc<dyn OwnershipTransferService>,
    /// Task 13 trusted team-manager sync facade; `None` keeps the team
    /// write routes unmounted (no verifier, no anonymous lane).
    pub team_manager_sync: Option<Arc<dyn TeamManagerSyncService>>,
}

/// Resolve the authority Core over the shared Memory bot lifecycle state
/// (`MemoryBotRepo` plays both roles — one critical section, spec §13.2).
pub fn memory_authority_core(
    repo: Arc<bcs_bot_store::MemoryBotRepo>,
) -> Arc<dyn BotAuthorityCoreService> {
    let authority_repo: Arc<dyn BotAuthorityRepoPort> = repo;
    Arc::new(bcs_edge_permission::authority::BotAuthorityCoreServiceImpl::new(
        authority_repo,
    ))
}

/// Resolve the SQL authority store over the SELECTED datasource. An
/// unmatched plugin kind is a startup error — the composition root must
/// fail closed BEFORE any other store is built (the authority lane is the
/// first datasource consumer, spec §13 composition order).
pub fn db_bot_authority_lane(
    db: Arc<dyn bcs_db_api::DbPlugin>,
    db_kind: &DbPluginKind,
    env: &str,
) -> crate::Result<AuthorityLane> {
    let repo: Arc<dyn BotAuthorityRepoPort> = match db_kind {
        DbPluginKind::LocalSqlite => Arc::new(
            bcs_edge_permission_store::DbBotAuthorityStore::sqlite(db, env.to_string()),
        ),
        DbPluginKind::Mysql => Arc::new(
            bcs_edge_permission_store::DbBotAuthorityStore::mysql(db, env.to_string()),
        ),
        DbPluginKind::External(provider) => {
            return Err(crate::BcsError::InvalidConfig(format!(
                "external database plugin '{provider}' has no bot authority store wiring"
            )));
        }
    };
    Ok(AuthorityLane { repo })
}

/// The authority repo + core selected by [`db_bot_authority_lane`].
pub struct AuthorityLane {
    /// The resolved authority repo over the selected datasource.
    pub repo: Arc<dyn BotAuthorityRepoPort>,
}

impl AuthorityLane {
    /// Wrap the resolved repo with the production Core.
    pub fn into_core(self) -> Arc<dyn BotAuthorityCoreService> {
        Arc::new(bcs_edge_permission::authority::BotAuthorityCoreServiceImpl::new(self.repo))
    }
}

/// Assemble the full management wiring over an already-resolved authority
/// Core (shared by the memory lane, the durable lane, and the friend-lane
/// authority in the durable path — one core, one store, every consumer).
pub fn management_wiring_over_core(
    core: Arc<dyn BotAuthorityCoreService>,
    team_manager_sync: Option<Arc<dyn TeamManagerSyncService>>,
) -> BotManagementWiring {
    let hook: Arc<dyn BotAuthorityHook> =
        Arc::new(bcs_app_bot::BotAuthorityHookImpl::new(core.clone()));
    let bot_manager = Arc::new(bcs_app_bot::BotManagerServiceImpl::new(
        core.clone(),
        hook.clone(),
    ));
    let ownership_transfer = Arc::new(bcs_app_bot::OwnershipTransferServiceImpl::new(
        core.clone(),
        hook.clone(),
    ));
    BotManagementWiring {
        core,
        hook,
        bot_manager,
        ownership_transfer,
        team_manager_sync,
    }
}

/// Resolve the trusted team-manager sync facade from configuration and the
/// process environment / secret backend (Task 13's resolver chain):
///
/// - `enabled = false` (default): `Ok(None)` — the routes stay unmounted;
/// - enabled with resolvable material: the production verifier + facade;
/// - enabled with NO material: `Err` — the startup fails, the lane is
///   never mounted anonymously.
///
/// Material resolution precedes the verifier exactly once and the error
/// strings never carry the resolved material (Task 13's contract tests pin
/// that the key is absent from every message).
pub async fn resolve_team_manager_sync_service(
    config: &bcs_config_api::TeamManagerSyncConfig,
    authority_core: Arc<dyn BotAuthorityCoreService>,
    secret_access: Arc<dyn SecretAccessPort>,
    env: &str,
) -> crate::Result<Option<Arc<dyn TeamManagerSyncService>>> {
    if !config.enabled {
        return Ok(None);
    }
    let material = team_manager_signing_material(config, secret_access).await?;
    let verifier =
        crate::auth_wiring::build_team_manager_credential_verifier(config, Some(&material))
            .map_err(crate::BcsError::InvalidConfig)?;
    Ok(Some(Arc::new(
        bcs_app_bot::TeamManagerSyncServiceImpl::new(
            authority_core,
            verifier,
            bcs_app_bot::TeamManagerSyncServiceConfig {
                env: env.to_string(),
            },
        ),
    )))
}

/// The resolved signing-key material of a declared-enabled team lane.
async fn team_manager_signing_material(
    config: &bcs_config_api::TeamManagerSyncConfig,
    secret_access: Arc<dyn SecretAccessPort>,
) -> crate::Result<String> {
    // Environment reference first (the declared variable name, trimmed).
    let env_name = config.signing_key_env.trim();
    if !env_name.is_empty() {
        if let Ok(value) = std::env::var(env_name) {
            if !value.trim().is_empty() {
                return Ok(value);
            }
        }
    }
    // Secret-backend reference (a named backend entry, e.g. Mist).
    if let Some(reference) = config.signing_key_secret.as_ref() {
        let name = reference.trim();
        if !name.is_empty() {
            if let Ok(record) = secret_access.get_secret(name).await {
                if !record.value.trim().is_empty() {
                    return Ok(record.value);
                }
            }
        }
    }
    // Declared-enabled with no resolvable material: reuse the Task 13
    // resolver's fail-closed error verbatim — the single source of truth
    // for the fixed startup message (section name + the two sanctioned
    // material sources). The material was never in scope here, so the
    // error cannot leak it.
    crate::config::resolve_team_manager_sync(config, None)
        .map(|_| ())
        .map_err(crate::BcsError::InvalidConfig)?;
    unreachable!("resolve_team_manager_sync rejects enabled sections without material")
}

/// The assembled outbound protected-delivery authorization service (plan
/// Tasks 15/16): the STRICT authority Core plus the Group/Session snapshot
/// reads, exactly the one the WebSocket registry consults at enqueue and
/// dequeue time. Noop never: an unwired registry answers InvalidateBinding
/// fail-closed (pre-Task-18 safe default), a WIRED registry consults THIS
/// service with live committed authority.
pub fn build_delivery_authorization_service(
    authority: Arc<dyn BotAuthorityCoreService>,
    groups: Arc<dyn bcs_service_api::GroupCoreService>,
    sessions: Arc<dyn bcs_service_api::port::repo::SessionRepoPort>,
) -> Arc<dyn DeliveryAuthorizationService> {
    Arc::new(bcs_app_session::DeliveryAuthorizationServiceImpl::new(
        authority, groups, sessions,
    ))
}