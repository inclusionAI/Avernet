ALTER TABLE bcs_messages
    ADD COLUMN IF NOT EXISTS owner_bot_id VARCHAR(256) DEFAULT NULL COMMENT '消息历史所属视角 Bot ID';

CREATE INDEX idx_messages_session_owner_created
    ON bcs_messages(session_id, owner_bot_id, created_at, session_seq);
