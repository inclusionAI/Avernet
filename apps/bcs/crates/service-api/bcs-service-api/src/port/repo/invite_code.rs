use async_trait::async_trait;

use crate::types::ServiceResult;

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum InviteCodeStatus {
    Active,
    Bound,
    Disabled,
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct InviteCodeRecord {
    #[serde(default)]
    pub id: u64,
    pub code_hash: String,
    pub code_hint: String,
    pub status: InviteCodeStatus,
    pub bound_user_id: Option<String>,
    pub bound_at: Option<u64>,
    pub created_by: Option<String>,
    pub created_at: u64,
    pub updated_at: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum InviteCodeBindOutcome {
    Bound(InviteCodeRecord),
    AlreadyBoundToSameCode(InviteCodeRecord),
    AlreadyBoundToDifferentCode(InviteCodeRecord),
    Unavailable,
}

#[async_trait]
pub trait InviteCodeRepoPort: Send + Sync {
    async fn insert_code(&self, record: InviteCodeRecord) -> ServiceResult<bool>;

    async fn count_by_created_by(&self, created_by: &str) -> ServiceResult<u64>;

    async fn bind_code(
        &self,
        code_hash: &str,
        user_id: &str,
        bound_at: u64,
    ) -> ServiceResult<InviteCodeBindOutcome>;

    async fn find_by_user_id(&self, user_id: &str) -> ServiceResult<Option<InviteCodeRecord>>;

    async fn find_by_code_hash(&self, code_hash: &str) -> ServiceResult<Option<InviteCodeRecord>>;
}
