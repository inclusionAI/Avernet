-- Existing installations only; fresh schemas include this nullable column.
-- Standard ADD COLUMN syntax supports MySQL, PostgreSQL and SQLite.
-- Back up first and check the column is absent; this is a one-time migration.
ALTER TABLE ac_genome_promotion ADD COLUMN authorization TEXT NULL;
