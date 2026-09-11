CREATE INDEX idx_delivery_pending_abort ON bcs_message_deliveries (env, status, abort_request_id, delivery_id);
