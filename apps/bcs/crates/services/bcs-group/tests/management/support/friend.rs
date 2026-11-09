use super::*;

#[derive(Default)]
pub(super) struct FakeFriendCoreService {
    pairs: RwLock<HashSet<(String, String)>>,
}

fn canonical_pair(a: &str, b: &str) -> (String, String) {
    if a <= b {
        (a.to_string(), b.to_string())
    } else {
        (b.to_string(), a.to_string())
    }
}

impl FakeFriendCoreService {
    pub(super) fn insert(&self, a: &str, b: &str) {
        self.pairs.write().unwrap().insert(canonical_pair(a, b));
    }
}

#[async_trait]
impl FriendCoreService for FakeFriendCoreService {
    async fn list_friends(&self, bot_id: &str) -> Vec<String> {
        self.pairs
            .read()
            .unwrap()
            .iter()
            .filter_map(|(a, b)| {
                if a == bot_id {
                    Some(b.clone())
                } else if b == bot_id {
                    Some(a.clone())
                } else {
                    None
                }
            })
            .collect()
    }

    async fn are_friends(&self, bot_a: &str, bot_b: &str) -> bool {
        self.pairs
            .read()
            .unwrap()
            .contains(&canonical_pair(bot_a, bot_b))
    }

    async fn are_all_friends(&self, bot_id: &str, others: &[String]) -> ServiceResult<()> {
        let pairs = self.pairs.read().unwrap();
        let not_friends: Vec<String> = others
            .iter()
            .filter(|other| !pairs.contains(&canonical_pair(bot_id, other)))
            .cloned()
            .collect();
        if not_friends.is_empty() {
            Ok(())
        } else {
            Err(ServiceError::NotFriends(not_friends))
        }
    }

    async fn add_friendship(&self, bot_a: &str, bot_b: &str) -> ServiceResult<()> {
        self.insert(bot_a, bot_b);
        Ok(())
    }

    async fn remove_all_friendships(&self, bot_id: &str) -> ServiceResult<usize> {
        let mut pairs = self.pairs.write().unwrap();
        let before = pairs.len();
        pairs.retain(|(a, b)| a != bot_id && b != bot_id);
        Ok(before - pairs.len())
    }
}
