-- Additive management metadata; legacy rows/inserts remain SQL NULL.
ALTER TABLE bcs_bots ADD COLUMN catalog_labels JSON NULL;
ALTER TABLE bcs_group_agent_cards ADD COLUMN catalog_labels JSON NULL;
