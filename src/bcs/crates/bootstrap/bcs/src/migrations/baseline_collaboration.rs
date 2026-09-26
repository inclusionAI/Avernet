//! Collaboration DDL (groups / sessions / state machines / collaboration definitions).
//!
//! Each section is one `&[&str]` group that the migration runner
//! iterates in the order given by the facade's master ordered list
//! — order matters because some sections depend on earlier ones.

pub(super) const GROUPS: &[&str] = &[
    // ── groups ────────────────────────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_groups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        group_id TEXT NOT NULL,
        label TEXT DEFAULT NULL,
        status TEXT NOT NULL,
        driver_bot TEXT NOT NULL,
        originator TEXT DEFAULT NULL,
        env TEXT NOT NULL,
        routing_policy_json TEXT DEFAULT NULL,
        context TEXT DEFAULT NULL,
        opening_message_json TEXT DEFAULT NULL,
        group_kind TEXT NOT NULL DEFAULT 'normal',
        dm_pair_key TEXT DEFAULT NULL,
        service_group_uuid TEXT DEFAULT NULL,
        service_mode TEXT DEFAULT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        record_status TEXT NOT NULL DEFAULT 'active',
        lifecycle_status TEXT NOT NULL DEFAULT 'active',
        group_strategy TEXT NOT NULL DEFAULT 'chat',
        participants TEXT DEFAULT NULL,
        service_spec TEXT DEFAULT NULL,
        created_by TEXT DEFAULT NULL,
        visibility TEXT NOT NULL DEFAULT 'private'
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_groups_group_env ON bcs_groups(group_id, env)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_groups_dm_pair ON bcs_groups(env, dm_pair_key)",
    "CREATE INDEX IF NOT EXISTS idx_groups_driver ON bcs_groups(driver_bot)",
    "CREATE INDEX IF NOT EXISTS idx_groups_service_uuid ON bcs_groups(service_group_uuid)",
    "CREATE INDEX IF NOT EXISTS idx_groups_label ON bcs_groups(label)",
    "CREATE INDEX IF NOT EXISTS idx_groups_visibility ON bcs_groups(visibility)",
];

pub(super) const CHAT_RUNS: &[&str] = &[
    // ── chat_runs (Direct Chat async governance, #1546) ─────
    "CREATE TABLE IF NOT EXISTS bcs_chat_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        env TEXT NOT NULL,
        run_id TEXT NOT NULL,
        bot_uuid TEXT NOT NULL,
        from_bot_id TEXT NOT NULL,
        session_key TEXT NOT NULL,
        state TEXT NOT NULL,
        accumulated_content TEXT,
        error_message TEXT,
        original_request TEXT,
        completed_at_ms INTEGER,
        expires_at_ms INTEGER NOT NULL,
        version INTEGER NOT NULL,
        content_truncated INTEGER NOT NULL DEFAULT 0,
        client TEXT,
        response_mode TEXT NOT NULL,
        completion_policy TEXT NOT NULL,
        delivery_ack_at_ms INTEGER,
        CONSTRAINT uk_env_run_id UNIQUE (env, run_id)
    )",
    "CREATE INDEX IF NOT EXISTS idx_env_expires ON bcs_chat_runs(env, state, expires_at_ms)",
    "CREATE INDEX IF NOT EXISTS idx_env_completed ON bcs_chat_runs(env, state, completed_at_ms)",
    "CREATE INDEX IF NOT EXISTS idx_env_from_bot ON bcs_chat_runs(env, from_bot_id)",
    "CREATE INDEX IF NOT EXISTS idx_env_bot ON bcs_chat_runs(env, bot_uuid)",
];

pub(super) const GROUP_PARTICIPANTS: &[&str] = &[
    // ── group_participants ────────────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_group_participants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        group_id TEXT NOT NULL,
        bot_uuid TEXT NOT NULL,
        role TEXT NOT NULL,
        env TEXT NOT NULL,
        actor_kind TEXT NOT NULL DEFAULT 'bot',
        mode TEXT NOT NULL DEFAULT 'auto',
        tags_json TEXT DEFAULT NULL,
        message_view_scope TEXT NOT NULL DEFAULT 'full'
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_participants_env_group_bot ON bcs_group_participants(env, group_id, bot_uuid)",
    "CREATE INDEX IF NOT EXISTS idx_participants_bot ON bcs_group_participants(bot_uuid)",
    "CREATE INDEX IF NOT EXISTS idx_participants_group ON bcs_group_participants(group_id)",
];

pub(super) const GROUP_SESSIONS: &[&str] = &[
    // ── group_sessions ────────────────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_group_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        session_id TEXT NOT NULL,
        group_id TEXT NOT NULL,
        env TEXT NOT NULL DEFAULT 'prod',
        status TEXT NOT NULL DEFAULT 'running',
        session_kind TEXT NOT NULL DEFAULT 'chat',
        message_visibility_version INTEGER NOT NULL DEFAULT 0,
        session_title TEXT DEFAULT NULL,
        group_version INTEGER DEFAULT NULL,
        caller_id TEXT DEFAULT NULL,
        input TEXT DEFAULT NULL,
        output TEXT DEFAULT NULL,
        error_message TEXT DEFAULT NULL,
        callback_status TEXT DEFAULT NULL,
        callback_lease_owner TEXT DEFAULT NULL,
        callback_lease_token INTEGER DEFAULT NULL,
        callback_lease_until_ms INTEGER DEFAULT NULL,
        activation_count INTEGER NOT NULL DEFAULT 1,
        caller_principal TEXT DEFAULT NULL,
        created_by TEXT DEFAULT NULL,
        participants TEXT NOT NULL,
        completed_at INTEGER DEFAULT NULL,
        meta TEXT DEFAULT NULL,
        current_msg_seq INTEGER NOT NULL DEFAULT 0,
        participant_join_seq TEXT DEFAULT NULL
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_sessions_id ON bcs_group_sessions(env, session_id)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_group_status ON bcs_group_sessions(env, group_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_group_kind_status ON bcs_group_sessions(env, group_id, session_kind, status)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_caller_principal ON bcs_group_sessions(env, caller_principal)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_callback_status ON bcs_group_sessions(env, callback_status)",
    "CREATE INDEX IF NOT EXISTS idx_session_callback_recovery ON bcs_group_sessions(env, session_kind, status, callback_status, callback_lease_token, callback_lease_until_ms, session_id)",
];

pub(super) const SESSION_PARTICIPANTS: &[&str] = &[
    // ── session_participants ──────────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_session_participants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        session_id TEXT NOT NULL,
        group_id TEXT NOT NULL,
        bot_uuid TEXT NOT NULL,
        role TEXT NOT NULL,
        env TEXT NOT NULL DEFAULT 'prod',
        collected INTEGER NOT NULL DEFAULT 0,
        collected_at TEXT
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_session_participants_env_session_bot ON bcs_session_participants(env, session_id, bot_uuid)",
    "CREATE INDEX IF NOT EXISTS idx_session_participants_bot ON bcs_session_participants(env, bot_uuid)",
    "CREATE INDEX IF NOT EXISTS idx_session_participants_session ON bcs_session_participants(env, session_id)",
];

pub(super) const COLLABORATION_DEFINITIONS: &[&str] = &[
    // ── collaboration_definitions ─────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_collaboration_definitions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        env TEXT NOT NULL,
        definition_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        name TEXT NOT NULL,
        description TEXT DEFAULT NULL,
        source_format TEXT NOT NULL DEFAULT 'yaml',
        content_hash TEXT NOT NULL,
        blob_id TEXT DEFAULT NULL,
        yaml_text TEXT DEFAULT NULL,
        normalized_json TEXT DEFAULT NULL,
        metadata_json TEXT DEFAULT NULL,
        record_status TEXT NOT NULL DEFAULT 'active',
        created_by TEXT DEFAULT NULL
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_collab_def_version ON bcs_collaboration_definitions(env, definition_id, version)",
    "CREATE INDEX IF NOT EXISTS idx_collab_def_hash ON bcs_collaboration_definitions(env, content_hash)",
    "CREATE INDEX IF NOT EXISTS idx_collab_def_blob ON bcs_collaboration_definitions(env, blob_id)",
    "CREATE INDEX IF NOT EXISTS idx_collab_def_status ON bcs_collaboration_definitions(env, record_status)",
];

pub(super) const COLLABORATION_DEFINITION_BLOBS: &[&str] = &[
    // ── collaboration_definition_blobs ────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_collaboration_definition_blobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        env TEXT NOT NULL,
        blob_id TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        content_encoding TEXT NOT NULL DEFAULT 'identity',
        content_size INTEGER NOT NULL,
        content BLOB DEFAULT NULL,
        external_uri TEXT DEFAULT NULL,
        created_by TEXT DEFAULT NULL
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_collab_blob_id ON bcs_collaboration_definition_blobs(env, blob_id)",
    "CREATE INDEX IF NOT EXISTS idx_collab_blob_hash ON bcs_collaboration_definition_blobs(env, content_hash)",
];

pub(super) const COLLABORATION_EVENTS: &[&str] = &[
    // ── collaboration_events ──────────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_collaboration_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        env TEXT NOT NULL,
        state_machine_run_id TEXT NOT NULL,
        node_id TEXT DEFAULT NULL,
        attempt INTEGER DEFAULT NULL,
        event_type TEXT NOT NULL,
        payload_json TEXT DEFAULT NULL,
        created_at_ms INTEGER NOT NULL,
        record_status TEXT NOT NULL DEFAULT 'active'
    )",
    "CREATE INDEX IF NOT EXISTS idx_collab_events_run ON bcs_collaboration_events(env, state_machine_run_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_collab_events_run_node ON bcs_collaboration_events(env, state_machine_run_id, node_id, attempt, id)",
    "CREATE INDEX IF NOT EXISTS idx_collab_events_type_time ON bcs_collaboration_events(env, event_type, created_at_ms)",
    "CREATE INDEX IF NOT EXISTS idx_collab_events_record_status ON bcs_collaboration_events(env, record_status)",
];

pub(super) const COLLABORATION_TEMPLATES: &[&str] = &[
    // ── collaboration_templates ───────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_collaboration_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        env TEXT NOT NULL,
        template_id TEXT NOT NULL,
        source_type TEXT NOT NULL DEFAULT 'system',
        visibility TEXT NOT NULL DEFAULT 'public',
        owner_user_id TEXT DEFAULT NULL,
        priority INTEGER NOT NULL DEFAULT 4294967295,
        record_status TEXT NOT NULL DEFAULT 'active',
        created_by TEXT DEFAULT NULL,
        updated_by TEXT DEFAULT NULL
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_bct_template ON bcs_collaboration_templates(env, template_id)",
    "CREATE INDEX IF NOT EXISTS idx_bct_env_status_priority ON bcs_collaboration_templates(env, record_status, priority, template_id)",
    "CREATE INDEX IF NOT EXISTS idx_bct_env_visibility_priority ON bcs_collaboration_templates(env, visibility, record_status, priority, template_id)",
    "CREATE INDEX IF NOT EXISTS idx_bct_env_owner_status ON bcs_collaboration_templates(env, owner_user_id, record_status, priority, template_id)",
];

pub(super) const COLLABORATION_TEMPLATE_CONTENTS: &[&str] = &[
    // ── collaboration_template_contents ───────────────────
    "CREATE TABLE IF NOT EXISTS bcs_collaboration_template_contents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        env TEXT NOT NULL,
        template_id TEXT NOT NULL,
        lang TEXT NOT NULL,
        name TEXT NOT NULL,
        description TEXT DEFAULT NULL,
        participant_summary_json TEXT NOT NULL,
        definition_json TEXT NOT NULL,
        yaml_text TEXT NOT NULL,
        yaml_sha256 TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        record_status TEXT NOT NULL DEFAULT 'active'
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_bctc_template_lang ON bcs_collaboration_template_contents(env, template_id, lang)",
    "CREATE INDEX IF NOT EXISTS idx_bctc_env_lang_status ON bcs_collaboration_template_contents(env, lang, record_status, template_id)",
    "CREATE INDEX IF NOT EXISTS idx_bctc_env_hash ON bcs_collaboration_template_contents(env, yaml_sha256)",
];

pub(super) const COLLABORATION_TEMPLATE_TAGS: &[&str] = &[
    // ── collaboration_template_tags ───────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_collaboration_template_tags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        env TEXT NOT NULL,
        template_id TEXT NOT NULL,
        tag TEXT NOT NULL
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_bctt_template_tag ON bcs_collaboration_template_tags(env, template_id, tag)",
    "CREATE INDEX IF NOT EXISTS idx_bctt_env_tag ON bcs_collaboration_template_tags(env, tag, template_id)",
];

pub(super) const STATE_MACHINE_RUNS: &[&str] = &[
    // ── state_machine_runs ────────────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_state_machine_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        env TEXT NOT NULL,
        run_id TEXT NOT NULL,
        root_run_id TEXT DEFAULT NULL,
        rerun_of TEXT DEFAULT NULL,
        definition_id TEXT NOT NULL,
        definition_version INTEGER NOT NULL,
        group_id TEXT NOT NULL,
        group_version INTEGER NOT NULL,
        session_id TEXT NOT NULL,
        session_activation_count INTEGER DEFAULT NULL,
        created_by TEXT DEFAULT NULL,
        status TEXT NOT NULL,
        input_json TEXT DEFAULT NULL,
        opening_message_override_json TEXT DEFAULT NULL,
        output_text TEXT DEFAULT NULL,
        error_message TEXT DEFAULT NULL,
        created_at_ms INTEGER NOT NULL,
        updated_at_ms INTEGER NOT NULL,
        completed_at_ms INTEGER DEFAULT NULL,
        record_status TEXT NOT NULL DEFAULT 'active'
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_sm_run_id ON bcs_state_machine_runs(env, run_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_sm_run_rerun_of ON bcs_state_machine_runs(env, rerun_of)",
    "CREATE INDEX IF NOT EXISTS idx_sm_runs_root ON bcs_state_machine_runs(env, root_run_id, created_at_ms)",
    "CREATE INDEX IF NOT EXISTS idx_sm_runs_session ON bcs_state_machine_runs(env, session_id)",
    "CREATE INDEX IF NOT EXISTS idx_sm_runs_created_by ON bcs_state_machine_runs(env, created_by, created_at_ms)",
    "CREATE INDEX IF NOT EXISTS idx_sm_runs_group_status ON bcs_state_machine_runs(env, group_id, status, created_at_ms)",
    "CREATE INDEX IF NOT EXISTS idx_sm_runs_status_updated ON bcs_state_machine_runs(env, status, updated_at_ms)",
    "CREATE INDEX IF NOT EXISTS idx_sm_runs_definition ON bcs_state_machine_runs(env, definition_id, definition_version)",
    "CREATE INDEX IF NOT EXISTS idx_sm_runs_record_status ON bcs_state_machine_runs(env, record_status)",
];

pub(super) const STATE_MACHINE_NODE_RUNS: &[&str] = &[
    // ── state_machine_node_runs ───────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_state_machine_node_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        env TEXT NOT NULL,
        run_id TEXT NOT NULL,
        node_id TEXT NOT NULL,
        status TEXT NOT NULL,
        attempt INTEGER NOT NULL DEFAULT 0,
        node_timeout_ms INTEGER DEFAULT NULL,
        timeout_deadline_ms INTEGER DEFAULT NULL,
        max_attempts INTEGER NOT NULL DEFAULT 1,
        assignee_bot_id TEXT NOT NULL,
        outcome TEXT DEFAULT NULL,
        responded_by TEXT DEFAULT NULL,
        delivery_request_id TEXT DEFAULT NULL,
        bot_delivery_run_id TEXT DEFAULT NULL,
        artifact_text TEXT DEFAULT NULL,
        error_message TEXT DEFAULT NULL,
        started_at_ms INTEGER DEFAULT NULL,
        completed_at_ms INTEGER DEFAULT NULL,
        record_status TEXT NOT NULL DEFAULT 'active'
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_sm_node_run ON bcs_state_machine_node_runs(env, run_id, node_id)",
    "CREATE INDEX IF NOT EXISTS idx_sm_nodes_run_status ON bcs_state_machine_node_runs(env, run_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_sm_nodes_status_started ON bcs_state_machine_node_runs(env, status, started_at_ms)",
    "CREATE INDEX IF NOT EXISTS idx_sm_nodes_timeout_deadline ON bcs_state_machine_node_runs(env, status, timeout_deadline_ms)",
    "CREATE INDEX IF NOT EXISTS idx_sm_nodes_assignee_status ON bcs_state_machine_node_runs(env, assignee_bot_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_sm_nodes_delivery_request ON bcs_state_machine_node_runs(env, delivery_request_id)",
    "CREATE INDEX IF NOT EXISTS idx_sm_nodes_bot_delivery_run ON bcs_state_machine_node_runs(env, bot_delivery_run_id)",
    "CREATE INDEX IF NOT EXISTS idx_sm_nodes_record_status ON bcs_state_machine_node_runs(env, record_status)",
];

pub(super) const STATE_MACHINE_DELIVERY_CORRELATIONS: &[&str] = &[
    // ── state_machine_delivery_correlations ───────────────
    "CREATE TABLE IF NOT EXISTS bcs_state_machine_delivery_correlations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        env TEXT NOT NULL,
        state_machine_run_id TEXT NOT NULL,
        node_id TEXT NOT NULL,
        attempt INTEGER NOT NULL,
        assignee_bot_id TEXT NOT NULL,
        delivery_request_id TEXT NOT NULL,
        bot_delivery_run_id TEXT DEFAULT NULL,
        created_at_ms INTEGER NOT NULL,
        updated_at_ms INTEGER NOT NULL,
        record_status TEXT NOT NULL DEFAULT 'active'
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_sm_corr_delivery_request ON bcs_state_machine_delivery_correlations(env, delivery_request_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_sm_corr_bot_delivery_run ON bcs_state_machine_delivery_correlations(env, bot_delivery_run_id)",
    "CREATE INDEX IF NOT EXISTS idx_sm_corr_run_node_attempt ON bcs_state_machine_delivery_correlations(env, state_machine_run_id, node_id, attempt)",
    "CREATE INDEX IF NOT EXISTS idx_sm_corr_assignee ON bcs_state_machine_delivery_correlations(env, assignee_bot_id, created_at_ms)",
    "CREATE INDEX IF NOT EXISTS idx_sm_corr_record_status ON bcs_state_machine_delivery_correlations(env, record_status)",
];

pub(super) const STATE_MACHINE_DEFINITION_SNAPSHOTS: &[&str] = &[
    // ── state_machine_definition_snapshots ────────────────
    "CREATE TABLE IF NOT EXISTS bcs_state_machine_definition_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        env TEXT NOT NULL,
        run_id TEXT NOT NULL,
        group_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        group_version INTEGER NOT NULL,
        definition_id TEXT NOT NULL,
        definition_version INTEGER NOT NULL,
        definition_content_hash TEXT NOT NULL,
        snapshot_blob_id TEXT DEFAULT NULL,
        snapshot_json TEXT DEFAULT NULL,
        source_format TEXT NOT NULL DEFAULT 'yaml',
        resolved_participant_bindings_json TEXT DEFAULT NULL
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_def_snapshot_run ON bcs_state_machine_definition_snapshots(env, run_id)",
    "CREATE INDEX IF NOT EXISTS idx_def_snapshot_group_version ON bcs_state_machine_definition_snapshots(env, group_id, group_version)",
    "CREATE INDEX IF NOT EXISTS idx_def_snapshot_definition ON bcs_state_machine_definition_snapshots(env, definition_id, definition_version)",
];

pub(super) const GROUP_RUNTIME_BINDINGS: &[&str] = &[
    // ── group_runtime_bindings ────────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_group_runtime_bindings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        env TEXT NOT NULL,
        group_id TEXT NOT NULL,
        group_version INTEGER NOT NULL,
        next_group_version INTEGER NOT NULL DEFAULT 2147483647,
        default_definition_id TEXT DEFAULT NULL,
        default_definition_version INTEGER DEFAULT NULL,
        definition_content_hash TEXT DEFAULT NULL,
        definition_blob_id TEXT DEFAULT NULL,
        auto_start_on_service_invocation INTEGER NOT NULL DEFAULT 0,
        record_status TEXT NOT NULL DEFAULT 'active',
        updated_by TEXT DEFAULT NULL,
        participant_bindings_json TEXT DEFAULT NULL
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_group_binding_version ON bcs_group_runtime_bindings(env, group_id, group_version)",
    "CREATE INDEX IF NOT EXISTS idx_group_binding_current ON bcs_group_runtime_bindings(env, group_id, record_status, next_group_version)",
    "CREATE INDEX IF NOT EXISTS idx_group_binding_effective ON bcs_group_runtime_bindings(env, group_id, record_status, group_version, next_group_version)",
    "CREATE INDEX IF NOT EXISTS idx_group_binding_definition ON bcs_group_runtime_bindings(env, default_definition_id, default_definition_version)",
];

pub(super) const SERVICE_GROUP_TEMPLATES: &[&str] = &[
    // ── service_group_templates ───────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_service_group_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        uuid TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        publish_status TEXT NOT NULL DEFAULT 'draft',
        name TEXT NOT NULL,
        description TEXT DEFAULT NULL,
        participants TEXT NOT NULL,
        service_mode TEXT NOT NULL,
        mode_config TEXT DEFAULT NULL,
        callback_config TEXT DEFAULT NULL,
        max_concurrency INTEGER NOT NULL DEFAULT -1,
        created_by TEXT NOT NULL,
        modified_by TEXT NOT NULL,
        env TEXT NOT NULL
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_sgt_uuid_version ON bcs_service_group_templates(uuid, version)",
    "CREATE INDEX IF NOT EXISTS idx_sgt_uuid ON bcs_service_group_templates(uuid)",
    "CREATE INDEX IF NOT EXISTS idx_sgt_created_by ON bcs_service_group_templates(created_by)",
];

pub(super) const SERVICE_GROUP_INSTANCES: &[&str] = &[
    // ── service_group_instances ───────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_service_group_instances (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        group_id TEXT NOT NULL,
        service_group_uuid TEXT NOT NULL,
        service_group_version INTEGER NOT NULL,
        instance_meta TEXT DEFAULT NULL,
        callback_status TEXT DEFAULT NULL,
        reactivation_log TEXT DEFAULT NULL,
        instance_result TEXT DEFAULT NULL
    )",
    "CREATE INDEX IF NOT EXISTS idx_sgi_group_id ON bcs_service_group_instances(group_id)",
    "CREATE INDEX IF NOT EXISTS idx_sgi_service_group_uuid ON bcs_service_group_instances(service_group_uuid)",
    "CREATE INDEX IF NOT EXISTS idx_sgi_callback_status ON bcs_service_group_instances(callback_status)",
];
