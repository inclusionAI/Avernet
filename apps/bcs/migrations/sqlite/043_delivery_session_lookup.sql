-- 043_delivery_session_lookup: find a Session's unfinished Send deliveries
-- by Session identity (Session deletion cancels every lane, including Bots
-- that are no longer participants). Purely additive index.
CREATE INDEX IF NOT EXISTS idx_delivery_session_message ON bcs_message_deliveries (env, session_id, source_message_id, kind, status);
