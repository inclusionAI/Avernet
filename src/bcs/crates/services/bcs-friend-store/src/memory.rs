use std::collections::{HashMap, HashSet};
use std::path::PathBuf;

use async_trait::async_trait;
use bcs_service_api::{
    FriendRequest, FriendRequestDirection, FriendRequestStatus, ServiceError, ServiceResult,
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
