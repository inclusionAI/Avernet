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

// `oauth` is now a `pub mod` on the `bcs_cli` library (public extension point,
// resolved via `inventory`). Public builds link no provider and fall back to a
// stub; internal builds link an Ant provider that performs the real flow.
use bcs_cli::oauth;


use std::collections::{BTreeMap, BTreeSet, HashMap};

use std::path::{Path, PathBuf};


use anyhow::{Result, anyhow};

#[cfg(debug_assertions)]
use clap::ArgAction;

use clap::{Parser, Subcommand};

use serde::{Deserialize, Serialize};

use serde_json::json;

use tracing::{Level, debug, info, warn};

use tracing_subscriber::FmtSubscriber;


use bcs_cli::{
    BcsClient, ChatAsyncError, ChatRunOutcome, CreateCustomGroupOptions,
    RunSessionCollaborationOptions,
};

use bcs_protocol::{BCS_PROTOCOL_VERSION, BotConnectParams};


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


/// Skill→BCS interactive debug
macro_rules! skill_debug_request {
    ($debug:expr, $method:expr, $endpoint:expr, $body:expr) => {
        if $debug {
            eprintln!("[Skill→BCS] {} {}", $method, $endpoint);
            if !$body.is_null() {
                eprintln!("    {}", serde_json::to_string(&$body).unwrap_or_default());
            }
        }
    };
}


/// BCS→Skill response debug
macro_rules! skill_debug_response {
    ($debug:expr, $status:expr, $body:expr) => {
        if $debug {
            eprintln!("[BCS→Skill] Status: {}", $status);
            eprintln!(
                "    {}",
                serde_json::to_string_pretty(&$body).unwrap_or_default()
            );
        }
    };
}


// ============================================================================
// Tests
// ============================================================================

#[cfg(test)]
#[path = "command_tests/mod.rs"]
mod tests;


#[path = "commands/health.rs"]
mod commands_health;

#[path = "commands/connect.rs"]
mod commands_connect;

#[path = "commands/onboard.rs"]
mod commands_onboard;

#[path = "commands/list.rs"]
mod commands_list;

#[path = "commands/get.rs"]
mod commands_get;

#[path = "commands/discover.rs"]
mod commands_discover;

#[path = "commands/update_status.rs"]
mod commands_update_status;

#[path = "commands/request_group_help.rs"]
mod commands_request_group_help;

#[path = "commands/confirm_group_help.rs"]
mod commands_confirm_group_help;

#[path = "commands/create_group.rs"]
mod commands_create_group;

#[path = "commands/collaboration.rs"]
mod commands_collaboration;

#[path = "commands/get_group.rs"]
mod commands_get_group;

#[path = "commands/fuse.rs"]
mod commands_fuse;

#[path = "commands/list_groups.rs"]
mod commands_list_groups;

#[path = "commands/add_member.rs"]
mod commands_add_member;

#[path = "commands/chat.rs"]
mod commands_chat;

#[path = "commands/chat_run.rs"]
mod commands_chat_run;

#[path = "commands/group_status.rs"]
mod commands_group_status;

#[path = "commands/terminate_group.rs"]
mod commands_terminate_group;

#[path = "commands/friend.rs"]
mod commands_friend;

#[path = "commands/channel.rs"]
mod commands_channel;

#[path = "commands/visibility.rs"]
mod commands_visibility;

#[path = "commands/session.rs"]
mod commands_session;

#[path = "commands/service.rs"]
mod commands_service;


#[path = "command_support/auth_via_agent_pass.rs"]
mod auth_via_agent_pass;
use auth_via_agent_pass::*;
#[path = "command_support/apply_group_participant_tags.rs"]
mod apply_group_participant_tags;
use apply_group_participant_tags::*;
#[path = "command_support/friendcommands.rs"]
mod friendcommands;
use friendcommands::*;
pub use friendcommands::{run};

/// Binary entry. Delegates to [`run`] so the internal overlay can reuse this
/// module (`#[path]`) and call `run()` directly under its own runtime.
fn main() -> Result<()> {
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;
    runtime.block_on(run())
}
