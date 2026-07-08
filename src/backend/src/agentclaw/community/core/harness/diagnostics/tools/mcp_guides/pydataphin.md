### Dataphin 资产查询 (pydataphin) - ⚠️ 强制规范
**服务器**: `mcp.ant.faas.pydataphin.assets`
针对 Dataphin 的查询，必须严格遵守以下红线，否则会导致任务失败：

* **表 GUID 格式**：必须带 `odps.` 前缀，格式为 `odps.project_name.table_name`。
* **参数必填**：`retrieve_tables_knowledge` 中的 `types` 不能为空。
* **业务分离**：查"贷款支用是什么"用 `retrieve_biz_knowledge`；查"贷款支用技术方案"才用语雀。

```bash
# 1. 查业务知识/语义
mcporter call mcp.ant.faas.pydataphin.assets.retrieve_biz_knowledge --args '{"utterance":"关键词"}'

# 2. 查表元数据/字段 (types必填)
mcporter call mcp.ant.faas.pydataphin.assets.retrieve_tables_knowledge \
  --args '{"tableGuids": "odps.project.table", "types": "BASIC_INFO,COLUMN_BASIC_INFO"}'

# 3. 查表内容/采样数据
mcporter call mcp.ant.faas.pydataphin.assets.retrieve_tables_content_knowledge --args '{"tableGuids": "odps.project.table"}'

# 4. 权限校验 (必须用 JSON 格式)
mcporter call mcp.ant.faas.pydataphin.assets.validate_privilege \
  --args '{"accountType":"PERSONAL_ACCOUNT","account":"工号","resourceGuid":"odps.project.table","privilegeType":"SELECT"}'

# 5. 血缘查询
mcporter call mcp.ant.faas.pydataphin.assets.retrieve_assets_lineage \
  --args '{"guid": "odps.xxx", "direction": "UP"}'
```