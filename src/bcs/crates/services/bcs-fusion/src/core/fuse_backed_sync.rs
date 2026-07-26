//! Worker sync logic: build requests and retry helpers.

use std::collections::HashMap;
use std::time::Duration;

use bcs_config_api::BcsFuseConfig;
use bcs_fuse_client::{FuseClient, SkillSet, SyncProfileData, SyncWorkerRequest};
use bcs_service_api::{ContextBotSummary, Skill};

use super::fuse_backed::build_participant_id;

fn sync_retry_backoff(
    attempt: u32,
    max_attempts: u32,
    base_delay_ms: u64,
) -> Option<Duration> {
    if attempt + 1 >= max_attempts {
        return None;
    }
    let multiplier = 1u64.checked_shl(attempt).unwrap_or(u64::MAX);
    Some(Duration::from_millis(
        base_delay_ms.saturating_mul(multiplier),
    ))
}

/// Build a `SyncWorkerRequest` from onboard data and bot context.
pub fn build_sync_request(
    config: &BcsFuseConfig,
    bot_id: &str,
    name: &str,
    summary: Option<&str>,
    domains: &[String],
    skills: &[Skill],
    bot_context: &ContextBotSummary,
    visibility: &str,
) -> SyncWorkerRequest {
    let profile_id = &config.profile_id;

    let capabilities: Vec<serde_json::Value> = domains
        .iter()
        .map(|d| serde_json::json!({"name": d, "level": "expert"}))
        .chain(
            skills
                .iter()
                .map(|s| serde_json::json!({"name": &s.name, "level": "expert"})),
        )
        .collect();

    let skill_values: Vec<serde_json::Value> = skills
        .iter()
        .map(
            |s| serde_json::json!({"name": &s.name, "source": "builtin", "trust_level": "trusted"}),
        )
        .collect();

    SyncWorkerRequest {
        worker_type: "bot".to_string(),
        name: name.to_string(),
        description: summary.map(String::from),
        responsibilities: vec!["general".to_string()],
        domains: domains.to_vec(),
        capabilities,
        skills: skill_values,
        availability: visibility.to_string(),
        trust_level: "guarded".to_string(),
        profile_key: Some(build_participant_id(bot_id, profile_id)),
        profile: SyncProfileData {
            profile_id: profile_id.clone(),
            display_name: Some(name.to_string()),
            soul_md: bot_context.soul.clone(),
            contents: build_contents_from_context(bot_context),
            skill_sets: skills
                .iter()
                .map(|s| SkillSet {
                    name: s.name.clone(),
                    description: s.description.clone(),
                })
                .collect(),
            activate: true,
        },
    }
}

/// Sync worker with configured attempts and exponential backoff.
///
/// Designed to be called inside `tokio::spawn` — never panics, only logs.
pub async fn sync_worker_with_retry(
    client: &FuseClient,
    bot_id: &str,
    sync_req: &SyncWorkerRequest,
    config: &BcsFuseConfig,
) {
    let max_attempts = config.sync_max_attempts.max(1);
    for attempt in 0..max_attempts {
        match client.sync_worker(bot_id, sync_req.clone()).await {
            Ok(resp) => {
                if !resp.profile_activated {
                    tracing::warn!(
                        bot_id = %bot_id,
                        worker_id = %resp.worker_id,
                        "Worker synced but profile NOT activated — fusion may produce empty perspectives"
                    );
                } else {
                    tracing::info!(
                        bot_id = %bot_id,
                        worker_id = %resp.worker_id,
                        created = resp.created,
                        "Worker synced to bcsfuse"
                    );
                }
                return;
            }
            Err(e) => {
                let backoff = sync_retry_backoff(
                    attempt,
                    max_attempts,
                    config.sync_retry_base_delay_ms,
                );
                tracing::warn!(
                    bot_id = %bot_id,
                    attempt = attempt + 1,
                    error = %e,
                    will_retry = backoff.is_some(),
                    "Worker sync failed"
                );
                if let Some(backoff) = backoff {
                    tokio::time::sleep(backoff).await;
                }
            }
        }
    }
    tracing::error!(
        bot_id = %bot_id,
        attempts = max_attempts,
        "Worker sync exhausted retries, will retry on next onboard/reconnect"
    );
}

/// Build bcsfuse `contents` map from bot context files.
fn build_contents_from_context(ctx: &ContextBotSummary) -> HashMap<String, String> {
    let mut contents = HashMap::new();
    if let Some(ref identity) = ctx.identity {
        contents.insert("identity.md".to_string(), identity.clone());
    }
    if let Some(ref rules) = ctx.rules {
        contents.insert("rules.md".to_string(), rules.clone());
    }
    if let Some(ref memory) = ctx.memory {
        contents.insert("memory.md".to_string(), memory.clone());
    }
    contents
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_config_api::BcsFuseConfig;

    #[test]
    fn retry_backoff_does_not_sleep_after_last_attempt() {
        assert_eq!(
            sync_retry_backoff(0, 3, 1_000),
            Some(Duration::from_secs(1))
        );
        assert_eq!(
            sync_retry_backoff(1, 3, 1_000),
            Some(Duration::from_secs(2))
        );
        assert_eq!(sync_retry_backoff(2, 3, 1_000), None);
    }

    #[test]
    fn retry_backoff_uses_configured_attempts_and_base_delay() {
        assert_eq!(
            sync_retry_backoff(0, 4, 5),
            Some(Duration::from_millis(5))
        );
        assert_eq!(
            sync_retry_backoff(1, 4, 5),
            Some(Duration::from_millis(10))
        );
        assert_eq!(
            sync_retry_backoff(2, 4, 5),
            Some(Duration::from_millis(20))
        );
        assert_eq!(sync_retry_backoff(3, 4, 5), None);
    }

    fn make_context(
        identity: Option<&str>,
        soul: Option<&str>,
        rules: Option<&str>,
        memory: Option<&str>,
    ) -> ContextBotSummary {
        ContextBotSummary {
            bot_uuid: "test-bot".into(),
            name: Some("TestBot".into()),
            emoji: None,
            identity: identity.map(String::from),
            soul: soul.map(String::from),
            rules: rules.map(String::from),
            memory: memory.map(String::from),
        }
    }

    #[test]
    fn test_build_sync_request_basic() {
        let config = BcsFuseConfig::default();
        let ctx = make_context(None, Some("I am helpful"), None, None);
        let req = build_sync_request(
            &config,
            "bot1",
            "Bot One",
            Some("A helper bot"),
            &["dev".into()],
            &["code_review".into()],
            &ctx,
            "public",
        );

        assert_eq!(req.worker_type, "bot");
        assert_eq!(req.name, "Bot One");
        assert_eq!(req.description, Some("A helper bot".into()));
        assert_eq!(req.domains, vec!["dev"]);
        assert_eq!(req.availability, "public");
        assert_eq!(req.trust_level, "guarded");
        assert_eq!(req.profile_key, Some("bot1:default".into()));

        // Profile
        assert_eq!(req.profile.profile_id, "default");
        assert_eq!(req.profile.display_name, Some("Bot One".into()));
        assert_eq!(req.profile.soul_md, Some("I am helpful".into()));
        assert!(req.profile.activate);
        assert_eq!(req.profile.skill_sets.len(), 1);
        assert_eq!(req.profile.skill_sets[0].name, "code_review");
    }

    #[test]
    fn test_build_sync_request_contents() {
        let config = BcsFuseConfig::default();
        let ctx = make_context(
            Some("identity text"),
            None,
            Some("rules text"),
            Some("memory text"),
        );
        let req = build_sync_request(&config, "bot2", "Bot2", None, &[], &[], &ctx, "protected");

        let contents = &req.profile.contents;
        assert_eq!(contents.get("identity.md").unwrap(), "identity text");
        assert_eq!(contents.get("rules.md").unwrap(), "rules text");
        assert_eq!(contents.get("memory.md").unwrap(), "memory text");
        assert!(!contents.contains_key("soul.md")); // soul goes to soul_md, not contents
    }

    #[test]
    fn test_build_contents_from_context_empty() {
        let ctx = make_context(None, None, None, None);
        let contents = build_contents_from_context(&ctx);
        assert!(contents.is_empty());
    }
}
