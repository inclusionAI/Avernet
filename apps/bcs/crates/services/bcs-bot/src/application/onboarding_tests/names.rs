//! Name-merge, binding-merge, and admin-onboarding tests for
//! [`super::super::onboarding::BotOnboarding`].
//!
//! Moved verbatim from the inline tests module in `onboarding.rs`.

use super::*;

#[tokio::test]
async fn static_registry_update_status_rejects_heartbeat_renewal() {
    let registry = Arc::new(StaticRegistry::new(vec![]));
    assert!(!registry.update_status("heartbeat-renewal-unsupported").await);
}

#[tokio::test]
async fn partial_update_preserves_existing_fields() {
    let registry = Arc::new(StaticRegistry::new(vec![bot(
        "target",
        BotCapabilities::default(),
    )]));
    let service = BotOnboarding::new(
        registry.clone(),
        Arc::new(RecordingRelationCoreService::default()),
        true,
        None,
    );

    let mut first = command("target");
    first.name = "OriginalName".to_string();
    first.summary = Some("Original summary".to_string());
    first.domains = vec!["backend".to_string(), "infra".to_string()];
    first.skills = vec![Skill::new("rust"), Skill::new("python")];
    first.scopes = vec!["internal".to_string()];
    service.onboard_bot(first).await.unwrap();

    let mut second = command("target");
    second.name = "UpdatedName".to_string();
    second.summary = Some("Updated summary".to_string());
    second.domains = Vec::new();
    second.skills = Vec::new();
    second.scopes = Vec::new();
    let result = service.onboard_bot(second).await.unwrap();

    let stored = registry.get("target").await.unwrap();
    assert_eq!(result.name.as_deref(), Some("UpdatedName"));
    assert_eq!(stored.capabilities.name.as_deref(), Some("UpdatedName"));
    assert_eq!(
        stored.capabilities.summary.as_deref(),
        Some("Updated summary")
    );
    assert_eq!(
        stored.capabilities.domains,
        vec!["backend".to_string(), "infra".to_string()]
    );
    assert_eq!(stored.capabilities.skills.len(), 2);
    assert_eq!(stored.capabilities.scopes, vec!["internal".to_string()]);
}

#[tokio::test]
async fn partial_update_empty_name_preserves_existing() {
    let registry = Arc::new(StaticRegistry::with_onboarded(
        vec![bot(
            "target",
            BotCapabilities {
                name: Some("KeepThisName".to_string()),
                summary: Some("old summary".to_string()),
                ..Default::default()
            },
        )],
        ["target"],
    ));
    let service = BotOnboarding::new(
        registry.clone(),
        Arc::new(RecordingRelationCoreService::default()),
        true,
        None,
    );

    let mut cmd = command("target");
    cmd.name = String::new();
    cmd.summary = Some("new summary".to_string());
    let result = service.onboard_bot(cmd).await.unwrap();

    let stored = registry.get("target").await.unwrap();
    assert_eq!(result.name.as_deref(), Some("KeepThisName"));
    assert_eq!(stored.capabilities.name.as_deref(), Some("KeepThisName"));
    assert_eq!(stored.capabilities.summary.as_deref(), Some("new summary"));
}

#[tokio::test]
async fn first_onboard_empty_name_returns_error() {
    let registry = Arc::new(StaticRegistry::new(vec![bot(
        "target",
        BotCapabilities::default(),
    )]));
    let service = BotOnboarding::new(
        registry,
        Arc::new(RecordingRelationCoreService::default()),
        true,
        None,
    );

    let mut cmd = command("target");
    cmd.name = String::new();
    let result = service.onboard_bot(cmd).await;

    assert!(matches!(
        result,
        Err(ServiceError::InvalidOperation { message, .. })
            if message == "name is required for first onboard"
    ));
}

#[tokio::test]
async fn first_onboard_without_summary_leaves_summary_empty() {
    let registry = Arc::new(StaticRegistry::new(vec![bot(
        "target",
        BotCapabilities::default(),
    )]));
    let service = BotOnboarding::new(
        registry.clone(),
        Arc::new(RecordingRelationCoreService::default()),
        true,
        None,
    );

    let mut cmd = command("target");
    cmd.name = "MyBot".to_string();
    cmd.summary = None;
    service.onboard_bot(cmd).await.unwrap();

    let stored = registry.get("target").await.unwrap();
    assert_eq!(stored.capabilities.name.as_deref(), Some("MyBot"));
    assert_eq!(stored.capabilities.summary, None);
}

#[tokio::test]
async fn binding_conflict_excludes_conflicted_channel() {
    let registry = Arc::new(StaticRegistry::new(vec![
        bot("target", BotCapabilities::default()),
        bot(
            "other",
            BotCapabilities {
                name: Some("Other Bot".to_string()),
                binding_channels: Some(binding("same-key")),
                ..Default::default()
            },
        ),
    ]));
    let service = BotOnboarding::new(
        registry.clone(),
        Arc::new(RecordingRelationCoreService::default()),
        true,
        None,
    );

    let mut cmd = command("target");
    cmd.binding_channels = Some(binding("same-key"));
    let result = service.onboard_bot(cmd).await.unwrap();

    assert_eq!(result.binding_results["antding"]["status"], "conflict");
    assert!(result.binding_results["antding"]["message"]
        .as_str()
        .unwrap()
        .contains("Other Bot"));
    assert!(registry
        .get("target")
        .await
        .unwrap()
        .capabilities
        .binding_channels
        .is_none());
}

#[tokio::test]
async fn binding_unbound_reports_removed_channels_and_keeps_successful_bindings() {
    let mut old_bindings = binding("old-key");
    old_bindings.insert(
        "wechat".to_string(),
        BindingChannel {
            binding_key: "wechat-key".to_string(),
        },
    );
    let registry = Arc::new(StaticRegistry::new(vec![bot(
        "target",
        BotCapabilities {
            binding_channels: Some(old_bindings),
            ..Default::default()
        },
    )]));
    let service = BotOnboarding::new(
        registry.clone(),
        Arc::new(RecordingRelationCoreService::default()),
        true,
        None,
    );

    let mut cmd = command("target");
    cmd.binding_channels = Some(binding("new-key"));
    let result = service.onboard_bot(cmd).await.unwrap();

    assert_eq!(result.unbound, vec!["wechat: wechat-key".to_string()]);
    let stored = registry.get("target").await.unwrap();
    assert_eq!(
        stored
            .capabilities
            .binding_channels
            .unwrap()
            .get("antding")
            .unwrap()
            .binding_key,
        "new-key"
    );
}

#[tokio::test]
async fn binding_disabled_preserves_existing_bindings() {
    let registry = Arc::new(StaticRegistry::new(vec![bot(
        "target",
        BotCapabilities {
            binding_channels: Some(binding("old-key")),
            ..Default::default()
        },
    )]));
    let service = BotOnboarding::new(
        registry.clone(),
        Arc::new(RecordingRelationCoreService::default()),
        false,
        None,
    );

    let mut cmd = command("target");
    cmd.binding_channels = Some(binding("new-key"));
    let result = service.onboard_bot(cmd).await.unwrap();

    assert!(result.binding_results.is_empty());
    let stored = registry.get("target").await.unwrap();
    assert_eq!(
        stored
            .capabilities
            .binding_channels
            .unwrap()
            .get("antding")
            .unwrap()
            .binding_key,
        "old-key"
    );
}

#[tokio::test]
async fn binding_channels_absent_preserves_existing_and_reports_no_unbound() {
    let registry = Arc::new(StaticRegistry::with_onboarded(
        vec![bot(
            "target",
            BotCapabilities {
                binding_channels: Some(binding("old-key")),
                ..Default::default()
            },
        )],
        ["target"],
    ));
    let service = BotOnboarding::new(
        registry.clone(),
        Arc::new(RecordingRelationCoreService::default()),
        true,
        None,
    );

    let mut cmd = command("target");
    cmd.binding_channels = None;
    let result = service.onboard_bot(cmd).await.unwrap();

    assert!(result.unbound.is_empty());
    assert!(result.binding_results.is_empty());
    let stored = registry.get("target").await.unwrap();
    assert_eq!(
        stored
            .capabilities
            .binding_channels
            .unwrap()
            .get("antding")
            .unwrap()
            .binding_key,
        "old-key"
    );
}

#[tokio::test]
async fn visibility_prefers_existing_then_configured_default_then_protected() {
    let existing_registry = Arc::new(StaticRegistry::new(vec![bot(
        "existing",
        BotCapabilities {
            visibility: "public".to_string(),
            ..Default::default()
        },
    )]));
    let existing_service = BotOnboarding::new(
        existing_registry.clone(),
        Arc::new(RecordingRelationCoreService::default()),
        true,
        Some("private".to_string()),
    );
    existing_service
        .onboard_bot(command("existing"))
        .await
        .unwrap();
    assert_eq!(
        existing_registry
            .get("existing")
            .await
            .unwrap()
            .capabilities
            .visibility,
        "public"
    );

    let default_registry = Arc::new(StaticRegistry::new(vec![bot(
        "defaulted",
        BotCapabilities {
            visibility: String::new(),
            ..Default::default()
        },
    )]));
    let default_service = BotOnboarding::new(
        default_registry.clone(),
        Arc::new(RecordingRelationCoreService::default()),
        true,
        Some("private".to_string()),
    );
    default_service
        .onboard_bot(command("defaulted"))
        .await
        .unwrap();
    assert_eq!(
        default_registry
            .get("defaulted")
            .await
            .unwrap()
            .capabilities
            .visibility,
        "private"
    );

    let fallback_registry = Arc::new(StaticRegistry::new(vec![bot(
        "fallback",
        BotCapabilities {
            visibility: String::new(),
            ..Default::default()
        },
    )]));
    let fallback_service = BotOnboarding::new(
        fallback_registry.clone(),
        Arc::new(RecordingRelationCoreService::default()),
        true,
        None,
    );
    fallback_service
        .onboard_bot(command("fallback"))
        .await
        .unwrap();
    assert_eq!(
        fallback_registry
            .get("fallback")
            .await
            .unwrap()
            .capabilities
            .visibility,
        "protected"
    );
}

#[tokio::test]
async fn admin_onboard_preserves_existing_name_and_summary_when_missing_or_empty() {
    let registry = Arc::new(StaticRegistry::with_onboarded(
        vec![bot(
            "target",
            BotCapabilities {
                name: Some("Existing Bot".to_string()),
                summary: Some("Existing summary".to_string()),
                domains: vec!["existing-domain".to_string()],
                skills: vec![Skill::new("existing-skill")],
                scopes: vec!["existing-scope".to_string()],
                visibility: "public".to_string(),
                ..Default::default()
            },
        )],
        ["target"],
    ));
    let service = BotOnboarding::new(
        registry.clone(),
        Arc::new(RecordingRelationCoreService::default()),
        true,
        None,
    );

    let result = service
        .admin_onboard_bot(AdminBotOnboardCommand {
            bot_uuid: "target".to_string(),
            name: Some(String::new()),
            summary: None,
            domains: vec!["ops".to_string()],
            skills: Vec::new(),
            scopes: Vec::new(),
            binding_channels: None,
            actor_identity: None,
            avatar: bcs_service_api::types::bot_profile::AvatarChange::Keep,
        })
        .await
        .unwrap();

    assert_eq!(result.name.as_deref(), Some("Existing Bot"));
    let stored = registry.get("target").await.unwrap();
    assert_eq!(stored.capabilities.name.as_deref(), Some("Existing Bot"));
    assert_eq!(
        stored.capabilities.summary.as_deref(),
        Some("Existing summary")
    );
    assert_eq!(stored.capabilities.domains, vec!["ops".to_string()]);
    assert_eq!(stored.capabilities.skills.len(), 1);
    assert_eq!(
        stored.capabilities.scopes,
        vec!["existing-scope".to_string()]
    );
}

#[tokio::test]
async fn first_admin_onboard_without_name_returns_error() {
    let registry = Arc::new(StaticRegistry::new(vec![bot(
        "target",
        BotCapabilities::default(),
    )]));
    let service = BotOnboarding::new(
        registry,
        Arc::new(RecordingRelationCoreService::default()),
        true,
        None,
    );

    let result = service
        .admin_onboard_bot(AdminBotOnboardCommand {
            bot_uuid: "target".to_string(),
            name: None,
            summary: None,
            domains: Vec::new(),
            skills: Vec::new(),
            scopes: Vec::new(),
            binding_channels: None,
            actor_identity: None,
            avatar: bcs_service_api::types::bot_profile::AvatarChange::Keep,
        })
        .await;

    assert!(matches!(
        result,
        Err(ServiceError::InvalidOperation { message, .. })
            if message == "name is required for first onboard"
    ));
}

#[tokio::test]
async fn works_with_real_registry_for_basic_onboard() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register(
            "real-bot".to_string(),
            BotCapabilities {
                visibility: "public".to_string(),
                ..Default::default()
            },
        )
        .await
        .unwrap();
    let service = BotOnboarding::new(
        registry.clone(),
        Arc::new(RecordingRelationCoreService::default()),
        true,
        None,
    );

    service.onboard_bot(command("real-bot")).await.unwrap();

    let stored = registry.get("real-bot").await.unwrap();
    assert_eq!(stored.capabilities.name.as_deref(), Some("Bot"));
    assert_eq!(stored.capabilities.visibility, "public");
}

#[tokio::test]
async fn real_registry_partial_update_persists_merged_fields() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register(
            "real-bot".to_string(),
            BotCapabilities {
                visibility: "public".to_string(),
                ..Default::default()
            },
        )
        .await
        .unwrap();
    let service = BotOnboarding::new(
        registry.clone(),
        Arc::new(RecordingRelationCoreService::default()),
        true,
        None,
    );

    let mut first = command("real-bot");
    first.name = "DiskBot".to_string();
    first.summary = Some("Disk summary".to_string());
    first.domains = vec!["backend".to_string()];
    first.skills = vec![Skill::new("rust")];
    first.scopes = vec!["internal".to_string()];
    service.onboard_bot(first).await.unwrap();

    let mut second = command("real-bot");
    second.name = "DiskBotV2".to_string();
    second.summary = None;
    second.domains = Vec::new();
    second.skills = Vec::new();
    second.scopes = Vec::new();
    service.onboard_bot(second).await.unwrap();

    let memory = registry.get("real-bot").await.unwrap().capabilities;
    let disk = registry.load_from_storage("real-bot").await.unwrap();

    for caps in [memory, disk] {
        assert_eq!(caps.name.as_deref(), Some("DiskBotV2"));
        assert_eq!(caps.summary.as_deref(), Some("Disk summary"));
        assert_eq!(caps.domains, vec!["backend".to_string()]);
        assert_eq!(caps.skills.len(), 1);
        assert_eq!(caps.scopes, vec!["internal".to_string()]);
        assert_eq!(caps.visibility, "public");
    }
}
