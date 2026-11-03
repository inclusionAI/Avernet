use super::collaboration_args::CollaborationCommands;
use super::resource_args::{
    ChannelCommands, ChatRunCommands, FriendCommands, GroupContextCommands, ServiceCommands,
    VisibilityCommands,
};
use super::session_args::SessionCommands;
use super::{BCS_CLI_VERSION, DEFAULT_GROUP_BATCH_SIZE};
#[cfg(debug_assertions)]
use clap::ArgAction;
use clap::{Parser, Subcommand};

/// CLI tools for Bot Coordination Service.
#[derive(Parser)]
#[command(name = "bcs-cli")]
#[command(version = BCS_CLI_VERSION)]
#[command(about = "CLI tools for Bot Coordination Service", long_about = None)]
pub(super) struct Cli {
    /// BCS API base URL (also reads BCS_API_BASE_URL/MOLTIS_BCS_URL)
    #[arg(short, long, env = "MOLTIS_BCS_URL")]
    pub(super) url: Option<String>,

    /// Cookie header for authentication (also reads BCS_COOKIE)
    #[arg(short, long, env = "BCS_COOKIE")]
    pub(super) cookie: Option<String>,

    /// Log level
    #[arg(short, long, default_value = "info")]
    pub(super) log_level: String,

    /// Output in JSON format (default: enabled). Use --no-json for interactive/human mode.
    #[arg(short, long)]
    pub(super) json: bool,

    /// Disable JSON output for interactive/human use
    #[arg(long)]
    pub(super) no_json: bool,

    /// Debug mode - print HTTP request/response details
    #[cfg(debug_assertions)]
    #[arg(short = 'D', long, env = "BCS_DEBUG", action = ArgAction::SetTrue)]
    pub(super) debug: bool,

    #[command(subcommand)]
    pub(super) command: Commands,
}

#[derive(Subcommand)]
pub(super) enum Commands {
    /// Health check for BCS (no authentication required)
    Health,

    /// Connect to BCS network via HTTP (alternative to WebSocket)
    /// Returns a session token for subsequent API calls.
    Connect {
        /// Optional token from previous session for reconnection
        #[arg(short, long)]
        token: Option<String>,

        /// Optional preconfigured bot_id
        #[arg(long)]
        bot_id: Option<String>,
    },

    /// Onboard to the BCS network - register bot details after WebSocket connection
    Onboard {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        /// Bot display name
        #[arg(short = 'n', long)]
        name: String,

        /// Bot capability summary
        #[arg(long)]
        summary: Option<String>,

        /// Skills (comma-separated or JSON array)
        #[arg(short, long)]
        skills: Option<String>,

        /// Domains (comma-separated)
        #[arg(short, long)]
        domains: Option<String>,

        /// Scopes (comma-separated)
        #[arg(long)]
        scopes: Option<String>,

        /// Channel bindings for message routing (JSON format)
        /// Example: '{"antding":{"binding_key":"11111111"},"wechat":{"binding_key":"vid_1294"}}'
        #[arg(long)]
        binding_channels: Option<String>,

        /// Output a web registration URL instead of calling the onboard API directly.
        #[arg(long)]
        web: bool,
    },

    /// List all registered bots
    List {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,
    },

    /// Get a specific bot's info
    Get {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        /// Bot UUID to get info for
        bot_uuid: String,
    },

    /// Discover bots by query
    Discover {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        /// Search query
        #[arg(short, long)]
        query: Option<String>,

        /// Require an exact skill match (repeat for multiple required skills)
        #[arg(long = "skill")]
        skills: Vec<String>,

        /// Filter by visibility ("public" or "protected")
        #[arg(long)]
        visibility: Option<String>,

        /// Return bots that this bot can collaborate with (public + friends).
        /// Pass a bot_uuid to filter by collaboration eligibility.
        #[arg(long)]
        collaborate_bot: Option<String>,

        /// Organization code for scoped discovery.
        #[arg(long)]
        organization_code: Option<String>,

        /// Organization member role filter. Requires --organization-code.
        #[arg(long)]
        role: Option<String>,

        /// Output discover results in human-readable text instead of JSON
        #[arg(long)]
        no_json: bool,
    },

    /// Update bot status
    UpdateStatus {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        /// Status (idle/busy/offline)
        #[arg(short, long)]
        status: String,

        /// Dynamic summary
        #[arg(short = 'm', long)]
        summary: Option<String>,

        /// Load (0.0-1.0)
        #[arg(short, long)]
        load: Option<f32>,
    },

    /// Create a group directly
    CreateGroup {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        /// Create only the group, without an initial session or bootstrap run.
        /// Requires a server that supports create_initial_session=false.
        #[arg(long)]
        no_session: bool,

        /// Group ID (optional, auto-generated if not provided)
        #[arg(short, long, hide = true)]
        id: Option<String>,

        /// Driver bot ID for a regular chat group (required unless --manager is used)
        #[arg(long, required_unless_present = "manager", conflicts_with = "manager")]
        driver: Option<String>,

        /// Manager bot ID for a manager-worker group (required unless --driver is used)
        #[arg(long, required_unless_present = "driver", conflicts_with = "driver")]
        manager: Option<String>,

        /// Participants (comma-separated bot UUIDs, e.g. "bot1,20260412_abc:100005")
        #[arg(short, long)]
        participants: String,

        /// Provider routing tag for a participant; repeat as BOT_UUID=TAG
        #[arg(long = "participant-tag", value_name = "BOT_UUID=TAG")]
        participant_tags: Vec<String>,

        /// Group context (optional description of collaboration goal/background)
        #[arg(long)]
        context: Option<String>,

        /// Group topic (sets the group label)
        #[arg(long)]
        topic: Option<String>,
    },

    /// Validate, create, or run custom collaborations
    #[command(visible_alias = "collaborate")]
    Collaboration {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        #[command(subcommand)]
        command: CollaborationCommands,
    },

    /// Get group info
    GetGroup {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        /// Group ID
        #[arg(long)]
        id: String,
    },

    /// Fuse contexts from participants
    Fuse {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        /// Group ID
        #[arg(long)]
        group: String,

        /// Question to fuse for
        #[arg(short, long)]
        question: String,

        /// Participants (comma-separated bot IDs)
        #[arg(short, long)]
        participants: String,

        /// Focus area
        #[arg(short, long)]
        focus: Option<String>,
    },

    /// List groups the authenticated human or bot formally participates in
    ListGroups {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        /// Maximum number of groups to return in this batch
        #[arg(long, default_value_t = DEFAULT_GROUP_BATCH_SIZE)]
        batch_size: u64,

        /// Start listing at this zero-based group offset
        #[arg(long, default_value_t = 0)]
        offset: u64,

        /// Fetch all groups by following every batch
        #[arg(long)]
        all: bool,
    },

    /// Add a member to an existing group
    AddMember {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        /// Group ID
        #[arg(long)]
        group: String,

        /// Bot UUID to add
        #[arg(long)]
        bot_uuid: String,
    },

    /// Chat with another bot (1:1 message via BCS routing)
    ///
    /// Uses the async submit + long-poll flow so long-running bot tasks
    /// (e.g. multi-step agent reasoning) are not subject to any single HTTP
    /// timeout on the client side.
    #[command(visible_alias = "invoke")]
    Chat {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        /// Target bot UUID (the bot's unique identifier assigned by BCS)
        #[arg(short = 'b', long)]
        bot_uuid: String,

        /// Message to send
        #[arg(short, long)]
        message: String,

        /// Local polling budget in milliseconds (up to 24 hours).
        ///
        /// This never changes the BCS run or downstream bot execution timeout.
        /// Defaults to 30 minutes for the blocking flow, or 60 seconds when
        /// `--detach` is set (the local budget for observing the first ack).
        #[arg(long, value_parser = clap::value_parser!(u64).range(1..=86_400_000))]
        timeout_ms: Option<u64>,

        /// Optional stable session identifier. When provided, multiple calls
        /// land in the same session on the bot side so context is shared.
        #[arg(long)]
        session_id: Option<String>,

        /// Provider routing tag. Repeat to send multiple tags.
        #[arg(long = "tag", value_name = "TAG")]
        tags: Vec<String>,

        /// Response content mode: full or after-last-tool-call.
        #[arg(long, value_parser = ["full", "after-last-tool-call"], default_value = "after-last-tool-call")]
        response_mode: Option<String>,

        /// Per-poll HTTP wait budget in milliseconds.
        #[arg(long, default_value_t = 15_000u64, hide = true)]
        poll_wait_ms: u64,

        /// Return as soon as BCS durably accepts the request, including queued requests.
        #[arg(long, default_value_t = false)]
        detach: bool,

        /// Wait for downstream execution to start, without waiting for completion.
        #[arg(long, value_parser = ["running"], conflicts_with = "detach")]
        wait_until: Option<String>,

        /// Organization code for scoped A2A chat. This is request metadata only.
        #[arg(long)]
        organization_code: Option<String>,
    },

    /// Query or cancel an existing Direct A2A run.
    ChatRun {
        #[arg(short, long)]
        token: Option<String>,
        #[command(subcommand)]
        command: ChatRunCommands,
    },

    /// Update group status (coordinator/originator only)
    /// Use this to mark a group as completed or closed.
    GroupStatus {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        /// Group ID
        #[arg(short, long)]
        group: String,

        /// New status (active/completed/closed/inactive)
        #[arg(short, long)]
        status: String,

        /// Optional reason for status change
        #[arg(short, long)]
        reason: Option<String>,
    },

    /// Terminate a group session (driver only)
    /// This marks the group as completed and broadcasts termination to participants.
    TerminateGroup {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        /// Group ID
        #[arg(short, long)]
        group: String,
    },

    /// Manage Group Context entries through the BCN-compatible HTTP API.
    GroupContext {
        /// Authentication credential (auto-discovered if not provided)
        #[arg(short = 'a', long = "auth", value_name = "AUTH")]
        auth: Option<String>,

        #[command(subcommand)]
        command: GroupContextCommands,
    },

    /// Manage bot friendships (request, accept, reject, list)
    Friend {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        #[command(subcommand)]
        command: FriendCommands,
    },

    /// Manage channel (IM bridge) bindings
    Channel {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        #[command(subcommand)]
        command: ChannelCommands,
    },

    /// Get or set bot visibility (auto-resolves bot UUID from token)
    Visibility {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        #[command(subcommand)]
        command: VisibilityCommands,
    },

    /// Manage sessions within a group (create, list, get, chat, messages)
    Session {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        #[command(subcommand)]
        command: SessionCommands,
    },

    /// Drive a service-invocation flow on an existing group.
    ///
    /// Requires a bot token. The server records the caller as `bot:<bot_id>`.
    Service {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        #[command(subcommand)]
        command: ServiceCommands,
    },
}
