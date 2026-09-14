-- Stable bounded Inject selection, separate from mutable downstream identity.
ALTER TABLE bcs_message_deliveries ADD COLUMN context_selection_json TEXT DEFAULT NULL;
CREATE INDEX idx_delivery_bound_seq ON bcs_message_deliveries (env, bound_to_delivery_id, status, source_session_seq, delivery_id);
