use super::*;
use crate::memory::{MemoryEdgeGrantStore, MemoryPermissionProfileStore};

async fn check_candidates(store: &dyn EdgeGrantRepoPort, profiles: &dyn PermissionProfileRepoPort) {
    let human = "human_alice";
    let env = "dev";
    let active = profiles
        .ensure_default_profile("friend", env)
        .await
        .unwrap();
    let edge = store
        .insert_grant(default_grant(human, "friend", env, active))
        .await
        .unwrap();
    let omitted = profiles
        .ensure_default_profile("omitted", env)
        .await
        .unwrap();
    store
        .insert_grant(default_grant(human, "omitted", env, omitted))
        .await
        .unwrap();
    let non_default = profiles
        .ensure_default_profile("non-default", env)
        .await
        .unwrap()
        + 100;
    store
        .insert_grant(default_grant(human, "non-default", env, non_default))
        .await
        .unwrap();
    let other_env = profiles
        .ensure_default_profile("elsewhere", "other")
        .await
        .unwrap();
    store
        .insert_grant(default_grant(human, "elsewhere", "other", other_env))
        .await
        .unwrap();
    let other_owner = profiles
        .ensure_default_profile("other-human", env)
        .await
        .unwrap();
    store
        .insert_grant(default_grant("human_bob", "other-human", env, other_owner))
        .await
        .unwrap();

    let mut candidates: Vec<_> = (0..101).map(|n| format!("unknown-{n}")).collect();
    candidates.extend(
        [
            "friend",
            "friend",
            "Friend",
            "non-default",
            "elsewhere",
            "other-human",
        ]
        .map(str::to_string),
    );
    let result = store
        .find_human_friend_bots(human, env, &candidates)
        .await
        .unwrap();
    assert_eq!(result, HashSet::from(["friend".to_string()]));
    assert!(
        store
            .find_human_friend_bots("human_Alice", env, &candidates)
            .await
            .unwrap()
            .is_empty()
    );
    store.revoke_grant(edge, env).await.unwrap();
    assert!(
        store
            .find_human_friend_bots(human, env, &candidates)
            .await
            .unwrap()
            .is_empty()
    );
}

#[tokio::test]
async fn sqlite_human_friend_lookup_is_bounded_and_fallible() {
    let store = sqlite_store().await;
    let profiles = DbPermissionProfileStore::sqlite(store.db.clone());
    check_candidates(&store, &profiles).await;
    store
        .db
        .execute(DbStatement::new("DROP TABLE permission_profiles"))
        .await
        .unwrap();
    assert!(
        store
            .find_human_friend_bots("human_alice", "dev", &["friend".into()])
            .await
            .is_err()
    );
    assert!(
        store
            .find_human_friend_bots("human_alice", "dev", &[])
            .await
            .unwrap()
            .is_empty()
    );
}

#[tokio::test]
async fn memory_human_friend_lookup_matches_sqlite() {
    let profiles = Arc::new(MemoryPermissionProfileStore::new());
    let store = MemoryEdgeGrantStore::new(profiles.clone());
    check_candidates(&store, profiles.as_ref()).await;
}
