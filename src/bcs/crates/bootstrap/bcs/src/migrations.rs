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
        agent_code TEXT DEFAULT NULL,
        task_claim_mode INTEGER NOT NULL DEFAULT 0,
        task_dream_mode INTEGER NOT NULL DEFAULT 0,
        user_visibility TEXT NOT NULL DEFAULT 'protected',
        friend_ext TEXT DEFAULT NULL,
        friend_check_in_strategy TEXT NOT NULL DEFAULT 'APPROVAL'
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
    // ── HumanInput IM requests ────────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_human_input_requests (
        request_id TEXT PRIMARY KEY,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        session_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        node_id TEXT NOT NULL,
        binding_id TEXT NOT NULL,
        channel_type TEXT NOT NULL,
        account_ref TEXT NOT NULL,
        notification_mode TEXT NOT NULL,
        reply_scope_key TEXT NOT NULL,
        active_slot_key TEXT DEFAULT NULL,
        assignee_actor_id TEXT NOT NULL,
        im_conversation_id TEXT NOT NULL,
        im_conversation_type TEXT NOT NULL,
        im_user_id TEXT DEFAULT NULL,
        node_display_name TEXT NOT NULL,
        notification_text TEXT NOT NULL,
        deadline_ms INTEGER NOT NULL,
        status TEXT NOT NULL,
        provider_message_ref TEXT DEFAULT NULL,
        delivery_attempts INTEGER NOT NULL DEFAULT 0,
        last_delivery_error TEXT DEFAULT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        activated_at INTEGER DEFAULT NULL,
        responded_at INTEGER DEFAULT NULL
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_human_input_active_slot ON bcs_human_input_requests(active_slot_key)",
    "CREATE INDEX IF NOT EXISTS idx_human_input_scope_status ON bcs_human_input_requests(reply_scope_key, status, deadline_ms, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_human_input_run_node ON bcs_human_input_requests(run_id, node_id)",
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
        tags_json TEXT DEFAULT NULL
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

    // ── session_files ─────────────────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_session_files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        env TEXT NOT NULL,
        file_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        owner_actor_kind TEXT NOT NULL,
        owner_actor_id TEXT NOT NULL,
        file_name TEXT NOT NULL,
        mime_type TEXT NOT NULL,
        size INTEGER NOT NULL,
        sha256 TEXT,
        storage_backend TEXT NOT NULL,
        object_handle TEXT NOT NULL,
        status TEXT NOT NULL
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_session_file ON bcs_session_files (env, session_id, file_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_env_file_id ON bcs_session_files (env, file_id)",
    "CREATE INDEX IF NOT EXISTS idx_session_files_session ON bcs_session_files (env, session_id, gmt_create)",
    // ── public Eventing ──────────────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_event_subscriptions (
        subscription_id TEXT NOT NULL PRIMARY KEY,
        name TEXT NOT NULL,
        scope_type TEXT NOT NULL,
        scope_id TEXT NOT NULL,
        status TEXT NOT NULL,
        current_revision INTEGER NOT NULL,
        created_by_type TEXT NOT NULL,
        created_by_id TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        deleted_at TEXT DEFAULT NULL,
        env TEXT NOT NULL
    )",
    "CREATE INDEX IF NOT EXISTS idx_event_subscription_scope
        ON bcs_event_subscriptions(env, scope_type, scope_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_event_subscription_status
        ON bcs_event_subscriptions(env, status, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_event_subscription_creator
        ON bcs_event_subscriptions(env, created_by_type, created_by_id)",
    "CREATE TABLE IF NOT EXISTS bcs_event_subscription_revisions (
        subscription_id TEXT NOT NULL,
        revision INTEGER NOT NULL,
        event_filters_json TEXT NOT NULL,
        payload_mode TEXT NOT NULL,
        endpoint_url TEXT NOT NULL,
        request_timeout_ms INTEGER NOT NULL,
        activated_at TEXT NOT NULL,
        retired_at TEXT DEFAULT NULL,
        env TEXT NOT NULL,
        PRIMARY KEY(subscription_id, revision)
    )",
    "CREATE INDEX IF NOT EXISTS idx_event_revision_active
        ON bcs_event_subscription_revisions(env, subscription_id, retired_at)",
    "CREATE TABLE IF NOT EXISTS bcs_event_scope_epochs (
        env TEXT NOT NULL,
        scope_type TEXT NOT NULL,
        scope_id TEXT NOT NULL,
        epoch INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(env, scope_type, scope_id)
    )",
    "CREATE TABLE IF NOT EXISTS bcs_event_streams (
        env TEXT NOT NULL,
        stream_key TEXT NOT NULL,
        last_sequence INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(env, stream_key)
    )",
    "CREATE TABLE IF NOT EXISTS bcs_events (
        event_id TEXT NOT NULL PRIMARY KEY,
        event_type TEXT NOT NULL,
        schema_version TEXT NOT NULL,
        producer TEXT NOT NULL,
        producer_key TEXT NOT NULL,
        subject_type TEXT NOT NULL,
        subject_id TEXT NOT NULL,
        group_id TEXT DEFAULT NULL,
        session_id TEXT DEFAULT NULL,
        task_id TEXT DEFAULT NULL,
        run_id TEXT DEFAULT NULL,
        stream_key TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        actor_json TEXT DEFAULT NULL,
        correlation_id TEXT DEFAULT NULL,
        causation_event_id TEXT DEFAULT NULL,
        trace_id TEXT DEFAULT NULL,
        data_json TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        fanout_status TEXT NOT NULL,
        retention_until TEXT NOT NULL,
        env TEXT NOT NULL
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_event_producer
        ON bcs_events(env, producer, producer_key, event_type)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_event_stream_sequence
        ON bcs_events(env, stream_key, sequence)",
    "CREATE INDEX IF NOT EXISTS idx_event_fanout_status
        ON bcs_events(env, fanout_status, recorded_at)",
    "CREATE INDEX IF NOT EXISTS idx_event_scope_type
        ON bcs_events(env, group_id, session_id, event_type, recorded_at)",
    "CREATE INDEX IF NOT EXISTS idx_event_causation
        ON bcs_events(env, causation_event_id)",
    "CREATE INDEX IF NOT EXISTS idx_event_retention
        ON bcs_events(env, retention_until, fanout_status)",
    "CREATE TABLE IF NOT EXISTS bcs_event_fanout_targets (
        target_id TEXT NOT NULL PRIMARY KEY,
        event_id TEXT NOT NULL,
        subscription_id TEXT NOT NULL,
        subscription_revision INTEGER NOT NULL,
        purpose TEXT NOT NULL,
        replay_request_id TEXT NOT NULL DEFAULT '',
        replay_of_delivery_id TEXT DEFAULT NULL,
        depends_on_target_id TEXT DEFAULT NULL,
        status TEXT NOT NULL,
        lease_owner TEXT DEFAULT NULL,
        lease_until TEXT DEFAULT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        materialized_at TEXT DEFAULT NULL,
        cancelled_at TEXT DEFAULT NULL,
        env TEXT NOT NULL
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_event_target_idempotency
        ON bcs_event_fanout_targets(env, subscription_id, subscription_revision, event_id, purpose, replay_request_id)",
    "CREATE INDEX IF NOT EXISTS idx_event_target_pending
        ON bcs_event_fanout_targets(env, status, lease_until, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_event_target_dependency
        ON bcs_event_fanout_targets(env, depends_on_target_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_event_target_subscription
        ON bcs_event_fanout_targets(env, subscription_id, subscription_revision, status)",
    "CREATE TABLE IF NOT EXISTS bcs_event_deliveries (
        delivery_id TEXT NOT NULL PRIMARY KEY,
        fanout_target_id TEXT NOT NULL,
        event_id TEXT NOT NULL,
        subscription_id TEXT NOT NULL,
        subscription_revision INTEGER NOT NULL,
        stream_key TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        payload_bytes BLOB NOT NULL,
        payload_sha256 TEXT NOT NULL,
        status TEXT NOT NULL,
        attempt_count INTEGER NOT NULL DEFAULT 0,
        first_attempt_at TEXT DEFAULT NULL,
        last_attempt_at TEXT DEFAULT NULL,
        next_attempt_at TEXT DEFAULT NULL,
        lease_owner TEXT DEFAULT NULL,
        lease_until TEXT DEFAULT NULL,
        last_http_status INTEGER DEFAULT NULL,
        last_error_category TEXT DEFAULT NULL,
        last_error_summary TEXT DEFAULT NULL,
        dead_lettered_at TEXT DEFAULT NULL,
        cancelled_at TEXT DEFAULT NULL,
        skipped_at TEXT DEFAULT NULL,
        skip_actor TEXT DEFAULT NULL,
        skip_reason TEXT DEFAULT NULL,
        replay_of_delivery_id TEXT DEFAULT NULL,
        resolved_by_delivery_id TEXT DEFAULT NULL,
        resolved_at TEXT DEFAULT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        succeeded_at TEXT DEFAULT NULL,
        env TEXT NOT NULL
    )",
    "CREATE UNIQUE INDEX IF NOT EXISTS uk_event_delivery_target
        ON bcs_event_deliveries(env, fanout_target_id)",
    "CREATE INDEX IF NOT EXISTS idx_event_claim_due
        ON bcs_event_deliveries(env, status, next_attempt_at, lease_until)",
    "CREATE INDEX IF NOT EXISTS idx_event_strict_lane
        ON bcs_event_deliveries(env, subscription_id, stream_key, status, sequence)",
    "CREATE INDEX IF NOT EXISTS idx_event_delivery_subscription
        ON bcs_event_deliveries(env, subscription_id, status, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_event_delivery_replay
        ON bcs_event_deliveries(env, replay_of_delivery_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_event_delivery_retention
        ON bcs_event_deliveries(env, status, succeeded_at, dead_lettered_at)",
    "CREATE TABLE IF NOT EXISTS bcs_event_delivery_attempts (
        delivery_id TEXT NOT NULL,
        attempt_no INTEGER NOT NULL,
        started_at TEXT NOT NULL,
        completed_at TEXT DEFAULT NULL,
        latency_ms INTEGER DEFAULT NULL,
        result TEXT DEFAULT NULL,
        http_status INTEGER DEFAULT NULL,
        error_category TEXT DEFAULT NULL,
        error_summary TEXT DEFAULT NULL,
        response_bytes_observed INTEGER DEFAULT NULL,
        worker_id TEXT NOT NULL,
        PRIMARY KEY(delivery_id, attempt_no)
    )",
    "CREATE INDEX IF NOT EXISTS idx_event_attempt_result
        ON bcs_event_delivery_attempts(result, started_at)",
    "CREATE TABLE IF NOT EXISTS bcs_event_subscription_audits (
        audit_id TEXT NOT NULL PRIMARY KEY,
        subscription_id TEXT NOT NULL,
        revision INTEGER DEFAULT NULL,
        action TEXT NOT NULL,
        actor_type TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        reason TEXT DEFAULT NULL,
        details_json TEXT DEFAULT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        env TEXT NOT NULL
    )",
    "CREATE INDEX IF NOT EXISTS idx_event_audit_subscription
        ON bcs_event_subscription_audits(env, subscription_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_event_audit_actor
        ON bcs_event_subscription_audits(env, actor_type, actor_id, created_at)",
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
    SqliteMigration {
        version: 4,
        name: "add_session_collection",
    },
    SqliteMigration {
        version: 5,
        name: "add_session_collection_timestamp",
    },
    SqliteMigration {
        version: 6,
        name: "session_files",
    },
    SqliteMigration {
        version: 7,
        name: "human_input_output_metadata",
    },
    SqliteMigration {
        version: 8,
        name: "human_input_im_requests",
    },
    SqliteMigration {
        version: 9,
        name: "eventing",
    },
    SqliteMigration {
        version: 10,
        name: "eventing_plaintext_endpoint",
    },
    SqliteMigration {
        version: 11,
        name: "group_opening_message",
    },
    SqliteMigration {
        version: 12,
        name: "add_bot_task_modes",
    },
    SqliteMigration {
        version: 13,
        name: "edge_permission",
    },
    SqliteMigration {
        version: 14,
        name: "add_bot_internal_attributes",
    },
    SqliteMigration {
        version: 15,
        name: "group_participant_tags",
    },
    SqliteMigration {
        version: 16,
        name: "expand_session_ids",
    },
    SqliteMigration {
        version: 17,
        name: "session_callback_lease",
    },
    SqliteMigration {
        version: 18,
        name: "state_machine_rerun_lineage",
    },
];

pub fn sqlite_target_version() -> i64 {
    SQLITE_VERSIONED_MIGRATIONS
        .last()
        .map(|migration| migration.version)
        .unwrap_or(0)
}

#[allow(dead_code)]
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
    ensure_sqlite_session_collected_column(db).await?;
    ensure_bcs_session_files(db).await?;
    ensure_sqlite_bot_task_modes(db).await?;
    ensure_sqlite_bot_internal_attributes(db).await?;
    Ok(())
}

async fn ensure_sqlite_group_opening_message_column(db: &dyn DbPlugin) -> DbResult<()> {
    if !table_exists(db, "bcs_groups").await? {
        return Ok(());
    }
    let columns = sqlite_table_columns(db, "bcs_groups").await?;
    if !columns
        .iter()
        .any(|column| column == "opening_message_json")
    {
        db.execute(DbStatement::new(
            "ALTER TABLE bcs_groups ADD COLUMN opening_message_json TEXT DEFAULT NULL",
        ))
        .await?;
    }
    Ok(())
}

async fn ensure_sqlite_message_owner_bot_id(db: &dyn DbPlugin) -> DbResult<()> {
    let columns = db
        .query(DbStatement::new("PRAGMA table_info(bcs_messages)"))
        .await?;
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

async fn ensure_sqlite_bot_task_modes(db: &dyn DbPlugin) -> DbResult<()> {
    let columns = db
        .query(DbStatement::new("PRAGMA table_info(bcs_bots)"))
        .await?;
    let mut has_claim = false;
    let mut has_dream = false;
    for row in &columns {
        match row.get_string("name")?.as_deref() {
            Some("task_claim_mode") => has_claim = true,
            Some("task_dream_mode") => has_dream = true,
            _ => {}
        }
        if has_claim && has_dream {
            break;
        }
    }
    if !has_claim {
        db.execute(DbStatement::new(
            "ALTER TABLE bcs_bots ADD COLUMN task_claim_mode INTEGER NOT NULL DEFAULT 0",
        ))
        .await?;
    }
    if !has_dream {
        db.execute(DbStatement::new(
            "ALTER TABLE bcs_bots ADD COLUMN task_dream_mode INTEGER NOT NULL DEFAULT 0",
        ))
        .await?;
    }
    Ok(())
}

async fn ensure_sqlite_bot_internal_attributes(db: &dyn DbPlugin) -> DbResult<()> {
    if !table_exists(db, "bcs_bots").await? {
        return Ok(());
    }
    let columns = sqlite_table_columns(db, "bcs_bots").await?;
    if !columns.iter().any(|column| column == "user_visibility") {
        db.execute(DbStatement::new(
            "ALTER TABLE bcs_bots ADD COLUMN user_visibility TEXT NOT NULL DEFAULT 'protected'",
        ))
        .await?;
    }
    if !columns.iter().any(|column| column == "friend_ext") {
        db.execute(DbStatement::new(
            "ALTER TABLE bcs_bots ADD COLUMN friend_ext TEXT DEFAULT NULL",
        ))
        .await?;
    }
    if !columns
        .iter()
        .any(|column| column == "friend_check_in_strategy")
    {
        db.execute(DbStatement::new(
            "ALTER TABLE bcs_bots ADD COLUMN friend_check_in_strategy TEXT NOT NULL DEFAULT 'APPROVAL'",
        ))
        .await?;
    }
    Ok(())
}

async fn ensure_sqlite_session_collected_column(db: &dyn DbPlugin) -> DbResult<()> {
    if !table_exists(db, "bcs_session_participants").await? {
        return Ok(());
    }
    let columns = sqlite_table_columns(db, "bcs_session_participants").await?;
    if !columns.iter().any(|column| column == "collected") {
        db.execute(DbStatement::new(
            "ALTER TABLE bcs_session_participants ADD COLUMN collected INTEGER NOT NULL DEFAULT 0",
        ))
        .await?;
    }
    // collected_at: collected event timestamp (nullable). Added in the same
    // repair pass so legacy DBs gain both columns without a separate run.
    if !columns.iter().any(|column| column == "collected_at") {
        db.execute(DbStatement::new(
            "ALTER TABLE bcs_session_participants ADD COLUMN collected_at TEXT",
        ))
        .await?;
    }
    // The composite index covers (env, group_id, bot_uuid, collected) as a prefix
    // and so also serves any query the former idx_collected did — keep only this
    // one to avoid redundant write overhead. collected_at trailing lets the same
    // index satisfy the collected-list ORDER BY.
    db.execute(DbStatement::new(
        "CREATE INDEX IF NOT EXISTS idx_collected_at \
         ON bcs_session_participants(env, group_id, bot_uuid, collected, collected_at)",
    ))
    .await?;
    Ok(())
}

/// Ensure bcs_session_files table exists. For fresh databases the table is created
/// by run_sqlite_bootstrap_tables via SQLITE_DDL_STATEMENTS; this function handles
/// legacy databases and future schema repairs for the session_files table.
async fn ensure_bcs_session_files(db: &dyn DbPlugin) -> DbResult<()> {
    if !table_exists(db, "bcs_session_files").await? {
        db.execute(DbStatement::new(
            "CREATE TABLE IF NOT EXISTS bcs_session_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                env TEXT NOT NULL,
                file_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                owner_actor_kind TEXT NOT NULL,
                owner_actor_id TEXT NOT NULL,
                file_name TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                size INTEGER NOT NULL,
                sha256 TEXT,
                storage_backend TEXT NOT NULL,
                object_handle TEXT NOT NULL,
                status TEXT NOT NULL
            )",
        ))
        .await?;
        db.execute(DbStatement::new(
            "CREATE UNIQUE INDEX IF NOT EXISTS uk_session_file \
             ON bcs_session_files (env, session_id, file_id)",
        ))
        .await?;
        db.execute(DbStatement::new(
            "CREATE UNIQUE INDEX IF NOT EXISTS uk_env_file_id \
             ON bcs_session_files (env, file_id)",
        ))
        .await?;
        db.execute(DbStatement::new(
            "CREATE INDEX IF NOT EXISTS idx_session_files_session \
             ON bcs_session_files (env, session_id, gmt_create)",
        ))
        .await?;
    }
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
        // collected column is added by ensure_sqlite_session_collected_column in
        // run_sqlite_bootstrap_tables; version 4 only records progress.
        4 => Ok(()),
        // collected_at column is added by ensure_sqlite_session_collected_column
        // in run_sqlite_bootstrap_tables; version 5 only records progress.
        5 => Ok(()),
        // session_files table is created by run_sqlite_bootstrap_tables via
        // SQLITE_DDL_STATEMENTS; version 6 only records progress.
        6 => Ok(()),
        7 => add_sqlite_human_input_output_metadata_schema(db).await,
        // Startup DDL creates the HumanInput request table and indexes before
        // versioned migrations are recorded.
        8 => Ok(()),
        // Startup DDL creates the additive Eventing tables and indexes before
        // versioned migrations are recorded.
        9 => Ok(()),
        // Eventing was still under development when per-Subscription HMAC and
        // encrypted endpoint storage were removed. Repair empty local schemas
        // without discarding any persisted Subscription configuration.
        10 => migrate_sqlite_eventing_plaintext_endpoint(db).await,
        11 => ensure_sqlite_group_opening_message_column(db).await,
        // task_claim_mode / task_dream_mode columns are added by
        // ensure_sqlite_bot_task_modes in run_sqlite_bootstrap_tables;
        // version 12 only records progress.
        12 => Ok(()),
        // Edge-permission tables (friend unification) + bcs_bots config columns.
        13 => add_sqlite_edge_permission_schema(db).await,
        // Internal Bot attribute columns are added by
        // ensure_sqlite_bot_internal_attributes in run_sqlite_bootstrap_tables;
        // version 14 only records progress.
        14 => Ok(()),
        15 => add_sqlite_group_participant_tags_schema(db).await,
        // SQLite stores session identifiers as unbounded TEXT, so version 16
        // records dialect parity with the MySQL/OceanBase VARCHAR expansion.
        16 => Ok(()),
        17 => add_sqlite_session_callback_lease_schema(db).await,
        18 => add_sqlite_state_machine_rerun_lineage_schema(db).await,
        _ => Ok(()),
    }
}

async fn add_sqlite_state_machine_rerun_lineage_schema(db: &dyn DbPlugin) -> DbResult<()> {
    if !table_exists(db, "bcs_state_machine_runs").await? {
        return Ok(());
    }
    let columns = sqlite_table_columns(db, "bcs_state_machine_runs").await?;
    for (name, definition) in [
        ("root_run_id", "TEXT DEFAULT NULL"),
        ("rerun_of", "TEXT DEFAULT NULL"),
        ("session_activation_count", "INTEGER DEFAULT NULL"),
    ] {
        if !columns.iter().any(|column| column == name) {
            db.execute(DbStatement::new(format!(
                "ALTER TABLE bcs_state_machine_runs ADD COLUMN {name} {definition}"
            )))
            .await?;
        }
    }
    db.execute(DbStatement::new(
        "CREATE UNIQUE INDEX IF NOT EXISTS uk_sm_run_rerun_of \
         ON bcs_state_machine_runs(env, rerun_of)",
    ))
    .await?;
    db.execute(DbStatement::new(
        "CREATE INDEX IF NOT EXISTS idx_sm_runs_root \
         ON bcs_state_machine_runs(env, root_run_id, created_at_ms)",
    ))
    .await?;
    Ok(())
}

async fn add_sqlite_session_callback_lease_schema(db: &dyn DbPlugin) -> DbResult<()> {
    if !table_exists(db, "bcs_group_sessions").await? {
        return Ok(());
    }
    let columns = sqlite_table_columns(db, "bcs_group_sessions").await?;
    for (name, definition) in [
        ("callback_lease_owner", "TEXT DEFAULT NULL"),
        ("callback_lease_token", "INTEGER DEFAULT NULL"),
        ("callback_lease_until_ms", "INTEGER DEFAULT NULL"),
    ] {
        if !columns.iter().any(|column| column == name) {
            db.execute(DbStatement::new(format!(
                "ALTER TABLE bcs_group_sessions ADD COLUMN {name} {definition}"
            )))
            .await?;
        }
    }
    db.execute(DbStatement::new(
        "CREATE INDEX IF NOT EXISTS idx_session_callback_recovery \
         ON bcs_group_sessions(env, session_kind, status, callback_status, \
         callback_lease_token, callback_lease_until_ms, session_id)",
    ))
    .await?;
    Ok(())
}

async fn migrate_sqlite_eventing_plaintext_endpoint(db: &dyn DbPlugin) -> DbResult<()> {
    if !table_exists(db, "bcs_event_subscription_revisions").await? {
        return Ok(());
    }
    let columns = sqlite_table_columns(db, "bcs_event_subscription_revisions").await?;
    if columns.iter().any(|column| column == "endpoint_url") {
        return Ok(());
    }
    if !columns.iter().any(|column| column == "endpoint_ciphertext") {
        return Err(DbError::InvalidInput(
            "unsupported bcs_event_subscription_revisions schema".to_string(),
        ));
    }
    let rows = db
        .query(DbStatement::new(
            "SELECT COUNT(*) AS revision_count FROM bcs_event_subscription_revisions",
        ))
        .await?;
    let revision_count: i64 = db_get_column(&rows[0], "revision_count")?;
    if revision_count != 0 {
        return Err(DbError::InvalidInput(
            "cannot automatically replace encrypted Event Subscription endpoints; disable and recreate existing Subscriptions first"
                .to_string(),
        ));
    }

    db.transaction(vec![
        DbTransactionStep::Execute(DbStatement::new(
            "DROP TABLE IF EXISTS bcs_event_subscription_revisions__plaintext_migration",
        )),
        DbTransactionStep::Execute(DbStatement::new(
            "CREATE TABLE bcs_event_subscription_revisions__plaintext_migration (
                subscription_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                event_filters_json TEXT NOT NULL,
                payload_mode TEXT NOT NULL,
                endpoint_url TEXT NOT NULL,
                request_timeout_ms INTEGER NOT NULL,
                activated_at TEXT NOT NULL,
                retired_at TEXT DEFAULT NULL,
                env TEXT NOT NULL,
                PRIMARY KEY(subscription_id, revision)
            )",
        )),
        DbTransactionStep::Execute(DbStatement::new(
            "DROP TABLE bcs_event_subscription_revisions",
        )),
        DbTransactionStep::Execute(DbStatement::new(
            "ALTER TABLE bcs_event_subscription_revisions__plaintext_migration
             RENAME TO bcs_event_subscription_revisions",
        )),
    ])
    .await?;
    Ok(())
}

async fn add_sqlite_group_participant_tags_schema(db: &dyn DbPlugin) -> DbResult<()> {
    if table_exists(db, "bcs_group_participants").await? {
        let columns = sqlite_table_columns(db, "bcs_group_participants").await?;
        if !columns.iter().any(|column| column == "tags_json") {
            db.execute(DbStatement::new(
                "ALTER TABLE bcs_group_participants ADD COLUMN tags_json TEXT DEFAULT NULL",
            ))
            .await?;
        }
    }
    Ok(())
}

async fn add_sqlite_human_input_output_metadata_schema(db: &dyn DbPlugin) -> DbResult<()> {
    if table_exists(db, "bcs_state_machine_node_runs").await? {
        let columns = sqlite_table_columns(db, "bcs_state_machine_node_runs").await?;
        let additions = [
            ("outcome", "TEXT DEFAULT NULL"),
            ("responded_by", "TEXT DEFAULT NULL"),
        ];
        for (name, definition) in additions {
            if !columns.iter().any(|column| column == name) {
                db.execute(DbStatement::new(format!(
                    "ALTER TABLE bcs_state_machine_node_runs ADD COLUMN {name} {definition}"
                )))
                .await?;
            }
        }
    }
    Ok(())
}

async fn add_sqlite_edge_permission_schema(db: &dyn DbPlugin) -> DbResult<()> {
    // Five edge-permission tables (idempotent; spec §3.1).
    for stmt in [
        "CREATE TABLE IF NOT EXISTS edge_grants (id INTEGER PRIMARY KEY AUTOINCREMENT, env TEXT NOT NULL, from_id TEXT NOT NULL, to_id TEXT NOT NULL, grant_kind TEXT NOT NULL, grant_ref_id INTEGER NOT NULL, rules TEXT, status TEXT NOT NULL DEFAULT 'approved', originator_policy_type TEXT NOT NULL DEFAULT 'any', originator_policy_data TEXT, gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uk_edge_from_to_env_ref ON edge_grants(from_id, to_id, env, grant_ref_id)",
        "CREATE INDEX IF NOT EXISTS idx_edge_from_env_status ON edge_grants(from_id, env, status)",
        "CREATE INDEX IF NOT EXISTS idx_edge_to_env_status ON edge_grants(to_id, env, status)",
        "CREATE TABLE IF NOT EXISTS permission_profiles (id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id TEXT NOT NULL, env TEXT NOT NULL, name TEXT NOT NULL DEFAULT 'default', description TEXT, rules_template TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, digest TEXT NOT NULL, is_default INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active', created_by TEXT NOT NULL, updated_by TEXT, gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uk_profile_bot_env_default ON permission_profiles(bot_id, env, is_default) WHERE status = 'active'",
        "CREATE INDEX IF NOT EXISTS idx_profile_bot_env ON permission_profiles(bot_id, env, status)",
        "CREATE TABLE IF NOT EXISTS permission_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL, edge_id INTEGER, env TEXT NOT NULL, from_id TEXT NOT NULL, to_id TEXT NOT NULL, request_kind TEXT NOT NULL, requested_ref_id INTEGER, requested_rules TEXT, message TEXT, status TEXT NOT NULL DEFAULT 'pending', decision_reason TEXT, created_by TEXT NOT NULL, decided_by TEXT, decided_at TEXT, gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uk_req_request_id ON permission_requests(request_id)",
        "CREATE INDEX IF NOT EXISTS idx_req_to_env_status ON permission_requests(to_id, env, status)",
        "CREATE INDEX IF NOT EXISTS idx_req_from_env_status ON permission_requests(from_id, env, status)",
        "CREATE INDEX IF NOT EXISTS idx_req_edge ON permission_requests(edge_id)",
        "CREATE TABLE IF NOT EXISTS capabilities (id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id TEXT NOT NULL, env TEXT NOT NULL, tool TEXT NOT NULL, operation TEXT, specifier_schema TEXT, source TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', raw_metadata TEXT, gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
        "CREATE INDEX IF NOT EXISTS idx_cap_bot_env ON capabilities(bot_id, env, status)",
        "CREATE TABLE IF NOT EXISTS authz_decision_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, env TEXT NOT NULL, task_id TEXT, run_id TEXT, from_id TEXT NOT NULL, to_id TEXT NOT NULL, originator TEXT, context_type TEXT NOT NULL, decision TEXT NOT NULL, reason_code TEXT NOT NULL, grant_refs TEXT NOT NULL, context_json TEXT, gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
        "CREATE INDEX IF NOT EXISTS idx_adl_env_from_to ON authz_decision_logs(env, from_id, to_id)",
    ] {
        db.execute(DbStatement::new(stmt)).await?;
    }
    // Edge tables: backfill gmt_create / gmt_modified audit columns for DBs
    // that created the tables before the audit-column requirement landed.
    // CREATE TABLE IF NOT EXISTS will not add columns to an existing table,
    // so ALTER them in idempotently (spec §3.1 — 建表要求 gmt_create/gmt_modified).
    for table in [
        "edge_grants",
        "permission_profiles",
        "permission_requests",
        "capabilities",
        "authz_decision_logs",
    ] {
        if !table_exists(db, table).await? {
            continue;
        }
        let columns = sqlite_table_columns(db, table).await?;
        for (name, definition) in [
            ("gmt_create", "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"),
            ("gmt_modified", "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"),
        ] {
            if !columns.iter().any(|column| column == name) {
                db.execute(DbStatement::new(format!(
                    "ALTER TABLE {table} ADD COLUMN {name} {definition}"
                )))
                .await?;
            }
        }
    }
    Ok(())
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

    let gmt_create_expr = sqlite_channel_audit_expr(has_gmt_create, has_created_at, "gmt_create");
    let gmt_modified_expr =
        sqlite_channel_audit_expr(has_gmt_modified, has_created_at, "gmt_modified");
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

    async fn index_exists(db: &dyn DbPlugin, index: &str) -> DbResult<bool> {
        let rows = db
            .query(DbStatement::with_params(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?",
                vec![DbValue::from(index)],
            ))
            .await?;
        Ok(!rows.is_empty())
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
    async fn fresh_sqlite_migrations_create_human_output_metadata() -> DbResult<()> {
        let db = LocalSqliteDbPlugin::new()?;

        run_sqlite_migrations(&db).await?;

        let columns = column_names(&db, "bcs_bots").await?;
        assert!(columns.iter().any(|column| column == "agent_code"));
        assert!(columns.iter().any(|column| column == "task_claim_mode"));
        assert!(columns.iter().any(|column| column == "task_dream_mode"));
        assert!(columns.iter().any(|column| column == "user_visibility"));
        assert!(columns.iter().any(|column| column == "friend_ext"));
        assert!(
            columns
                .iter()
                .any(|column| column == "friend_check_in_strategy")
        );
        let node_columns = column_names(&db, "bcs_state_machine_node_runs").await?;
        assert!(node_columns.iter().any(|column| column == "outcome"));
        assert!(node_columns.iter().any(|column| column == "responded_by"));
        let request_columns = column_names(&db, "bcs_human_input_requests").await?;
        assert!(
            request_columns
                .iter()
                .any(|column| column == "active_slot_key")
        );
        assert!(
            request_columns
                .iter()
                .any(|column| column == "provider_message_ref")
        );
        assert!(request_columns.iter().any(|column| column == "created_at"));
        let session_columns = column_names(&db, "bcs_group_sessions").await?;
        assert!(
            session_columns
                .iter()
                .any(|column| column == "callback_lease_owner")
        );
        assert!(
            session_columns
                .iter()
                .any(|column| column == "callback_lease_token")
        );
        assert!(
            session_columns
                .iter()
                .any(|column| column == "callback_lease_until_ms")
        );
        assert!(index_exists(&db, "idx_session_callback_recovery").await?);
        assert_eq!(
            migration_rows(&db).await?,
            vec![
                (1, "init_schema".to_string(), "sqlite".to_string()),
                (
                    2,
                    "channel_binding_audit_timestamps".to_string(),
                    "sqlite".to_string()
                ),
                (3, "add_organizations".to_string(), "sqlite".to_string()),
                (
                    4,
                    "add_session_collection".to_string(),
                    "sqlite".to_string()
                ),
                (
                    5,
                    "add_session_collection_timestamp".to_string(),
                    "sqlite".to_string()
                ),
                (6, "session_files".to_string(), "sqlite".to_string()),
                (
                    7,
                    "human_input_output_metadata".to_string(),
                    "sqlite".to_string()
                ),
                (
                    8,
                    "human_input_im_requests".to_string(),
                    "sqlite".to_string()
                ),
(9, "eventing".to_string(), "sqlite".to_string()),
                (
                    10,
                    "eventing_plaintext_endpoint".to_string(),
                    "sqlite".to_string()
                ),
                (
                    11,
                    "group_opening_message".to_string(),
                    "sqlite".to_string()
                ),
                (
                    12,
"add_bot_task_modes".to_string(),
                    "sqlite".to_string()
                ),
                (
                    13,
                    "edge_permission".to_string(),
                    "sqlite".to_string()
                ),
                (
                    14,
                    "add_bot_internal_attributes".to_string(),
                    "sqlite".to_string()
                ),
                (
                    15,
                    "group_participant_tags".to_string(),
                    "sqlite".to_string()
                ),
                (
                    16,
                    "expand_session_ids".to_string(),
                    "sqlite".to_string()
                ),
                (
                    17,
                    "session_callback_lease".to_string(),
                    "sqlite".to_string()
                ),
                (
                    18,
                    "state_machine_rerun_lineage".to_string(),
                    "sqlite".to_string()
                )
            ]
        );
        Ok(())
    }

    #[tokio::test]
    async fn sqlite_migration_plan_reports_all_versions() -> DbResult<()> {
        let db = LocalSqliteDbPlugin::new()?;

        let report = check_sqlite_migrations(&db).await?;

        assert_eq!(report.pending_versions.len(), 18);
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
        assert_eq!(report.pending_versions[3].version, 4);
        assert_eq!(report.pending_versions[3].name, "add_session_collection");
        assert_eq!(report.pending_versions[4].version, 5);
        assert_eq!(
            report.pending_versions[4].name,
            "add_session_collection_timestamp"
        );
        assert_eq!(report.pending_versions[5].version, 6);
        assert_eq!(report.pending_versions[5].name, "session_files");
        assert_eq!(report.pending_versions[6].version, 7);
        assert_eq!(
            report.pending_versions[6].name,
            "human_input_output_metadata"
        );
        assert_eq!(report.pending_versions[7].version, 8);
        assert_eq!(report.pending_versions[7].name, "human_input_im_requests");
        assert_eq!(report.pending_versions[8].version, 9);
        assert_eq!(report.pending_versions[8].name, "eventing");
        assert_eq!(report.pending_versions[9].version, 10);
        assert_eq!(
            report.pending_versions[9].name,
            "eventing_plaintext_endpoint"
        );
assert_eq!(report.pending_versions[10].version, 11);
        assert_eq!(report.pending_versions[10].name, "group_opening_message");
        assert_eq!(report.pending_versions[11].version, 12);
        assert_eq!(report.pending_versions[11].name, "add_bot_task_modes");
        assert_eq!(report.pending_versions[12].version, 13);
        assert_eq!(report.pending_versions[12].name, "edge_permission");
        assert_eq!(report.pending_versions[13].version, 14);
        assert_eq!(
            report.pending_versions[13].name,
            "add_bot_internal_attributes"
        );
        assert_eq!(report.pending_versions[14].version, 15);
        assert_eq!(
            report.pending_versions[14].name,
            "group_participant_tags"
        );
        assert_eq!(report.pending_versions[15].version, 16);
        assert_eq!(report.pending_versions[15].name, "expand_session_ids");
        assert_eq!(report.pending_versions[16].version, 17);
        assert_eq!(report.pending_versions[16].name, "session_callback_lease");
        assert_eq!(report.pending_versions[17].version, 18);
        assert_eq!(
            report.pending_versions[17].name,
            "state_machine_rerun_lineage"
        );
        Ok(())
    }

    #[tokio::test]
    async fn sqlite_callback_lease_migration_repairs_legacy_session_table() -> DbResult<()> {
        let db = LocalSqliteDbPlugin::new()?;
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_group_sessions (
                session_id TEXT NOT NULL,
                env TEXT NOT NULL DEFAULT 'prod',
                session_kind TEXT NOT NULL DEFAULT 'chat',
                status TEXT NOT NULL DEFAULT 'running',
                callback_status TEXT DEFAULT NULL
            )",
        ))
        .await?;

        add_sqlite_session_callback_lease_schema(&db).await?;
        add_sqlite_session_callback_lease_schema(&db).await?;

        let columns = column_names(&db, "bcs_group_sessions").await?;
        for expected in [
            "callback_lease_owner",
            "callback_lease_token",
            "callback_lease_until_ms",
        ] {
            assert!(columns.iter().any(|column| column == expected));
        }
        assert!(index_exists(&db, "idx_session_callback_recovery").await?);
        Ok(())
    }

    #[test]
    fn mysql_callback_lease_migration_adds_recovery_index() {
        let migration = include_str!("../../../../migrations/mysql/016_session_callback_lease.sql");
        assert!(migration.contains("ADD INDEX `idx_session_callback_recovery`"));
        for column in [
            "`env`",
            "`session_kind`",
            "`status`",
            "`callback_status`",
            "`callback_lease_token`",
            "`callback_lease_until_ms`",
            "`session_id`",
        ] {
            assert!(migration.contains(column), "missing index column {column}");
        }
    }

    #[tokio::test]
    async fn sqlite_rerun_lineage_migration_preserves_legacy_null_root() -> DbResult<()> {
        let db = LocalSqliteDbPlugin::new()?;
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_state_machine_runs (
                env TEXT NOT NULL,
                run_id TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL
            )",
        ))
        .await?;
        db.execute(DbStatement::new(
            "INSERT INTO bcs_state_machine_runs (env, run_id, created_at_ms) \
             VALUES ('test', 'legacy-run', 1)",
        ))
        .await?;

        add_sqlite_state_machine_rerun_lineage_schema(&db).await?;
        add_sqlite_state_machine_rerun_lineage_schema(&db).await?;

        let columns = column_names(&db, "bcs_state_machine_runs").await?;
        for expected in ["root_run_id", "rerun_of", "session_activation_count"] {
            assert!(columns.iter().any(|column| column == expected));
        }
        assert!(index_exists(&db, "uk_sm_run_rerun_of").await?);
        assert!(index_exists(&db, "idx_sm_runs_root").await?);
        let rows = db
            .query(DbStatement::new(
                "SELECT root_run_id FROM bcs_state_machine_runs WHERE run_id = 'legacy-run'",
            ))
            .await?;
        let legacy_root: Option<String> =
            bcs_db_api::db_get_column_opt(&rows[0], "root_run_id")?;
        assert_eq!(legacy_root, None);
        Ok(())
    }

    #[test]
    fn mysql_rerun_lineage_migration_adds_unique_direct_child_constraint() {
        let migration =
            include_str!("../../../../migrations/mysql/017_state_machine_rerun_lineage.sql");
        for column in ["`root_run_id`", "`rerun_of`", "`session_activation_count`"] {
            assert!(migration.contains(column), "missing rerun column {column}");
        }
        assert!(
            migration.contains("ADD UNIQUE INDEX `uk_sm_run_rerun_of` (`env`, `rerun_of`)")
        );
        assert!(migration.contains(
            "ADD INDEX `idx_sm_runs_root` (`env`, `root_run_id`, `created_at_ms`)"
        ));
        assert!(!migration.contains("SET `root_run_id` = `run_id`"));
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
                (3, "add_organizations".to_string(), "sqlite".to_string()),
                (
                    4,
                    "add_session_collection".to_string(),
                    "sqlite".to_string()
                ),
                (
                    5,
                    "add_session_collection_timestamp".to_string(),
                    "sqlite".to_string()
                ),
                (6, "session_files".to_string(), "sqlite".to_string()),
                (
                    7,
                    "human_input_output_metadata".to_string(),
                    "sqlite".to_string()
                ),
                (
                    8,
                    "human_input_im_requests".to_string(),
                    "sqlite".to_string()
                ),
(9, "eventing".to_string(), "sqlite".to_string()),
                (
                    10,
                    "eventing_plaintext_endpoint".to_string(),
                    "sqlite".to_string()
                ),
                (
                    11,
                    "group_opening_message".to_string(),
                    "sqlite".to_string()
                ),
                (
                    12,
"add_bot_task_modes".to_string(),
                    "sqlite".to_string()
                ),
                (
                    13,
                    "edge_permission".to_string(),
                    "sqlite".to_string()
                ),
                (
                    14,
                    "add_bot_internal_attributes".to_string(),
                    "sqlite".to_string()
                ),
                (
                    15,
                    "group_participant_tags".to_string(),
                    "sqlite".to_string()
                ),
                (
                    16,
                    "expand_session_ids".to_string(),
                    "sqlite".to_string()
                ),
                (
                    17,
                    "session_callback_lease".to_string(),
                    "sqlite".to_string()
                ),
                (
                    18,
                    "state_machine_rerun_lineage".to_string(),
                    "sqlite".to_string()
                )
            ]
        );
        Ok(())
    }

    #[tokio::test]
    async fn sqlite_eventing_database_upgrades_to_group_opening_message() -> DbResult<()> {
        let db = LocalSqliteDbPlugin::new()?;
        run_sqlite_migrations(&db).await?;
        db.execute(DbStatement::new(
            "DELETE FROM bcs_schema_migrations WHERE version = 11",
        ))
        .await?;
        db.execute(DbStatement::new(
            "ALTER TABLE bcs_groups DROP COLUMN opening_message_json",
        ))
        .await?;

        let before = check_sqlite_migrations(&db).await?;
        // Deleting only the v11 (group_opening_message) record leaves later
        // migrations applied, so the max applied version stays at the latest
        // schema version even though v11 is the sole pending re-apply.
        assert_eq!(before.current_version, Some(sqlite_target_version()));
        assert_eq!(
            before
                .pending_versions
                .iter()
                .map(|migration| (migration.version, migration.name.as_str()))
                .collect::<Vec<_>>(),
            vec![(11, "group_opening_message")]
        );

        run_sqlite_migrations(&db).await?;

        assert!(
            column_names(&db, "bcs_groups")
                .await?
                .iter()
                .any(|column| column == "opening_message_json")
        );
        // group_opening_message is no longer the tail migration (task_modes at v12
        // follows it), so assert it was re-applied as the version-11 row rather than
        // as the last row. The column check above already proves the migration
        // re-added opening_message_json; this row check pins it to the right version.
        assert!(migration_rows(&db)
            .await?
            .iter()
            .any(|(version, name, _)| *version == 11 && name == "group_opening_message"));
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

    #[tokio::test]
    async fn sqlite_bootstrap_adds_internal_attributes_to_legacy_bots() -> DbResult<()> {
        let db = LocalSqliteDbPlugin::new()?;
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_bots (bot_uuid TEXT NOT NULL, env TEXT NOT NULL, PRIMARY KEY (bot_uuid, env))",
        ))
        .await?;
        db.execute(DbStatement::new(
            "INSERT INTO bcs_bots (bot_uuid, env) VALUES ('legacy-bot', 'dev')",
        ))
        .await?;

        ensure_sqlite_bot_internal_attributes(&db).await?;

        let columns = column_names(&db, "bcs_bots").await?;
        assert!(columns.iter().any(|column| column == "user_visibility"));
        assert!(columns.iter().any(|column| column == "friend_ext"));
        assert!(
            columns
                .iter()
                .any(|column| column == "friend_check_in_strategy")
        );
        let rows = db
            .query(DbStatement::new(
                "SELECT user_visibility, friend_check_in_strategy FROM bcs_bots WHERE bot_uuid = 'legacy-bot'",
            ))
            .await?;
        let row = rows.first().expect("legacy Bot row");
        assert_eq!(
            db_get_column::<String>(row, "user_visibility")?,
            "protected"
        );
        assert_eq!(
            db_get_column::<String>(row, "friend_check_in_strategy")?,
            "APPROVAL"
        );
        Ok(())
    }

    // 建表要求: every edge-permission table must carry gmt_create / gmt_modified.
    #[tokio::test]
    async fn fresh_migrations_create_edge_tables_with_gmt_audit_columns() -> DbResult<()> {
        let db = LocalSqliteDbPlugin::new()?;
        run_sqlite_migrations(&db).await?;
        for table in [
            "edge_grants",
            "permission_profiles",
            "permission_requests",
            "capabilities",
            "authz_decision_logs",
        ] {
            let columns = column_names(&db, table).await?;
            assert!(
                columns.iter().any(|c| c == "gmt_create"),
                "{table} missing gmt_create"
            );
            assert!(
                columns.iter().any(|c| c == "gmt_modified"),
                "{table} missing gmt_modified"
            );
        }
        let request_columns = column_names(&db, "permission_requests").await?;
        assert!(request_columns.iter().any(|c| c == "request_id"));
        Ok(())
    }

    // Repair path: a legacy DB that created the edge tables without gmt_* must
    // get the audit columns backfilled by the idempotent ALTER in the migration.
    #[tokio::test]
    async fn edge_table_audit_columns_backfilled_for_legacy_db() -> DbResult<()> {
        let db = LocalSqliteDbPlugin::new()?;
        // Legacy shape: edge_grants as built before the gmt_* audit-column
        // requirement landed (current bigint PK + real columns, minus gmt_create/gmt_modified).
        db.execute(DbStatement::new(
            "CREATE TABLE edge_grants (id INTEGER PRIMARY KEY AUTOINCREMENT, env TEXT NOT NULL, \
             from_id TEXT NOT NULL, to_id TEXT NOT NULL, grant_kind TEXT NOT NULL, \
             grant_ref_id INTEGER NOT NULL, rules TEXT, status TEXT NOT NULL DEFAULT 'approved', \
             originator_policy_type TEXT NOT NULL DEFAULT 'any', originator_policy_data TEXT)",
        ))
        .await?;
        // add_sqlite_edge_permission_schema also ALTERs bcs_bots; give it a stub.
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_bots (bot_uuid TEXT NOT NULL, env TEXT NOT NULL, \
             PRIMARY KEY (bot_uuid, env))",
        ))
        .await?;
        // Re-running the edge-permission migration (v9) must ADD the gmt columns
        // via the idempotent ALTER repair (CREATE TABLE IF NOT EXISTS is a no-op).
        add_sqlite_edge_permission_schema(&db).await?;
        let columns = column_names(&db, "edge_grants").await?;
        assert!(columns.iter().any(|c| c == "gmt_create"));
        assert!(columns.iter().any(|c| c == "gmt_modified"));
        Ok(())
    }
}

#[cfg(test)]
mod collection_migration_tests {
    use super::*;
    use bcs_db_local::LocalSqliteDbPlugin;

    async fn fresh_db() -> LocalSqliteDbPlugin {
        let db = LocalSqliteDbPlugin::new().expect("open in-memory sqlite");
        run_sqlite_bootstrap_tables(&db).await.expect("bootstrap");
        run_sqlite_versioned_migrations(&db)
            .await
            .expect("versioned");
        db
    }

    #[tokio::test]
    async fn fresh_db_has_session_participants_collected_column() {
        let db = fresh_db().await;
        let cols = sqlite_table_columns(&db, "bcs_session_participants")
            .await
            .unwrap();
        assert!(
            cols.iter().any(|c| c == "collected"),
            "bcs_session_participants must have a collected column on fresh DB; got {cols:?}"
        );
    }

    #[tokio::test]
    async fn fresh_db_has_session_participants_collected_at_column() {
        let db = fresh_db().await;
        let cols = sqlite_table_columns(&db, "bcs_session_participants")
            .await
            .unwrap();
        assert!(
            cols.iter().any(|c| c == "collected_at"),
            "bcs_session_participants must have a collected_at column on fresh DB; got {cols:?}"
        );
    }

    #[tokio::test]
    async fn ensure_function_adds_collected_to_legacy_table() {
        let db = LocalSqliteDbPlugin::new().expect("open in-memory sqlite");
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_session_participants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                session_id TEXT NOT NULL,
                group_id TEXT NOT NULL,
                bot_uuid TEXT NOT NULL,
                role TEXT NOT NULL,
                env TEXT NOT NULL DEFAULT 'prod'
            )",
        ))
        .await
        .unwrap();
        run_sqlite_bootstrap_tables(&db)
            .await
            .expect("bootstrap repairs legacy table");
        let cols = sqlite_table_columns(&db, "bcs_session_participants")
            .await
            .unwrap();
        assert!(
            cols.iter().any(|c| c == "collected"),
            "ensure function must add collected to legacy bcs_session_participants; got {cols:?}"
        );
        assert!(
            cols.iter().any(|c| c == "collected_at"),
            "ensure function must add collected_at to legacy bcs_session_participants; got {cols:?}"
        );
    }
}

#[cfg(test)]
mod eventing_migration_tests {
    use super::*;
    use bcs_db_local::LocalSqliteDbPlugin;

    const MYSQL_EVENTING_MIGRATION: &str =
        include_str!("../../../../migrations/mysql/009_eventing.sql");
    const MYSQL_GROUP_OPENING_MIGRATION: &str =
        include_str!("../../../../migrations/mysql/010_group_opening_message.sql");

    const EVENTING_TABLES: &[&str] = &[
        "bcs_event_subscriptions",
        "bcs_event_subscription_revisions",
        "bcs_event_scope_epochs",
        "bcs_event_streams",
        "bcs_events",
        "bcs_event_fanout_targets",
        "bcs_event_deliveries",
        "bcs_event_delivery_attempts",
        "bcs_event_subscription_audits",
    ];

    async fn sqlite_index_names(db: &dyn DbPlugin) -> DbResult<Vec<String>> {
        let rows = db
            .query(DbStatement::new(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_event_%' ORDER BY name",
            ))
            .await?;
        rows.into_iter()
            .map(|row| db_get_column(&row, "name"))
            .collect()
    }

    #[tokio::test]
    async fn empty_encrypted_revision_schema_migrates_to_plaintext_endpoint() -> DbResult<()> {
        let db = LocalSqliteDbPlugin::new()?;
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_event_subscription_revisions (
                subscription_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                event_filters_json TEXT NOT NULL,
                payload_mode TEXT NOT NULL,
                endpoint_ciphertext BLOB NOT NULL,
                endpoint_key_id TEXT NOT NULL,
                endpoint_key_version INTEGER NOT NULL,
                endpoint_nonce BLOB NOT NULL,
                endpoint_auth_tag BLOB NOT NULL,
                secret_ciphertext BLOB NOT NULL,
                secret_key_id TEXT NOT NULL,
                secret_key_version INTEGER NOT NULL,
                secret_nonce BLOB NOT NULL,
                secret_auth_tag BLOB NOT NULL,
                request_timeout_ms INTEGER NOT NULL,
                activated_at TEXT NOT NULL,
                retired_at TEXT DEFAULT NULL,
                env TEXT NOT NULL,
                PRIMARY KEY(subscription_id, revision)
            )",
        ))
        .await?;

        migrate_sqlite_eventing_plaintext_endpoint(&db).await?;

        let columns = sqlite_table_columns(&db, "bcs_event_subscription_revisions").await?;
        assert!(columns.iter().any(|column| column == "endpoint_url"));
        assert!(!columns.iter().any(|column| column == "endpoint_ciphertext"));
        assert!(!columns.iter().any(|column| column == "secret_ciphertext"));
        Ok(())
    }

    #[tokio::test]
    async fn fresh_sqlite_schema_contains_all_eventing_tables_columns_and_indexes() -> DbResult<()>
    {
        let db = LocalSqliteDbPlugin::new()?;
        run_sqlite_migrations(&db).await?;

        let group_columns = sqlite_table_columns(&db, "bcs_groups").await?;
        assert!(
            group_columns
                .iter()
                .any(|column| column == "opening_message_json"),
            "bcs_groups missing opening_message_json"
        );

        for table in EVENTING_TABLES {
            assert!(
                table_exists(&db, table).await?,
                "missing Eventing table {table}"
            );
        }
        for (table, required_columns) in [
            (
                "bcs_event_subscriptions",
                &[
                    "subscription_id",
                    "scope_type",
                    "scope_id",
                    "current_revision",
                    "env",
                ][..],
            ),
            (
                "bcs_event_subscription_revisions",
                &["subscription_id", "revision", "endpoint_url"][..],
            ),
            (
                "bcs_events",
                &[
                    "event_id",
                    "producer_key",
                    "stream_key",
                    "sequence",
                    "retention_until",
                ][..],
            ),
            (
                "bcs_event_fanout_targets",
                &[
                    "target_id",
                    "purpose",
                    "depends_on_target_id",
                    "replay_request_id",
                    "lease_owner",
                    "lease_until",
                ][..],
            ),
            (
                "bcs_event_deliveries",
                &[
                    "delivery_id",
                    "payload_bytes",
                    "payload_sha256",
                    "lease_owner",
                    "lease_until",
                ][..],
            ),
            (
                "bcs_event_delivery_attempts",
                &["delivery_id", "attempt_no", "result", "worker_id"][..],
            ),
        ] {
            let columns = sqlite_table_columns(&db, table).await?;
            for required in required_columns {
                assert!(
                    columns.iter().any(|column| column == required),
                    "{table} missing column {required}"
                );
            }
        }

        let indexes = sqlite_index_names(&db).await?;
        for required in [
            "idx_event_subscription_scope",
            "idx_event_subscription_status",
            "idx_event_claim_due",
            "idx_event_strict_lane",
            "idx_event_retention",
        ] {
            assert!(
                indexes.iter().any(|index| index == required),
                "missing index {required}"
            );
        }
        Ok(())
    }

    #[tokio::test]
    async fn sqlite_scope_epoch_is_scope_local_and_has_no_global_offset_table() -> DbResult<()> {
        let db = LocalSqliteDbPlugin::new()?;
        run_sqlite_migrations(&db).await?;

        let columns = db
            .query(DbStatement::new(
                "PRAGMA table_info(bcs_event_scope_epochs)",
            ))
            .await?;
        let primary_key = columns
            .into_iter()
            .filter_map(|row| {
                let order: i64 = db_get_column(&row, "pk").ok()?;
                let name: String = db_get_column(&row, "name").ok()?;
                (order > 0).then_some((order, name))
            })
            .collect::<Vec<_>>();
        assert_eq!(
            primary_key,
            vec![
                (1, "env".to_string()),
                (2, "scope_type".to_string()),
                (3, "scope_id".to_string()),
            ]
        );
        assert!(!table_exists(&db, "bcs_event_offsets").await?);
        assert!(!table_exists(&db, "bcs_event_global_cursor").await?);
        Ok(())
    }

    #[test]
    fn mysql_eventing_migration_is_additive_scope_local_and_indexed() {
        for table in EVENTING_TABLES {
            assert!(
                MYSQL_EVENTING_MIGRATION.contains(&format!("CREATE TABLE IF NOT EXISTS `{table}`")),
                "missing MySQL Eventing table {table}"
            );
        }
        assert!(MYSQL_EVENTING_MIGRATION.contains("PRIMARY KEY (`env`, `scope_type`, `scope_id`)"));
        assert!(MYSQL_EVENTING_MIGRATION.contains("KEY `idx_event_claim_due`"));
        assert!(MYSQL_EVENTING_MIGRATION.contains("KEY `idx_event_strict_lane`"));
        assert!(MYSQL_EVENTING_MIGRATION.contains("KEY `idx_event_retention`"));
        assert!(!MYSQL_EVENTING_MIGRATION.contains("bcs_event_offsets"));
        assert!(!MYSQL_EVENTING_MIGRATION.contains("bcs_event_global_cursor"));
        assert!(!MYSQL_EVENTING_MIGRATION.contains("ALTER TABLE"));
        assert!(
            MYSQL_GROUP_OPENING_MIGRATION
                .contains("ADD COLUMN `opening_message_json` text DEFAULT NULL")
        );
    }
}
