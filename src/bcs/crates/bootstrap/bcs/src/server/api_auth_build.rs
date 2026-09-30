//! `[api.auth]` assembly inside the bootstrap construction paths (Task 12).
//!
//! Composition order (spec §6, plan step 3):
//! 1. discover the registered source inventory (default registry);
//! 2. validate the FINAL merged `[api.auth]` config against it;
//! 3. resolve secrets ONLY for chain-enabled sources (the build fns read
//!    their own tables; disabled instances are never touched);
//! 4. reuse the already-constructed shared identity/session infra (the
//!    strict `AuthSessionIdentityPort` over the ONE identity store) and
//!    build the ONE strict engine of the `[api.auth]` chain family over it;
//! 5. build the V1 login facade when an OAuth source is in chain.
//!
//! The result is published construct-together with the state assembly: the
//! caller assigns the composite verifier into BOTH the V1 ApiState and the
//! state's retained verifier field (same `Arc`) only after every build
//! succeeded — a build failure propagates as `Err` and the server never
//! starts, so no partial chain is ever observable.

use std::sync::Arc;

use super::*;

/// Assemble the `[api.auth]` chain for a construction path.
///
/// Returns `None` in compat mode (no `[api.auth]` section): today's legacy
/// wiring (Task 11) stays byte-for-byte. Returns `Err` when the section is
/// present but any enabled source fails to validate or build — a startup
/// failure, never a warning-and-skip.
pub(super) fn build_built_api_auth_blocking(
    config: &BcsConfig,
    secret_access: Arc<dyn SecretAccessPort>,
    sessions: Option<Arc<dyn bcs_auth_api::AuthSessionIdentityPort>>,
) -> crate::Result<Option<crate::api_auth_wiring::BuiltApiAuth>> {
    let Some(api_auth) = config.api.as_ref().and_then(|api| api.auth.as_ref()) else {
        return Ok(None);
    };
    let sessions = sessions.ok_or_else(|| {
        crate::BcsError::InvalidConfig(
            "[api.auth] requires the strict identity/session port; no identity store is wired"
                .to_string(),
        )
    })?;

    // Discover registrations (production inventory) and pre-compute whether
    // an OAuth source is in chain, so the shared engine + signing material
    // are resolved exactly once, before any source build runs.
    let registrations = crate::api_auth_registry::default_api_auth_registrations();
    let has_oauth = registrations
        .iter()
        .any(|registration| {
            registration.capabilities.is_oauth_provider
                && api_auth.chain.iter().any(|name| name == registration.name)
        });

    let env = crate::config_loader::Environment::resolve().as_str().to_string();
    let built = std::thread::scope(|scope| {
        scope
            .spawn(|| {
                tokio::runtime::Runtime::new()
                    .expect("temp runtime for [api.auth] assembly")
                    .block_on(async {
                        // OAuth chain: resolve the session signing material
                        // once and build the ONE engine over the shared
                        // identity/session port.
                        let (material, engine) = if has_oauth {
                            // Only the logical field path appears on failure —
                            // never the resolved value or backend detail.
                            let material =
                                crate::api_auth_provider_configs::resolve_source_secret(
                                    secret_access.as_ref(),
                                    "",
                                    "session_signing_key_secret",
                                    api_auth.session_signing_key_secret.as_deref(),
                                )
                                .await
                                .map_err(crate::BcsError::InvalidConfig)?;
                            let engine = crate::api_auth_wiring::build_api_auth_oauth_engine(
                                &material,
                                sessions.clone(),
                                env.clone(),
                                api_auth.session_idle_timeout_minutes * 60,
                            );
                            (Some(material), Some(engine))
                        } else {
                            (None, None)
                        };

                        crate::api_auth_wiring::build_api_auth(
                            crate::api_auth_wiring::ApiAuthAssemblyInputs {
                                registrations,
                                config: api_auth.clone(),
                                secret_access,
                                sessions,
                                env,
                                oauth: engine,
                                session_signing_material: material,
                            },
                        )
                        .await
                        .map_err(crate::BcsError::InvalidConfig)
                    })
            })
            .join()
            .expect("[api.auth] assembly thread panicked")
    })?;
    Ok(Some(built))
}
