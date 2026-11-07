use std::path::PathBuf;

use clap::Subcommand;

#[derive(Subcommand)]
pub(super) enum CollaborationCommands {
    /// Store and share immutable collaboration-definition revisions
    Draft {
        #[command(subcommand)]
        command: bcs_cli::collaboration_draft::DraftCommands,
    },
    /// Build a read-only collaboration-definition preview message without network access
    Preview {
        /// UTF-8 YAML file to include verbatim in the snapshot
        file: PathBuf,

        /// Optional display title (1..=120 Unicode code points)
        #[arg(long)]
        title: Option<String>,

        /// Existing document UUID for a later snapshot, or an explicit first-version UUID
        #[arg(long)]
        document_id: Option<String>,

        /// Direct parent snapshot UUID; requires --document-id
        #[arg(long)]
        parent_snapshot_id: Option<String>,

        /// Explicit snapshot UUID for replaying the same constructed snapshot
        #[arg(long)]
        snapshot_id: Option<String>,
    },

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
