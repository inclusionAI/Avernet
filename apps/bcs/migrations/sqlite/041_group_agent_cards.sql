-- Equivalent template storage for the local Group repository.
-- The repository explicitly maintains gmt_modified on template updates.
CREATE TABLE IF NOT EXISTS bcs_group_agent_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    env TEXT NOT NULL,
    group_id TEXT NOT NULL,
    card_template TEXT NOT NULL,
    UNIQUE (env, group_id)
);
