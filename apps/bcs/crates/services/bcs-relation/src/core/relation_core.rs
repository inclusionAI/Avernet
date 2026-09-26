use std::sync::Arc;

use async_trait::async_trait;
use bcs_relation_store::MemoryRelationRepo;
use bcs_service_api::{
    EnsureOwnerEdgesResult, RelationCoreService, RelationEdge, RelationRepoPort, ServiceResult,
};

/// Core relation service implementation.
///
/// `RelationCore` exposes relation-graph business operations and delegates
/// persistence to a repository.
#[derive(Clone)]
pub struct RelationCore {
    repo: Arc<dyn RelationRepoPort>,
}

impl RelationCore {
    pub fn new() -> Self {
        Self::memory()
    }

    pub fn memory() -> Self {
        Self::with_repo(Arc::new(MemoryRelationRepo::new()))
    }

    pub fn with_repo(repo: Arc<dyn RelationRepoPort>) -> Self {
        Self { repo }
    }
}

impl Default for RelationCore {
    fn default() -> Self {
        Self::memory()
    }
}

#[async_trait]
impl RelationCoreService for RelationCore {
    async fn upsert_edge(&self, edge: RelationEdge) -> ServiceResult<()> {
        self.repo.upsert_edge(edge).await
    }

    async fn delete_edge(&self, from_id: &str, to_id: &str, env: &str) -> ServiceResult<()> {
        self.repo.delete_edge(from_id, to_id, env).await
    }

    async fn get_edge(
        &self,
        from_id: &str,
        to_id: &str,
        env: &str,
    ) -> ServiceResult<Option<RelationEdge>> {
        self.repo.get_edge(from_id, to_id, env).await
    }

    async fn ensure_owner_edges(
        &self,
        human_id: &str,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<()> {
        self.repo.ensure_owner_edges(human_id, bot_id, env).await
    }

    async fn ensure_owner_edges_counted(
        &self,
        human_id: &str,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<EnsureOwnerEdgesResult> {
        self.repo
            .ensure_owner_edges_counted(human_id, bot_id, env)
            .await
    }

    async fn add_friend_edges(&self, a: &str, b: &str, env: &str) -> ServiceResult<()> {
        self.repo.add_friend_edges(a, b, env).await
    }

    async fn remove_friend_edges(&self, a: &str, b: &str, env: &str) -> ServiceResult<()> {
        self.repo.remove_friend_edges(a, b, env).await
    }

    async fn remove_all_friend_edges(&self, actor_id: &str, env: &str) -> ServiceResult<()> {
        self.repo.remove_all_friend_edges(actor_id, env).await
    }

    async fn add_relation_edge(&self, caller: &str, target: &str, env: &str) -> ServiceResult<()> {
        self.repo.add_relation_edge(caller, target, env).await
    }

    async fn list_friends_via_relation(
        &self,
        actor_id: &str,
        env: &str,
    ) -> ServiceResult<Vec<String>> {
        self.repo.list_friends_via_relation(actor_id, env).await
    }
}
