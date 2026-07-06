use async_trait::async_trait;

use crate::types::{EnsureOwnerEdgesResult, RelationEdge, ServiceResult};

/// Repository contract for relation graph persistence implementations.
///
/// This is intentionally independent from `RelationCoreService`: repositories
/// own storage and row/domain mapping, while the core service owns relation
/// graph behavior exposed to application services.
#[async_trait]
pub trait RelationRepoPort: Send + Sync {
    async fn upsert_edge(&self, edge: RelationEdge) -> ServiceResult<()>;
    async fn delete_edge(&self, from_id: &str, to_id: &str, env: &str) -> ServiceResult<()>;
    async fn get_edge(
        &self,
        from_id: &str,
        to_id: &str,
        env: &str,
    ) -> ServiceResult<Option<RelationEdge>>;
    async fn ensure_owner_edges(
        &self,
        human_id: &str,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<()>;
    async fn ensure_owner_edges_counted(
        &self,
        human_id: &str,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<EnsureOwnerEdgesResult>;
    async fn add_friend_edges(&self, a: &str, b: &str, env: &str) -> ServiceResult<()>;
    async fn remove_friend_edges(&self, a: &str, b: &str, env: &str) -> ServiceResult<()>;
    async fn remove_all_friend_edges(&self, actor_id: &str, env: &str) -> ServiceResult<()>;
    async fn add_relation_edge(&self, caller: &str, target: &str, env: &str) -> ServiceResult<()>;
    async fn list_friends_via_relation(
        &self,
        actor_id: &str,
        env: &str,
    ) -> ServiceResult<Vec<String>>;
}
