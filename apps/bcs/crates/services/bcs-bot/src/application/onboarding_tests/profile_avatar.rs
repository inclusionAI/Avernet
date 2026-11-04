//! Admin onboard avatar application tests (bot-avatar plan, Task 5).
//!
//! The admin update entry point carries an `AvatarChange` alongside the
//! merged capabilities and applies both through the existing-only profile
//! write. The key regression is the missing-after-precheck window: the
//! precheck `get` still observes the bot, but the existing-only write
//! classifies the target `Missing` — the response keeps the legacy
//! `onboarded=false` shape and no row is created.

use bcs_service_api::types::bot_profile::AvatarChange;

use super::*;

fn admin_command(bot_uuid: &str, avatar: AvatarChange) -> AdminBotOnboardCommand {
    AdminBotOnboardCommand {
        bot_uuid: bot_uuid.to_string(),
        name: Some("Admin Bot".to_string()),
        summary: None,
        domains: vec!["ops".to_string()],
        skills: Vec::new(),
        scopes: Vec::new(),
        binding_channels: None,
        actor_identity: None,
        avatar,
    }
}

fn avatar_service(
    registry: Arc<StaticRegistry>,
) -> BotOnboarding {
    BotOnboarding::new(
        registry,
        Arc::new(RecordingRelationCoreService::default()),
        true,
        None,
    )
}

#[tokio::test]
async fn admin_onboard_applies_an_explicit_avatar_with_the_profile_write() {
    let registry = Arc::new(StaticRegistry::new(vec![bot(
        "target",
        BotCapabilities::default(),
    )]));
    let service = avatar_service(registry.clone());

    let result = service
        .admin_onboard_bot(admin_command(
            "target",
            AvatarChange::Set("https://cdn.example.com/admin.png".to_string()),
        ))
        .await
        .unwrap();

    assert!(result.onboarded);
    assert_eq!(
        registry.stored_avatar("target").await,
        Some(Some("https://cdn.example.com/admin.png".to_string()))
    );
    let writes = registry.profile_writes().await;
    assert_eq!(writes.len(), 1, "one existing-only profile write");
    assert_eq!(writes[0].0, "target");
    assert_eq!(
        writes[0].1.avatar,
        AvatarChange::Set("https://cdn.example.com/admin.png".to_string())
    );
    // The profile write replaced the capabilities in the same write.
    let stored = registry.get("target").await.unwrap();
    assert_eq!(stored.capabilities.name.as_deref(), Some("Admin Bot"));
    assert_eq!(stored.capabilities.domains, vec!["ops".to_string()]);
}

#[tokio::test]
async fn admin_onboard_keeps_the_stored_avatar_when_the_field_is_omitted() {
    let registry = Arc::new(StaticRegistry::new(vec![bot(
        "target",
        BotCapabilities::default(),
    )]));
    registry
        .store_avatar("target", Some("https://cdn.example.com/old.png"))
        .await;
    let service = avatar_service(registry.clone());

    let result = service
        .admin_onboard_bot(admin_command("target", AvatarChange::Keep))
        .await
        .unwrap();

    assert!(result.onboarded);
    assert_eq!(
        registry.stored_avatar("target").await,
        Some(Some("https://cdn.example.com/old.png".to_string())),
        "an omitted avatar must preserve the stored value"
    );
    let writes = registry.profile_writes().await;
    assert_eq!(writes.len(), 1);
    assert_eq!(writes[0].1.avatar, AvatarChange::Keep);
}

#[tokio::test]
async fn admin_onboard_clears_the_stored_avatar_with_an_empty_transition() {
    let registry = Arc::new(StaticRegistry::new(vec![bot(
        "target",
        BotCapabilities::default(),
    )]));
    registry
        .store_avatar("target", Some("https://cdn.example.com/old.png"))
        .await;
    let service = avatar_service(registry.clone());

    let result = service
        .admin_onboard_bot(admin_command("target", AvatarChange::Clear))
        .await
        .unwrap();

    assert!(result.onboarded);
    assert_eq!(registry.stored_avatar("target").await, Some(None));
}

#[tokio::test]
async fn admin_onboard_maps_a_missing_profile_write_to_onboarded_false_without_new_rows() {
    let registry = Arc::new(
        StaticRegistry::new(vec![bot("target", BotCapabilities::default())])
            .with_missing_profile_writes(),
    );
    let service = avatar_service(registry.clone());

    // The precheck still observes the bot: the registrability window is
    // only inside the existing-only write.
    assert!(registry.get("target").await.is_some());
    let rows_before = registry.row_count().await;

    let result = service
        .admin_onboard_bot(admin_command(
            "target",
            AvatarChange::Set("https://cdn.example.com/admin.png".to_string()),
        ))
        .await
        .unwrap();

    assert!(!result.onboarded);
    assert_eq!(result.bot_uuid, "target");
    assert!(
        result
            .message
            .as_deref()
            .is_some_and(|message| message.contains("未在协作网络注册")),
        "the precheck-to-write race keeps the missing-bot response: {result:?}"
    );
    // DB row count regression: nothing may be created by the failed write.
    assert_eq!(registry.row_count().await, rows_before);
    assert!(registry.register_calls().await.is_empty());
    assert!(registry.saved_created_by_calls().await.is_empty());
    assert_eq!(
        registry.profile_writes().await.len(),
        1,
        "the profile write request reached the existing-only store window"
    );
    assert!(
        registry.stored_avatar("target").await.is_none(),
        "the avatar write never publishes behind a missing row"
    );
    assert!(result.capabilities.is_none());
}
