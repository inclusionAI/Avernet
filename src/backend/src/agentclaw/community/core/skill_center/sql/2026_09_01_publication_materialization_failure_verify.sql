-- ODC disallows information_schema cross-database queries. Verify these
-- results after the three ordered ALTER statements above have completed.
SHOW COLUMNS FROM ac_skill_publication_attempt
  LIKE 'materialization_retry_count';

SHOW CREATE TABLE ac_skill_publication_attempt;
