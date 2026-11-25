-- PostgreSQL (including 14): safe parsing of legacy ac_bots.ext TEXT.
-- Run as the schema owner when automatic schema bootstrap is disabled.
-- Runtime needs only schema USAGE, function EXECUTE, and ordinary table SELECT.
-- SECURITY INVOKER does not grant access to any other data or privilege.
CREATE OR REPLACE FUNCTION ac_expert_try_jsonb(value text)
RETURNS jsonb
LANGUAGE plpgsql
IMMUTABLE STRICT SECURITY INVOKER
SET search_path = pg_catalog
AS $expert_json$
BEGIN
  RETURN value::jsonb;
EXCEPTION
  WHEN invalid_text_representation OR numeric_value_out_of_range OR untranslatable_character THEN
    RETURN NULL;
END;
$expert_json$;
