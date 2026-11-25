//! Owner-binding and ensure-bot tests for
//! [`super::super::onboarding::BotOnboarding`].
//!
//! Moved verbatim from the inline tests module in `onboarding.rs`.

use super::*;

#[tokio::test]
async fn owner_binding_overwrites_only_when_bot_uuid_ends_with_staff_no() {
    let relation = Arc::new(RecordingRelationCoreService::default());
    let registry = Arc::new(StaticRegistry::new(vec![
        bot(
            "bot-alice",
            BotCapabilities {
                visibility: "protected".to_string(),
                ..Default::default()
            },
        ),
        bot(
            "bot-bob",
            BotCapabilities {
                visibility: "protected".to_string(),
                ..Default::default()
            },
        ),
    ]));
    registry
        .save_created_by("bot-bob", "existing-owner", true)
        .await
        .unwrap();
    let service = BotOnboarding::new(registry.clone(), relation.clone(), true, None);

    let mut alice = command("bot-alice");
    alice.actor_identity = Some(OnboardActorIdentity {
        staff_no: "alice".to_string(),
        nick_name: Some("Alice".to_string()),
    });
    service.onboard_bot(alice).await.unwrap();

    let mut bob = command("bot-bob");
    bob.actor_identity = Some(OnboardActorIdentity {
        staff_no: "alice".to_string(),
        nick_name: Some("Alice".to_string()),
    });
    service.onboard_bot(bob).await.unwrap();

    let calls = registry.saved_created_by_calls().await;
    assert!(calls.contains(&("bot-alice".to_string(), "alice".to_string(), true)));
    assert!(calls.contains(&("bot-bob".to_string(), "alice".to_string(), false)));
    assert_eq!(
        registry.created_by("bot-alice").await.as_deref(),
        Some("alice")
    );
    assert_eq!(
        registry.created_by("bot-bob").await.as_deref(),
        Some("existing-owner")
    );
    assert_eq!(relation.owner_edges.lock().await.len(), 2);
}

#[tokio::test]
async fn ensure_bot_registers_absent_bot_and_binds_owner_edges() {
    let registry = Arc::new(StaticRegistry::new(vec![]));
    let relation = Arc::new(RecordingRelationCoreService::default());
    let service = BotOnboarding::new(registry.clone(), relation.clone(), true, None);

    let result = service
        .ensure_bot(EnsureBotCommand {
            bot_uuid: "bot-85020".to_string(),
            name: "MyBot".to_string(),
            summary: Some("dev bot".to_string()),
            visibility: "protected".to_string(),
            actor_identity: Some(OnboardActorIdentity {
                staff_no: "85020".to_string(),
                nick_name: Some("Alice".to_string()),
            }),
        })
        .await
        .unwrap();

    assert!(result.ensured);
    assert!(result.created);

    // Bot is now registered.
    let stored = registry.get("bot-85020").await.expect("bot registered");
    assert_eq!(stored.capabilities.name.as_deref(), Some("MyBot"));
    assert_eq!(stored.capabilities.summary.as_deref(), Some("dev bot"));
    assert_eq!(stored.capabilities.visibility, "protected");
    // created_by was saved with overwrite (bot_uuid ends with staff_no).
    assert_eq!(stored.created_by.as_deref(), Some("85020"));
    // Owner edge added.
    let owner_edges = relation.owner_edges.lock().await.clone();
    assert!(owner_edges
        .iter()
        .any(|(human, bot, _)| human == "human_85020" && bot == "bot-85020"));
    // Human actor registered.
    assert!(registry.get("human_85020").await.is_some());
}

#[tokio::test]
async fn ensure_bot_idempotent_when_bot_exists_returns_created_false() {
    let registry = Arc::new(StaticRegistry::new(vec![bot(
        "bot-85020",
        BotCapabilities {
            name: Some("Existing".to_string()),
            summary: Some("existing summary".to_string()),
            visibility: "public".to_string(),
            ..Default::default()
        },
    )]));
    let relation = Arc::new(RecordingRelationCoreService::default());
    let service = BotOnboarding::new(registry.clone(), relation.clone(), true, None);

    let result = service
        .ensure_bot(EnsureBotCommand {
            bot_uuid: "bot-85020".to_string(),
            name: "Updated".to_string(),
            summary: Some("updated summary".to_string()),
            visibility: "protected".to_string(),
            actor_identity: Some(OnboardActorIdentity {
                staff_no: "85020".to_string(),
                nick_name: Some("Alice".to_string()),
            }),
        })
        .await
        .unwrap();

    assert!(result.ensured);
    assert!(!result.created);

    // Existing bot's metadata updated.
    let stored = registry.get("bot-85020").await.unwrap();
    assert_eq!(stored.capabilities.name.as_deref(), Some("Updated"));
    assert_eq!(stored.capabilities.summary.as_deref(), Some("updated summary"));
    assert_eq!(stored.capabilities.visibility, "protected");
}

#[tokio::test]
async fn ensure_bot_preserves_existing_metadata_when_request_omits_them() {
    let registry = Arc::new(StaticRegistry::new(vec![bot(
        "bot-85020",
        BotCapabilities {
            name: Some("KeepName".to_string()),
            summary: Some("keep summary".to_string()),
            visibility: "public".to_string(),
            ..Default::default()
        },
    )]));
    let relation = Arc::new(RecordingRelationCoreService::default());
    let service = BotOnboarding::new(registry.clone(), relation, true, None);

    // Re-ensure with empty name/summary/visibility and empty staff_no
    // (registration-only backfill, no owner binding).
    let result = service
        .ensure_bot(EnsureBotCommand {
            bot_uuid: "bot-85020".to_string(),
            name: String::new(),
            summary: None,
            visibility: String::new(),
            actor_identity: None,
        })
        .await
        .unwrap();

    assert!(result.ensured);
    assert!(!result.created);

    let stored = registry.get("bot-85020").await.unwrap();
    assert_eq!(stored.capabilities.name.as_deref(), Some("KeepName"));
    assert_eq!(stored.capabilities.summary.as_deref(), Some("keep summary"));
    assert_eq!(stored.capabilities.visibility, "public");
}

#[tokio::test]
async fn ensure_bot_empty_staff_no_skips_owner_binding() {
    let registry = Arc::new(StaticRegistry::new(vec![]));
    let relation = Arc::new(RecordingRelationCoreService::default());
    let service = BotOnboarding::new(registry.clone(), relation.clone(), true, None);

    let result = service
        .ensure_bot(EnsureBotCommand {
            bot_uuid: "bot-anon".to_string(),
            name: "Anon".to_string(),
            summary: None,
            visibility: "protected".to_string(),
            actor_identity: None,
        })
        .await
        .unwrap();

    assert!(result.ensured);
    assert!(result.created);

    // Bot registered; no owner edges + no human actor.
    assert!(registry.get("bot-anon").await.is_some());
    assert!(relation.owner_edges.lock().await.is_empty());
    assert!(registry.get("human_anon").await.is_none());
}

#[tokio::test]
async fn ensure_bot_seeds_default_profile_when_profiles_injected() {
    use bcs_service_api::port::repo::PermissionProfileRepoPort;

    struct RecordingProfiles {
        calls: Mutex<Vec<(String, String)>>,
    }

    #[async_trait]
    impl PermissionProfileRepoPort for RecordingProfiles {
        async fn ensure_default_profile(
            &self,
            bot_uuid: &str,
            env: &str,
        ) -> ServiceResult<u64> {
            self.calls
                .lock()
                .await
                .push((bot_uuid.to_string(), env.to_string()));
            Ok(1)
        }

        async fn get_active_default(
            &self,
            _bot_id: &str,
            _env: &str,
        ) -> Option<bcs_service_api::types::edge_permission::PermissionProfile>
        {
            None
        }

        async fn upsert_revision(
            &self,
            _profile: bcs_service_api::types::edge_permission::PermissionProfile,
        ) -> ServiceResult<()> {
            Ok(())
        }
    }

    let profiles = Arc::new(RecordingProfiles {
        calls: Mutex::new(Vec::new()),
    });
    let registry = Arc::new(StaticRegistry::new(vec![]));
    let relation = Arc::new(RecordingRelationCoreService::default());
    let service = BotOnboarding::new(registry.clone(), relation, true, None)
        .with_profiles(profiles.clone());

    service
        .ensure_bot(EnsureBotCommand {
            bot_uuid: "bot-85020".to_string(),
            name: "MyBot".to_string(),
            summary: None,
            visibility: "protected".to_string(),
            actor_identity: Some(OnboardActorIdentity {
                staff_no: "85020".to_string(),
                nick_name: None,
            }),
        })
        .await
        .unwrap();

    let calls = profiles.calls.lock().await.clone();
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].0, "bot-85020");
    // Seeded with the resolved env string (non-empty).
    assert!(!calls[0].1.is_empty());
}
