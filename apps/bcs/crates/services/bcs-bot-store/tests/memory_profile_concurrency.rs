use bcs_bot_store::MemoryBotRepo;
use bcs_service_api::types::bot_profile::{AvatarChange, ExistingProfileUpdate, ProfileWrite};
use bcs_service_api::{BotCapabilities, BotControlPlanePatch, BotControlPlaneRepoPort, BotRepoPort};

async fn fixture() -> (tempfile::TempDir, MemoryBotRepo) {
    let dir = tempfile::tempdir().unwrap();
    let repo = MemoryBotRepo::with_base_dir(dir.path().into());
    assert!(repo.create_registration_if_absent(
        "bot".into(), BotCapabilities::default(), "owner", "test-token",
    ).await.unwrap());
    (dir, repo)
}

async fn assert_avatar(repo: &MemoryBotRepo, expected: &str) {
    let avatars = repo.read_avatar_batch(&["bot".into()]).await.unwrap();
    assert_eq!(avatars["bot"].as_deref(), Some(expected));
}

#[tokio::test]
async fn profile_keep_and_avatar_set_preserve_both_concurrent_changes() {
    let (_dir, repo) = fixture().await;
    for round in 0..20 {
        let name = format!("Name {round}");
        let url = format!("https://example.test/{round}.png");
        let (profile, avatar) = tokio::join!(
            repo.update_profile_existing("bot", ExistingProfileUpdate {
                capabilities: BotCapabilities { name: Some(name.clone()), ..Default::default() },
                avatar: AvatarChange::Keep,
            }),
            repo.update_avatar_existing("bot", AvatarChange::Set(url.clone())),
        );
        assert_eq!(profile.unwrap(), ProfileWrite::Updated);
        assert_eq!(avatar.unwrap(), ProfileWrite::Updated);
        assert_eq!(repo.load_from_storage("bot").await.unwrap().name.as_deref(), Some(name.as_str()));
        assert_eq!(repo.get("bot").await.unwrap().capabilities.name.as_deref(), Some(name.as_str()));
        assert_avatar(&repo, &url).await;
    }
}

#[tokio::test]
async fn ordinary_capabilities_and_avatar_writes_preserve_both_changes() {
    let (_dir, repo) = fixture().await;
    for round in 0..20 {
        let caps = BotCapabilities { name: Some(format!("Name {round}")), ..Default::default() };
        let url = format!("https://example.test/{round}.png");
        let (saved, avatar) = tokio::join!(
            repo.save_to_storage("bot", &caps),
            repo.update_avatar_existing("bot", AvatarChange::Set(url.clone())),
        );
        saved.unwrap();
        assert_eq!(avatar.unwrap(), ProfileWrite::Updated);
        assert_eq!(repo.load_from_storage("bot").await.unwrap().name, caps.name);
        assert_avatar(&repo, &url).await;
    }
}

#[tokio::test]
async fn token_rotation_and_avatar_writes_preserve_both_changes() {
    let (_dir, repo) = fixture().await;
    for round in 0..20 {
        let token = format!("test-token-{round}");
        let url = format!("https://example.test/{round}.png");
        let (saved, avatar) = tokio::join!(
            repo.save_token("bot", &token),
            repo.update_avatar_existing("bot", AvatarChange::Set(url.clone())),
        );
        saved.unwrap();
        assert_eq!(avatar.unwrap(), ProfileWrite::Updated);
        // A fresh repository reads the durable token, not the runtime index.
        let restarted = MemoryBotRepo::with_base_dir(_dir.path().into());
        assert_eq!(restarted.load_token("bot").await.as_deref(), Some(token.as_str()));
        assert_avatar(&repo, &url).await;
    }
}

#[tokio::test]
async fn control_plane_patch_and_avatar_writes_preserve_both_changes() {
    let (_dir, repo) = fixture().await;
    let env = bcs_config::resolve_env_str();
    for round in 0..20 {
        let name = format!("Patched {round}");
        let url = format!("https://example.test/{round}.png");
        let (patched, avatar) = tokio::join!(
            repo.patch_control_plane("bot", &env, BotControlPlanePatch {
                name: Some(name.clone()), ..Default::default()
            }),
            repo.update_avatar_existing("bot", AvatarChange::Set(url.clone())),
        );
        assert!(patched.unwrap().is_some());
        assert_eq!(avatar.unwrap(), ProfileWrite::Updated);
        assert_eq!(repo.load_from_storage("bot").await.unwrap().name.as_deref(), Some(name.as_str()));
        assert_avatar(&repo, &url).await;
    }
}

#[tokio::test]
async fn readers_only_observe_complete_records_during_avatar_writes() {
    let (dir, repo) = fixture().await;
    let path = dir.path().join("bot/bot.json");
    let writer = async {
        for round in 0..30 {
            let avatar = if round % 2 == 0 {
                AvatarChange::Set("x".repeat(64 * 1024))
            } else {
                AvatarChange::Clear
            };
            repo.update_avatar_existing("bot", avatar).await.unwrap();
        }
    };
    let reader = async {
        for _ in 0..100 {
            let bytes = tokio::fs::read(&path).await.unwrap();
            let record: serde_json::Value = serde_json::from_slice(&bytes).expect("complete JSON record");
            assert_eq!(record["bot_id"], "bot");
            assert_eq!(record["token"], "test-token");
        }
    };
    tokio::join!(writer, reader);
}

#[cfg(unix)]
#[tokio::test]
async fn failed_replacement_keeps_the_durable_and_live_profile() {
    use std::os::unix::fs::PermissionsExt;

    let (dir, repo) = fixture().await;
    let record_dir = dir.path().join("bot");
    let path = record_dir.join("bot.json");
    let before = std::fs::read(&path).unwrap();
    let permissions = std::fs::metadata(&record_dir).unwrap().permissions();
    std::fs::set_permissions(&record_dir, std::fs::Permissions::from_mode(0o555)).unwrap();
    let result = repo.update_profile_existing("bot", ExistingProfileUpdate {
        capabilities: BotCapabilities { name: Some("Must not publish".into()), ..Default::default() },
        avatar: AvatarChange::Set("https://example.test/failed.png".into()),
    }).await;
    // Restore before assertions so failure does not interfere with fixture cleanup.
    std::fs::set_permissions(&record_dir, permissions).unwrap();

    assert!(result.is_err());
    assert_eq!(std::fs::read(&path).unwrap(), before);
    assert_eq!(repo.get("bot").await.unwrap().capabilities.name, None);
    assert_eq!(repo.read_avatar_batch(&["bot".into()]).await.unwrap()["bot"], None);
}
