//! `PermissionProfileRepoPort` — persistence port for `permission_profiles`.
use async_trait::async_trait;
use bcs_domain::edge_permission::PermissionProfile;

use crate::core::error::ServiceResult;

#[async_trait]
pub trait PermissionProfileRepoPort: Send + Sync {
    /// Idempotent: seed bot's default profile (wildcard-allow) if absent.
    /// Never overwrites or bumps revision of an existing default (D12 rule 2).
    async fn ensure_default_profile(&self, bot_id: &str, env: &str) -> ServiceResult<u64>;

    async fn get_active_default(&self, bot_id: &str, env: &str) -> Option<PermissionProfile>;

    /// Bump `rules_template` / `revision` / `digest` (profile_id unchanged, D12 rule 2).
    async fn upsert_revision(&self, profile: PermissionProfile) -> ServiceResult<()>;
}
