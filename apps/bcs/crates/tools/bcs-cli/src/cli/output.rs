use std::path::PathBuf;

use anyhow::Result;
use serde::Serialize;

use super::Cli;

#[derive(Debug, Serialize)]
pub(super) struct GroupListOutput {
    pub(super) items: Vec<serde_json::Value>,
    pub(super) offset: u64,
    pub(super) returned: u64,
    pub(super) total: u64,
    pub(super) has_more: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(super) next_offset: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(super) next_command: Option<String>,
}

#[derive(Debug, Serialize, Default)]
pub(super) struct StructuredResult {
    pub(super) status: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(super) message: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(super) network_env: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(super) auth_url: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(super) timeout_secs: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(super) log_file: Option<String>,
}

pub(super) fn is_structured_mode(cli: &Cli) -> bool {
    // Default is structured (JSON) mode. --no-json disables it.
    // --json flag kept for backward compatibility (OpenClaw already passes it).
    !cli.no_json
}

pub(super) fn build_log_file_path() -> PathBuf {
    let dir = std::env::var("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."))
        .join(".bcs/logs");
    dir.join("bcs-cli.log")
}

pub(super) fn emit_structured_result(result: &StructuredResult) {
    println!(
        "{}",
        serde_json::to_string(result)
            .unwrap_or_else(|_| "{\"status\":\"request_failed\"}".to_string())
    );
}

pub(super) fn render_discover_result(
    result: &bcs_protocol::DiscoverBotsExtendedResponse,
    json_output: bool,
) -> Result<String> {
    if json_output {
        return Ok(serde_json::to_string_pretty(result)?);
    }

    let mut lines = vec![format!("Discovered {} bots:", result.count)];
    for bot in &result.bots {
        let name = bot.capabilities.name.as_deref().unwrap_or("unnamed");
        let vis = &bot.visibility;
        let friend_tag = match bot.is_friend {
            Some(true) => " ★friend",
            Some(false) | None => "",
        };
        let provider_tag = bot
            .provider_info
            .as_ref()
            .map(|provider| {
                format!(
                    " provider={}/{}",
                    provider.provider_name, provider.provider_id
                )
            })
            .unwrap_or_default();
        let agent_code_tag = bot
            .agent_code
            .as_ref()
            .map(|agent_code| format!(" agent_code={agent_code}"))
            .unwrap_or_default();
        lines.push(format!(
            "  - {} ({}) [{}]{}{}{}",
            bot.bot_uuid, name, vis, friend_tag, provider_tag, agent_code_tag
        ));
    }

    Ok(lines.join("\n"))
}

pub(super) fn classify_auth_error_message(msg: &str) -> &'static str {
    if msg.contains("timed out locally") {
        "auth_timeout"
    } else if msg.contains("expired on server") || msg.contains("authorization expired") {
        "auth_expired"
    } else if msg.contains("auth URL")
        || msg.contains("NeedAuth")
        || msg.contains("authorization required")
    {
        "auth_required"
    } else if msg.contains("OAuth2") || msg.contains("OAuth") {
        "auth_failed"
    } else {
        "request_failed"
    }
}
