//! `BotAuthorityPort` — the narrow authority interface business
//! entrypoints consult instead of legacy `created_by` checks.
//!
//! Every authorized read returns a `Result` (spec §8: 授权读取返回
//! `Result`，不复用吞错误的布尔查询); the hook never swallows storage
//! or integrity failures into a boolean deny. The hook delegates to
//! the authority Core (`core::bot_authority`); adapters and business
//! cores must not depend on the Repo port directly.

use async_trait::async_trait;

use crate::ServiceResult;
use crate::types::bot_authority::{BotAccessRelation, BotRoleQuery};

#[async_trait]
pub trait BotAuthorityPort: Send + Sync {
    /// Role of `user_id` (verified User ID) on `bot_id` in the
    /// hook's bound environment.
    async fn role(&self, user_id: &str, bot_id: &str) -> ServiceResult<Option<BotAccessRelation>>;

    /// Batch role read; same length and order as `queries`.
    async fn roles_for(
        &self,
        queries: &[BotRoleQuery],
    ) -> ServiceResult<Vec<Option<BotAccessRelation>>>;

    /// Current owner/manager authorization for Human-controlled
    /// operations. `Ok(false)` is a strict deny only when the role
    /// read positively found no authorized relation.
    async fn can_manage(&self, user_id: &str, bot_id: &str) -> ServiceResult<bool>;

    /// Deny unless `user_id` is the current owner of `bot_id`.
    async fn require_owner(&self, user_id: &str, bot_id: &str) -> ServiceResult<()>;

    /// Deny unless `user_id` holds the owner or manager role.
    async fn require_manage(&self, user_id: &str, bot_id: &str) -> ServiceResult<()>;
}
