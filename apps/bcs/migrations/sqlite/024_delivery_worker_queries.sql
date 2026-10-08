ALTER TABLE bcs_message_deliveries ADD COLUMN downstream_run_id TEXT;
UPDATE bcs_message_deliveries SET downstream_run_id = json_extract(transport_context_json, '$.downstream_run_id') WHERE transport_context_json IS NOT NULL AND json_type(transport_context_json, '$.downstream_run_id') = 'text';
CREATE INDEX idx_delivery_run_alias ON bcs_message_deliveries (env, target_bot_id, downstream_run_id);
CREATE INDEX idx_delivery_queued_bots ON bcs_message_deliveries (env, kind, status, target_bot_id);
CREATE INDEX idx_delivery_heads ON bcs_message_deliveries (env, target_bot_id, kind, status, session_id, source_session_seq);
CREATE INDEX idx_delivery_expire ON bcs_message_deliveries (env, status, expire_at_ms, delivery_id);
CREATE INDEX idx_delivery_run_deadline ON bcs_message_deliveries (env, status, run_deadline_at_ms, delivery_id);
CREATE INDEX idx_delivery_cancel_deadline ON bcs_message_deliveries (env, status, cancel_deadline_at_ms, delivery_id);
