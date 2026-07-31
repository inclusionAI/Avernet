use std::collections::{HashMap, HashSet};
use std::path::PathBuf;

use async_trait::async_trait;
use bcs_service_api::{
    FriendRequest, FriendRequestDirection, FriendRequestStatus, Friendship, ServiceError,
    ServiceResult,
};
use serde::{Deserialize, Serialize};
use tokio::sync::RwLock;
use tracing::info;

use crate::{FriendRepoPort, FriendRequestRepoPort};

/// A persisted friendship record.
/// `left_bot < right_bot` by lexicographic order to ensure uniqueness.
#[derive(Debug, Clone, Serialize, Deserialize)]
struct FriendshipRecord {
    left_bot: String,
    right_bot: String,
    created_at: u64,
}

/// In-memory friendship repository with optional file persistence.
pub struct MemoryFriendRepo {
    pairs: RwLock<HashSet<(String, String)>>,
    records: RwLock<Vec<FriendshipRecord>>,
    data_dir: Option<PathBuf>,
}

impl MemoryFriendRepo {
    pub fn new() -> Self {
        Self {
            pairs: RwLock::new(HashSet::new()),
            records: RwLock::new(Vec::new()),
            data_dir: None,
        }
    }

    pub fn with_data_dir(data_dir: PathBuf) -> Self {
        Self {
            pairs: RwLock::new(HashSet::new()),
            records: RwLock::new(Vec::new()),
            data_dir: Some(data_dir),
        }
    }

    pub async fn load_from_disk(&self) -> ServiceResult<()> {
        let Some(ref dir) = self.data_dir else {
            return Ok(());
        };
        let path = dir.join("friendships.json");
        if !path.exists() {
            return Ok(());
        }

        let data = tokio::fs::read_to_string(&path).await?;
        let loaded: Vec<FriendshipRecord> = serde_json::from_str(&data)?;
        let mut pairs = self.pairs.write().await;
        pairs.clear();
        for record in &loaded {
            pairs.insert((record.left_bot.clone(), record.right_bot.clone()));
        }
        drop(pairs);

        let count = loaded.len();
        *self.records.write().await = loaded;
        info!(count, "Loaded friendships from disk");
        Ok(())
    }

    async fn save_to_disk(&self) -> ServiceResult<()> {
        let Some(ref dir) = self.data_dir else {
            return Ok(());
        };
        tokio::fs::create_dir_all(dir).await?;
        let records = self.records.read().await;
        let data = serde_json::to_string_pretty(&*records)?;
        tokio::fs::write(dir.join("friendships.json"), data).await?;
        Ok(())
    }

    fn normalize_pair(a: &str, b: &str) -> (String, String) {
        if a <= b {
            (a.to_string(), b.to_string())
        } else {
            (b.to_string(), a.to_string())
        }
    }
}

impl Default for MemoryFriendRepo {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl FriendRepoPort for MemoryFriendRepo {
    async fn list_friends(&self, bot_id: &str) -> ServiceResult<Vec<String>> {
        let pairs = self.pairs.read().await;
        let mut friends = Vec::new();
        for (left, right) in pairs.iter() {
            if left == bot_id {
                friends.push(right.clone());
            } else if right == bot_id {
                friends.push(left.clone());
            }
        }
        Ok(friends)
    }

    async fn are_friends(&self, bot_a: &str, bot_b: &str) -> ServiceResult<bool> {
        let pair = Self::normalize_pair(bot_a, bot_b);
        Ok(self.pairs.read().await.contains(&pair))
    }

    async fn add_friendship(&self, bot_a: &str, bot_b: &str) -> ServiceResult<()> {
        let (left, right) = Self::normalize_pair(bot_a, bot_b);
        let mut pairs = self.pairs.write().await;
        if !pairs.insert((left.clone(), right.clone())) {
            return Ok(());
        }
        drop(pairs);

        self.records.write().await.push(FriendshipRecord {
            left_bot: left.clone(),
            right_bot: right.clone(),
            created_at: now_millis(),
        });

        if let Err(err) = self.save_to_disk().await {
            tracing::warn!(left_bot = %left, right_bot = %right, path = ?self.data_dir, error = %err, "Failed to persist friendship to disk");
        }
        info!(left_bot = %left, right_bot = %right, "Friendship stored");
        Ok(())
    }

    async fn remove_all_friendships(&self, bot_id: &str) -> ServiceResult<usize> {
        let mut pairs = self.pairs.write().await;
        let before = pairs.len();
        pairs.retain(|(left, right)| left != bot_id && right != bot_id);
        let removed = before - pairs.len();
        drop(pairs);

        self.records
            .write()
            .await
            .retain(|record| record.left_bot != bot_id && record.right_bot != bot_id);

        if removed > 0 {
            if let Err(err) = self.save_to_disk().await {
                tracing::warn!(bot_id = %bot_id, path = ?self.data_dir, error = %err, "Failed to persist friendship removal to disk");
            }
            info!(bot_id = %bot_id, removed, "Removed friendships for bot");
        }
        Ok(removed)
    }

    async fn list_friendships_paginated(
        &self,
        bot_id: &str,
        offset: u64,
        limit: u64,
    ) -> ServiceResult<(Vec<Friendship>, u64)> {
        let records = self.records.read().await;
        let mut projected: Vec<Friendship> = records
            .iter()
            .filter(|record| record.left_bot == bot_id || record.right_bot == bot_id)
            .map(|record| {
                let friend_bot_uuid = if record.left_bot == bot_id {
                    record.right_bot.clone()
                } else {
                    record.left_bot.clone()
                };
                Friendship {
                    bot_uuid: bot_id.to_string(),
                    friend_bot_uuid,
                    created_at: record.created_at,
                }
            })
            .collect();
        // created_at DESC, friend_bot_uuid ASC tie-breaker.
        projected.sort_by(|a, b| {
            b.created_at
                .cmp(&a.created_at)
                .then_with(|| a.friend_bot_uuid.cmp(&b.friend_bot_uuid))
        });
        let total = projected.len() as u64;
        let page = projected
            .into_iter()
            .skip(usize::try_from(offset).unwrap_or(0))
            .take(usize::try_from(limit).unwrap_or(usize::MAX))
            .collect();
        Ok((page, total))
    }

    async fn remove_friendship(&self, bot_a: &str, bot_b: &str) -> ServiceResult<bool> {
        let (left, right) = Self::normalize_pair(bot_a, bot_b);
        let existed = {
            let mut pairs = self.pairs.write().await;
            pairs.remove(&(left.clone(), right.clone()))
        };

        if !existed {
            return Ok(false);
        }

        // Capture the record before removing it so the in-memory state can be
        // restored if file persistence fails — keeping memory consistent with
        // the still-present on-disk record (a restart would reload it from
        // disk since the file write did not happen).
        let removed_record = {
            let mut records = self.records.write().await;
            let mut removed = None;
            records.retain(|record| {
                if record.left_bot == left && record.right_bot == right {
                    removed = Some(record.clone());
                    false
                } else {
                    true
                }
            });
            removed
        };

        if let Err(err) = self.save_to_disk().await {
            // Persistence failed: restore the in-memory friendship so memory
            // matches the unchanged on-disk record, and propagate the failure
            // so callers know the deletion was not durable instead of
            // reporting a false success that a restart would undo.
            tracing::error!(
                left_bot = %left,
                right_bot = %right,
                path = ?self.data_dir,
                error = %err,
                "Failed to persist friendship removal to disk; restoring in-memory state",
            );
            self.pairs
                .write()
                .await
                .insert((left.clone(), right.clone()));
            if let Some(record) = removed_record {
                self.records.write().await.push(record);
            }
            return Err(ServiceError::InternalError(format!(
                "failed to persist friendship removal ({left}, {right}) to disk"
            )));
        }

        info!(left_bot = %left, right_bot = %right, "Friendship removed");
        Ok(true)
    }
}

/// In-memory friend-request repository with optional file persistence.
pub struct MemoryFriendRequestRepo {
    requests: RwLock<HashMap<String, FriendRequest>>,
    data_dir: Option<PathBuf>,
}

impl MemoryFriendRequestRepo {
    pub fn new() -> Self {
        Self {
            requests: RwLock::new(HashMap::new()),
            data_dir: None,
        }
    }

    pub fn with_data_dir(data_dir: PathBuf) -> Self {
        Self {
            requests: RwLock::new(HashMap::new()),
            data_dir: Some(data_dir),
        }
    }

    pub async fn load_from_disk(&self) -> ServiceResult<()> {
        let Some(ref dir) = self.data_dir else {
            return Ok(());
        };
        let path = dir.join("friend_requests.json");
        if !path.exists() {
            return Ok(());
        }

        let data = tokio::fs::read_to_string(&path).await?;
        let loaded: Vec<FriendRequest> = serde_json::from_str(&data)?;
        let count = loaded.len();
        let mut requests = self.requests.write().await;
        requests.clear();
        for request in loaded {
            requests.insert(request.id.clone(), request);
        }
        info!(count, "Loaded friend requests from disk");
        Ok(())
    }

    async fn save_to_disk(&self) -> ServiceResult<()> {
        let Some(ref dir) = self.data_dir else {
            return Ok(());
        };
        tokio::fs::create_dir_all(dir).await?;
        let requests = self.requests.read().await;
        let records: Vec<&FriendRequest> = requests.values().collect();
        let data = serde_json::to_string_pretty(&records)?;
        tokio::fs::write(dir.join("friend_requests.json"), data).await?;
        Ok(())
    }
}

impl Default for MemoryFriendRequestRepo {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl FriendRequestRepoPort for MemoryFriendRequestRepo {
    async fn find_pending_request(
        &self,
        from_bot: &str,
        to_bot: &str,
    ) -> ServiceResult<Option<FriendRequest>> {
        Ok(self
            .requests
            .read()
            .await
            .values()
            .find(|request| {
                request.from_bot == from_bot
                    && request.to_bot == to_bot
                    && request.status == FriendRequestStatus::Pending
            })
            .cloned())
    }

    async fn insert_pending_request_if_absent(
        &self,
        request: FriendRequest,
    ) -> ServiceResult<Option<FriendRequest>> {
        let mut requests = self.requests.write().await;
        if let Some(existing) = requests
            .values()
            .find(|existing| {
                existing.from_bot == request.from_bot
                    && existing.to_bot == request.to_bot
                    && existing.status == FriendRequestStatus::Pending
            })
            .cloned()
        {
            return Ok(Some(existing));
        }

        requests.insert(request.id.clone(), request.clone());
        drop(requests);

        if let Err(err) = self.save_to_disk().await {
            tracing::warn!(request_id = %request.id, path = ?self.data_dir, error = %err, "Failed to persist friend request to disk");
        }
        Ok(None)
    }

    async fn insert_request(&self, request: FriendRequest) -> ServiceResult<()> {
        self.requests
            .write()
            .await
            .insert(request.id.clone(), request.clone());
        if let Err(err) = self.save_to_disk().await {
            tracing::warn!(request_id = %request.id, path = ?self.data_dir, error = %err, "Failed to persist friend request to disk");
        }
        Ok(())
    }

    async fn update_request_status(
        &self,
        request_id: &str,
        status: FriendRequestStatus,
    ) -> ServiceResult<()> {
        let mut requests = self.requests.write().await;
        let request = requests
            .get_mut(request_id)
            .ok_or_else(|| ServiceError::FriendRequestNotFound(request_id.to_string()))?;
        request.status = status;
        request.updated_at = now_millis();
        drop(requests);

        if let Err(err) = self.save_to_disk().await {
            tracing::warn!(request_id = %request_id, path = ?self.data_dir, error = %err, "Failed to persist friend request status update");
        }
        Ok(())
    }

    async fn accept_reverse_pending_requests(
        &self,
        from_bot: &str,
        to_bot: &str,
    ) -> ServiceResult<usize> {
        let mut requests = self.requests.write().await;
        let now = now_millis();
        let mut affected = 0;
        for request in requests.values_mut() {
            if request.from_bot == to_bot
                && request.to_bot == from_bot
                && request.status == FriendRequestStatus::Pending
            {
                request.status = FriendRequestStatus::Accepted;
                request.updated_at = now;
                affected += 1;
            }
        }
        drop(requests);

        if affected > 0
            && let Err(err) = self.save_to_disk().await
        {
            tracing::warn!(from = %to_bot, to = %from_bot, path = ?self.data_dir, error = %err, "Failed to persist reverse friend request acceptance");
        }
        Ok(affected)
    }

    async fn get_request(&self, request_id: &str) -> ServiceResult<FriendRequest> {
        self.requests
            .read()
            .await
            .get(request_id)
            .cloned()
            .ok_or_else(|| ServiceError::FriendRequestNotFound(request_id.to_string()))
    }

    async fn list_requests(
        &self,
        bot_id: &str,
        direction: FriendRequestDirection,
        status_filter: Option<FriendRequestStatus>,
    ) -> Vec<FriendRequest> {
        self.requests
            .read()
            .await
            .values()
            .filter(|request| {
                let direction_match = match direction {
                    FriendRequestDirection::Received => request.to_bot == bot_id,
                    FriendRequestDirection::Sent => request.from_bot == bot_id,
                    FriendRequestDirection::All => {
                        request.from_bot == bot_id || request.to_bot == bot_id
                    }
                };
                let status_match = status_filter
                    .as_ref()
                    .map(|status| request.status == *status)
                    .unwrap_or(true);
                direction_match && status_match
            })
            .cloned()
            .collect()
    }

    async fn delete_pending_requests_for_bot(&self, bot_id: &str) -> ServiceResult<usize> {
        let mut requests = self.requests.write().await;
        let before = requests.len();
        requests.retain(|_, request| {
            !(request.status == FriendRequestStatus::Pending
                && (request.from_bot == bot_id || request.to_bot == bot_id))
        });
        let removed = before - requests.len();
        drop(requests);

        if removed > 0
            && let Err(err) = self.save_to_disk().await
        {
            tracing::warn!(bot_id = %bot_id, path = ?self.data_dir, error = %err, "Failed to persist pending friend request deletion");
        }
        Ok(removed)
    }

    async fn insert_accepted_request_if_absent(
        &self,
        request: FriendRequest,
    ) -> ServiceResult<FriendRequest> {
        let mut requests = self.requests.write().await;
        requests.insert(request.id.clone(), request.clone());
        drop(requests);

        if let Err(err) = self.save_to_disk().await {
            tracing::warn!(request_id = %request.id, path = ?self.data_dir, error = %err, "Failed to persist accepted friend request to disk");
        }
        Ok(request)
    }
}

fn now_millis() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn must_service<T>(result: ServiceResult<T>) -> T {
        match result {
            Ok(value) => value,
            Err(err) => panic!("expected service Ok, got {}", err),
        }
    }

    /// Seed a friendship with a controlled `created_at` so ordering is
    /// deterministic (independent of wall-clock millisecond resolution).
    /// Pairs are normalized to `left <= right` to match `add_friendship`.
    async fn seed(repo: &MemoryFriendRepo, left: &str, right: &str, created_at: u64) {
        let (l, r) = if left <= right {
            (left.to_string(), right.to_string())
        } else {
            (right.to_string(), left.to_string())
        };
        repo.pairs.write().await.insert((l.clone(), r.clone()));
        repo.records.write().await.push(FriendshipRecord {
            left_bot: l,
            right_bot: r,
            created_at,
        });
    }

    #[tokio::test]
    async fn list_friendships_paginated_orders_desc_and_paginates() {
        let repo = MemoryFriendRepo::new();
        // a-b (300), a-c (200), b-c (100). For bot "a": a-b, a-c.
        seed(&repo, "a", "b", 300).await;
        seed(&repo, "a", "c", 200).await;
        seed(&repo, "b", "c", 100).await;

        let (page, total) = must_service(repo.list_friendships_paginated("a", 0, 10).await);
        assert_eq!(total, 2);
        assert_eq!(page.len(), 2);
        assert_eq!(page[0].bot_uuid, "a");
        assert_eq!(page[0].friend_bot_uuid, "b"); // created_at 300 → first
        assert_eq!(page[0].created_at, 300);
        assert_eq!(page[1].friend_bot_uuid, "c"); // created_at 200 → second
        assert_eq!(page[1].created_at, 200);

        // limit
        let (first_only, total) = must_service(repo.list_friendships_paginated("a", 0, 1).await);
        assert_eq!(total, 2);
        assert_eq!(first_only.len(), 1);
        assert_eq!(first_only[0].friend_bot_uuid, "b");

        // offset
        let (second, total) = must_service(repo.list_friendships_paginated("a", 1, 10).await);
        assert_eq!(total, 2);
        assert_eq!(second.len(), 1);
        assert_eq!(second[0].friend_bot_uuid, "c");

        // symmetric: bot "b" sees b-a(300) and b-c(100), DESC → friend a then c.
        let (b_page, b_total) = must_service(repo.list_friendships_paginated("b", 0, 10).await);
        assert_eq!(b_total, 2);
        assert_eq!(b_page[0].bot_uuid, "b");
        assert_eq!(b_page[0].friend_bot_uuid, "a");
        assert_eq!(b_page[0].created_at, 300);
        assert_eq!(b_page[1].friend_bot_uuid, "c");
        assert_eq!(b_page[1].created_at, 100);

        // unknown bot → empty, total 0
        let (empty, zero) = must_service(repo.list_friendships_paginated("zzz", 0, 10).await);
        assert!(empty.is_empty());
        assert_eq!(zero, 0);
    }

    #[tokio::test]
    async fn list_friendships_paginated_tiebreaker_friend_bot_uuid_asc() {
        let repo = MemoryFriendRepo::new();
        // Same created_at for both of a's friendships → tie broken by
        // friend_bot_uuid ASC: b before c.
        seed(&repo, "a", "b", 500).await;
        seed(&repo, "a", "c", 500).await;

        let (page, total) = must_service(repo.list_friendships_paginated("a", 0, 10).await);
        assert_eq!(total, 2);
        assert_eq!(page[0].friend_bot_uuid, "b");
        assert_eq!(page[1].friend_bot_uuid, "c");
    }

    #[tokio::test]
    async fn remove_friendship_is_idempotent_and_excludes_from_list() {
        let repo = MemoryFriendRepo::new();
        seed(&repo, "a", "b", 300).await;
        seed(&repo, "a", "c", 200).await;

        // Remove via reversed argument order — normalize_pair hits same row.
        let first = must_service(repo.remove_friendship("b", "a").await);
        assert!(first, "first remove of existing pair returns true");

        // Subsequent remove is idempotent (returns false, no row touched).
        let second = must_service(repo.remove_friendship("a", "b").await);
        assert!(!second, "second remove of absent pair returns false");

        // list now excludes a-b, keeps a-c, total drops to 1.
        let (page, total) = must_service(repo.list_friendships_paginated("a", 0, 10).await);
        assert_eq!(total, 1);
        assert_eq!(page.len(), 1);
        assert_eq!(page[0].friend_bot_uuid, "c");

        // are_friends reflects removal; a-c still friends.
        assert!(!must_service(repo.are_friends("a", "b").await));
        assert!(must_service(repo.are_friends("a", "c").await));
    }

    #[tokio::test]
    async fn remove_friendship_propagates_disk_persistence_failure() {
        // Point `data_dir` at a regular file so `save_to_disk`'s
        // `create_dir_all` fails, simulating a read-only/full filesystem.
        let temp = match tempfile::tempdir() {
            Ok(t) => t,
            Err(err) => panic!("tempdir failed: {err}"),
        };
        let blocker = temp.path().join("blocker");
        if let Err(err) = tokio::fs::write(&blocker, b"not a dir").await {
            panic!("write blocker file failed: {err}");
        }
        let repo = MemoryFriendRepo::with_data_dir(blocker);

        // Seed a friendship directly into memory (do not touch disk).
        seed(&repo, "a", "b", 300).await;
        assert!(
            must_service(repo.are_friends("a", "b").await),
            "seeded friendship should be present",
        );

        // Removing must propagate the persistence failure rather than Ok(true),
        // otherwise the API would report a durable deletion that a restart
        // reloads from disk.
        let result = repo.remove_friendship("a", "b").await;
        assert!(
            result.is_err(),
            "remove_friendship should return Err when disk persistence fails, got {result:?}",
        );

        // In-memory state must stay consistent with the (unchanged) disk
        // state: the friendship is still present after the failed removal.
        assert!(
            must_service(repo.are_friends("a", "b").await),
            "in-memory friendship should be restored after persistence failure",
        );
        let (page, total) = must_service(repo.list_friendships_paginated("a", 0, 10).await);
        assert_eq!(total, 1, "friendship record should be restored");
        assert_eq!(page.len(), 1);
        assert_eq!(page[0].friend_bot_uuid, "b");
    }

    #[tokio::test]
    async fn list_friends_compat_returns_uuids_after_new_methods() {
        // Compat guard: legacy list_friends still returns the uuid list (no
        // created_at, no ordering contract) alongside the new methods.
        let repo = MemoryFriendRepo::new();
        seed(&repo, "a", "b", 300).await;
        seed(&repo, "a", "c", 200).await;

        let mut friends = must_service(repo.list_friends("a").await);
        friends.sort();
        assert_eq!(friends, vec!["b".to_string(), "c".to_string()]);

        // are_friends legacy API unchanged (symmetric).
        assert!(must_service(repo.are_friends("b", "a").await));
    }
}
