//! In-memory implementation of [`RelationRepoPort`].
//!
//! Suitable for unit tests and local single-node mode. The store is keyed by
//! `(from_id, to_id, env)` exactly like the MySQL UNIQUE key in
//! `bcs_actor_relations`.

use std::collections::HashMap;

use async_trait::async_trait;
use tokio::sync::RwLock;
use tracing::debug;

use bcs_service_api::{EnsureOwnerEdgesResult, RelationEdge, RelationRepoPort, ServiceResult};

type EdgeKey = (String, String, String);

/// Pure in-memory implementation of [`RelationRepoPort`].
///
/// All edges live behind a single [`RwLock`]. This is acceptable because
/// relation writes are rare compared to chat traffic (creator binding, friend
/// add / remove, subscribe).
#[derive(Debug, Default)]
pub struct MemoryRelationRepo {
    edges: RwLock<HashMap<EdgeKey, RelationEdge>>,
}

impl MemoryRelationRepo {
    /// Create a new empty in-memory relation store.
    pub fn new() -> Self {
        Self::default()
    }

    fn key(from_id: &str, to_id: &str, env: &str) -> EdgeKey {
        (from_id.to_string(), to_id.to_string(), env.to_string())
    }

    /// Internal helper: insert or update an edge while preserving
    /// `is_creator=TRUE` (cf. `GREATEST(is_creator, VALUES(is_creator))`
    /// in the SQL backend).
    fn upsert_locked(edges: &mut HashMap<EdgeKey, RelationEdge>, edge: RelationEdge) {
        let key = Self::key(&edge.from_id, &edge.to_id, &edge.env);
        edges
            .entry(key)
            .and_modify(|existing| {
                existing.kinds = edge.kinds;
                existing.allow = edge.allow;
                existing.deny = edge.deny;
                // is_creator MUST NOT be downgraded.
                existing.is_creator = existing.is_creator || edge.is_creator;
            })
            .or_insert(edge);
    }

    fn insert_if_absent_locked(edges: &mut HashMap<EdgeKey, RelationEdge>, edge: RelationEdge) {
        let key = Self::key(&edge.from_id, &edge.to_id, &edge.env);
        edges.entry(key).or_insert(edge);
    }

    fn empty_friend_edge(from: &str, to: &str, env: &str, is_creator: bool) -> RelationEdge {
        RelationEdge {
            from_id: from.to_string(),
            to_id: to.to_string(),
            env: env.to_string(),
            kinds: 0,
            allow: 0,
            deny: 0,
            is_creator,
        }
    }
}

#[async_trait]
impl RelationRepoPort for MemoryRelationRepo {
    async fn upsert_edge(&self, edge: RelationEdge) -> ServiceResult<()> {
        let mut edges = self.edges.write().await;
        Self::upsert_locked(&mut edges, edge);
        Ok(())
    }

    async fn delete_edge(&self, from_id: &str, to_id: &str, env: &str) -> ServiceResult<()> {
        let mut edges = self.edges.write().await;
        edges.remove(&Self::key(from_id, to_id, env));
        Ok(())
    }

    async fn get_edge(
        &self,
        from_id: &str,
        to_id: &str,
        env: &str,
    ) -> ServiceResult<Option<RelationEdge>> {
        let edges = self.edges.read().await;
        Ok(edges.get(&Self::key(from_id, to_id, env)).cloned())
    }

    async fn ensure_owner_edges(
        &self,
        human_id: &str,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<()> {
        let mut edges = self.edges.write().await;
        // human → bot, is_creator=TRUE (canonical owner edge)
        Self::upsert_locked(
            &mut edges,
            Self::empty_friend_edge(human_id, bot_id, env, true),
        );
        // bot → human, is_creator=FALSE (reverse edge for traversal)
        Self::insert_if_absent_locked(
            &mut edges,
            Self::empty_friend_edge(bot_id, human_id, env, false),
        );
        debug!(
            human_id = %human_id,
            bot_id = %bot_id,
            env = %env,
            "memory_relation: ensure_owner_edges committed"
        );
        Ok(())
    }

    async fn ensure_owner_edges_counted(
        &self,
        human_id: &str,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<EnsureOwnerEdgesResult> {
        let mut result = EnsureOwnerEdgesResult::default();
        let mut edges = self.edges.write().await;

        // Forward edge: human → bot, is_creator=TRUE
        let fwd_key = Self::key(human_id, bot_id, env);
        let fwd_edge = Self::empty_friend_edge(human_id, bot_id, env, true);
        match edges.get(&fwd_key) {
            None => {
                edges.insert(fwd_key, fwd_edge);
                result.created += 1;
            }
            Some(existing) if !existing.is_creator => {
                // Upgrade is_creator FALSE → TRUE
                Self::upsert_locked(&mut edges, fwd_edge);
                result.upgraded += 1;
            }
            Some(_) => {
                // Already is_creator=TRUE, no change
            }
        }

        // Reverse edge: bot → human, is_creator=FALSE
        let rev_key = Self::key(bot_id, human_id, env);
        if let std::collections::hash_map::Entry::Vacant(entry) = edges.entry(rev_key) {
            entry.insert(Self::empty_friend_edge(bot_id, human_id, env, false));
            result.created += 1;
        }

        debug!(
            human_id = %human_id,
            bot_id = %bot_id,
            env = %env,
            created = result.created,
            upgraded = result.upgraded,
            "memory_relation: ensure_owner_edges_counted committed"
        );
        Ok(result)
    }

    async fn add_friend_edges(&self, a: &str, b: &str, env: &str) -> ServiceResult<()> {
        let mut edges = self.edges.write().await;
        Self::insert_if_absent_locked(&mut edges, Self::empty_friend_edge(a, b, env, false));
        Self::insert_if_absent_locked(&mut edges, Self::empty_friend_edge(b, a, env, false));
        Ok(())
    }

    async fn remove_friend_edges(&self, a: &str, b: &str, env: &str) -> ServiceResult<()> {
        let mut edges = self.edges.write().await;
        // Only remove non-creator edges; never downgrade an is_creator=TRUE
        // owner edge as a side effect of a friend removal.
        for key in [Self::key(a, b, env), Self::key(b, a, env)] {
            if let Some(edge) = edges.get(&key)
                && !edge.is_creator
            {
                edges.remove(&key);
            }
        }
        Ok(())
    }

    async fn remove_all_friend_edges(&self, actor_id: &str, env: &str) -> ServiceResult<()> {
        let mut edges = self.edges.write().await;
        edges.retain(|(from, to, e), edge| {
            let touches = e == env && (from == actor_id || to == actor_id);
            // Keep all edges that do NOT touch this actor;
            // for edges that touch, keep only those flagged as creator (owner).
            !touches || edge.is_creator
        });
        Ok(())
    }

    async fn add_relation_edge(&self, caller: &str, target: &str, env: &str) -> ServiceResult<()> {
        let mut edges = self.edges.write().await;
        // Subscribe / one-directional relation: caller → target, no reverse.
        Self::insert_if_absent_locked(
            &mut edges,
            Self::empty_friend_edge(caller, target, env, false),
        );
        Ok(())
    }

    async fn list_friends_via_relation(
        &self,
        actor_id: &str,
        env: &str,
    ) -> ServiceResult<Vec<String>> {
        let edges = self.edges.read().await;
        let mut out = Vec::new();
        let mut seen = std::collections::HashSet::new();

        // Bidirectional friendship: BOTH the outgoing edge (actor → peer)
        // AND the incoming edge (peer → actor) must exist with
        // `is_creator == false`. We MUST NOT only check the existence of
        // the reverse edge — an owner edge pair
        //   (actor → bot, is_creator=true) + (bot → actor, is_creator=false)
        // would otherwise leak `bot` into the friend list when queried
        // from `bot`'s perspective.
        for ((from, to, e), edge) in edges.iter() {
            if e != env || edge.is_creator {
                continue;
            }
            if from == actor_id {
                let reverse = Self::key(to, from, env);
                let reverse_is_friend = edges
                    .get(&reverse)
                    .map(|rev| !rev.is_creator)
                    .unwrap_or(false);
                if reverse_is_friend && seen.insert(to.clone()) {
                    out.push(to.clone());
                }
            }
        }
        Ok(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn edge(from: &str, to: &str, env: &str, is_creator: bool) -> RelationEdge {
        RelationEdge {
            from_id: from.to_string(),
            to_id: to.to_string(),
            env: env.to_string(),
            kinds: 0,
            allow: 0,
            deny: 0,
            is_creator,
        }
    }

    #[tokio::test]
    async fn upsert_then_get_round_trips_edge() {
        let svc = MemoryRelationRepo::new();
        svc.upsert_edge(edge("h", "b", "dev", true)).await.unwrap();
        let got = svc.get_edge("h", "b", "dev").await.unwrap().unwrap();
        assert_eq!(got.from_id, "h");
        assert_eq!(got.to_id, "b");
        assert!(got.is_creator);
    }

    #[tokio::test]
    async fn upsert_does_not_downgrade_is_creator() {
        let svc = MemoryRelationRepo::new();
        svc.upsert_edge(edge("h", "b", "dev", true)).await.unwrap();
        // A subsequent friend-style write with is_creator=false MUST NOT
        // overwrite the existing TRUE flag.
        svc.upsert_edge(edge("h", "b", "dev", false)).await.unwrap();
        let got = svc.get_edge("h", "b", "dev").await.unwrap().unwrap();
        assert!(got.is_creator, "is_creator must be sticky");
    }

    #[tokio::test]
    async fn ensure_owner_edges_creates_two_edges_with_correct_flags() {
        let svc = MemoryRelationRepo::new();
        svc.ensure_owner_edges("h1", "b1", "dev").await.unwrap();

        let owner = svc.get_edge("h1", "b1", "dev").await.unwrap().unwrap();
        let reverse = svc.get_edge("b1", "h1", "dev").await.unwrap().unwrap();
        assert!(owner.is_creator, "human→bot must be is_creator=true");
        assert!(!reverse.is_creator, "bot→human must be is_creator=false");
    }

    #[tokio::test]
    async fn add_friend_edges_writes_both_directions_idempotently() {
        let svc = MemoryRelationRepo::new();
        svc.add_friend_edges("a", "b", "dev").await.unwrap();
        svc.add_friend_edges("a", "b", "dev").await.unwrap(); // idempotent
        assert!(svc.get_edge("a", "b", "dev").await.unwrap().is_some());
        assert!(svc.get_edge("b", "a", "dev").await.unwrap().is_some());
    }

    #[tokio::test]
    async fn remove_friend_edges_does_not_remove_owner_edges() {
        let svc = MemoryRelationRepo::new();
        svc.ensure_owner_edges("h", "b", "dev").await.unwrap();
        svc.add_friend_edges("h", "b", "dev").await.unwrap();

        svc.remove_friend_edges("h", "b", "dev").await.unwrap();

        // The owner (is_creator=true) edge survives.
        let owner = svc.get_edge("h", "b", "dev").await.unwrap();
        assert!(owner.is_some(), "owner edge must survive friend removal");
        assert!(owner.unwrap().is_creator);
        // The reverse non-creator edge is removed.
        assert!(svc.get_edge("b", "h", "dev").await.unwrap().is_none());
    }

    #[tokio::test]
    async fn remove_all_friend_edges_keeps_owner_edges() {
        let svc = MemoryRelationRepo::new();
        svc.ensure_owner_edges("h", "b1", "dev").await.unwrap();
        svc.add_friend_edges("h", "b2", "dev").await.unwrap();
        svc.add_friend_edges("h", "b3", "dev").await.unwrap();

        svc.remove_all_friend_edges("h", "dev").await.unwrap();

        assert!(
            svc.get_edge("h", "b1", "dev").await.unwrap().is_some(),
            "owner kept"
        );
        assert!(
            svc.get_edge("h", "b2", "dev").await.unwrap().is_none(),
            "friend removed"
        );
        assert!(
            svc.get_edge("h", "b3", "dev").await.unwrap().is_none(),
            "friend removed"
        );
    }

    #[tokio::test]
    async fn add_relation_edge_is_one_directional() {
        let svc = MemoryRelationRepo::new();
        svc.add_relation_edge("caller", "target", "dev")
            .await
            .unwrap();
        assert!(
            svc.get_edge("caller", "target", "dev")
                .await
                .unwrap()
                .is_some()
        );
        assert!(
            svc.get_edge("target", "caller", "dev")
                .await
                .unwrap()
                .is_none(),
            "subscribe must NOT create reverse edge"
        );
    }

    #[tokio::test]
    async fn list_friends_via_relation_requires_both_directions_and_skips_creator() {
        let svc = MemoryRelationRepo::new();
        // a ↔ b friend (both directions, non-creator) → counted
        svc.add_friend_edges("a", "b", "dev").await.unwrap();
        // a ↔ c only one direction → NOT counted
        svc.add_relation_edge("a", "c", "dev").await.unwrap();
        // a → owner → b2 (is_creator=true) → NOT counted as friend
        svc.ensure_owner_edges("a", "b2", "dev").await.unwrap();

        let mut friends = svc.list_friends_via_relation("a", "dev").await.unwrap();
        friends.sort();
        assert_eq!(friends, vec!["b".to_string()]);
    }

    /// Regression test for review Finding #2.
    ///
    /// Querying from the *bot* side of an owner relationship MUST NOT
    /// surface the human as a friend, because the owner edge
    /// `human → bot (is_creator=true)` paired with the reverse
    /// `bot → human (is_creator=false)` does NOT constitute a bidirectional
    /// friendship. Earlier implementation only checked existence of the
    /// reverse edge and would have leaked `human` into the friend list.
    #[tokio::test]
    async fn list_friends_via_relation_does_not_leak_owner_as_friend_from_bot_side() {
        let svc = MemoryRelationRepo::new();
        // Owner pair only: human "a" creates bot "b2".
        svc.ensure_owner_edges("a", "b2", "dev").await.unwrap();

        // From the bot's side there are NO non-creator edges in BOTH
        // directions, so the human MUST NOT appear as a friend of the bot.
        let friends_from_bot = svc.list_friends_via_relation("b2", "dev").await.unwrap();
        assert!(
            friends_from_bot.is_empty(),
            "owner edge must not leak into friend list when queried from the bot side; got {:?}",
            friends_from_bot,
        );

        // And conversely, from the human's side the owned bot also
        // MUST NOT appear as a friend (it's an ownership relationship,
        // not a friendship).
        let friends_from_human = svc.list_friends_via_relation("a", "dev").await.unwrap();
        assert!(
            friends_from_human.is_empty(),
            "owner edge must not leak into friend list when queried from the human side; got {:?}",
            friends_from_human,
        );
    }

    #[tokio::test]
    async fn delete_edge_is_idempotent() {
        let svc = MemoryRelationRepo::new();
        svc.delete_edge("a", "b", "dev").await.unwrap(); // no-op
        svc.upsert_edge(edge("a", "b", "dev", false)).await.unwrap();
        svc.delete_edge("a", "b", "dev").await.unwrap();
        assert!(svc.get_edge("a", "b", "dev").await.unwrap().is_none());
    }

    #[tokio::test]
    async fn env_isolation_holds() {
        let svc = MemoryRelationRepo::new();
        svc.add_friend_edges("a", "b", "dev").await.unwrap();
        svc.add_friend_edges("a", "b", "prod").await.unwrap();
        // Removing in dev does not touch prod.
        svc.remove_friend_edges("a", "b", "dev").await.unwrap();
        assert!(svc.get_edge("a", "b", "dev").await.unwrap().is_none());
        assert!(svc.get_edge("a", "b", "prod").await.unwrap().is_some());
    }
}
