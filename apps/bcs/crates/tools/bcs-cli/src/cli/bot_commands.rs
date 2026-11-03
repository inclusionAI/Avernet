use std::collections::HashMap;

use anyhow::{Result, anyhow};
use bcs_cli::BcsClient;
use bcs_protocol::{BCS_PROTOCOL_VERSION, BotConnectParams};
use serde_json::json;
use tracing::debug;

use super::CLI_LOG_TARGET;
use super::Commands;
use super::authentication::create_client;
use super::environment::{SessionInfo, get_optional_session_file_path, get_token,
    normalize_bcs_ws_url,
};
use super::output::{StructuredResult, emit_structured_result, render_discover_result};

/// Parse skills input - supports JSON object array, JSON string array, and comma-separated format.
///
/// Examples:
/// - `[{"name":"sql","description":"SQL analysis"}]` → structured Skill objects
/// - `["sql","debug"]` → Skill objects with name only
/// - `"sql, debug"` → Skill objects with name only
pub(super) fn parse_skills_input(input: &str) -> Vec<bcs_protocol::Skill> {
    if let Ok(value) = serde_json::from_str::<serde_json::Value>(input) {
        if let Some(arr) = value.as_array() {
            let skills: Vec<bcs_protocol::Skill> = arr
                .iter()
                .filter_map(|v| match v {
                    serde_json::Value::String(s) => Some(bcs_protocol::Skill::new(s)),
                    serde_json::Value::Object(_) => serde_json::from_value(v.clone()).ok(),
                    _ => None,
                })
                .collect();
            if !skills.is_empty() {
                return skills;
            }
        }
    }
    input
        .split(',')
        .map(|s| bcs_protocol::Skill::new(s.trim()))
        .filter(|s| !s.name.is_empty())
        .collect()
}

/// Execute a bot lifecycle command: health, connect, onboard, list,
/// get, discover, or update status.
pub(super) async fn execute(
    command: Commands,
    bcs_url: String,
    bcs_cookie: Option<String>,
    oauth_headers: Option<HashMap<String, String>>,
    json: bool,
    structured_mode: bool,
    debug: bool,
) -> Result<()> {
    match command {
        Commands::Health => {
            let mut client = BcsClient::new(&bcs_url);
            if let Some(ref cookie) = bcs_cookie {
                client.set_cookie(cookie);
            }
            if let Some(ref headers) = oauth_headers {
                client.set_oauth_headers(headers.clone());
            }
            let healthy = match client.health_check().await {
                Ok(h) => h,
                Err(e) => {
                    // Under structured_mode, a network/connection error must
                    // surface as a structured JSON result (honoring the output
                    // contract), not a raw traceback on stderr. Human mode
                    // propagates the error unchanged.
                    if structured_mode {
                        let result = StructuredResult {
                            status: "unhealthy".to_string(),
                            message: Some(format!("BCS health check failed: {}", e)),
                            ..Default::default()
                        };
                        emit_structured_result(&result);
                        std::process::exit(1);
                    } else {
                        return Err(e);
                    }
                }
            };
            if structured_mode {
                let result = StructuredResult {
                    status: if healthy {
                        "healthy".to_string()
                    } else {
                        "unhealthy".to_string()
                    },
                    message: Some(format!(
                        "BCS is {} at {}",
                        if healthy { "healthy" } else { "unhealthy" },
                        bcs_url
                    )),
                    ..Default::default()
                };
                emit_structured_result(&result);
                if !healthy {
                    std::process::exit(1);
                }
            } else {
                if healthy {
                    println!("✓ BCS is healthy at {}", bcs_url);
                } else {
                    println!("✗ BCS health check failed");
                    std::process::exit(1);
                }
            }
        }

        Commands::Connect { token, bot_id } => {
            let mut client = BcsClient::new(&bcs_url);
            if let Some(ref cookie) = bcs_cookie {
                client.set_cookie(cookie);
            }
            if let Some(ref headers) = oauth_headers {
                client.set_oauth_headers(headers.clone());
            }

            let caller = bcs_cli::CallerContext::from_env_with_token(token.as_deref())?;
            if caller.actor_id().is_some_and(|actor| bot_id.as_deref().is_some_and(|id| id != actor)) {
                anyhow::bail!("--bot-id must match BCN_ACTOR_BOT_ID when connecting");
            }
            client.set_actor_id(caller.actor_id())?;
            let selected = get_token(token.as_deref())?;
            let resolved_token = (!selected.is_empty()).then_some(selected);
            if let Some(token) = &resolved_token { client.set_token(token); }
            let bot_id = bot_id.or_else(|| caller.actor_id().map(str::to_owned));

            // If we found a session token and no bot_id override, warn the user
            // that this bot is already registered (will reconnect instead of creating new).
            let already_registered = resolved_token.is_some() && bot_id.is_none();

            let params = BotConnectParams {
                token: resolved_token,
                bot_id,
                protocol_version: Some(BCS_PROTOCOL_VERSION),
                client_kind: None,
            };

            debug_request!(
                debug,
                "POST",
                "/bots/connect",
                json!({
                    "token": params.token.as_ref().map(|_| "***"),
                    "bot_id": &params.bot_id
                })
            );

            let result = client.connect(params).await?;

            // Actor-scoped execution must not overwrite a shared session file.
            if caller.actor_id().is_none() {
                let session_bcs_url =
                    normalize_bcs_ws_url(&bcs_url).unwrap_or_else(|| bcs_url.clone());
                let session = SessionInfo {
                    bot_uuid: Some(result.bot_uuid.clone()),
                    token: result.token.clone(),
                    bcs_url: Some(session_bcs_url),
                    api_base_url: Some(bcs_url.clone()),
                };
                let session_path = get_optional_session_file_path();
                if let Some(path) = session_path {
                    if let Some(parent) = path.parent() {
                        let _ = std::fs::create_dir_all(parent);
                    }
                    if let Err(e) = std::fs::write(
                        &path,
                        serde_json::to_string_pretty(&session).unwrap_or_default(),
                    ) {
                        eprintln!("Warning: failed to save session to {:?}: {}", path, e);
                    } else {
                        debug!(target: CLI_LOG_TARGET, "Session saved to {:?}", path);
                    }
                }
            }

            debug_response!(
                debug,
                "200",
                json!({
                    "is_new": result.is_new,
                    "bot_uuid": &result.bot_uuid
                })
            );

            if structured_mode {
                println!("{}", serde_json::to_string(&result)?);
            } else {
                if already_registered && !result.is_new {
                    println!("ℹ Bot already registered, reconnecting:");
                } else if result.is_new {
                    println!("✓ New bot connected to BCS network:");
                } else {
                    println!("✓ Bot reconnected to BCS network:");
                }
                println!("  Bot UUID: {}", result.bot_uuid);
                println!("  Token: {}...", &result.token[..8.min(result.token.len())]);
                if result.is_new {
                    println!("\n  Save this token for reconnection!");
                }
            }
        }

        Commands::Onboard {
            token,
            name,
            summary,
            skills,
            domains,
            scopes,
            binding_channels,
            web,
        } => {
            if web {
                let token = get_token(token.as_deref())?;

                let skills_vec = skills
                    .as_deref()
                    .map(parse_skills_input)
                    .unwrap_or_default();

                let domains_vec = domains.as_deref().map(|d| {
                    d.split(',')
                        .map(|dom| dom.trim().to_string())
                        .collect::<Vec<_>>()
                });
                let scopes_vec = scopes.as_deref().map(|s| {
                    s.split(',')
                        .map(|sc| sc.trim().to_string())
                        .collect::<Vec<_>>()
                });

                // The onboard URL is generated by the BCS server.
                let client = create_client(
                    &bcs_url,
                    &token,
                    bcs_cookie.as_deref(),
                    oauth_headers.as_ref(),
                );

                let server_url = client
                    .get_onboard_url(
                        &token,
                        &name,
                        summary.as_deref(),
                        Some(&skills_vec),
                        domains_vec.as_deref(),
                        scopes_vec.as_deref(),
                        None,
                    )
                    .await;

                let url = match server_url {
                    Ok(Some(url)) => url,
                    Ok(None) => {
                        // Server does not support onboard URL generation (404) or is
                        // unreachable. The URL must come from the server — fail clearly.
                        return Err(anyhow!(
                            "BCS server does not support onboard URL generation. \
                             Please upgrade the BCS server to a version that provides the \
                             onboard URL endpoint."
                        ));
                    }
                    Err(err) => {
                        // Server returned 400 (config error) or other failure → surface the error
                        return Err(err);
                    }
                };

                if url.len() > 2000 {
                    eprintln!(
                        "Warning: Registration URL is very long ({} chars). Some browsers may truncate it.",
                        url.len()
                    );
                }

                if json {
                    println!("{}", json!({"url": url}));
                } else {
                    println!("Registration URL:");
                    println!("{}", url);
                }
            } else {
                // Direct API mode
                let token = get_token(token.as_deref())?;
                let client = create_client(
                    &bcs_url,
                    &token,
                    bcs_cookie.as_deref(),
                    oauth_headers.as_ref(),
                );

                let skills_vec = skills
                    .as_deref()
                    .map(parse_skills_input)
                    .unwrap_or_default();
                let domains_vec = domains
                    .as_deref()
                    .map(|d| d.split(',').map(|dom| dom.trim().to_string()).collect())
                    .unwrap_or_default();
                let scopes_vec = scopes
                    .as_deref()
                    .map(|s| s.split(',').map(|sc| sc.trim().to_string()).collect())
                    .unwrap_or_default();

                // Parse binding_channels JSON
                let binding_channels_parsed: Option<bcs_protocol::BindingChannels> =
                    binding_channels
                        .as_deref()
                        .map(|s| serde_json::from_str(s))
                        .transpose()
                        .map_err(|e| anyhow!("Invalid binding_channels JSON: {}", e))?;

                debug_request!(
                    debug,
                    "POST",
                    "/bots/onboard",
                    json!({
                        "name": &name,
                        "summary": &summary,
                        "skills": &skills_vec,
                        "domains": &domains_vec,
                        "scopes": &scopes_vec,
                        "binding_channels": &binding_channels_parsed
                    })
                );

                let result = client
                    .onboard(
                        &name,
                        summary.as_deref(),
                        Some(skills_vec),
                        Some(domains_vec),
                        Some(scopes_vec),
                        binding_channels_parsed,
                    )
                    .await?;

                debug_response!(
                    debug,
                    "200",
                    json!({
                        "bot_id": &result.bot_uuid,
                        "onboarded": result.onboarded,
                        "name": &result.name,
                        "binding_results": &result.binding_results,
                        "unbound": &result.unbound
                    })
                );

                if json {
                    println!("{}", serde_json::to_string(&result)?);
                } else {
                    println!("✓ Bot onboarded to BCS network:");
                    println!("  Bot ID: {}", result.bot_uuid);
                    println!("  Name: {}", result.name);

                    // Show binding results
                    if !result.binding_results.is_empty() {
                        println!("  Binding results:");
                        for (channel, res) in &result.binding_results {
                            let status = res
                                .get("status")
                                .and_then(|s| s.as_str())
                                .unwrap_or("unknown");
                            if status == "success" {
                                println!("    ✓ {}: {}", channel, status);
                            } else {
                                let msg = res.get("message").and_then(|m| m.as_str()).unwrap_or("");
                                println!("    ✗ {}: {} - {}", channel, status, msg);
                            }
                        }
                    }

                    // Show unbound channels
                    if !result.unbound.is_empty() {
                        println!("  Unbound channels:");
                        for ub in &result.unbound {
                            println!("    - {}", ub);
                        }
                    }
                }
            }
        }

        Commands::List { token } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            debug_request!(debug, "GET", "/bots", json!({}));

            let bots = client.list_bots().await?;

            debug_response!(
                debug,
                "200",
                json!({
                    "count": bots.len()
                })
            );

            println!("Bots in network ({}):", bots.len());
            for bot in bots {
                println!(
                    "  - {} ({})",
                    bot.bot_uuid,
                    bot.capabilities.name.as_deref().unwrap_or("unnamed")
                );
                if let Some(summary) = &bot.capabilities.summary {
                    println!("    {}", summary);
                }
            }
        }

        Commands::Get { token, bot_uuid } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            debug_request!(debug, "GET", &format!("/bots/{}", &bot_uuid), json!({}));

            let bot = client.get_bot(&bot_uuid).await?;

            debug_response!(
                debug,
                "200",
                json!({
                    "bot_id": &bot.bot_uuid,
                    "capabilities": &bot.capabilities
                })
            );

            println!("Bot: {}", bot.bot_uuid);
            if let Some(name) = &bot.capabilities.name {
                println!("  Name: {}", name);
            }
            if let Some(summary) = &bot.capabilities.summary {
                println!("  Summary: {}", summary);
            }
            if !bot.capabilities.skills.is_empty() {
                println!(
                    "  Skills: {}",
                    bot.capabilities
                        .skills
                        .iter()
                        .map(|s| s.name.as_str())
                        .collect::<Vec<_>>()
                        .join(", ")
                );
            }
            if !bot.capabilities.domains.is_empty() {
                println!("  Domains: {}", bot.capabilities.domains.join(", "));
            }
            println!(
                "  Visibility: {}",
                if bot.capabilities.visibility.is_empty() {
                    "protected"
                } else {
                    &bot.capabilities.visibility
                }
            );
        }

        Commands::Discover {
            token,
            query,
            skills,
            visibility,
            collaborate_bot,
            organization_code,
            role,
            no_json,
        } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            debug_request!(
                debug,
                "GET",
                "/bots/discover",
                json!({
                    "q": &query,
                    "skills": &skills,
                    "visibility": &visibility,
                    "collaborate_bot": &collaborate_bot,
                    "organization_code": &organization_code,
                    "role": &role,
                })
            );

            let result = client
                .discover_bots_extended(
                    query.as_deref(),
                    &skills,
                    visibility.as_deref(),
                    collaborate_bot.as_deref(),
                    organization_code.as_deref(),
                    role.as_deref(),
                )
                .await?;

            debug_response!(
                debug,
                "200",
                json!({
                    "count": result.count
                })
            );

            println!(
                "{}",
                render_discover_result(&result, structured_mode && !no_json)?
            );
        }

        Commands::UpdateStatus {
            token,
            status,
            summary,
            load,
        } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            let dynamic_status = bcs_protocol::BotDynamicStatus {
                status,
                dynamic_summary: summary,
                load,
                updated_at: Some(
                    std::time::SystemTime::now()
                        .duration_since(std::time::UNIX_EPOCH)?
                        .as_millis() as u64,
                ),
            };

            debug_request!(
                debug,
                "POST",
                "/bots/status",
                json!({
                    "status": &dynamic_status
                })
            );

            let result = client.update_status_with_token(dynamic_status).await?;

            debug_response!(
                debug,
                "200",
                json!({
                    "updated": result.updated
                })
            );

            if result.updated {
                println!("✓ Status updated");
            } else {
                println!("✗ Failed to update status");
            }
        }

        _ => unreachable!("wrong command family"),
    }

    Ok(())
}
