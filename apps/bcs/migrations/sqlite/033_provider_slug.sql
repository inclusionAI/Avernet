ALTER TABLE bcs_providers ADD COLUMN slug TEXT NULL;
CREATE UNIQUE INDEX uk_bcs_providers_env_slug ON bcs_providers (env, slug);
