# Skill Bot environment

Skill registration carries the selected directory entry's `botEnv` through
`GET /api/evolve/skill-assets/available` and `POST /api/evolve/skill-assets`.
`BotSkillGateway` forwards that optional field to the host, which filters the
request user's accessible Bots by ID and exact environment before selecting the
existing Skill API and file transport. An explicit environment is never a grant
of access and must not fall back to another environment.

New assets store `ce_skill_assets.bot_env`. Task defaults, direct and advanced
launches, candidate snapshots, edits, uploads, rollbacks, and acceptance use the
saved environment. Task requests specifying a different environment are rejected
before dispatch. Bot ID, external Skill ID, permissions, business Skills, and
native Bot evolution flows keep their existing semantics.

Legacy assets remain NULL; no environment is guessed or backfilled. Legacy
callers may omit `botEnv`; the host still rejects an ambiguous Bot lookup.
`uk_owner_bot_skill` is unchanged: different Skills have different Skill IDs;
Bot ID reuse is not evidence of duplicate Skill IDs.

## Managed database deployment

Fresh installations use `../evolve-schema-v135/01-new-tables.mysql.sql`, which
already includes `bot_env`; do not apply the ALTER below to those new tables.

Before deploying the matching public and internal CW changes, add one nullable
column to the existing table. No indexes are added or changed:

```sql
ALTER TABLE `ce_skill_assets`
  ADD COLUMN `bot_env` varchar(32) DEFAULT NULL
  COMMENT '来源Bot环境；历史未记录时为空';
```

SQLite applies migration v139 at startup. Managed databases that prohibit runtime
DDL require the SQL above in advance. Reverting code does not require removing
the column or deleting any assets/versions. No Runner/Skill package update is
needed. The internal host changes only CW Skill environment selection; it does
not add or modify upstream backend endpoints.
