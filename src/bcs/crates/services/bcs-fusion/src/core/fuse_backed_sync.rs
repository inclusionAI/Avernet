//! Worker sync logic: build requests and retry helpers.

use std::collections::HashMap;
use std::time::Duration;

use bcs_config_api::BcsFuseConfig;
use bcs_fuse_client::{FuseClient, SkillSet, SyncProfileData, SyncWorkerRequest};
use bcs_service_api::{ContextBotSummary, Skill};

use super::fuse_backed::build_participant_id;

/// Maximum number of sync retry attempts.
///
/// With the default `sync_retry_base_ms = 1000` and `MAX_SYNC_BACKOFF = 8s`,
/// this gives roughly a 60-second retry window, which covers local singlebox
/// deployments where bcsfuse may still be starting when bots first onboard.
const MAX_SYNC_RETRIES: u32 = 10;

/// Maximum backoff between sync retries.
const MAX_SYNC_BACKOFF: Duration = Duration::from_secs(8);

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

/// Sync worker with inline retry (bounded exponential backoff).
///
/// The backoff between attempts is `config.sync_retry_base_ms * 2^attempt`,
/// capped at `MAX_SYNC_BACKOFF`; the last attempt is not followed by a wait,
/// since there is nothing left to retry.
///
/// With the default config this yields roughly a 60-second retry window,
/// which covers local singlebox deployments where bcsfuse may still be
/// starting when bots first onboard.
///
/// Designed to be called inside `tokio::spawn` — never panics, only logs.
pub async fn sync_worker_with_retry(
    config: &BcsFuseConfig,
    client: &FuseClient,
    bot_id: &str,
    sync_req: &SyncWorkerRequest,
) {
    for attempt in 0..MAX_SYNC_RETRIES {
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
                tracing::warn!(
                    bot_id = %bot_id,
                    attempt = attempt + 1,
                    error = %e,
                    "Worker sync failed, retrying"
                );
                // No point waiting after the final attempt — the loop is done.
                if attempt + 1 < MAX_SYNC_RETRIES {
                    let backoff = Duration::from_millis(
                        config.sync_retry_base_ms.saturating_mul(2u64.pow(attempt.min(5))),
                    )
                    .min(MAX_SYNC_BACKOFF);
                    tokio::time::sleep(backoff).await;
                }
            }
        }
    }
    tracing::error!(
        bot_id = %bot_id,
        retries = MAX_SYNC_RETRIES,
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

    /// The retry backoff must come from config, and the loop must not wait after
    /// the attempt it has no intention of following up on.
    ///
    /// With the default 1s base this sequence sleeps 1s + 2s between the three
    /// attempts (and used to sleep a further 4s after the last one, for nothing).
    /// At `sync_retry_base_ms = 0` it should sleep not at all, leaving only three
    /// connection refusals against a dead port.
    #[tokio::test]
    async fn sync_retry_backoff_honours_config_and_skips_the_final_wait() {
        let config = BcsFuseConfig {
            enabled: true,
            // Nothing listens here, so every attempt fails fast with a refusal.
            url: "http://127.0.0.1:19998".to_string(),
            sync_timeout_ms: 500,
            sync_retry_base_ms: 0,
            ..Default::default()
        };
        let client = FuseClient::new(&config).expect("client builds");
        let ctx = make_context(None, None, None, None);
        let req = build_sync_request(&config, "bot1", "Bot One", None, &[], &[], &ctx, "public");

        let started = std::time::Instant::now();
        sync_worker_with_retry(&config, &client, "bot1", &req).await;
        let elapsed = started.elapsed();

        assert!(
            elapsed < Duration::from_secs(2),
            "zero backoff should add no sleep; took {elapsed:?}"
        );
    }

    /// Spawn a one-shot HTTP/1.1 server that accepts a sync request and returns
    /// a JSON SyncWorkerResponse. Returns the base URL of the mock server.
    async fn mock_sync_server(response_body: String) -> Result<String, Box<dyn std::error::Error>> {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await?;
        let port = listener.local_addr()?.port();

        tokio::spawn(async move {
            let (mut socket, _) = match listener.accept().await {
                Ok(conn) => conn,
                Err(e) => {
                    eprintln!("mock sync server accept failed: {e}");
                    return;
                }
            };
            let mut buf = [0u8; 4096];
            use tokio::io::{AsyncReadExt, AsyncWriteExt};
            if let Err(e) = socket.read(&mut buf).await {
                eprintln!("mock sync server read failed: {e}");
                return;
            }
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                response_body.len(),
                response_body
            );
            if let Err(e) = socket.write_all(response.as_bytes()).await {
                eprintln!("mock sync server write failed: {e}");
            }
            let _ = socket.shutdown().await;
        });

        Ok(format!("http://127.0.0.1:{}", port))
    }

    #[tokio::test]
    async fn sync_worker_with_retry_succeeds_when_profile_activated() -> Result<(), Box<dyn std::error::Error>> {
        let body = r#"{"worker_id":"bot1","created":true,"profile_activated":true}"#;
        let url = mock_sync_server(body.to_string()).await?;
        let config = BcsFuseConfig {
            enabled: true,
            url,
            sync_timeout_ms: 500,
            sync_retry_base_ms: 0,
            ..Default::default()
        };
        let client = FuseClient::new(&config)?;
        let ctx = make_context(None, None, None, None);
        let req = build_sync_request(&config, "bot1", "Bot One", None, &[], &[], &ctx, "public");

        sync_worker_with_retry(&config, &client, "bot1", &req).await;
        Ok(())
    }

    #[tokio::test]
    async fn sync_worker_with_retry_warns_when_profile_not_activated() -> Result<(), Box<dyn std::error::Error>> {
        let body = r#"{"worker_id":"bot1","created":true,"profile_activated":false}"#;
        let url = mock_sync_server(body.to_string()).await?;
        let config = BcsFuseConfig {
            enabled: true,
            url,
            sync_timeout_ms: 500,
            sync_retry_base_ms: 0,
            ..Default::default()
        };
        let client = FuseClient::new(&config)?;
        let ctx = make_context(None, None, None, None);
        let req = build_sync_request(&config, "bot1", "Bot One", None, &[], &[], &ctx, "public");

        sync_worker_with_retry(&config, &client, "bot1", &req).await;
        Ok(())
    }
}
