//! Delivery DDL (channel bindings / messages / session files / eventing).
//!
//! Each section is one `&[&str]` group that the migration runner
//! iterates in the order given by the facade's master ordered list
//! — order matters because some sections depend on earlier ones.

pub(super) const CHANNEL_BINDINGS: &[&str] = &[
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
];

pub(super) const CHANNEL_CONVERSATIONS: &[&str] = &[
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
];

pub(super) const CHANNEL_IM_PARTICIPANTS: &[&str] = &[
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
];

pub(super) const HUMAN_INPUT_IM_REQUESTS: &[&str] = &[
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
];

pub(super) const MESSAGES: &[&str] = &[
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
        visibility_domain TEXT DEFAULT NULL,
        audience_kind TEXT DEFAULT NULL,
        audience_actor_ids_json TEXT DEFAULT NULL,
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
    "CREATE INDEX IF NOT EXISTS idx_messages_session_audience_created ON bcs_messages(session_id, visibility_domain, audience_kind, created_at, session_seq)",
];

pub(super) const SESSION_FILES: &[&str] = &[
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
];

pub(super) const PUBLIC_EVENTING: &[&str] = &[
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
