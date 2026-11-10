use clap::Subcommand;

#[derive(Subcommand)]
pub(super) enum SessionCommands {
    /// Create a new session under a group.
    ///
    /// The server assigns a random session id of the form `{group_id}:{8_hex}`.
    /// Listing sessions never creates one. Existing legacy session ids
    /// (`{group_id}:00000000`) remain supported.
    Create {
        /// Group ID this session belongs to
        #[arg(long)]
        group: String,

        /// Optional session title (shown in UI)
        #[arg(long)]
        title: Option<String>,

        /// Session kind: chat (default) or service_invocation
        #[arg(long)]
        kind: Option<String>,

        /// Optional input payload (JSON)
        #[arg(long)]
        input: Option<String>,

        /// Optional metadata (JSON)
        #[arg(long)]
        meta: Option<String>,

        /// Initial GroupContext delivery for the driver (send or inject)
        #[arg(long, value_parser = ["send", "inject"])]
        group_context_delivery: Option<String>,
    },

    /// List sessions under a group.
    List {
        /// Group ID
        #[arg(long)]
        group: String,

        /// Filter by status (running or completed)
        #[arg(long)]
        status: Option<String>,

        /// Search query — substring match against session title
        #[arg(short, long)]
        q: Option<String>,

        /// Filter by participant (bot_uuid or human actor_id)
        #[arg(long)]
        participant: Option<String>,

        /// Pagination offset
        #[arg(long)]
        offset: Option<u64>,

        /// Pagination limit
        #[arg(long)]
        limit: Option<u64>,
    },

    /// Get a single session by id.
    Get {
        /// Session ID (format: {group_id}:{8_hex})
        session: String,
    },

    /// Send a chat message into a session.
    /// Caller is resolved from the bearer token.
    Chat {
        /// Session ID
        #[arg(short, long)]
        session: String,

        /// Message text
        #[arg(short, long)]
        message: String,
    },

    /// Fetch message history for a session.
    Messages {
        /// Session ID
        session: String,

        /// View as a specific bot (filters visibility per participant mode)
        #[arg(long)]
        view_bot: Option<String>,

        /// Limit the number of messages returned
        #[arg(long)]
        limit: Option<u64>,

        /// Return messages with timestamp strictly less than this (Unix ms)
        #[arg(long)]
        before: Option<u64>,
    },

    /// Update session title.
    Patch {
        /// Session ID (format: {group_id}:{8_hex})
        session: String,

        /// New session title
        #[arg(long)]
        title: String,
    },

    /// Complete a running chat session (driver-only).
    ///
    /// ServiceInvocation sessions are rejected; use `service` commands instead.
    Complete {
        /// Session ID (format: {group_id}:{8_hex})
        session: String,

        /// Output payload (JSON literal or @path/to/file.json)
        #[arg(long)]
        output: Option<String>,

        /// Error message (marks the session as failed)
        #[arg(long)]
        error: Option<String>,
    },

    /// Add a bot participant to a session.
    AddMember {
        /// Session ID (format: {group_id}:{8_hex})
        session: String,

        /// Bot UUID to add
        #[arg(long)]
        bot_uuid: String,

        /// Role: driver, consultant, observer, manager, worker
        #[arg(short, long)]
        role: Option<String>,
    },

    /// Remove a participant from a session.
    RemoveMember {
        /// Session ID (format: {group_id}:{8_hex})
        session: String,

        /// Bot UUID (or human actor_id) to remove
        bot_uuid: String,
    },

    /// Update a participant's mode in a session.
    ///
    /// Valid modes: auto, muted, present, absent.
    /// For human actors not yet in the session, the server auto-adds them
    /// as Observer before applying the mode.
    SetMemberMode {
        /// Session ID (format: {group_id}:{8_hex})
        session: String,

        /// Bot UUID (or human actor_id) to update
        bot_uuid: String,

        /// New mode: auto, muted, present, absent
        #[arg(long)]
        mode: String,
    },

    /// Create an invite link for a session.
    ///
    /// Returns a short-lived token that allows human users to join the session.
    InviteLink {
        /// Session ID (format: {group_id}:{8_hex})
        session: String,

        /// Link expiration time in seconds
        #[arg(long, value_name = "TTL")]
        ttl_seconds: Option<u64>,
    },

    /// Manage the session shared file workspace (upload/download/share/list/delete).
    File {
        #[command(subcommand)]
        command: SessionFileCommands,
    },
}

#[derive(Subcommand)]
pub(super) enum SessionFileCommands {
    /// Upload a local file (auto three-stage: prepare -> PUT -> complete).
    Upload {
        #[arg(short, long)]
        session: String,
        #[arg(long)]
        path: String,
        #[arg(long)]
        name: Option<String>,
        #[arg(long)]
        mime: Option<String>,
    },
    /// List files in the session workspace.
    List {
        #[arg(short, long)]
        session: String,
        #[arg(long)]
        prefix: Option<String>,
        #[arg(long)]
        status: Option<String>,
        #[arg(long)]
        limit: Option<u32>,
        #[arg(long)]
        offset: Option<u32>,
    },
    /// Download a file's bytes (follows presigned redirect or streams).
    Download {
        #[arg(short, long)]
        session: String,
        #[arg(long)]
        file_id: String,
        #[arg(long)]
        out: Option<String>,
        #[arg(long)]
        ttl: Option<u64>,
    },
    /// Delete a file or cancel an in-progress upload.
    Delete {
        #[arg(short, long)]
        session: String,
        #[arg(long)]
        file_id: String,
    },
    /// Generate a no-auth share link (valid until ttl expiry).
    Share {
        #[arg(short, long)]
        session: String,
        #[arg(long)]
        file_id: String,
        #[arg(long)]
        ttl: Option<u64>,
    },
    /// Query backend capabilities (storage / presign / max_size).
    Capabilities {
        #[arg(short, long)]
        session: String,
    },
}
