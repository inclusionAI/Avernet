//! Bot-authority composition wiring (bot-authority plan
//! Task 5).
//!
//! Same-source assembly of the authority surface: the
//! env-scoped store (SQL or the shared memory state), the
//! authority Core/Hook, and the `BotAuthorityServiceImpl`
//! application facade with the resolved trusted-App scope.
//! Nothing here reads the raw config env or fabricates a
//! fallback store: a missing dependency fails loudly (a
//! no-Store wiring would pretend authority exists).

use std::sync::Arc;

use bcs_app_bot::authority::BotAuthorityServiceImpl;
use bcs_app_bot::authority_policy::AuthorityServicePolicy;
use bcs_db_api::DbPlugin;
use bcs_edge_permission::BotAuthorityCoreServiceImpl;
use bcs_service_api::application::v1::bot_authority::BotAuthorityService;
use bcs_service_api::application::v1::invite_code::InviteCodeService;
use bcs_service_api::port::bot_authority::BotAuthorityPort;
use bcs_service_api::port::repo::BotAuthorityRepoPort;

use crate::config::BcsConfig;
use crate::plugins::DbPluginKind;

/// The composed authority wiring for one environment.
pub struct BotAuthorityWiring {
    /// The authority application facade (serves the
    /// `/api/v1/collaboration/` routes).
    pub service: Arc<dyn BotAuthorityService>,
    /// The narrow authorization hook business
    /// entrypoints consult; delegates to the SAME
    /// Core instance.
    pub hook: Arc<dyn BotAuthorityPort>,
    /// The Core (diagnostics/consumers).
    pub core: Arc<BotAuthorityCoreServiceImpl>,
}

/// The memory-mode authority Repo facet: the SAME
/// `MemoryBotRepo` Arc the registration/profile/Provider
/// wiring uses (one shared atomic state memory, spec §7).
/// The legacy `created_by` fallback posture (migration
/// window) is written onto the shared repo for its authority
/// facade only.
pub fn memory_authority_repo(
    bot_repo: &Arc<bcs_bot_store::MemoryBotRepo>,
    legacy_created_by_fallback: bool,
) -> Arc<dyn BotAuthorityRepoPort> {
    bot_repo.set_legacy_created_by_fallback(legacy_created_by_fallback);
    bot_repo.clone()
}

/// The SQL-mode authority Store for the instance's
/// database; unsupported external providers fail loudly.
pub fn db_authority_repo(
    db: Arc<dyn DbPlugin>,
    db_kind: &DbPluginKind,
    env: &str,
    legacy_created_by_fallback: bool,
) -> Arc<dyn BotAuthorityRepoPort> {
    match db_kind {
        DbPluginKind::LocalSqlite => Arc::new(
            bcs_edge_permission_store::DbBotAuthorityStore::sqlite(
                db,
                env.to_string(),
            )
            .with_legacy_created_by_fallback(legacy_created_by_fallback),
        ),
        DbPluginKind::Mysql => Arc::new(
            bcs_edge_permission_store::DbBotAuthorityStore::mysql(
                db,
                env.to_string(),
            )
            .with_legacy_created_by_fallback(legacy_created_by_fallback),
        ),
        DbPluginKind::External(provider) => {
            panic!(
                "external database plugin '{provider}' has no bot \
                 authority store wiring"
            );
        }
    }
}

/// Compose the authority Core/Hook/service from one Repo
/// port, the resolved app-scope configuration, and the
/// invite-code application (constructor injection only: no
/// raw env reads). The scope configuration must already
/// have validated at config load; a resolution failure
/// here is a hard startup error.
pub fn build(
    repo: Arc<dyn BotAuthorityRepoPort>,
    config: &BcsConfig,
    invite_code_service: Arc<dyn InviteCodeService>,
    invite_gate_enabled: bool,
    env: &str,
) -> Result<BotAuthorityWiring, String> {
    let scope: AuthorityServicePolicy = config
        .bot_authority
        .resolve(env)
        .map_err(|error| {
            format!(
                "bot authority trusted-app scope configuration \
                 failed to load: {error}"
            )
        })?;
    let core = Arc::new(BotAuthorityCoreServiceImpl::new(repo, env));
    let hook: Arc<dyn BotAuthorityPort> = core.clone();
    let service: Arc<dyn BotAuthorityService> = Arc::new(
        BotAuthorityServiceImpl::new(
            hook.clone(),
            core.clone(),
            invite_code_service,
            invite_gate_enabled,
            scope,
            env,
        ),
    );
    Ok(BotAuthorityWiring {
        service,
        hook,
        core,
    })
}
