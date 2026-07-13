//! SQLite schema initialization and local upgrade runner.
//!
//! The runner first creates missing tables, then applies versioned SQLite
//! migrations, then creates indexes. Version 1 is the open-source baseline
//! record; later schema changes should be added as new versions.

use bcs_db_api::{
    DbError, DbPlugin, DbResult, DbStatement, DbTransactionStep, DbValue, db_get_column,
};
use sha2::{Digest, Sha256};

/// SQLite DDL statements executed at local-mode startup.
/// All use IF NOT EXISTS for idempotency.
///
/// Excluded tables:
/// - `bcs_group_session` (singular, legacy, no store reference)
/// - database client test tables (non-business)
const SQLITE_DDL_STATEMENTS: &[&str] = &[
    // ── schema_migrations ─────────────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_schema_migrations (
        version INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        dialect TEXT NOT NULL,
        checksum TEXT NOT NULL,
        applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )",

    // ── bots ──────────────────────────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_bots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        bot_uuid TEXT NOT NULL,
        name TEXT NOT NULL,
        bot_info TEXT DEFAULT NULL,
        session_token TEXT DEFAULT NULL,
        registered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        env TEXT DEFAULT NULL,
        visibility TEXT NOT NULL DEFAULT 'public',
        created_by TEXT DEFAULT NULL,
        actor_kind TEXT NOT NULL DEFAULT 'bot',
        status TEXT NOT NULL DEFAULT 'online',
        is_deleted INTEGER NOT NULL DEFAULT 0,
        agent_code TEXT DEFAULT NULL
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_bots_session_token ON bcs_bots(session_token)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_bots_bot_env ON bcs_bots(bot_uuid, env)",
    "CREATE INDEX IF NOT EXISTS idx_bots_actor_kind ON bcs_bots(actor_kind)",
    "CREATE INDEX IF NOT EXISTS idx_bots_agent_code ON bcs_bots(agent_code)",

    // ── friendships ───────────────────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_friendships (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        left_bot TEXT NOT NULL,
        right_bot TEXT NOT NULL,
        env TEXT NOT NULL DEFAULT 'dev'
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_friendships_pair ON bcs_friendships(left_bot, right_bot)",
    "CREATE INDEX IF NOT EXISTS idx_friendships_left ON bcs_friendships(left_bot)",
    "CREATE INDEX IF NOT EXISTS idx_friendships_right ON bcs_friendships(right_bot)",

    // ── friend_requests ───────────────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_friend_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        request_id TEXT NOT NULL,
        from_bot TEXT NOT NULL,
        to_bot TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        env TEXT NOT NULL DEFAULT 'dev'
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_friend_requests_req ON bcs_friend_requests(request_id)",
    "CREATE INDEX IF NOT EXISTS idx_friend_requests_from ON bcs_friend_requests(from_bot, status)",
    "CREATE INDEX IF NOT EXISTS idx_friend_requests_to ON bcs_friend_requests(to_bot, status)",

    // ── actor_relations ───────────────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_actor_relations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        from_id TEXT NOT NULL,
        to_id TEXT NOT NULL,
        env TEXT NOT NULL,
        kinds INTEGER NOT NULL DEFAULT 0,
        allow INTEGER NOT NULL DEFAULT 0,
        deny INTEGER NOT NULL DEFAULT 0,
        is_creator INTEGER NOT NULL DEFAULT 0
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_relations_from_to_env ON bcs_actor_relations(from_id, to_id, env)",
    "CREATE INDEX IF NOT EXISTS idx_relations_to_env ON bcs_actor_relations(to_id, env)",
    "CREATE INDEX IF NOT EXISTS idx_relations_from_env_creator ON bcs_actor_relations(from_id, env, is_creator)",

    // ── providers ─────────────────────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_providers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        provider_id TEXT NOT NULL,
        env TEXT NOT NULL,
        name TEXT NOT NULL,
        config TEXT NOT NULL,
        disabled INTEGER NOT NULL DEFAULT 0,
        created_by TEXT NOT NULL,
        owners TEXT NOT NULL
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_providers_env ON bcs_providers(env, provider_id)",

    // ── organizations ─────────────────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_organizations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        env TEXT NOT NULL,
        code TEXT NOT NULL,
        name TEXT NOT NULL,
        description TEXT DEFAULT NULL,
        managing_provider_id TEXT NOT NULL,
        disabled INTEGER NOT NULL DEFAULT 0
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_org_env_code ON bcs_organizations(env, code)",
    "CREATE INDEX IF NOT EXISTS idx_org_env_disabled ON bcs_organizations(env, disabled)",
    "CREATE INDEX IF NOT EXISTS idx_org_env_provider ON bcs_organizations(env, managing_provider_id)",

    // ── organization_members ──────────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_organization_members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        env TEXT NOT NULL,
        organization_code TEXT NOT NULL,
        bot_uuid TEXT NOT NULL,
        role TEXT DEFAULT NULL,
        disabled INTEGER NOT NULL DEFAULT 0
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_org_member ON bcs_organization_members(env, organization_code, bot_uuid)",
    "CREATE INDEX IF NOT EXISTS idx_member_bot ON bcs_organization_members(env, bot_uuid)",
    "CREATE INDEX IF NOT EXISTS idx_member_org_disabled_role ON bcs_organization_members(env, organization_code, disabled, role)",

    // ── provider_bot_bindings ─────────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_provider_bot_bindings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        bot_uuid TEXT NOT NULL,
        provider_id TEXT NOT NULL,
        provider_bot_ref TEXT NOT NULL,
        env TEXT NOT NULL,
        disabled INTEGER NOT NULL DEFAULT 0
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_provider_ref_env ON bcs_provider_bot_bindings(env, provider_id, provider_bot_ref)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_bot_uuid_env ON bcs_provider_bot_bindings(env, bot_uuid)",

    // ── channel bindings ─────────────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_channel_bindings (
        id TEXT PRIMARY KEY,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        channel_type TEXT NOT NULL,
        account_ref TEXT NOT NULL,
        target_json TEXT NOT NULL,
        group_chat_scope TEXT DEFAULT NULL,
        visibility TEXT NOT NULL,
        env TEXT NOT NULL,
        status TEXT NOT NULL,
        created_by TEXT DEFAULT NULL,
        config_json TEXT NOT NULL
    )",
    "CREATE INDEX IF NOT EXISTS idx_channel_bindings_account ON bcs_channel_bindings(channel_type, account_ref, status)",

    // ── channel conversations ─────────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_channel_conversations (
        binding_id TEXT NOT NULL,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        im_conversation_id TEXT NOT NULL,
        im_conversation_type TEXT NOT NULL,
        session_scope TEXT NOT NULL,
        im_user_id TEXT NOT NULL DEFAULT '',
        bcs_session_id TEXT NOT NULL,
        last_active_at INTEGER NOT NULL,
        PRIMARY KEY (binding_id, im_conversation_id, session_scope, im_user_id)
    )",
    "CREATE INDEX IF NOT EXISTS idx_channel_conversations_session ON bcs_channel_conversations(binding_id, bcs_session_id)",
    "CREATE INDEX IF NOT EXISTS idx_channel_conversations_bcs_session ON bcs_channel_conversations(bcs_session_id, binding_id)",

    // ── channel IM participants ───────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_channel_im_participants (
        channel_type TEXT NOT NULL,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        account_ref TEXT NOT NULL,
        im_user_id TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        display_name TEXT DEFAULT NULL,
        PRIMARY KEY (channel_type, account_ref, im_user_id)
    )",

    // ── provider_credentials ──────────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_provider_credentials (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        provider_id TEXT NOT NULL,
        env TEXT NOT NULL,
        credential_kind TEXT NOT NULL,
        secret_value TEXT NOT NULL,
        disabled INTEGER NOT NULL DEFAULT 0
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_provider_cred_kind ON bcs_provider_credentials(env, provider_id, credential_kind)",
    "CREATE INDEX IF NOT EXISTS idx_credential_lookup ON bcs_provider_credentials(env, credential_kind, secret_value)",

    // ── user_identities ───────────────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_user_identities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        auth_source TEXT NOT NULL,
        external_user_id TEXT NOT NULL,
        user_name TEXT DEFAULT NULL,
        external_user_name TEXT DEFAULT NULL,
        avatar TEXT DEFAULT NULL,
        token TEXT DEFAULT NULL,
        token_expire_at TEXT DEFAULT NULL,
        env TEXT NOT NULL,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_user_id ON bcs_user_identities(user_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_external ON bcs_user_identities(auth_source, external_user_id, env)",
    "CREATE INDEX IF NOT EXISTS idx_external ON bcs_user_identities(external_user_id, env)",

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
        mode TEXT NOT NULL DEFAULT 'auto'
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_participants_env_group_bot ON bcs_group_participants(env, group_id, bot_uuid)",
    "CREATE INDEX IF NOT EXISTS idx_participants_bot ON bcs_group_participants(bot_uuid)",
    "CREATE INDEX IF NOT EXISTS idx_participants_group ON bcs_group_participants(group_id)",

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
        session_title TEXT DEFAULT NULL,
        group_version INTEGER DEFAULT NULL,
        caller_id TEXT DEFAULT NULL,
        input TEXT DEFAULT NULL,
        output TEXT DEFAULT NULL,
        error_message TEXT DEFAULT NULL,
        callback_status TEXT DEFAULT NULL,
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

    // ── session_participants ──────────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_session_participants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        session_id TEXT NOT NULL,
        group_id TEXT NOT NULL,
        bot_uuid TEXT NOT NULL,
        role TEXT NOT NULL,
        env TEXT NOT NULL DEFAULT 'prod'
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_session_participants_env_session_bot ON bcs_session_participants(env, session_id, bot_uuid)",
    "CREATE INDEX IF NOT EXISTS idx_session_participants_bot ON bcs_session_participants(env, bot_uuid)",
    "CREATE INDEX IF NOT EXISTS idx_session_participants_session ON bcs_session_participants(env, session_id)",

    // ── messages ──────────────────────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_messages (
        message_id TEXT NOT NULL PRIMARY KEY,
        group_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        session_seq INTEGER NOT NULL,
        env TEXT NOT NULL,
        sender_id TEXT NOT NULL,
        sender_type TEXT NOT NULL,
        message_type TEXT NOT NULL,
        content TEXT NOT NULL,
        client_msg_id TEXT DEFAULT NULL,
        owner_bot_id TEXT DEFAULT NULL,
        status TEXT DEFAULT 'normal',
        created_at INTEGER NOT NULL,
        ttl_until INTEGER DEFAULT NULL,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        run_id TEXT NOT NULL DEFAULT ''
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_messages_session_seq ON bcs_messages(session_id, session_seq)",
    "CREATE INDEX IF NOT EXISTS idx_messages_group_created ON bcs_messages(group_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_messages_group_session ON bcs_messages(group_id, session_id)",
    "CREATE INDEX IF NOT EXISTS idx_messages_session_created ON bcs_messages(session_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_messages_session_sender_created ON bcs_messages(session_id, sender_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_messages_session_type_created ON bcs_messages(session_id, message_type, created_at)",

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

    // ── state_machine_runs ────────────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_state_machine_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        env TEXT NOT NULL,
        run_id TEXT NOT NULL,
        definition_id TEXT NOT NULL,
        definition_version INTEGER NOT NULL,
        group_id TEXT NOT NULL,
        group_version INTEGER NOT NULL,
        session_id TEXT NOT NULL,
        created_by TEXT DEFAULT NULL,
        status TEXT NOT NULL,
        input_json TEXT DEFAULT NULL,
        output_text TEXT DEFAULT NULL,
        error_message TEXT DEFAULT NULL,
        created_at_ms INTEGER NOT NULL,
        updated_at_ms INTEGER NOT NULL,
        completed_at_ms INTEGER DEFAULT NULL,
        record_status TEXT NOT NULL DEFAULT 'active'
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_sm_run_id ON bcs_state_machine_runs(env, run_id)",
    "CREATE INDEX IF NOT EXISTS idx_sm_runs_session ON bcs_state_machine_runs(env, session_id)",
    "CREATE INDEX IF NOT EXISTS idx_sm_runs_created_by ON bcs_state_machine_runs(env, created_by, created_at_ms)",
    "CREATE INDEX IF NOT EXISTS idx_sm_runs_group_status ON bcs_state_machine_runs(env, group_id, status, created_at_ms)",
    "CREATE INDEX IF NOT EXISTS idx_sm_runs_status_updated ON bcs_state_machine_runs(env, status, updated_at_ms)",
    "CREATE INDEX IF NOT EXISTS idx_sm_runs_definition ON bcs_state_machine_runs(env, definition_id, definition_version)",
    "CREATE INDEX IF NOT EXISTS idx_sm_runs_record_status ON bcs_state_machine_runs(env, record_status)",

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

    // ── identity_links ────────────────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_identity_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        internal_id TEXT NOT NULL,
        auth_source TEXT NOT NULL,
        external_id TEXT NOT NULL,
        external_owner_id TEXT DEFAULT NULL,
        provider_id TEXT DEFAULT NULL,
        actor_kind TEXT NOT NULL,
        env TEXT NOT NULL,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_identity ON bcs_identity_links(auth_source, external_id, external_owner_id, provider_id, env)",
    "CREATE INDEX IF NOT EXISTS idx_identity_internal ON bcs_identity_links(internal_id, env)",
    "CREATE INDEX IF NOT EXISTS idx_identity_external ON bcs_identity_links(external_id, env)",
    "CREATE INDEX IF NOT EXISTS idx_identity_provider ON bcs_identity_links(provider_id, external_id, env)",

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

#[derive(Debug, Clone, Copy)]
struct SqliteMigration {
    version: i64,
    name: &'static str,
}

const SQLITE_VERSIONED_MIGRATIONS: &[SqliteMigration] = &[
    SqliteMigration {
        version: 1,
        name: "init_schema",
    },
    SqliteMigration {
        version: 2,
        name: "channel_binding_audit_timestamps",
    },
    SqliteMigration {
        version: 3,
        name: "add_organizations",
    },
];

pub fn sqlite_target_version() -> i64 {
    SQLITE_VERSIONED_MIGRATIONS
        .last()
        .map(|migration| migration.version)
        .unwrap_or(0)
}

pub fn sqlite_migration_count() -> usize {
    SQLITE_VERSIONED_MIGRATIONS.len()
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SqliteMigrationReport {
    pub current_version: Option<i64>,
    pub target_version: i64,
    pub pending_versions: Vec<SqliteMigrationPlan>,
    pub applied_versions: Vec<SqliteMigrationPlan>,
    pub repaired_columns: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SqliteMigrationPlan {
    pub version: i64,
    pub name: String,
    pub checksum: String,
    pub statements: Vec<String>,
    pub repairs: Vec<String>,
}

/// Execute all SQLite schema work against the given DB plugin.
pub async fn run_sqlite_migrations(db: &dyn DbPlugin) -> DbResult<()> {
    run_sqlite_migrations_with_report(db).await?;
    Ok(())
}

/// Execute all SQLite schema work and return a summary report.
pub async fn run_sqlite_migrations_with_report(
    db: &dyn DbPlugin,
) -> DbResult<SqliteMigrationReport> {
    let before = check_sqlite_migrations(db).await?;
    run_sqlite_bootstrap_tables(db).await?;
    run_sqlite_versioned_migrations(db).await?;
    run_sqlite_bootstrap_indexes(db).await?;
    let mut after = check_sqlite_migrations(db).await?;
    after.applied_versions = before.pending_versions;
    after.repaired_columns = after
        .applied_versions
        .iter()
        .flat_map(|migration| migration.repairs.iter().cloned())
        .collect();
    Ok(after)
}

/// Inspect the current SQLite migration state without mutating the database.
pub async fn check_sqlite_migrations(db: &dyn DbPlugin) -> DbResult<SqliteMigrationReport> {
    let schema_table_exists = table_exists(db, "bcs_schema_migrations").await?;
    let current_version = current_sqlite_version(db, schema_table_exists).await?;
    let mut pending_versions = Vec::new();

    for migration in SQLITE_VERSIONED_MIGRATIONS {
        let checksum = sqlite_migration_checksum(migration);
        if schema_table_exists
            && let Some(applied) = applied_sqlite_migration(db, migration.version).await?
        {
            if applied.checksum != checksum {
                return Err(DbError::InvalidInput(format!(
                    "sqlite migration checksum mismatch for version {} ({}): applied={}, current={}",
                    migration.version, applied.name, applied.checksum, checksum
                )));
            }
            continue;
        }

        pending_versions.push(sqlite_migration_plan(migration, checksum));
    }

    Ok(SqliteMigrationReport {
        current_version,
        target_version: sqlite_target_version(),
        pending_versions,
        applied_versions: Vec::new(),
        repaired_columns: Vec::new(),
    })
}

/// Create missing SQLite tables for fresh local databases.
///
/// This intentionally skips indexes so versioned migrations can run before the
/// current indexes are created.
pub async fn run_sqlite_bootstrap_tables(db: &dyn DbPlugin) -> DbResult<()> {
    for ddl in SQLITE_DDL_STATEMENTS {
        if is_create_table(ddl) {
            db.execute(DbStatement::new(*ddl)).await?;
        }
    }
    ensure_sqlite_message_owner_bot_id(db).await?;
    Ok(())
}

async fn ensure_sqlite_message_owner_bot_id(db: &dyn DbPlugin) -> DbResult<()> {
    let columns = db.query(DbStatement::new("PRAGMA table_info(bcs_messages)")).await?;
    let mut has_owner_bot_id = false;
    for row in &columns {
        if row.get_string("name")?.as_deref() == Some("owner_bot_id") {
            has_owner_bot_id = true;
            break;
        }
    }
    if !has_owner_bot_id {
        db.execute(DbStatement::new(
            "ALTER TABLE bcs_messages ADD COLUMN owner_bot_id TEXT DEFAULT NULL",
        ))
        .await?;
    }
    db.execute(DbStatement::new(
        "CREATE INDEX IF NOT EXISTS idx_messages_session_owner_created \
         ON bcs_messages(session_id, owner_bot_id, created_at, session_seq)",
    ))
    .await?;
    Ok(())
}

/// Create missing SQLite indexes after versioned migrations have run.
pub async fn run_sqlite_bootstrap_indexes(db: &dyn DbPlugin) -> DbResult<()> {
    for ddl in SQLITE_DDL_STATEMENTS {
        if is_create_index(ddl) {
            db.execute(DbStatement::new(*ddl)).await?;
        }
    }
    Ok(())
}

/// Apply versioned SQLite migrations and record successful versions.
pub async fn run_sqlite_versioned_migrations(db: &dyn DbPlugin) -> DbResult<()> {
    for migration in SQLITE_VERSIONED_MIGRATIONS {
        apply_sqlite_migration(db, migration).await?;
    }
    Ok(())
}

async fn apply_sqlite_migration(db: &dyn DbPlugin, migration: &SqliteMigration) -> DbResult<()> {
    let checksum = sqlite_migration_checksum(migration);
    if let Some(applied) = applied_sqlite_migration(db, migration.version).await? {
        if applied.checksum != checksum {
            return Err(DbError::InvalidInput(format!(
                "sqlite migration checksum mismatch for version {} ({}): applied={}, current={}",
                migration.version, applied.name, applied.checksum, checksum
            )));
        }
        return Ok(());
    }

    apply_sqlite_migration_body(db, migration).await?;

    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_schema_migrations (version, name, dialect, checksum) VALUES (?, ?, ?, ?)",
        vec![
            DbValue::from(migration.version),
            DbValue::from(migration.name),
            DbValue::from("sqlite"),
            DbValue::from(checksum.as_str()),
        ],
    ))
    .await?;
    Ok(())
}

async fn apply_sqlite_migration_body(
    db: &dyn DbPlugin,
    migration: &SqliteMigration,
) -> DbResult<()> {
    match migration.version {
        2 => repair_sqlite_channel_bindings_audit_schema(db).await,
        // Startup creates any missing organization tables before recording version 3.
        3 => Ok(()),
        _ => Ok(()),
    }
}

async fn repair_sqlite_channel_bindings_audit_schema(db: &dyn DbPlugin) -> DbResult<()> {
    if !table_exists(db, "bcs_channel_bindings").await? {
        return Ok(());
    }
    let columns = sqlite_table_columns(db, "bcs_channel_bindings").await?;
    let has_created_at = columns.iter().any(|column| column == "created_at");
    let has_gmt_create = columns.iter().any(|column| column == "gmt_create");
    let has_gmt_modified = columns.iter().any(|column| column == "gmt_modified");
    if has_gmt_create && has_gmt_modified && !has_created_at {
        return Ok(());
    }

    let gmt_create_expr = sqlite_channel_audit_expr(
        has_gmt_create,
        has_created_at,
        "gmt_create",
    );
    let gmt_modified_expr = sqlite_channel_audit_expr(
        has_gmt_modified,
        has_created_at,
        "gmt_modified",
    );
    db.transaction(vec![
        DbTransactionStep::Execute(DbStatement::new(
            "DROP TABLE IF EXISTS bcs_channel_bindings__audit_migration",
        )),
        DbTransactionStep::Execute(DbStatement::new(
            "CREATE TABLE bcs_channel_bindings__audit_migration (
                id TEXT PRIMARY KEY,
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                channel_type TEXT NOT NULL,
                account_ref TEXT NOT NULL,
                target_json TEXT NOT NULL,
                group_chat_scope TEXT DEFAULT NULL,
                visibility TEXT NOT NULL,
                env TEXT NOT NULL,
                status TEXT NOT NULL,
                created_by TEXT DEFAULT NULL,
                config_json TEXT NOT NULL
            )",
        )),
        DbTransactionStep::Execute(DbStatement::new(format!(
            "INSERT INTO bcs_channel_bindings__audit_migration \
             (id, gmt_create, gmt_modified, channel_type, account_ref, target_json, group_chat_scope, \
              visibility, env, status, created_by, config_json) \
             SELECT id, {gmt_create_expr}, {gmt_modified_expr}, channel_type, account_ref, target_json, group_chat_scope, \
                    visibility, env, status, created_by, config_json \
             FROM bcs_channel_bindings"
        ))),
        DbTransactionStep::Execute(DbStatement::new("DROP TABLE bcs_channel_bindings")),
        DbTransactionStep::Execute(DbStatement::new(
            "ALTER TABLE bcs_channel_bindings__audit_migration RENAME TO bcs_channel_bindings",
        )),
    ])
    .await?;
    Ok(())
}

fn sqlite_channel_audit_expr(
    has_audit_column: bool,
    has_created_at: bool,
    audit_column: &'static str,
) -> &'static str {
    if has_audit_column {
        audit_column
    } else if has_created_at {
        "datetime(created_at / 1000, 'unixepoch')"
    } else {
        "CURRENT_TIMESTAMP"
    }
}

#[derive(Debug)]
struct AppliedMigration {
    name: String,
    checksum: String,
}

async fn applied_sqlite_migration(
    db: &dyn DbPlugin,
    version: i64,
) -> DbResult<Option<AppliedMigration>> {
    let rows = db
        .query(DbStatement::with_params(
            "SELECT name, checksum FROM bcs_schema_migrations WHERE version = ?",
            vec![DbValue::from(version)],
        ))
        .await?;
    rows.into_iter()
        .next()
        .map(|row| {
            Ok(AppliedMigration {
                name: db_get_column(&row, "name")?,
                checksum: db_get_column(&row, "checksum")?,
            })
        })
        .transpose()
}

async fn current_sqlite_version(
    db: &dyn DbPlugin,
    schema_table_exists: bool,
) -> DbResult<Option<i64>> {
    if !schema_table_exists {
        return Ok(None);
    }
    let rows = db
        .query(DbStatement::new(
            "SELECT version FROM bcs_schema_migrations ORDER BY version DESC LIMIT 1",
        ))
        .await?;
    rows.into_iter()
        .next()
        .map(|row| db_get_column(&row, "version"))
        .transpose()
}

fn sqlite_migration_plan(migration: &SqliteMigration, checksum: String) -> SqliteMigrationPlan {
    SqliteMigrationPlan {
        version: migration.version,
        name: migration.name.to_string(),
        checksum,
        statements: Vec::new(),
        repairs: Vec::new(),
    }
}

async fn table_exists(db: &dyn DbPlugin, table: &str) -> DbResult<bool> {
    let rows = db
        .query(DbStatement::with_params(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            vec![DbValue::from(table)],
        ))
        .await?;
    Ok(!rows.is_empty())
}

async fn sqlite_table_columns(db: &dyn DbPlugin, table: &str) -> DbResult<Vec<String>> {
    let rows = db
        .query(DbStatement::new(format!("PRAGMA table_info({table})")))
        .await?;
    rows.into_iter()
        .map(|row| db_get_column(&row, "name"))
        .collect()
}

fn sqlite_migration_checksum(migration: &SqliteMigration) -> String {
    let mut hasher = Sha256::new();
    hasher.update(migration.version.to_string().as_bytes());
    hasher.update(b"\n");
    hasher.update(migration.name.as_bytes());
    hasher.update(b"\n");
    hex::encode(hasher.finalize())
}

fn is_create_table(sql: &str) -> bool {
    sql.trim_start()
        .to_ascii_uppercase()
        .starts_with("CREATE TABLE")
}

fn is_create_index(sql: &str) -> bool {
    let sql = sql.trim_start().to_ascii_uppercase();
    sql.starts_with("CREATE INDEX") || sql.starts_with("CREATE UNIQUE INDEX")
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_db_local::LocalSqliteDbPlugin;

    async fn column_names(db: &dyn DbPlugin, table: &str) -> DbResult<Vec<String>> {
        let rows = db
            .query(DbStatement::new(format!("PRAGMA table_info({table})")))
            .await?;
        rows.into_iter()
            .map(|row| db_get_column(&row, "name"))
            .collect()
    }

    async fn migration_rows(db: &dyn DbPlugin) -> DbResult<Vec<(i64, String, String)>> {
        let rows = db
            .query(DbStatement::new(
                "SELECT version, name, dialect FROM bcs_schema_migrations ORDER BY version",
            ))
            .await?;
        rows.into_iter()
            .map(|row| {
                Ok((
                    db_get_column(&row, "version")?,
                    db_get_column(&row, "name")?,
                    db_get_column(&row, "dialect")?,
                ))
            })
            .collect()
    }

    #[tokio::test]
    async fn fresh_sqlite_migrations_create_baseline_record_and_agent_code() -> DbResult<()> {
        let db = LocalSqliteDbPlugin::new()?;

        run_sqlite_migrations(&db).await?;

        let columns = column_names(&db, "bcs_bots").await?;
        assert!(columns.iter().any(|column| column == "agent_code"));
        assert_eq!(
            migration_rows(&db).await?,
            vec![
                (1, "init_schema".to_string(), "sqlite".to_string()),
                (
                    2,
                    "channel_binding_audit_timestamps".to_string(),
                    "sqlite".to_string()
                ),
                (3, "add_organizations".to_string(), "sqlite".to_string())
            ]
        );
        Ok(())
    }

    #[tokio::test]
    async fn sqlite_migration_plan_reports_init_and_channel_audit_versions() -> DbResult<()> {
        let db = LocalSqliteDbPlugin::new()?;

        let report = check_sqlite_migrations(&db).await?;

        assert_eq!(report.pending_versions.len(), 3);
        assert_eq!(report.pending_versions[0].version, 1);
        assert_eq!(report.pending_versions[0].name, "init_schema");
        assert!(report.pending_versions[0].statements.is_empty());
        assert!(report.pending_versions[0].repairs.is_empty());
        assert_eq!(report.pending_versions[1].version, 2);
        assert_eq!(
            report.pending_versions[1].name,
            "channel_binding_audit_timestamps"
        );
        assert_eq!(report.pending_versions[2].version, 3);
        assert_eq!(report.pending_versions[2].name, "add_organizations");
        Ok(())
    }

    #[tokio::test]
    async fn sqlite_migrations_are_idempotent() -> DbResult<()> {
        let db = LocalSqliteDbPlugin::new()?;

        run_sqlite_migrations(&db).await?;
        run_sqlite_migrations(&db).await?;

        assert_eq!(
            migration_rows(&db).await?,
            vec![
                (1, "init_schema".to_string(), "sqlite".to_string()),
                (
                    2,
                    "channel_binding_audit_timestamps".to_string(),
                    "sqlite".to_string()
                ),
                (3, "add_organizations".to_string(), "sqlite".to_string())
            ]
        );
        Ok(())
    }

    #[tokio::test]
    async fn sqlite_migrations_repair_legacy_channel_binding_created_at() -> DbResult<()> {
        let db = LocalSqliteDbPlugin::new()?;
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                dialect TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )",
        ))
        .await?;
        db.execute(DbStatement::with_params(
            "INSERT INTO bcs_schema_migrations (version, name, dialect, checksum) VALUES (?, ?, ?, ?)",
            vec![
                DbValue::from(1_i64),
                DbValue::from("init_schema"),
                DbValue::from("sqlite"),
                DbValue::from(sqlite_migration_checksum(&SQLITE_VERSIONED_MIGRATIONS[0])),
            ],
        ))
        .await?;
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_channel_bindings (
                id TEXT PRIMARY KEY,
                channel_type TEXT NOT NULL,
                account_ref TEXT NOT NULL,
                target_json TEXT NOT NULL,
                group_chat_scope TEXT DEFAULT NULL,
                visibility TEXT NOT NULL,
                env TEXT NOT NULL,
                status TEXT NOT NULL,
                created_by TEXT DEFAULT NULL,
                created_at INTEGER NOT NULL,
                config_json TEXT NOT NULL
            )",
        ))
        .await?;
        db.execute(DbStatement::with_params(
            "INSERT INTO bcs_channel_bindings \
             (id, channel_type, account_ref, target_json, group_chat_scope, visibility, env, status, created_by, created_at, config_json) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            vec![
                DbValue::from("legacy_binding"),
                DbValue::from("dingtalk"),
                DbValue::from("robot_1"),
                DbValue::from(r#"{"type":"group","group_id":"group_1"}"#),
                DbValue::from("per_sender"),
                DbValue::from("full_transcript"),
                DbValue::from("dev"),
                DbValue::from("active"),
                DbValue::from("creator"),
                DbValue::from(100_i64),
                DbValue::from(r#"{"send_mode":{"mode":"normal"}}"#),
            ],
        ))
        .await?;

        run_sqlite_migrations(&db).await?;

        let columns = column_names(&db, "bcs_channel_bindings").await?;
        assert!(columns.iter().any(|column| column == "gmt_create"));
        assert!(columns.iter().any(|column| column == "gmt_modified"));
        assert!(!columns.iter().any(|column| column == "created_at"));
        db.execute(DbStatement::with_params(
            "INSERT INTO bcs_channel_bindings \
             (id, channel_type, account_ref, target_json, group_chat_scope, visibility, env, status, created_by, config_json) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            vec![
                DbValue::from("new_binding"),
                DbValue::from("dingtalk"),
                DbValue::from("robot_2"),
                DbValue::from(r#"{"type":"group","group_id":"group_2"}"#),
                DbValue::from("per_sender"),
                DbValue::from("full_transcript"),
                DbValue::from("dev"),
                DbValue::from("active"),
                DbValue::from("creator"),
                DbValue::from(r#"{"send_mode":{"mode":"normal"}}"#),
            ],
        ))
        .await?;

        Ok(())
    }

    #[tokio::test]
    async fn sqlite_migration_checksum_mismatch_errors() -> DbResult<()> {
        let db = LocalSqliteDbPlugin::new()?;
        run_sqlite_migrations(&db).await?;
        db.execute(DbStatement::with_params(
            "UPDATE bcs_schema_migrations SET checksum = ? WHERE version = ?",
            vec![DbValue::from("bad-checksum"), DbValue::from(1_i64)],
        ))
        .await?;

        let err = run_sqlite_migrations(&db)
            .await
            .expect_err("checksum mismatch should fail startup");

        assert!(err.to_string().contains("checksum mismatch"));
        Ok(())
    }
}
