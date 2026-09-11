CREATE INDEX idx_message_run_segments ON bcs_messages (env, session_id, sender_id, run_id, session_seq);
