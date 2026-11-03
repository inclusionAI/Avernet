-- Optional pre-production initialization only. Never overwrite an operator's
-- existing configuration. Missing configuration remains disabled in all envs.
INSERT INTO ac_config_category (category, category_name, env, creator, modifier, gmt_create, gmt_modified)
SELECT 'dws_hosting', 'DWS hosting', 'pre', '165137', '165137', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
WHERE NOT EXISTS (SELECT 1 FROM ac_config_category WHERE category = 'dws_hosting' AND env = 'pre');

INSERT INTO ac_config_item (parent_id, config_key, config_value, description, creator, modifier, gmt_create, gmt_modified)
SELECT c.id, 'rollout', '{"mode":"allowlist","staff_ids":["165137"]}',
       'DWS pre-production rollout', '165137', '165137', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
FROM ac_config_category c
WHERE c.category = 'dws_hosting' AND c.env = 'pre'
AND NOT EXISTS (SELECT 1 FROM ac_config_item i WHERE i.parent_id = c.id AND i.config_key = 'rollout');
