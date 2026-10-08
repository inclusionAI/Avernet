//! Standalone ChatRun table snapshot through SQLite migration 032.
//! Used only to create an absent table in repository tests/local standalone use.
//! Existing databases must be upgraded by the bootstrap migration runner;
//! never replay ChatRun ALTER statements over this snapshot.

pub(super) const CREATE_CHAT_RUNS: &str = "CREATE TABLE IF NOT EXISTS bcs_chat_runs (\
            id INTEGER PRIMARY KEY AUTOINCREMENT,\
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,\
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,\
            env TEXT NOT NULL,\
            run_id TEXT NOT NULL,\
            bot_uuid TEXT NOT NULL,\
            from_bot_id TEXT NOT NULL,\
            session_key TEXT NOT NULL,\
            delivery_id TEXT,\
            source_message_id TEXT,\
            state TEXT NOT NULL,\
            accumulated_content TEXT,\
            error_message TEXT,\
            original_request TEXT,\
            completed_at_ms INTEGER,\
            expires_at_ms INTEGER NOT NULL,\
            version INTEGER NOT NULL,\
            content_truncated INTEGER NOT NULL DEFAULT 0,\
            client TEXT,\
            response_mode TEXT NOT NULL,\
            completion_policy TEXT NOT NULL,\
            delivery_ack_at_ms INTEGER,\
            CONSTRAINT uk_env_run_id UNIQUE (env, run_id))";
