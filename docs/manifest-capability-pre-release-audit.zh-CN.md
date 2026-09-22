# Manifest Capability 发布前只读审计

适用版本：`REL20260922`。本清单只读，不清理数据、不重放 Apply、不切换开关。执行时必须固定 `avernet_tenant` 与 `env`，保存完整结果和执行时间；发现异常后由发布负责人逐条接受或另开清理任务。

## 1. 已保存 Manifest

```sql
SELECT avernet_tenant, env, entity_id, bot_id, schema_version,
       size_bytes, modifier, gmt_modified
FROM ac_bot_config_manifest
WHERE avernet_tenant = :tenant AND env = :env
ORDER BY entity_id, bot_id;
```

逐条读取 `document`，记录 `manifest.skills` / `manifest.mcp` 是缺失、空数组还是非空数组。不要把删除文档解释成空数组 Apply。

## 2. Direct Installation、SkillSet 与 Default exclusion

```sql
SELECT i.owner_id, i.bot_id, i.skill_id, s.name, s.git_path
FROM ac_bot_skill_installation i
JOIN ac_skill s ON s.id = i.skill_id
WHERE i.avernet_tenant = :tenant AND i.env = :env
ORDER BY i.owner_id, i.bot_id, i.skill_id;

SELECT ss.user_id AS owner_id, ss.bolt_id AS bot_id, ss.id AS skill_set_id,
       ss.is_default, ss.is_active, m.skill_id
FROM ac_skill_set ss
JOIN ac_skill_set_skill m ON m.skill_set_id = ss.id
WHERE ss.avernet_tenant = :tenant AND ss.env = :env
ORDER BY owner_id, bot_id, skill_set_id, m.skill_id;

SELECT e.user_id AS owner_id, e.bot_id, e.skill_set_id, e.skill_id
FROM ac_default_skillset_skill_exclusion e
JOIN ac_skill_set ss
  ON ss.id = e.skill_set_id
 AND ss.avernet_tenant = e.avernet_tenant
WHERE e.avernet_tenant = :tenant AND ss.env = :env
ORDER BY owner_id, bot_id, e.skill_set_id, e.skill_id;
```

MCP 使用同一维度读取 `ac_bot_mcp_installation`、`ac_bot_mcp_config`、`ac_skill_set_mcp` 与 `ac_default_skillset_mcp_exclusion`。重点标记同一 Bot/Capability 同时存在 ordinary membership、Default exclusion、Installation 或 Bot override 的组合。

## 3. Bot-owned Local Skill 资产

```sql
SELECT user_id AS owner_id, bolt_id AS bot_id, id AS skill_id,
       name, git_path, status, gmt_modified
FROM ac_skill
WHERE avernet_tenant = :tenant AND env = :env
  AND user_id IS NOT NULL AND bolt_id IS NOT NULL
  AND git_path LIKE 'local://%'
ORDER BY owner_id, bot_id, name, skill_id;
```

将结果与 Installation、所有 active/inactive SkillSet membership 对齐。孤立 Local 行不是自动可删结论；还要确认物理包存在性。包状态无法确定时禁止删除数据库行。

## 4. exclusion + Installation 组合

```sql
SELECT e.user_id AS owner_id, e.bot_id, e.skill_set_id,
       e.server_code, i.id AS installation_id, c.id AS override_id
FROM ac_default_skillset_mcp_exclusion e
JOIN ac_skill_set ss
  ON ss.id = e.skill_set_id
 AND ss.avernet_tenant = e.avernet_tenant
LEFT JOIN ac_bot_mcp_installation i
  ON i.avernet_tenant = e.avernet_tenant
 AND i.env = :env
 AND i.owner_id = e.user_id
 AND i.bot_id = e.bot_id
 AND i.server_code = e.server_code
LEFT JOIN ac_bot_mcp_config c
  ON c.avernet_tenant = e.avernet_tenant
 AND c.env = :env
 AND c.owner_id = e.user_id
 AND c.bot_id = e.bot_id
 AND c.server_code = e.server_code
WHERE e.avernet_tenant = :tenant AND ss.env = :env
ORDER BY owner_id, bot_id, e.server_code;
```

`exclusion + Installation` 是合法 Direct 状态，不得按“脏数据”自动清除。只有 exclusion、没有 Installation 表示继承供应被抑制；只有 override、没有有效显式或依赖供应需要单独确认来源。

## 5. 失败与未完成 Apply

```sql
SELECT apply_id, entity_id, bot_id, apply_trigger, status,
       actor, started_at, finished_at
FROM ac_bot_config_manifest_apply
WHERE avernet_tenant = :tenant AND env = :env
  AND status IN ('FAILED', 'PARTIAL', 'RUNNING')
ORDER BY started_at DESC;
```

读取 `report`，分别统计失败 category、`partially_written`、Local cleanup 失败及 Runtime Projection note。`PARTIAL` 的 Local 资产必须保留在下一次 Apply 可发现的 catalog 中。

## 通过条件

- 每个保存文档的 destructive section 状态已确认。
- 每个 exclusion+Installation 组合已标记为接受的 Direct 或待清理异常。
- 没有无法归属的 Bot override；dependency-only override 的保留理由明确。
- 每个 Local 资产的引用与物理存在性可解释。
- FAILED/PARTIAL/stale RUNNING Apply 已逐条接受或建立清理任务。

审计通过只说明控制面数据可进入新语义，不等于 PR 已合并、镜像已部署或 Runtime 已收敛。
