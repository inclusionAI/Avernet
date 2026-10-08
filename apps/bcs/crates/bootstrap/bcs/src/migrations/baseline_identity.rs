//! Identity DDL (bot registry / friends / providers / organizations / user identities / identity links).
//!
//! Each section is one `&[&str]` group that the migration runner
//! iterates in the order given by the facade's master ordered list
//! — order matters because some sections depend on earlier ones.

pub(super) const SCHEMA_MIGRATIONS: &[&str] = &[
    // ── schema_migrations ─────────────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_schema_migrations (
        version INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        dialect TEXT NOT NULL,
        checksum TEXT NOT NULL,
        applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )",
];

pub(super) const BOTS: &[&str] = &[
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
];

pub(super) const FRIENDSHIPS: &[&str] = &[
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
];

pub(super) const FRIEND_REQUESTS: &[&str] = &[
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
];

pub(super) const INVITE_CODES: &[&str] = &[
    // ── invite_codes ─────────────────────────────────────
    "CREATE TABLE IF NOT EXISTS bcs_invite_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code_hash TEXT NOT NULL UNIQUE,
        code_hint TEXT NOT NULL,
        status TEXT NOT NULL,
        bound_user_id TEXT NULL UNIQUE,
        bound_at INTEGER NULL,
        created_by TEXT NULL,
        env TEXT NOT NULL,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )",
    "CREATE INDEX IF NOT EXISTS idx_invite_codes_env_status ON bcs_invite_codes(env, status)",
];

pub(super) const ACTOR_RELATIONS: &[&str] = &[
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
];

pub(super) const PROVIDERS: &[&str] = &[
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
];

pub(super) const ORGANIZATIONS: &[&str] = &[
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
];

pub(super) const ORGANIZATION_MEMBERS: &[&str] = &[
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
];

pub(super) const PROVIDER_BOT_BINDINGS: &[&str] = &[
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
];

pub(super) const PROVIDER_CREDENTIALS: &[&str] = &[
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
];

pub(super) const USER_IDENTITIES: &[&str] = &[
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
];

pub(super) const IDENTITY_LINKS: &[&str] = &[
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
];
