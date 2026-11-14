//! Shared Provider tombstone reactivation scenarios.
use super::*;
use bcs_service_api::ProviderBotBindingRepoPort;

fn provider_initialization(owner: &str, managers: &[&str], id: &str) -> OwnershipInitialization {
    OwnershipInitialization {
        owner_user_id: owner.into(),
        initial_manager_user_ids: managers.iter().map(|manager| (*manager).into()).collect(),
        mutation: AuthorityMutation {
            mutation_id: id.into(),
            actor: AuthorityActor::Provider { provider_id: "provider-a".into() },
        },
    }
}

pub async fn provider_reactivation_replaces_registration_and_authority(fixture: &Fixture) {
    let mut old_channels = bcs_service_api::BindingChannels::new();
    old_channels.insert(
        "old-channel".into(),
        bcs_service_api::BindingChannel { binding_key: "old-binding".into() },
    );
    let old = provider_record("provider-reactivate", BotConnectionMode::Gateway);
    fixture.provider_repo.create_provider_admin_bot_with_ownership(
        old.clone(),
        BotCapabilities {
            name: Some("Old name".into()),
            summary: Some("old summary".into()),
            domains: vec!["old-domain".into()],
            skills: vec![bcs_service_api::Skill::new("old-skill")],
            scopes: vec!["old-scope".into()],
            binding_channels: Some(old_channels),
            visibility: "protected".into(),
            ..Default::default()
        },
        "old-owner",
        "old-runtime-token",
        provider_initialization("old-owner", &["old-manager"], "reactivate-old-initialize"),
    ).await.expect("create old registration");
    seed_pending_transfer(fixture, "provider-reactivate", "human_transfer-target").await;
    fixture.provider_repo.retire_with_authority(ProviderRetire {
        provider_id: "provider-a".into(),
        bot_id: "provider-reactivate".into(),
        provider_bot_ref: "ref-1".into(),
        mutation: retire_mutation_provider("provider-a"),
        expected_branch: ProviderRetireBranch::Metadata,
    }).await.expect("retire old registration");

    // Ordinary strict creation keeps its tombstone conflict contract.
    assert!(fixture.provider_repo.create_provider_bot(
        old.clone(), capabilities(), "someone", "strict-create-token",
    ).await.is_err());

    let mut restored_record = old;
    restored_record.webhook_url = Some("https://new.example.test/webhook".into());
    fixture.provider_repo.restore_provider_bot_with_ownership(
        restored_record,
        BotCapabilities {
            name: Some("New name".into()),
            summary: None,
            domains: vec![],
            skills: vec![],
            scopes: vec![],
            binding_channels: None,
            visibility: "protected".into(),
            ..Default::default()
        },
        "new-owner",
        "new-runtime-token",
        provider_initialization("new-owner", &["new-manager"], "reactivate-new-initialize"),
    ).await.expect("reactivate tombstone");

    let restored = fixture.provider_repo.get_provider_bot("provider-reactivate").await.unwrap().unwrap();
    assert!(!restored.is_deleted);
    assert_eq!(restored.webhook_url.as_deref(), Some("https://new.example.test/webhook"));
    let binding = fixture.provider_repo.get_binding_by_bot_uuid("provider-reactivate").await.unwrap().unwrap();
    assert!(!binding.disabled);
    assert_eq!(binding.webhook_url, restored.webhook_url);
    assert_eq!(fixture.bot_repo.find_bot_by_token("old-runtime-token").await, None);
    assert_eq!(fixture.bot_repo.find_bot_by_token("new-runtime-token").await.as_deref(), Some("provider-reactivate"));

    let rows = fixture.db.query(DbStatement::new(
        "SELECT bot_info, created_by, ownership_version, is_deleted FROM bcs_bots \
         WHERE bot_uuid = 'provider-reactivate'",
    )).await.unwrap();
    assert_eq!(rows[0].get_string("created_by").unwrap().as_deref(), Some("new-owner"));
    assert_eq!(rows[0].get("ownership_version").unwrap().as_i64(), Some(1));
    assert_eq!(rows[0].get("is_deleted").unwrap().as_i64(), Some(0));
    let info: serde_json::Value = serde_json::from_str(&rows[0].get_string("bot_info").unwrap().unwrap()).unwrap();
    assert_eq!(info["name"], "New name");
    assert!(info["summary"].is_null());
    assert_eq!(info["domains"], serde_json::json!([]));
    assert_eq!(info["skills"], serde_json::json!([]));
    assert_eq!(info["scopes"], serde_json::json!([]));
    assert!(info.get("binding_channels").is_none() || info["binding_channels"].is_null());

    for (user, expected) in [
        ("old-owner", None),
        ("old-manager", None),
        ("new-owner", Some(BotAccessRelation::Owner)),
        ("new-manager", Some(BotAccessRelation::Manager)),
    ] {
        assert_eq!(fixture.authority.role(&format!("human_{user}"), "provider-reactivate").await.unwrap(), expected);
    }
    assert!(fixture.profile_port.get_active_default("provider-reactivate", &resolve_env()).await.is_some());
    let transfers = fixture.db.query(DbStatement::new(
        "SELECT COUNT(*) AS c FROM bot_ownership_transfers WHERE bot_id = 'provider-reactivate'",
    )).await.unwrap();
    assert_eq!(transfers[0].get("c").unwrap().as_i64(), Some(0));
}

pub async fn provider_reactivation_restores_legacy_binding_tombstone(fixture: &Fixture) {
    fixture.bot_repo.create_registration_if_absent(
        "legacy-reactivate".into(), capabilities(), "legacy-owner", "legacy-token",
    ).await.expect("legacy runtime Bot");
    fixture.db.execute(DbStatement::with_params(
        "INSERT INTO bcs_provider_bot_bindings \
         (bot_uuid, env, provider_id, provider_bot_ref, disabled) \
         VALUES ('legacy-reactivate', ?, 'provider-a', 'legacy-ref', 0)",
        vec![resolve_env().as_str().into()],
    )).await.expect("legacy binding");
    fixture.provider_repo.retire_with_authority(ProviderRetire {
        provider_id: "provider-a".into(),
        bot_id: "legacy-reactivate".into(),
        provider_bot_ref: "legacy-ref".into(),
        mutation: retire_mutation_provider("provider-a"),
        expected_branch: ProviderRetireBranch::BindingFallback,
    }).await.expect("retire legacy binding");

    fixture.provider_repo.restore_provider_bot_with_ownership(
        BotProviderRecord {
            bot_uuid: "legacy-reactivate".into(), provider_id: "provider-a".into(),
            provider_bot_ref: "legacy-ref".into(), connection_mode: BotConnectionMode::Plugin,
            webhook_url: None, is_deleted: false,
        },
        BotCapabilities { name: Some("Legacy plugin".into()), ..capabilities() },
        "new-owner", "legacy-new-token",
        provider_initialization("new-owner", &[], "legacy-reactivate-initialize"),
    ).await.expect("reactivate legacy tombstone");

    let restored = fixture.provider_repo.get_provider_bot_by_ref("provider-a", "legacy-ref")
        .await.unwrap().unwrap();
    assert_eq!(restored.bot_uuid, "legacy-reactivate");
    assert_eq!(restored.connection_mode, BotConnectionMode::Plugin);
    assert!(!restored.is_deleted);
    assert!(fixture.provider_repo.get_binding_by_bot_uuid("legacy-reactivate").await.unwrap().is_none());
    assert_eq!(fixture.bot_repo.find_bot_by_token("legacy-token").await, None);
    assert_eq!(fixture.bot_repo.find_bot_by_token("legacy-new-token").await.as_deref(), Some("legacy-reactivate"));
    assert_eq!(fixture.authority.role("human_new-owner", "legacy-reactivate").await.unwrap(), Some(BotAccessRelation::Owner));
}
