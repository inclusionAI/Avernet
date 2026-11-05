use super::*;

#[derive(Default)]
pub(super) struct FakeRelationCoreService {
    edges: RwLock<HashMap<(String, String, String), RelationEdge>>,
}

impl FakeRelationCoreService {
    pub(super) fn insert_creator(&self, from_id: &str, to_id: &str, env: &str) {
        self.edges.write().unwrap().insert(
            (from_id.to_string(), to_id.to_string(), env.to_string()),
            RelationEdge {
                from_id: from_id.to_string(),
                to_id: to_id.to_string(),
                env: env.to_string(),
                kinds: 0,
                allow: 0,
                deny: 0,
                is_creator: true,
            },
        );
    }
}

#[async_trait]
impl RelationCoreService for FakeRelationCoreService {
    async fn upsert_edge(&self, edge: RelationEdge) -> ServiceResult<()> {
        self.edges.write().unwrap().insert(
            (edge.from_id.clone(), edge.to_id.clone(), edge.env.clone()),
            edge,
        );
        Ok(())
    }

    async fn delete_edge(&self, from_id: &str, to_id: &str, env: &str) -> ServiceResult<()> {
        self.edges.write().unwrap().remove(&(
            from_id.to_string(),
            to_id.to_string(),
            env.to_string(),
        ));
        Ok(())
    }

    async fn get_edge(
        &self,
        from_id: &str,
        to_id: &str,
        env: &str,
    ) -> ServiceResult<Option<RelationEdge>> {
        Ok(self
            .edges
            .read()
            .unwrap()
            .get(&(from_id.to_string(), to_id.to_string(), env.to_string()))
            .cloned())
    }

    async fn ensure_owner_edges(
        &self,
        human_id: &str,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<()> {
        self.insert_creator(human_id, bot_id, env);
        Ok(())
    }

    async fn ensure_owner_edges_counted(
        &self,
        human_id: &str,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<EnsureOwnerEdgesResult> {
        self.insert_creator(human_id, bot_id, env);
        Ok(EnsureOwnerEdgesResult {
            created: 1,
            upgraded: 0,
        })
    }

    async fn add_friend_edges(&self, _a: &str, _b: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn remove_friend_edges(&self, _a: &str, _b: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn remove_all_friend_edges(&self, _actor_id: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn add_relation_edge(&self, caller: &str, target: &str, env: &str) -> ServiceResult<()> {
        self.upsert_edge(RelationEdge {
            from_id: caller.to_string(),
            to_id: target.to_string(),
            env: env.to_string(),
            kinds: 0,
            allow: 0,
            deny: 0,
            is_creator: false,
        })
        .await
    }

    async fn list_friends_via_relation(
        &self,
        _actor_id: &str,
        _env: &str,
    ) -> ServiceResult<Vec<String>> {
        Ok(Vec::new())
    }
}
