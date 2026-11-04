//! Shared existing-only Bot profile/avatar store contract (bot-avatar plan
//! Task 3).
//!
//! One function drives every real store implementation through the same
//! semantics: `update_profile_existing`, `update_avatar_existing` and
//! `read_avatar_batch`. It is mounted by the bot-store conformance roots
//! (Memory + real SQLite with the full migration chain) and by the
//! bcs-admin full MySQL migration-chain gate, so the file must stay free of
//! fixture dependencies and take only `&dyn BotRepoPort`.

use bcs_service_api::types::bot_profile::{AvatarChange, ExistingProfileUpdate, ProfileWrite};
use bcs_service_api::{BindingChannels, BotCapabilities, BotRepoPort, ServiceError};

/// Capabilities used by the shared profile writes.
fn caps(name: &str, summary: &str, domain: &str) -> BotCapabilities {
    BotCapabilities {
        name: Some(name.to_string()),
        summary: Some(summary.to_string()),
        domains: vec![domain.to_string()],
        ..Default::default()
    }
}

fn profile_update(
    name: &str,
    summary: &str,
    domain: &str,
    avatar: AvatarChange,
) -> ExistingProfileUpdate {
    ExistingProfileUpdate {
        capabilities: caps(name, summary, domain),
        avatar,
    }
}

/// Binding channels for the routing-index contract writes.
fn channels(entries: &[(&str, &str)]) -> BindingChannels {
    entries
        .iter()
        .map(|(channel, key)| {
            (
                channel.to_string(),
                bcs_service_api::BindingChannel {
                    binding_key: key.to_string(),
                },
            )
        })
        .collect()
}

fn profile_update_bound(
    name: &str,
    bound: BindingChannels,
    avatar: AvatarChange,
) -> ExistingProfileUpdate {
    let mut capabilities = caps(name, "binding contract summary", "binding-domain");
    capabilities.binding_channels = Some(bound);
    ExistingProfileUpdate {
        capabilities,
        avatar,
    }
}

/// The plan's shared avatar + profile contract. See
/// `tests/conformance_bot_profile.rs` for the store mounts.
pub async fn avatar_contract(repo: &dyn BotRepoPort) {
    // Missing targets stay missing for every existing-only write; no
    // INSERT/upsert path may run on this branch.
    assert_eq!(
        repo.update_avatar_existing("missing", AvatarChange::Clear)
            .await
            .unwrap(),
        ProfileWrite::Missing,
    );
    assert_eq!(
        repo.update_profile_existing(
            "missing",
            profile_update("Lost", "never written", "lost", AvatarChange::Clear),
        )
        .await
        .unwrap(),
        ProfileWrite::Missing,
    );

    // The plan's mandatory avatar transition sequence: Set, Keep, Clear.
    assert!(
        repo.create_registration_if_absent(
            "avatar-bot".into(),
            BotCapabilities::default(),
            "owner",
            "test-runtime-token",
        )
        .await
        .unwrap()
    );
    let ids = vec!["avatar-bot".to_string()];
    for (change, expected) in [
        (
            AvatarChange::Set("https://example.com/avatar-a.png".into()),
            Some("https://example.com/avatar-a.png".to_string()),
        ),
        (
            AvatarChange::Keep,
            Some("https://example.com/avatar-a.png".to_string()),
        ),
        (AvatarChange::Clear, None),
    ] {
        assert_eq!(
            repo.update_avatar_existing("avatar-bot", change)
                .await
                .unwrap(),
            ProfileWrite::Updated,
        );
        assert_eq!(
            repo.read_avatar_batch(&ids).await.unwrap()["avatar-bot"],
            expected
        );
    }

    // 资料补齐: an existing-only profile write completes the capabilities and
    // sets the avatar in the same write; the existing token/owner survive.
    let completed = profile_update(
        "avatar-bot-2",
        "avatar contract summary",
        "avatar-domain",
        AvatarChange::Set("https://example.com/avatar-b.png".into()),
    );
    assert_eq!(
        repo.update_profile_existing("avatar-bot", completed.clone())
            .await
            .unwrap(),
        ProfileWrite::Updated,
    );
    let bot = repo.try_get("avatar-bot").await.unwrap().unwrap();
    assert_eq!(bot.capabilities.name.as_deref(), Some("avatar-bot-2"));
    assert_eq!(
        bot.capabilities.summary.as_deref(),
        Some("avatar contract summary")
    );
    assert_eq!(bot.capabilities.domains, vec!["avatar-domain".to_string()]);
    assert_eq!(bot.created_by.as_deref(), Some("owner"));
    assert_eq!(
        repo.try_load_token("avatar-bot").await.unwrap().as_deref(),
        Some("test-runtime-token"),
    );
    assert_eq!(
        repo.read_avatar_batch(&ids).await.unwrap()["avatar-bot"].as_deref(),
        Some("https://example.com/avatar-b.png"),
    );

    // 身份保留 (PR-3488 review): an existing-only profile write never wipes
    // the Bot's runtime AgentPass identity — a receiver without an
    // `agent_code` is silently skipped by the outbound interceptor chain.
    // Identity arrives through the ordinary registration paths, exactly like
    // AgentPass delivery (WS registration caps carry the code; the token is
    // delivered to the live entry only, never round-tripped through a
    // sanitized read).
    assert!(
        repo.create_registration_if_absent(
            "avatar-identity".into(),
            BotCapabilities::default(),
            "owner",
            "test-identity-session-token",
        )
        .await
        .unwrap()
    );
    // Same posture as an online WebSocket Bot: `register` refreshes the
    // live registry entry with the AgentPass code the session carries.
    repo.register(
        "avatar-identity".into(),
        BotCapabilities {
            agent_code: Some("agent-identity-code".into()),
            ..Default::default()
        },
    )
    .await
    .unwrap();
    repo.add_bot_info("avatar-identity", "agent_token", "agent-identity-token".into())
        .await;
    assert_eq!(
        repo.update_profile_existing(
            "avatar-identity",
            profile_update(
                "avatar-identity-updated",
                "profile-only write",
                "identity-domain",
                AvatarChange::Keep,
            ),
        )
        .await
        .unwrap(),
        ProfileWrite::Updated,
    );
    // `get_agent_credentials` is the interceptor chain's own read path.
    let credentials = repo
        .get_agent_credentials("avatar-identity")
        .await
        .expect("the profile-written Bot stays registered");
    assert_eq!(
        credentials.agent_code.as_deref(),
        Some("agent-identity-code")
    );
    assert_eq!(
        credentials.agent_token.as_deref(),
        Some("agent-identity-token")
    );
    // The sanitized registry read never carries the runtime token; it does
    // carry the code and the wholesale-updated profile fields.
    let identity_bot = repo.try_get("avatar-identity").await.unwrap().unwrap();
    assert_eq!(
        identity_bot.capabilities.agent_code.as_deref(),
        Some("agent-identity-code")
    );
    assert_eq!(
        identity_bot.capabilities.name.as_deref(),
        Some("avatar-identity-updated")
    );
    // Durable parity: a reloaded record keeps the AgentPass code on both
    // backends (the persistent row's dedicated column, the Memory record
    // file). The runtime token stays memory-only and is never written to
    // the shared database by this contract.
    let reloaded = repo
        .load_from_storage("avatar-identity")
        .await
        .expect("identity record reloads");
    assert_eq!(
        reloaded.agent_code.as_deref(),
        Some("agent-identity-code")
    );

    // 重复写: replaying the identical profile write stays `Updated` even when
    // the row already carries every value (MySQL then reports 0 changed rows).
    assert_eq!(
        repo.update_profile_existing("avatar-bot", completed)
            .await
            .unwrap(),
        ProfileWrite::Updated,
    );
    let bot = repo.try_get("avatar-bot").await.unwrap().unwrap();
    assert_eq!(bot.capabilities.name.as_deref(), Some("avatar-bot-2"));
    assert_eq!(
        repo.read_avatar_batch(&ids).await.unwrap()["avatar-bot"].as_deref(),
        Some("https://example.com/avatar-b.png"),
    );

    // An avatar `Keep` inside a profile write never clears the stored avatar.
    assert_eq!(
        repo.update_profile_existing(
            "avatar-bot",
            profile_update(
                "avatar-bot-2",
                "avatar contract summary",
                "avatar-domain",
                AvatarChange::Keep,
            ),
        )
        .await
        .unwrap(),
        ProfileWrite::Updated,
    );
    assert_eq!(
        repo.read_avatar_batch(&ids).await.unwrap()["avatar-bot"].as_deref(),
        Some("https://example.com/avatar-b.png"),
    );

    // The same profile write can clear the avatar in one statement.
    assert_eq!(
        repo.update_profile_existing(
            "avatar-bot",
            profile_update(
                "avatar-bot-2",
                "avatar contract summary",
                "avatar-domain",
                AvatarChange::Clear,
            ),
        )
        .await
        .unwrap(),
        ProfileWrite::Updated,
    );
    assert_eq!(
        repo.read_avatar_batch(&ids).await.unwrap()["avatar-bot"],
        None
    );

    // An avatar-only `Keep` reports existence without writing the avatar.
    assert_eq!(
        repo.update_avatar_existing("avatar-bot", AvatarChange::Keep)
            .await
            .unwrap(),
        ProfileWrite::Updated,
    );
    assert_eq!(
        repo.read_avatar_batch(&ids).await.unwrap()["avatar-bot"],
        None
    );

    // 重复写 of one avatar value: the unchanged-value update still reports
    // `Updated` (the MySQL affected_rows=0 case) and the value persists.
    assert_eq!(
        repo.update_avatar_existing(
            "avatar-bot",
            AvatarChange::Set("https://example.com/avatar-c.png".into()),
        )
        .await
        .unwrap(),
        ProfileWrite::Updated,
    );
    assert_eq!(
        repo.update_avatar_existing(
            "avatar-bot",
            AvatarChange::Set("https://example.com/avatar-c.png".into()),
        )
        .await
        .unwrap(),
        ProfileWrite::Updated,
    );
    assert_eq!(
        repo.read_avatar_batch(&ids).await.unwrap()["avatar-bot"].as_deref(),
        Some("https://example.com/avatar-c.png"),
    );

    // 路由索引: the profile write is the authoritative capabilities save, so
    // it refreshes the binding-channel reverse index exactly like the
    // ordinary save path — the committed binding resolves and a binding the
    // write replaced no longer does.
    assert_eq!(
        repo.update_profile_existing(
            "avatar-bot",
            profile_update_bound(
                "avatar-bound-1",
                channels(&[("avatar-old-channel", "avatar-old-key")]),
                AvatarChange::Keep,
            ),
        )
        .await
        .unwrap(),
        ProfileWrite::Updated,
    );
    assert_eq!(
        repo.find_bot_by_binding_channel("avatar-old-channel", "avatar-old-key")
            .await
            .as_deref(),
        Some("avatar-bot"),
    );
    assert_eq!(
        repo.update_profile_existing(
            "avatar-bot",
            profile_update_bound(
                "avatar-bound-2",
                channels(&[("avatar-new-channel", "avatar-new-key")]),
                AvatarChange::Keep,
            ),
        )
        .await
        .unwrap(),
        ProfileWrite::Updated,
    );
    assert_eq!(
        repo.find_bot_by_binding_channel("avatar-old-channel", "avatar-old-key").await,
        None,
    );
    assert_eq!(
        repo.find_bot_by_binding_channel("avatar-new-channel", "avatar-new-key")
            .await
            .as_deref(),
        Some("avatar-bot"),
    );

    // Human actors never appear in the Bot display read, including after
    // an ordinary profile save has persisted a local record for the Human.
    repo.ensure_human_actor("avatar-staff", "Human Contract")
        .await
        .unwrap();
    let humans = repo
        .read_avatar_batch(&["human_avatar-staff".to_string()])
        .await
        .unwrap();
    assert!(!humans.contains_key("human_avatar-staff"));
    let human_caps = repo.try_get("human_avatar-staff").await.unwrap().unwrap().capabilities;
    repo.save_to_storage("human_avatar-staff", &human_caps).await.unwrap();
    assert!(repo.read_avatar_batch(&["human_avatar-staff".into()]).await.unwrap().is_empty());

    // 软删除: tombstoned targets refuse a profile or avatar write, but the
    // retained row still serves tombstone display reads.
    assert!(repo.soft_delete("avatar-bot").await);
    assert_eq!(
        repo.update_avatar_existing(
            "avatar-bot",
            AvatarChange::Set("https://example.com/ghost.png".into()),
        )
        .await
        .unwrap(),
        ProfileWrite::Missing,
    );
    assert_eq!(
        repo.update_profile_existing(
            "avatar-bot",
            profile_update(
                "avatar-bot-ghost",
                "avatar contract summary",
                "avatar-domain",
                AvatarChange::Keep,
            ),
        )
        .await
        .unwrap(),
        ProfileWrite::Missing,
    );
    let tombstones = repo.read_avatar_batch(&ids).await.unwrap();
    assert_eq!(
        tombstones["avatar-bot"].as_deref(),
        Some("https://example.com/avatar-c.png"),
        "the tombstoned row keeps its stored avatar for display reads",
    );

    // Batch bounds: empty input queries nothing, duplicates collapse before
    // the bound is applied and one deduped id over the bound is refused.
    assert!(repo.read_avatar_batch(&[]).await.unwrap().is_empty());
    let duplicated: Vec<String> = std::iter::repeat("avatar-bot".to_string())
        .take(300)
        .collect();
    assert_eq!(repo.read_avatar_batch(&duplicated).await.unwrap().len(), 1);
    let too_many: Vec<String> = (0..=256)
        .map(|index| format!("avatar-bound-{index}"))
        .collect();
    assert!(matches!(
        repo.read_avatar_batch(&too_many).await,
        Err(ServiceError::InvalidOperation { .. })
    ));
}
