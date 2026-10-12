//! DB-backed [`AdmissionService`] implementation (spec §4.3 + §4.5 + §6.2),
//! split out of the former over-limit `lib.rs` (plan Task 12 lib split).
//!
//! Two-path same SoR (§4.3): the workbench path (`check_admission`) and the
//! A2A path (`build_authz_context`) both read the same `edge_grants` store,
//! giving a single source of truth for inbound admission and injected runtime
//! authz. The runtime grant whitelist (`grant_kind IN
//! ('permission_profile','rules')`) is enforced by the store's selection
//! (spec §13.4): role rows never join either path.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::edge_permission::{
    AdmissionReason, AdmissionResult, AuthzContext, AuthzGrantRef, GrantKind, GrantSource,
};
use bcs_service_api::application::admission::AdmissionService;
use bcs_service_api::port::repo::{
    BotActorConfigRepoPort, EdgeGrantRepoPort, PermissionProfileRepoPort,
};
use bcs_service_api::{ServiceResult};

/// DB-backed [`AdmissionService`] implementation (spec §4.3 + §4.5 + §6.2).
///
/// Unlike [`DbConnectService`] (which owns its env because `ConnectService`
/// methods take no env param), the `AdmissionService` trait methods carry an
/// `env: &str` argument, so this service holds no env field — it scopes every
/// repo call to the env passed into `check_admission` / `build_authz_context`.
///
/// Two-path same SoR (§4.3): the workbench path (`check_admission`) and the A2A
/// path (`build_authz_context`) both read the same `edge_grants` store, giving a
/// single source of truth for inbound admission and injected runtime authz.
pub struct DbAdmissionService {
    edge_grants: Arc<dyn EdgeGrantRepoPort>,
    bot_config: Arc<dyn BotActorConfigRepoPort>,
    profiles: Arc<dyn PermissionProfileRepoPort>,
}

impl DbAdmissionService {
    pub fn new(
        edge_grants: Arc<dyn EdgeGrantRepoPort>,
        bot_config: Arc<dyn BotActorConfigRepoPort>,
        profiles: Arc<dyn PermissionProfileRepoPort>,
    ) -> Self {
        Self {
            edge_grants,
            bot_config,
            profiles,
        }
    }

    /// Resolve the target bot's default profile as a runtime grant ref,
    /// preferring the edge-grant cache id and enriching `revision`/`digest`
    /// from the profile store (§4.3: "profile grant 解析出 revision/digest").
    async fn default_profile_ref(
        &self,
        bot: &str,
        env: &str,
        source: GrantSource,
    ) -> Option<AuthzGrantRef> {
        // Prefer the edge-grant cache; fall back to the profile store.
        let default_id = match self.edge_grants.get_default_profile_id(bot, env).await {
            Some(id) => id,
            None => self
                .profiles
                .get_active_default(bot, env)
                .await?
                .permission_profile_id,
        };

        // Enrich revision/digest from the profile store when available.
        let (revision, digest) = self
            .profiles
            .get_active_default(bot, env)
            .await
            .map(|p| (Some(p.revision), Some(p.digest)))
            .unwrap_or((None, None));

        Some(AuthzGrantRef {
            kind: GrantKind::PermissionProfile,
            ref_id: default_id,
            revision,
            digest,
            source,
        })
    }

    /// Resolve the default-profile grant ref for `bot`, calling
    /// `ensure_default_profile` first so a freshly-onboarded bot with no
    /// `permission_profiles` row yet still resolves (idempotent, D12 rule 2).
    /// Used by `check_admission` where we expect a resolvable default.
    async fn ensure_default_profile_ref(
        &self,
        bot: &str,
        env: &str,
        source: GrantSource,
    ) -> Option<AuthzGrantRef> {
        let _ = self.profiles.ensure_default_profile(bot, env).await;
        self.default_profile_ref(bot, env, source).await
    }
}

#[async_trait]
impl AdmissionService for DbAdmissionService {
    async fn check_admission(
        &self,
        actor: &str,
        bot: &str,
        originator: &str,
        env: &str,
    ) -> ServiceResult<AdmissionResult> {
        // spec §4.3 step 1. `None` config ⇒ bot not on-boarded in this env.
        // Returned as a deny AdmissionResult (api-contract reason_code
        // `bot_not_found`), NOT a ServiceResult error.
        let cfg = match self.bot_config.get(bot, env).await {
            Some(c) => c,
            None => {
                return Ok(AdmissionResult {
                    allowed: false,
                    grants: vec![],
                    reason_code: AdmissionReason::BotNotFound,
                    public_default: false,
                });
            }
        };

        // status=hidden ⇒ collaboration switch off ⇒ deny (spec §4.3 step 1).
        if cfg.status == "hidden" {
            return Ok(AdmissionResult {
                allowed: false,
                grants: vec![],
                reason_code: AdmissionReason::BotHidden,
                public_default: false,
            });
        }

        // §4.3 step 2: friend edge (any direction, D12 default-profile edge).
        // `originator` is accepted but friend edges carry
        // `originator_policy_type=any` (D7) so they are always active for any
        // originator — no policy matching needed at T14. Left as a hook for
        // `Specific`/`Owner` policies in a later installment.
        let _ = originator;

        if self.edge_grants.is_authorized(actor, bot, env).await {
            if let Some(grant) = self
                .ensure_default_profile_ref(bot, env, GrantSource::EdgeGrant)
                .await
            {
                return Ok(AdmissionResult {
                    allowed: true,
                    grants: vec![grant],
                    reason_code: AdmissionReason::Ok,
                    public_default: false,
                });
            }
            // Friend edge exists but the default profile is unexpectedly gone
            // (shouldn't happen: D12 rule 2 keeps it live while friend edges
            // exist). Treat as deny to fail safe.
            return Ok(AdmissionResult {
                allowed: false,
                grants: vec![],
                reason_code: AdmissionReason::NoEdge,
                public_default: false,
            });
        }

        // §4.3 step 4: no edge, bot is public ⇒ public_default (§6.2).
        if cfg.visibility == "public" {
            if let Some(grant) = self
                .ensure_default_profile_ref(bot, env, GrantSource::PublicDefault)
                .await
            {
                return Ok(AdmissionResult {
                    allowed: true,
                    grants: vec![grant],
                    reason_code: AdmissionReason::PublicDefault,
                    public_default: true,
                });
            }
            // Public bot without a default profile — treat as deny rather than
            // crash. ensure_default_profile should have seeded one above.
            return Ok(AdmissionResult {
                allowed: false,
                grants: vec![],
                reason_code: AdmissionReason::NoEdge,
                public_default: false,
            });
        }

        // §4.3 step 5: protected/private bot, no edge ⇒ deny.
        Ok(AdmissionResult {
            allowed: false,
            grants: vec![],
            reason_code: AdmissionReason::NoEdge,
            public_default: false,
        })
    }

    async fn build_authz_context(
        &self,
        from: &str,
        to: &str,
        originator: &str,
        task_id: &str,
        run_id: &str,
        env: &str,
    ) -> ServiceResult<AuthzContext> {
        // §4.3 A2A path: read the same `edge_grants` SoR as admission.
        let active = self.edge_grants.list_active_grants(from, to, env).await;

        let mut grants: Vec<AuthzGrantRef> = Vec::with_capacity(active.len());
        for g in active {
            // For permission_profile edges we COULD enrich revision/digest by
            // looking up the profile by `grant_ref_id`. `get_active_default`
            // takes `bot_id`, not `profile_id`, and we only need enrichment for
            // default-profile edges — so for a default-profile friend edge we
            // resolve via the profile store keyed by the target bot. Non-default
            // profile edges leave revision/digest None (acceptable at T14).
            let (revision, digest) = if g.grant_kind == GrantKind::PermissionProfile
                && self.is_default_profile_ref(g.grant_ref_id, to, env).await
            {
                self.profiles
                    .get_active_default(to, env)
                    .await
                    .map(|p| (Some(p.revision), Some(p.digest)))
                    .unwrap_or((None, None))
            } else {
                (None, None)
            };
            grants.push(AuthzGrantRef {
                kind: g.grant_kind,
                ref_id: g.grant_ref_id,
                revision,
                digest,
                source: GrantSource::EdgeGrant,
            });
        }

        // No active edge AND target is a public, non-hidden bot ⇒ fall back to
        // public_default so the A2A context admits via the runtime default.
        if grants.is_empty() {
            if let Some(cfg) = self.bot_config.get(to, env).await {
                if cfg.visibility == "public" && cfg.status != "hidden" {
                    if let Some(g) = self
                        .ensure_default_profile_ref(to, env, GrantSource::PublicDefault)
                        .await
                    {
                        grants.push(g);
                    }
                }
            }
        }

        Ok(AuthzContext {
            task_id: task_id.to_string(),
            run_id: run_id.to_string(),
            from_id: from.to_string(),
            to_id: to.to_string(),
            env: env.to_string(),
            originator: originator.to_string(),
            context: serde_json::json!({}),
            grants,
            signature: None,
        })
    }
}

// ---- AdmissionService private helpers ------------------------------------

impl DbAdmissionService {
    /// Is `ref_id` the default profile id of `bot`? Used to decide whether to
    /// enrich revision/digest for a permission_profile edge ref.
    async fn is_default_profile_ref(&self, ref_id: u64, bot: &str, env: &str) -> bool {
        if let Some(cached) = self.edge_grants.get_default_profile_id(bot, env).await {
            return cached == ref_id;
        }
        if let Some(p) = self.profiles.get_active_default(bot, env).await {
            return p.permission_profile_id == ref_id;
        }
        false
    }
}
