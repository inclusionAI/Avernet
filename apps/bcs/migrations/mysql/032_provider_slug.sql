ALTER TABLE bcs_providers ADD COLUMN slug VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL;
CREATE UNIQUE INDEX uk_bcs_providers_env_slug ON bcs_providers (env, slug);
