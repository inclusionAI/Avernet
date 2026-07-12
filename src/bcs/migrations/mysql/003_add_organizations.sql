CREATE TABLE bcs_organizations (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  env VARCHAR(64) NOT NULL,
  code VARCHAR(128) NOT NULL,
  name VARCHAR(256) NOT NULL,
  description TEXT DEFAULT NULL,
  managing_provider_id VARCHAR(256) NOT NULL,
  disabled TINYINT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uk_org_env_code (env, code),
  KEY idx_org_env_disabled (env, disabled),
  KEY idx_org_env_provider (env, managing_provider_id)
);

CREATE TABLE bcs_organization_members (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  env VARCHAR(64) NOT NULL,
  organization_code VARCHAR(128) NOT NULL,
  bot_uuid VARCHAR(256) NOT NULL,
  role VARCHAR(128) DEFAULT NULL,
  disabled TINYINT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uk_org_member (env, organization_code, bot_uuid),
  KEY idx_member_bot (env, bot_uuid),
  KEY idx_member_org_disabled_role (env, organization_code, disabled, role)
);
