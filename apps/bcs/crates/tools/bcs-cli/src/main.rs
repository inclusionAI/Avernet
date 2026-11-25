//! BCS CLI - Command-line tools for Bot Coordination Service.
//!
//! This binary provides CLI commands for:
//! - Bot lifecycle (onboard)
//! - Bot discovery (list, discover)
//! - Group collaboration (request, confirm, create)
//! - Cross-bot communication (chat)
//!
//! # Configuration
//!
//! BCS CLI can be configured via:
//! 1. `--url` (highest priority)
//! 2. `BCS_API_BASE_URL`
//! 3. `MOLTIS_BCS_URL`
//! 4. Session file: `$BOT_DATA_DIR/.bcs/session.json`
//! 5. Runtime-selected compiled distribution default
//! 6. Default local URL (lowest priority)
//!
//! # Environment-Based Configuration
//!
//! Distribution builds may compile both pre and production defaults into one
//! binary. Runtime environment priority is `AGENTCLAW_ENV`, `env`, then the
//! server environment chain. `pre`/`prepub` selects pre; every other value
//! selects production. Public builds omit both compiled defaults.
//!
//! # Token Discovery
//!
//! Token is discovered in the following order:
//! 1. `--token` CLI argument
//! 2. `BCN_BOT_TOKEN` environment variable (set by BCN plugin)
//! 3. `$BOT_DATA_DIR/.bcs/session.json` file (written by BCN plugin)
//!
//! Note: bcs-cli is stateless - it only READS session files, never writes.
//! Session persistence is handled by the BCN plugin (moltis-bcn crate).
//!
//! # Usage
//!
//! ```bash
//! # Token auto-discovered from env var or session file
//! bcs-cli onboard --name "My Bot" --summary "An assistant bot"
//!
//! # Or specify token explicitly
//! bcs-cli onboard --token <token> --name "My Bot" --summary "An assistant bot"
//!
//! # Use pre-production environment
//! env=pre bcs-cli health
//! ```

// `agentpass` is a bin-local module (dead code here: `AUTH_VIA_AGENT_PASS = false`).
// It stays as `src/agentpass.rs` so the internal `#[path]` reuse of this file
// resolves it identically.
mod collaboration_output;
mod agentpass;
mod collaboration_snapshot;
// `oauth` is now a `pub mod` on the `bcs_cli` library (public extension point,
// resolved via `inventory`). Public builds link no provider and fall back to a
// stub; internal builds link an Ant provider that performs the real flow.
use bcs_cli::oauth;

#[cfg(test)]
use std::path::PathBuf;

use anyhow::Result;
use clap::Parser;
#[cfg(test)]
use serde_json::json;
use tracing::Level;
use tracing_subscriber::FmtSubscriber;


// disable agentpass, agentpass token should be auto injected into the http headers
const AUTH_VIA_AGENT_PASS: bool = false;
const DEFAULT_GROUP_BATCH_SIZE: u64 = 20;

const CLI_LOG_TARGET: &str = module_path!();

const BCS_CLI_VERSION: &str = concat!(
    env!("CARGO_PKG_VERSION"),
    " (",
    env!("GIT_COMMIT_HASH"),
    " ",
    env!("BUILD_DATE"),
    ")",
);

/// Print HTTP request in debug mode
macro_rules! debug_request {
    ($debug:expr, $method:expr, $endpoint:expr, $body:expr) => {
        if $debug {
            eprintln!("\x1b[2m[→BCS] {} {}", $method, $endpoint);
            if !$body.is_null() {
                eprintln!(
                    "    Body: {}",
                    serde_json::to_string(&$body).unwrap_or_default()
                );
            }
            eprintln!("\x1b[0m");
        }
    };
}

/// Print HTTP response in debug mode
macro_rules! debug_response {
    ($debug:expr, $status:expr, $body:expr) => {
        if $debug {
            eprintln!("\x1b[2m[←BCS] Status: {}", $status);
            eprintln!(
                "    {}",
                serde_json::to_string_pretty(&$body).unwrap_or_default()
            );
            eprintln!("\x1b[0m");
        }
    };
}

#[path = "cli/args.rs"]
mod args;
#[path = "cli/authentication.rs"]
mod authentication;
#[path = "cli/bot_commands.rs"]
mod bot_commands;
#[path = "cli/channel_commands.rs"]
mod channel_commands;
#[path = "cli/chat_commands.rs"]
mod chat_commands;
#[path = "cli/collaboration_args.rs"]
mod collaboration_args;
#[path = "cli/collaboration_commands.rs"]
mod collaboration_commands;
#[path = "cli/collaboration_input.rs"]
mod collaboration_input;
#[path = "cli/environment.rs"]
mod environment;
#[path = "cli/group_commands.rs"]
mod group_commands;
#[path = "cli/group_context_commands.rs"]
mod group_context_commands;
#[path = "cli/input.rs"]
mod input;
#[path = "cli/output.rs"]
mod output;
#[path = "cli/relation_commands.rs"]
mod relation_commands;
#[path = "cli/resource_args.rs"]
mod resource_args;
#[path = "cli/service_commands.rs"]
mod service_commands;
#[path = "cli/session_args.rs"]
mod session_args;
#[path = "cli/session_commands.rs"]
mod session_commands;
#[path = "cli/session_file_commands.rs"]
mod session_file_commands;

use args::{Cli, Commands};
#[cfg(test)]
use channel_commands::{build_channel_bind_payload, redact_channel_bind_debug_payload};
use collaboration_args::CollaborationCommands;
#[cfg(test)]
use collaboration_input::{
    apply_group_participant_tags, build_panel_opening_message, parse_custom_group_bindings,
    validate_custom_group_bindings,
};
#[cfg(test)]
use environment::{NetworkEnv, detect_network_env, normalize_bcs_ws_url};
use environment::{get_current_env, resolve_bcs_url};
#[cfg(test)]
use environment::{discover_token, is_localhost_url, normalize_bcs_api_url};
#[cfg(test)]
use group_context_commands::{
    group_context_add_body, group_context_query, group_context_update_body,
    resolve_group_context_scope_levels,
};
#[cfg(test)]
use group_context_commands::group_context_insert_optional_timestamp;
#[cfg(test)]
use input::parse_json_arg;
use output::{build_log_file_path, is_structured_mode};
#[cfg(test)]
use output::{StructuredResult, classify_auth_error_message};
#[cfg(test)]
use resource_args::{ChannelCommands, GroupContextCommands, ServiceCommands};
#[cfg(test)]
use service_commands::split_service_sid;
#[cfg(test)]
use service_commands::service_session_summary_lines;
#[cfg(test)]
use session_args::SessionCommands;

/// Binary entry. Delegates to [`run`] so the internal overlay can reuse this
/// module (`#[path]`) and call `run()` directly under its own runtime.
fn main() -> Result<()> {
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;
    runtime.block_on(run())
}

/// Run the CLI.
///
/// Kept `pub` so an internal build can reuse this whole module (via `#[path]`)
/// and link an Ant-side OAuth provider — see the ocb overlay crate
/// `crates/tools/bcs-cli-internal`. The body is identical to the legacy
/// `#[tokio::main] async fn main`; only the entry is split out.
pub async fn run() -> Result<()> {
    let cli = Cli::parse();

    // The preview producer is intentionally dispatched before URL resolution,
    // network probing, authentication, logging setup, or service discovery.
    if let Commands::Collaboration {
        command:
            CollaborationCommands::Preview {
                file,
                title,
                document_id,
                parent_snapshot_id,
                snapshot_id,
            },
        ..
    } = &cli.command
    {
        let output = collaboration_snapshot::build_preview(
            collaboration_snapshot::PreviewOptions {
                file,
                title: title.as_deref(),
                document_id: document_id.as_deref(),
                parent_snapshot_id: parent_snapshot_id.as_deref(),
                snapshot_id: snapshot_id.as_deref(),
            },
        )?;
        // Unlike network/query commands, preview's primary stdout artifact is
        // the message itself. Structured output is therefore opt-in with
        // --json so bots can forward the default output verbatim.
        if cli.json && !cli.no_json {
            println!("{}", serde_json::to_string(&output)?);
        } else {
            println!("{}", output.message);
        }
        return Ok(());
    }

    // Setup logging
    let level = match cli.log_level.to_lowercase().as_str() {
        "trace" => Level::TRACE,
        "debug" => Level::DEBUG,
        "info" => Level::INFO,
        "warn" => Level::WARN,
        "error" => Level::ERROR,
        _ => Level::INFO,
    };
    let structured_mode = is_structured_mode(&cli);
    let log_file = build_log_file_path();

    // Signal structured mode to oauth.rs (which can't access the CLI struct)
    if structured_mode {
        oauth::set_structured_mode(true);
    }

    // Always write logs to file regardless of --no-json flag
    if let Some(parent) = log_file.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_file)?;
    FmtSubscriber::builder()
        .with_max_level(level)
        .with_target(false)
        .with_writer(std::sync::Mutex::new(file))
        .compact()
        .init();

    bcs_cli::CallerContext::new(None, std::env::var("BCN_ACTOR_BOT_ID").ok().as_deref())?;

    // Get current environment (defaults to "dev")
    let env = get_current_env();

    // Determine BCS URL: CLI arg > env var > session.json > compiled default > local
    // Note: bcs_url resolution is deferred for commands that don't need it (e.g., --web mode).
    let bcs_url = resolve_bcs_url(&cli).unwrap_or_default();

    // Determine cookie: CLI arg > env var
    let bcs_cookie = cli.cookie.or_else(|| std::env::var("BCS_COOKIE").ok());

    // Debug mode: print all HTTP communications (only available in debug builds)
    #[cfg(debug_assertions)]
    let debug = cli.debug || std::env::var("BCS_DEBUG").is_ok_and(|v| v == "true");
    #[cfg(not(debug_assertions))]
    let debug = false;
    if debug {
        eprintln!(
            "\x1b[2m[→BCS] Environment: {} | BCS URL: {}\x1b[0m",
            env, bcs_url
        );
        if bcs_cookie.is_some() {
            eprintln!("\x1b[2m[→BCS] Cookie: (set)\x1b[0m");
        }
    }

    // Resolve network environment and OAuth headers.
    // Priority: tc_sdb_nenv env var > auto-detect via health probe.
    // Skip network probing when bcs_url is empty (e.g., --web mode doesn't need BCS API).
    let (_network_env, oauth_headers) = match authentication::prepare_authentication(
        &bcs_url, debug, structured_mode, &log_file,
    ).await? {
        std::ops::ControlFlow::Break(()) => return Ok(()),
        std::ops::ControlFlow::Continue(prepared) => prepared,
    };

    match cli.command {
        command @ (Commands::Health | Commands::Connect { .. } | Commands::Onboard { .. } | Commands::List { .. } | Commands::Get { .. } | Commands::Discover { .. } | Commands::UpdateStatus { .. }) => bot_commands::execute(command, bcs_url, bcs_cookie, oauth_headers, cli.json, structured_mode, debug).await?,
        command @ (Commands::CreateGroup { .. } | Commands::GetGroup { .. } | Commands::Fuse { .. } | Commands::ListGroups { .. } | Commands::AddMember { .. } | Commands::GroupStatus { .. } | Commands::TerminateGroup { .. }) => group_commands::execute(command, bcs_url, bcs_cookie, oauth_headers, cli.json, structured_mode, debug).await?,
        command @ (Commands::Chat { .. } | Commands::ChatRun { .. }) => chat_commands::execute(command, bcs_url, bcs_cookie, oauth_headers, structured_mode, debug).await?,
        command @ Commands::Collaboration { .. } => collaboration_commands::execute(command, bcs_url, bcs_cookie, oauth_headers, structured_mode, debug).await?,
        command @ Commands::GroupContext { .. } => group_context_commands::execute(command, bcs_url, bcs_cookie, oauth_headers).await?,
        command @ (Commands::Friend { .. } | Commands::Visibility { .. }) => relation_commands::execute(command, bcs_url, bcs_cookie, oauth_headers, cli.json, debug).await?,
        command @ Commands::Channel { .. } => channel_commands::execute(command, bcs_url, bcs_cookie, oauth_headers, cli.json, debug).await?,
        command @ Commands::Session { .. } => session_commands::execute(command, bcs_url, bcs_cookie, oauth_headers, cli.json, debug).await?,
        command @ Commands::Service { .. } => service_commands::execute(command, bcs_url, bcs_cookie, oauth_headers, cli.json, debug).await?,
    }

    Ok(())
}

// ============================================================================
// Tests
// ============================================================================

#[cfg(test)]
#[path = "cli/tests.rs"]
mod tests;

#[cfg(test)]
mod retired_service_policy_tests {
    use super::*;
    #[test]
    fn service_invoke_rejects_retired_callback_flag() {
        assert!(Cli::try_parse_from(["bcs-cli", "service", "invoke", "--group", "g", "--baas-session-id", "old-target"]).is_err());
    }
}
