//! command support implementation.
use super::*;


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
        #[arg(short, long)] session: String,
        #[arg(long)] path: String,
        #[arg(long)] name: Option<String>,
        #[arg(long)] mime: Option<String>,
    },
    /// List files in the session workspace.
    List {
        #[arg(short, long)] session: String,
        #[arg(long)] prefix: Option<String>,
        #[arg(long)] status: Option<String>,
        #[arg(long)] limit: Option<u32>,
        #[arg(long)] offset: Option<u32>,
    },
    /// Download a file's bytes (follows presigned redirect or streams).
    Download {
        #[arg(short, long)] session: String,
        #[arg(long)] file_id: String,
        #[arg(long)] out: Option<String>,
        #[arg(long)] ttl: Option<u64>,
    },
    /// Delete a file or cancel an in-progress upload.
    Delete {
        #[arg(short, long)] session: String,
        #[arg(long)] file_id: String,
    },
    /// Generate a no-auth share link (valid until ttl expiry).
    Share {
        #[arg(short, long)] session: String,
        #[arg(long)] file_id: String,
        #[arg(long)] ttl: Option<u64>,
    },
    /// Query backend capabilities (storage / presign / max_size).
    Capabilities {
        #[arg(short, long)] session: String,
    },
}



#[derive(Subcommand)]
pub(super) enum ServiceCommands {
    /// Kick off (or reactivate) a service-invocation session on a group.
    ///
    /// The group must have a `service_spec` set (otherwise the server
    /// returns 400). By default this command short-polls until the session
    /// reaches `status="completed"` or the timeout fires; pass `--detach`
    /// to return immediately after the 202 with the session id.
    Invoke {
        /// Target group id (must have `service_spec` configured)
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

        /// BaaS conversation session id for callback delivery
        #[arg(long)]
        baas_session_id: Option<String>,

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



/// Run the CLI.
///
/// Kept `pub` so an internal build can reuse this whole module (via `#[path]`)
/// and link an Ant-side OAuth provider — see the ocb overlay crate
/// `crates/tools/bcs-cli-internal`. The body is identical to the legacy
/// `#[tokio::main] async fn main`; only the entry is split out.
pub async fn run() -> Result<()> {
    let cli = Cli::parse();

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

    // Get current environment (defaults to "dev")
    let env = get_current_env();

    // Determine BCS URL: CLI arg > env var > session.json > compiled default > local
    // Note: bcs_url resolution is deferred for commands that don't need it (e.g., --web mode).
    let bcs_url = resolve_bcs_url(&cli).unwrap_or_default();

    // Determine cookie: CLI arg > env var
    let bcs_cookie = cli
        .cookie
        .or_else(|| std::env::var("BCS_COOKIE").ok());

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
    let (_network_env, oauth_headers): (NetworkEnv, Option<HashMap<String, String>>) = if bcs_url
        .is_empty()
    {
        (NetworkEnv::Prod, None)
    } else {
        let env = match std::env::var("tc_sdb_nenv").ok().as_deref() {
            Some("production") => NetworkEnv::Prod,
            Some(_) => NetworkEnv::Office,
            None => detect_network_env(&bcs_url, structured_mode).await,
        };
        if debug {
            eprintln!("\x1b[2m[→BCS] Network: {:?}\x1b[0m", env);
        }

        let headers = if env == NetworkEnv::Office {
            if AUTH_VIA_AGENT_PASS {
                let err_msg = if let Ok(bot_data_dir) = std::env::var("BOT_DATA_DIR") {
                    let bot_data_path = Path::new(&bot_data_dir);

                    if let Ok(Some(session)) = load_optional_session_info() {
                        if let Some(ref bot_uuid) = session.bot_uuid {
                            if !session.token.is_empty() && !bot_uuid.is_empty() {
                                let summary = agentpass::load_bot_summary(bot_data_path);

                                info!("尝试 AgentPass 注册，bot_uuid: {}", bot_uuid);

                                match agentpass::try_register_and_auth(
                                    &session.token,
                                    bot_uuid,
                                    &summary,
                                    structured_mode,
                                )
                                .await
                                {
                                    Ok(()) => {
                                        info!("AgentPass 注册流程完成");
                                        None
                                    }
                                    Err(e) => Some(format!("AgentPass 注册失败: {}", e)),
                                }
                            } else {
                                Some(
                                    "AgentPass auth failed: token or bot_uuid is empty".to_string(),
                                )
                            }
                        } else {
                            Some("AgentPass auth failed: session has no bot_uuid".to_string())
                        }
                    } else {
                        Some("AgentPass auth failed: no session info found (check BOT_DATA_DIR/.bcs/session.json)".to_string())
                    }
                } else {
                    Some("AgentPass auth failed: BOT_DATA_DIR not set".to_string())
                };

                if let Some(msg) = err_msg {
                    if structured_mode {
                        emit_structured_result(&StructuredResult {
                            status: "agentpass_auth_failed".to_string(),
                            message: Some(msg),
                            network_env: Some("office".to_string()),
                            auth_url: None,
                            timeout_secs: None,
                            log_file: Some(log_file.display().to_string()),
                        });
                        return Ok(());
                    } else {
                        return Err(anyhow!(msg));
                    }
                }

                info!("auth_via_agent_pass enabled, skipping OAuth");
                None
            } else {
                info!("Office network detected, obtaining OAuth2 authentication...");
                let (client_id, client_secret) = resolve_oauth_credentials();
                let log_file_str = log_file.display().to_string();
                let on_auth_required: Option<oauth::AuthRequiredCallback> = if structured_mode {
                    Some(Box::new(move |auth_url: &str| {
                        emit_structured_result(&StructuredResult {
                            status: "auth_required".to_string(),
                            message: Some("OAuth2 authorization required. Waiting for browser authorization...".to_string()),
                            network_env: Some("office".to_string()),
                            auth_url: Some(auth_url.to_string()),
                            timeout_secs: Some(120),
                            log_file: Some(log_file_str),
                        });
                        use std::io::Write;
                        let _ = std::io::stdout().flush();
                    }))
                } else {
                    None
                };
                match oauth::try_get_oauth_headers(client_id, client_secret, on_auth_required).await
                {
                    Ok(headers) => Some(headers),
                    Err(e) => {
                        if structured_mode {
                            emit_structured_result(&StructuredResult {
                                status: classify_auth_error_message(&e.message).to_string(),
                                message: Some(e.message),
                                network_env: Some("office".to_string()),
                                auth_url: e.auth_url,
                                timeout_secs: Some(120),
                                log_file: Some(log_file.display().to_string()),
                            });
                            return Ok(());
                        }
                        return Err(e.into());
                    }
                }
            }
        } else {
            None
        };
        (env, headers)
    };

    let context = CommandContext { bcs_url, bcs_cookie, oauth_headers, debug, structured_mode, cli_json: cli.json };
    match cli.command {
        command @ Commands::Health => commands_health::execute_health(command, context).await?,

        command @ Commands::Connect { .. } => commands_connect::execute_connect(command, context).await?,

        command @ Commands::Onboard { .. } => commands_onboard::execute_onboard(command, context).await?,

        command @ Commands::List { .. } => commands_list::execute_list(command, context).await?,

        command @ Commands::Get { .. } => commands_get::execute_get(command, context).await?,

        command @ Commands::Discover { .. } => commands_discover::execute_discover(command, context).await?,

        command @ Commands::UpdateStatus { .. } => commands_update_status::execute_update_status(command, context).await?,

        command @ Commands::RequestGroupHelp { .. } => commands_request_group_help::execute_request_group_help(command, context).await?,

        command @ Commands::ConfirmGroupHelp { .. } => commands_confirm_group_help::execute_confirm_group_help(command, context).await?,

        command @ Commands::CreateGroup { .. } => commands_create_group::execute_create_group(command, context).await?,

        command @ Commands::Collaboration { .. } => commands_collaboration::execute_collaboration(command, context).await?,

        command @ Commands::GetGroup { .. } => commands_get_group::execute_get_group(command, context).await?,

        command @ Commands::Fuse { .. } => commands_fuse::execute_fuse(command, context).await?,

        command @ Commands::ListGroups { .. } => commands_list_groups::execute_list_groups(command, context).await?,

        command @ Commands::AddMember { .. } => commands_add_member::execute_add_member(command, context).await?,

        command @ Commands::Chat { .. } => commands_chat::execute_chat(command, context).await?,

        command @ Commands::ChatRun { .. } => commands_chat_run::execute_chat_run(command, context).await?,

        command @ Commands::GroupStatus { .. } => commands_group_status::execute_group_status(command, context).await?,

        command @ Commands::TerminateGroup { .. } => commands_terminate_group::execute_terminate_group(command, context).await?,

        command @ Commands::Friend { .. } => commands_friend::execute_friend(command, context).await?,

        command @ Commands::Channel { .. } => commands_channel::execute_channel(command, context).await?,

        command @ Commands::Visibility { .. } => commands_visibility::execute_visibility(command, context).await?,

        command @ Commands::Session { .. } => commands_session::execute_session(command, context).await?,

        command @ Commands::Service { .. } => commands_service::execute_service(command, context).await?,
    }

    Ok(())
}




#[derive(Debug, Subcommand)]
pub(super) enum ChatRunCommands {
    /// Read the run and its queue status.
    Status { #[arg(long)] run_id: String },
    /// Request cancellation; a downstream abort may remain unconfirmed.
    Cancel { #[arg(long)] run_id: String },
}



    /// Create a BCS client with optional cookie and OAuth headers.
    /// An empty `token` means no bot token is available; the client is built
    /// without Bearer auth (cookie / OAuth headers, if any, still apply).
pub(super) fn create_client(
        bcs_url: &str,
        token: &str,
        cookie: Option<&str>,
        oauth_headers: Option<&HashMap<String, String>>,
    ) -> BcsClient {
        let mut client = if let Some(oauth_headers) = oauth_headers {
            let mut client = if token.is_empty() {
                let mut c = BcsClient::new(bcs_url);
                c.set_oauth_headers(oauth_headers.clone());
                c
            } else {
                BcsClient::with_token_and_oauth(bcs_url, token, oauth_headers.clone())
            };
            if let Some(cookie) = cookie {
                client.set_cookie(cookie);
            }
            client
        } else if token.is_empty() {
            let mut client = BcsClient::new(bcs_url);
            if let Some(cookie) = cookie {
                client.set_cookie(cookie);
            }
            client
        } else if let Some(cookie) = cookie {
            BcsClient::with_token_and_cookie(bcs_url, token, cookie)
        } else {
            BcsClient::with_token(bcs_url, token)
        };
        client.set_client_identity(format!("bcs-cli/{}", env!("CARGO_PKG_VERSION")));
        if let Ok(traceparent) = std::env::var("TRACEPARENT") {
            if let Err(error) = client.set_traceparent(traceparent.trim()) {
                warn!(error = %error, "Ignoring invalid TRACEPARENT from tool environment");
            }
        }
        client
    }




pub(super) struct CommandContext {
    pub(super) bcs_url: String,
    pub(super) bcs_cookie: Option<String>,
    pub(super) oauth_headers: Option<HashMap<String, String>>,
    pub(super) debug: bool,
    pub(super) structured_mode: bool,
    pub(super) cli_json: bool,
}
