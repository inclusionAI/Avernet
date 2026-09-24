//! command support implementation.
use super::*;


pub(super) fn apply_group_participant_tags(
    participants: &mut Vec<bcs_protocol::ParticipantInfo>,
    lead_bot: &str,
    lead_role: Option<&str>,
    values: &[String],
) -> Result<()> {
    for value in values {
        let (raw_bot_id, raw_tag) = value.split_once('=').ok_or_else(|| {
            anyhow!(
                "Invalid --participant-tag '{}'; expected BOT_UUID=TAG",
                value
            )
        })?;
        let bot_id = raw_bot_id.trim();
        let tag = raw_tag.trim();
        if bot_id.is_empty() || tag.is_empty() {
            return Err(anyhow!(
                "Invalid --participant-tag '{}'; bot UUID and tag must not be empty",
                value
            ));
        }
        if bot_id == lead_bot
            && !participants
                .iter()
                .any(|participant| participant.bot_uuid == lead_bot)
        {
            participants.insert(
                0,
                bcs_protocol::ParticipantInfo {
                    bot_uuid: lead_bot.to_string(),
                    role: lead_role.map(str::to_string),
                    tags: Vec::new(),
                    message_view_scope: None,
                },
            );
        }
        let participant = participants
            .iter_mut()
            .find(|participant| participant.bot_uuid == bot_id)
            .ok_or_else(|| {
                anyhow!(
                    "Invalid --participant-tag '{}'; bot '{}' is not a group participant",
                    value,
                    bot_id
                )
            })?;
        participant.tags.push(tag.to_string());
    }
    Ok(())
}



pub(super) fn validate_custom_group_bindings(
    bindings: &BTreeMap<String, bcs_protocol::ParticipantBindingInfo>,
    validation: &serde_json::Value,
    driver: &str,
) -> Result<()> {
    let slots = validation
        .get("participants")
        .and_then(serde_json::Value::as_array)
        .ok_or_else(|| anyhow!("Validation response is missing participants"))?;
    let declared = slots
        .iter()
        .filter_map(|slot| slot.get("binding").and_then(serde_json::Value::as_str))
        .collect::<BTreeSet<_>>();
    for binding in bindings.keys() {
        if !declared.contains(binding.as_str()) {
            return Err(anyhow!("--binding references undeclared participant role: {binding}"));
        }
    }
    for slot in slots {
        let Some(binding) = slot.get("binding").and_then(serde_json::Value::as_str) else {
            continue;
        };
        let needs_binding = slot
            .get("required")
            .and_then(serde_json::Value::as_bool)
            .unwrap_or(false)
            || slot
                .get("assigned")
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(false);
        if needs_binding && !bindings.contains_key(binding) {
            return Err(anyhow!("Missing --binding for participant role: {binding}"));
        }
    }
    if !bindings
        .values()
        .flat_map(|binding| &binding.bot_ids)
        .any(|bot_id| bot_id == driver)
    {
        return Err(anyhow!(
            "Driver bot must appear in at least one --binding: {driver}"
        ));
    }
    Ok(())
}



pub(super) fn emit_collaboration_validation(validation: &serde_json::Value, structured_mode: bool) {
    if structured_mode {
        println!(
            "{}",
            serde_json::to_string(validation).unwrap_or_else(|_| "{}".to_string())
        );
        return;
    }
    if validation.get("valid").and_then(serde_json::Value::as_bool) == Some(true) {
        let summary = validation.get("summary").cloned().unwrap_or_default();
        println!("VALID");
        println!(
            "  Participants: {}",
            summary.get("participants").and_then(serde_json::Value::as_u64).unwrap_or(0)
        );
        println!(
            "  Nodes: {}",
            summary.get("nodes").and_then(serde_json::Value::as_u64).unwrap_or(0)
        );
        if let Some(graph) = validation.get("graph") {
            if graph.get("execution_graph_mode").is_some() {
                print!("{}", collaboration_output::render(graph));
            }
        }
    } else if let Some(errors) = validation.get("errors").and_then(serde_json::Value::as_array) {
        for error in errors {
            println!(
                "{} {}: {}",
                error.get("code").and_then(serde_json::Value::as_str).unwrap_or("INVALID"),
                error.get("path").and_then(serde_json::Value::as_str).unwrap_or("$"),
                error.get("message").and_then(serde_json::Value::as_str).unwrap_or("validation failed")
            );
        }
    }
    if let Some(warnings) = validation.get("warnings").and_then(serde_json::Value::as_array) {
        for warning in warnings {
            println!("WARNING {} {}: {}", warning.get("code").and_then(serde_json::Value::as_str).unwrap_or("WARNING"),
                warning.get("path").and_then(serde_json::Value::as_str).unwrap_or("$"),
                warning.get("message").and_then(serde_json::Value::as_str).unwrap_or(""));
        }
    }

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

    /// Request group help - create a collaboration proposal
    RequestGroupHelp {
        /// Authentication token (auto-discovered if not provided)
        #[arg(long)]
        token: Option<String>,

        /// Topic for the group collaboration
        #[arg(short, long)]
        topic: String,

        /// Suggested participants (comma-separated)
        #[arg(short, long)]
        participants: Option<String>,

        /// Suggested driver (currently ignored by server; driver is always the requesting bot)
        #[arg(long)]
        driver: Option<String>,
    },

    /// Confirm a group help proposal
    ConfirmGroupHelp {
        /// Confirm URL (full URL with token)
        #[arg(short, long)]
        url: String,
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

    /// Drive a service-invocation flow on a group with `service_spec` set.
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



#[derive(Subcommand)]
pub(super) enum CollaborationCommands {
    /// Query whether the authenticated Bot may run a state machine in a session
    Permission {
        /// Current BCS session ID
        #[arg(long)]
        session: String,
    },

    /// Submit YAML and role bindings once, then run it in the current session
    Run {
        /// YAML file containing the one-shot state-machine definition
        file: PathBuf,

        /// Current BCS session ID
        #[arg(long)]
        session: String,

        /// Logical participant binding in ROLE=BOT_UUID form; repeat for each role
        #[arg(long = "binding", value_name = "ROLE=BOT_UUID", required = true)]
        bindings: Vec<String>,

        /// Runtime input JSON, or @path/to/input.json
        #[arg(long, default_value = "{}")]
        input: String,

        /// AixUI panel component to show when this Run starts
        #[arg(long)]
        panel_component: Option<String>,

        /// Panel params JSON object, or @path/to/params.json
        #[arg(long, requires = "panel_component")]
        panel_params: Option<String>,

        /// Optional panel tab ID template
        #[arg(long, requires = "panel_component")]
        panel_tab_id: Option<String>,

        /// Optional panel tab title template
        #[arg(long, requires = "panel_component")]
        panel_tab_title: Option<String>,

        /// Whether the optional panel tab is closable
        #[arg(long, requires = "panel_component")]
        panel_tab_closable: Option<bool>,
    },

    /// Inspect a Run, graph, exact execution node, or pending Human inputs
    Query {
        #[arg(long)]
        run: String,
        /// Execution node ID copied from a Run or graph response
        #[arg(long, conflicts_with_all = ["graph", "pending"])]
        node: Option<String>,
        #[arg(long, conflicts_with = "pending")]
        graph: bool,
        #[arg(long)]
        pending: bool,
    },

    /// Reply to an exact pending Human execution node using natural language
    Respond {
        #[arg(long)]
        run: String,
        /// Copy the execution ID from `collaborate query --pending`
        #[arg(long)]
        node: String,
        #[arg(long)]
        content: String,
    },

    /// Validate a custom collaboration definition against the current BCS server
    Validate {
        /// YAML file to validate
        file: PathBuf,
    },

    /// Create a custom collaboration group from a validated definition
    Create {
        /// YAML file containing the custom collaboration definition
        file: PathBuf,

        /// Group ID (optional, auto-generated if not provided)
        #[arg(short, long)]
        id: Option<String>,

        /// Driver bot UUID; it must appear in at least one participant binding
        #[arg(long)]
        driver: String,

        /// Logical participant binding in ROLE=BOT_UUID form; repeat for each role
        #[arg(long = "binding", value_name = "ROLE=BOT_UUID", required = true)]
        bindings: Vec<String>,

        /// Group context describing the collaboration goal
        #[arg(long)]
        context: Option<String>,

        /// Group topic
        #[arg(long)]
        topic: Option<String>,

        /// Auto-start the workflow for later service invocations
        #[arg(long, default_value_t = false)]
        auto_start_on_service_invocation: bool,

        /// Save the group and workflow configuration without an initial session or run.
        /// Requires a server that supports create_initial_session=false.
        #[arg(long)]
        no_session: bool,
    },
}



#[derive(Subcommand)]
pub(super) enum ChannelCommands {
    /// Bind a DingTalk robot to a group or bot target
    Bind {
        /// DingTalk robot account_ref
        #[arg(long)]
        account: String,

        /// Target kind: group or bot
        #[arg(long, default_value = "group")]
        target_kind: String,

        /// Target group_id or bot_id
        #[arg(long)]
        target_id: String,

        /// DingTalk group scope: conversation_shared or per_sender
        #[arg(long)]
        group_chat_scope: Option<String>,

        /// Outbound visibility: full_transcript or lead_only
        #[arg(long, default_value = "lead_only")]
        visibility: String,

        /// Runtime environment label
        #[arg(long, default_value = "dev")]
        env: String,

        /// DingTalk robotCode
        #[arg(long)]
        robot_code: String,

        /// DingTalk client id
        #[arg(long)]
        client_id: String,

        /// DingTalk client secret
        #[arg(long)]
        client_secret: String,

        /// DingTalk send mode: normal or streaming_card
        #[arg(long, default_value = "normal")]
        send_mode: String,

        /// Required when --send-mode=streaming_card
        #[arg(long)]
        card_template_id: Option<String>,

        /// Message type: markdown or text
        #[arg(long, default_value = "markdown")]
        message_type: String,
    },

    /// List channel bindings
    List,

    /// Find DingTalk conversation IDs by BCS session ID
    ConversationId {
        /// BCS session id
        #[arg(long)]
        session: String,
    },

    /// Delete a channel binding
    Unbind {
        /// Binding id
        #[arg(long)]
        id: String,
    },
}



pub(super) fn build_channel_bind_payload(command: &ChannelCommands) -> Result<serde_json::Value> {
    let ChannelCommands::Bind {
        account,
        target_kind,
        target_id,
        group_chat_scope,
        visibility,
        env,
        robot_code,
        client_id,
        client_secret,
        send_mode,
        card_template_id,
        message_type,
    } = command else {
        return Err(anyhow!("channel bind payload requires bind command"));
    };

    let target = match target_kind.as_str() {
        "group" => json!({ "group": { "group_id": target_id } }),
        "bot" => json!({ "bot": { "bot_id": target_id } }),
        other => {
            return Err(anyhow!(
                "unsupported target-kind {}; expected group or bot",
                other
            ));
        }
    };

    let send_mode = match send_mode.as_str() {
        "normal" => json!({
            "mode": "normal",
            "message_type": message_type,
        }),
        "streaming_card" => {
            let Some(card_template_id) = card_template_id.as_deref() else {
                return Err(anyhow!(
                    "--card-template-id is required when --send-mode=streaming_card"
                ));
            };
            json!({
                "mode": "streaming_card",
                "card_template_id": card_template_id,
                "fallback_message_type": message_type,
            })
        }
        other => {
            return Err(anyhow!(
                "unsupported send-mode {}; expected normal or streaming_card",
                other
            ));
        }
    };

    let mut payload = json!({
        "channel_type": "ding_talk",
        "account_ref": account,
        "target": target,
        "outbound_visibility": visibility,
        "env": env,
        "config": {
            "channel_type": "ding_talk",
            "robot_code": robot_code,
            "client_id": client_id,
            "client_secret": client_secret,
            "send_mode": send_mode,
        }
    });

    if let Some(group_chat_scope) = group_chat_scope {
        payload["group_chat_scope"] = json!(group_chat_scope);
    }

    Ok(payload)
}



pub(super) fn redact_channel_bind_debug_payload(payload: &serde_json::Value) -> serde_json::Value {
    let mut redacted = payload.clone();
    if let Some(config) = redacted
        .get_mut("config")
        .and_then(|value| value.as_object_mut())
    {
        if config.contains_key("client_secret") {
            config.insert("client_secret".to_string(), json!("<redacted>"));
        }
    }
    redacted
}
