SELECT table_name
  FROM information_schema.tables
 WHERE table_schema = DATABASE()
   AND table_name IN (
     'ac_skill_center_reference_batch',
     'ac_skill_center_reference_item'
   );

SELECT table_name, index_name
  FROM information_schema.statistics
 WHERE table_schema = DATABASE()
   AND (
     (table_name = 'ac_skill' AND index_name = 'idx_skill_center_public_locator')
     OR (table_name = 'ac_skill_center_reference_batch'
         AND index_name IN ('uk_sc_reference_request', 'uk_sc_reference_idempotency'))
     OR (table_name = 'ac_skill_center_reference_item'
         AND index_name IN ('uk_sc_reference_id', 'uk_sc_reference_code',
                            'idx_sc_reference_collection',
                            'idx_sc_reference_request_items'))
   );
