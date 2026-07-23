//! Channel 持久化 port。
//!
//! 仿 `friend.rs`:repo 拥有存储与行/域映射;业务规则在 `ChannelService`。

use async_trait::async_trait;

use bcs_domain::{
    BindingTarget, ChannelBinding, ChannelType, ConversationSessionMap, ImParticipantMap,
    SessionScope,
};

use crate::types::ServiceResult;

/// ChannelBinding 持久化。
///
/// Repository 实例由 composition root 绑定到单一运行环境；所有读写都只能作用于该环境。
#[async_trait]
pub trait ChannelBindingRepoPort: Send + Sync {
    async fn create(&self, binding: ChannelBinding) -> ServiceResult<()>;
    async fn get(&self, id: &str) -> ServiceResult<Option<ChannelBinding>>;
    /// 入站主查询:按 (channel_type, account_ref) 找一个 Active binding。
    async fn find_active_by_account(
        &self,
        channel_type: ChannelType,
        account_ref: &str,
    ) -> ServiceResult<Option<ChannelBinding>>;
    async fn list(&self) -> ServiceResult<Vec<ChannelBinding>>;
    /// 管理查询：按 Bot/Group target 隔离，可选进一步限定 channel type。
    async fn list_by_target(
        &self,
        target: &BindingTarget,
        channel_type: Option<&str>,
    ) -> ServiceResult<Vec<ChannelBinding>>;
    async fn set_status(&self, id: &str, active: bool) -> ServiceResult<()>;
    async fn set_config(&self, id: &str, config: serde_json::Value) -> ServiceResult<()>;
    async fn delete(&self, id: &str) -> ServiceResult<()>;
}

/// 会话 → session 映射持久化。
/// 键 (binding_id, im_conversation_id, session_scope, im_user_id)。
/// 调用方负责按 scope 传入会话级 `None` 或 per-sender 的 normalized IM 用户 ID。
#[async_trait]
pub trait ConversationSessionRepoPort: Send + Sync {
    async fn get(
        &self,
        binding_id: &str,
        im_conversation_id: &str,
        session_scope: SessionScope,
        im_user_id: Option<&str>,
    ) -> ServiceResult<Option<ConversationSessionMap>>;
    /// 反查:按 bcs_session_id 找对应会话(出站定位 im_conversation_id 用)。
    async fn find_by_session(
        &self,
        binding_id: &str,
        bcs_session_id: &str,
    ) -> ServiceResult<Option<ConversationSessionMap>>;
    /// 出站主查询:按 BCS session 反查所有 channel conversation mapping。
    async fn list_by_bcs_session(
        &self,
        bcs_session_id: &str,
    ) -> ServiceResult<Vec<ConversationSessionMap>>;
    async fn upsert(&self, map: ConversationSessionMap) -> ServiceResult<()>;
}

/// IM 用户 → Human actor 映射持久化。键 (channel_type, account_ref, im_user_id)。
#[async_trait]
pub trait ImParticipantRepoPort: Send + Sync {
    async fn get(
        &self,
        channel_type: ChannelType,
        account_ref: &str,
        im_user_id: &str,
    ) -> ServiceResult<Option<ImParticipantMap>>;
    async fn upsert(&self, map: ImParticipantMap) -> ServiceResult<()>;
}
