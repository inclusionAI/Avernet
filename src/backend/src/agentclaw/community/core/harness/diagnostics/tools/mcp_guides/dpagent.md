### Dataphin 任务研发 (dpagent) - ⚠️ 强制规范

服务器: `mcp.ant.rpc.dpagent.dataprocess`

Dataphin 任务研发 MCP 服务，用于执行 SQL 查询、管理调度任务、查看表加工代码等。必须严格遵守以下调用规范，否则将导致高频业务错误。

## ⚠️ 强制红线

1. **project_id 强烈建议传入**：调用 `run_sql_query`、`check_sql_grammar`、`create_task` 等 Dataphin 相关接口时，强烈建议传入 `project_id`。缺失 `project_id` 会触发 Code 2021 错误（占总错误的 40.6%）。首次不确定项目 ID 时可留空，工具会返回用户常用项目列表供选择，后续调用务必传入。
2. **异步查询必须轮询**：`run_sql_query`、`run_task_query` 是异步提交，返回 `queryId`，必须轮询 `query_sql_status` 直到 `status` 变为 `SUCCESS/FAILED`，再调用 `query_sql_result` 获取结果。不可跳过轮询直接调 `query_sql_result`。
3. **轮询 ID 不可省略**：`query_submit_result` 需要上一步 `submit_task` 返回的 `submitId`，缺失会触发 Code 2005 错误。
4. **表名格式必须为 项目名.表名**：`read_table_processing_code` 要求 `table_name` 使用 `项目名.表名` 格式，否则触发 Code 2016。

## 标准调用

### ① SQL 查询（异步，三步工作流）

```bash
# 第1步：提交 SQL（异步，立即返回 queryId）
# ⚠️ project_id 强烈建议传入！缺失易触发 Code 2021 错误
mcporter call dataprocess.run_sql_query \
  command='{"sql_query":"SELECT * FROM project.table WHERE dt='\''20260429'\''","project_id":410825}' \
  --output json 2>&1

# 第2步：轮询状态（必须等待 SUCCESS/FAILED）
mcporter call dataprocess.query_sql_status \
  command='{"query_id":"7274432163595840","project_id":410825}' \
  --timeout 60000 --output json 2>&1

# 第3步：获取结果（status 为 SUCCESS 后调用）
mcporter call dataprocess.query_sql_result \
  command='{"query_id":"7274432163595840","project_id":410825}' \
  --timeout 60000 --output json 2>&1
```

### ② 任务搜索与查看

```bash
# 搜索任务
mcporter call dataprocess.search_task \
  command='{"keyword":"task_name","project_id":410825}' --output json 2>&1

# 读取任务内容
mcporter call dataprocess.read_task_content \
  command='{"file_id":"6730635492707615506","status":"DRAFT"}' --output json 2>&1
```

### ③ 任务创建与更新

```bash
# 创建任务（⚠️ project_id 强烈建议传入）
mcporter call dataprocess.create_task \
  command='{"name":"task_name","sql_content":"SELECT ...","project_id":410825}' --output json 2>&1

# 更新任务
mcporter call dataprocess.update_task \
  command='{"file_id":"6730635492707615506","sql_content":"SELECT ...","name":"new_task_name"}' --output json 2>&1
```

### ④ 任务完整生命周期（创建→校验→提交→发布）

```bash
# 提交任务（草稿态 → 已提交）
mcporter call dataprocess.submit_task \
  command='{"file_id":"6730635492707615506"}' --output json 2>&1

# 轮询提交结果（submitId 从上一步获取）
mcporter call dataprocess.query_submit_result \
  command='{"submit_id":"103385972"}' --output json 2>&1

# 发布前检查
mcporter call dataprocess.get_pre_publish_check_result \
  command='{"file_id":"6730635492707615506","version":"..."}' --output json 2>&1

# 发布
mcporter call dataprocess.publish \
  command='{"file_id":"6730635492707615506","version":"..."}' --output json 2>&1
```

### ⑤ 查看表加工代码

```bash
# ⚠️ table_name 必须为 "项目名.表名" 格式
mcporter call dataprocess.read_table_processing_code \
  command='{"table_name":"antaml_sg.adm_aml_crr_merchant_wf_cust_label_dd","project_name":"antaml_sg"}' \
  --output json 2>&1
```

## 常见错误速查

| Code | 含义 | 原因 | 解决 |
|------|------|------|------|
| 2021 | 项目未选择 | 未传 project_id | 传入 project_id |
| SYSTEM_ERROR | 服务端 NPE/null | 后端空指针异常 | 重试，如持续出现报 bug |
| 200 | 业务逻辑错误 | 查询结果过期/不存在 | 检查 queryId 时效性 |
| 2005 | 轮询 ID 为空 | query_submit_result 未传 submitId | 先调用 submit_task 获取 submitId |
| 2016 | 表名格式不合法 | 未用 项目名.表名 格式 | 加项目名前缀 |
| DPN.Filter.NoPermission | 权限不足 | 用户无该资源访问权限 | 检查用户权限配置 |