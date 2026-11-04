use clap::Subcommand;

#[derive(Debug, Subcommand)]
pub(super) enum ChatRunCommands {
    /// Read the run and its queue status.
    Status {
        #[arg(long)]
        run_id: String,
    },
    /// Request cancellation; a downstream abort may remain unconfirmed.
    Cancel {
        #[arg(long)]
        run_id: String,
    },
}

#[derive(Subcommand)]
pub(super) enum GroupContextCommands {
    /// Add a new active context.
    Add {
        #[arg(long)]
        group_id: Option<String>,
        #[arg(long)]
        session_id: Option<String>,
        #[arg(long)]
        round_id: Option<String>,
        #[arg(long)]
        name: String,
        #[arg(long)]
        scope_level: String,
        #[arg(long)]
        content: String,
        #[arg(long, default_value = r#"{"userIds":[]}"#)]
        visible_to: String,
        #[arg(long)]
        collect_from: Option<String>,
        #[arg(long)]
        valid_from: Option<String>,
        #[arg(long)]
        valid_to: Option<String>,
    },
    /// Supersede the active context version.
    Update {
        #[arg(long)]
        group_id: Option<String>,
        #[arg(long)]
        session_id: Option<String>,
        #[arg(long)]
        round_id: Option<String>,
        #[arg(long)]
        name: String,
        #[arg(long)]
        scope_level: String,
        #[arg(long)]
        content: Option<String>,
        #[arg(long)]
        visible_to: Option<String>,
        #[arg(long)]
        collect_from: Option<String>,
        /// Owner actor ids transferred to the new active version.
        #[arg(long)]
        owners: Option<String>,
        #[arg(long)]
        valid_from: Option<String>,
        #[arg(long)]
        valid_to: Option<String>,
        #[arg(long)]
        change_reason: String,
    },
    /// List visible or writable active contexts.
    List {
        #[arg(long)]
        group_id: Option<String>,
        #[arg(long)]
        session_id: Option<String>,
        #[arg(long)]
        round_id: Option<String>,
        /// Scope level filter. Repeat to pass multiple levels.
        #[arg(long = "scope-level")]
        scope_level: Vec<String>,
        /// Scope level filters as a comma-separated list, e.g. group,session.
        #[arg(long = "scope-levels", value_delimiter = ',')]
        scope_levels: Vec<String>,
    },
    /// Get visible active contexts ordered round/session/group.
    Get {
        #[arg(long)]
        group_id: Option<String>,
        #[arg(long)]
        session_id: Option<String>,
        #[arg(long)]
        round_id: Option<String>,
        #[arg(long)]
        name: String,
        /// Scope level filter. Repeat to pass multiple levels.
        #[arg(long = "scope-level")]
        scope_level: Vec<String>,
        /// Scope level filters as a comma-separated list, e.g. group,session.
        #[arg(long = "scope-levels", value_delimiter = ',')]
        scope_levels: Vec<String>,
        #[arg(long, default_value_t = 3)]
        limit: u32,
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

#[derive(Subcommand)]
pub(super) enum FriendCommands {
    /// Send a friend request to another bot
    Request {
        /// Target bot UUID to send friend request to
        #[arg(long)]
        bot_uuid: String,
    },

    /// Accept a friend request
    Accept {
        /// Friend request ID to accept
        #[arg(long)]
        request_id: String,
    },

    /// Reject a friend request
    Reject {
        /// Friend request ID to reject
        #[arg(long)]
        request_id: String,
    },

    /// List friends of the current bot (auto-resolves from session if not specified)
    List {
        /// Bot UUID (optional, auto-resolved from session if not provided)
        #[arg(long)]
        bot_uuid: Option<String>,
    },

    /// List friend requests (received, sent, or all)
    Requests {
        /// Direction: received (default), sent, all
        #[arg(short, long, default_value = "received")]
        direction: String,

        /// Filter by status: pending, accepted, rejected
        #[arg(short, long)]
        status: Option<String>,
    },
}

#[derive(Subcommand)]
pub(super) enum VisibilityCommands {
    /// Get current bot's visibility (auto-resolves from session if not specified)
    Get {
        /// Bot UUID (optional, auto-resolved from session if not provided)
        #[arg(long)]
        bot_uuid: Option<String>,
    },

    /// Set current bot's visibility (auto-resolves from session if not specified)
    Set {
        /// Visibility value (public, protected, or private)
        #[arg(long)]
        value: String,

        /// Bot UUID (optional, auto-resolved from session if not provided)
        #[arg(long)]
        bot_uuid: Option<String>,
    },
}

#[derive(Subcommand)]
pub(super) enum ServiceCommands {
    /// Kick off (or reactivate) a service-invocation session on a group.
    ///
    /// reaches `status="completed"` or the timeout fires; pass `--detach`
    /// to return immediately after the 202 with the session id.
    Invoke {
        /// Target group id
        #[arg(short = 'g', long)]
        group: String,

        /// Input payload as a JSON literal or `@path/to/file.json`
        #[arg(long)]
        input: Option<String>,

        /// Metadata as a JSON literal or `@path/to/file.json`
        #[arg(long)]
        meta: Option<String>,

        /// Reactivate an existing session id instead of allocating a new one
        #[arg(long)]
        session_id: Option<String>,

        /// Opaque caller-supplied id (recorded on the session row)
        #[arg(long)]
        caller_id: Option<String>,

        /// Session title (shown in UI)
        #[arg(long)]
        title: Option<String>,

        /// Return after the 202 instead of polling for completion
        #[arg(long, default_value_t = false)]
        detach: bool,

        /// Overall wait budget in milliseconds when blocking (default 30 min)
        #[arg(long, value_parser = clap::value_parser!(u64).range(1..=86_400_000))]
        timeout_ms: Option<u64>,
    },

    /// Single-shot poll for a service-invocation session.
    Status {
        /// Session id of the form `{group}:{8_hex}`
        sid: String,

        /// Override the group id (defaults to the prefix of `sid` before ':')
        #[arg(long)]
        group: Option<String>,
    },

    /// Block until a service-invocation session completes (or times out).
    Wait {
        /// Session id of the form `{group}:{8_hex}`
        sid: String,

        /// Override the group id (defaults to the prefix of `sid` before ':')
        #[arg(long)]
        group: Option<String>,

        /// Overall wait budget in milliseconds (default 30 min)
        #[arg(long, value_parser = clap::value_parser!(u64).range(1..=86_400_000))]
        timeout_ms: Option<u64>,
    },
}
