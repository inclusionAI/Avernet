### 智能搜索 — 灵光检索 (uctmcptools)
**服务器**: `mcp.ant.agentix.150490.uctmcptools`
**描述**: 提供大安全内部智能体基础数据查询服务（非安全域内）

**⚠️ 强制红线**:
1. **完整服务名** — 必须包含 `.uctmcptools`，禁止截断为 `mcp.ant.agentix.150490`
2. **仅使用 `--args` 传参** — 禁止位置参数 JSON、key=value、`--params` 格式（失败率 60%+）
3. **权限 -52301 不可重试** — 遇到无权限错误立即停止，禁止同 Session 反复调用（同一用户反复调用均失败）
4. **服务名末尾无重复** — 与 skylark 不同，uctmcptools 只出现一次，不要写成 `uctmcptools.uctmcptools`

- ❌ 其他所有非 `web_search_asap` 的工具名均为幻觉或属于其他 MCP server

```bash
# ===== 灵光智能检索 (唯一工具) =====
# query 必填：搜索关键词
# userId 必填：支付宝 2088 账号
# sceneId 可选：场景 ID，默认 arec2_asap_python
mcporter call mcp.ant.agentix.150490.uctmcptools.web_search_asap \
  --args '{"query":"大模型 AI 最新动态","userId":"2088031650465422","sceneId":"arec2_asap_python"}' \
  --output json 2>&1
```

**web_search_asap 参数规格**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | string | ✅ | 搜索关键词 |
| `userId` | string | ✅ | 用户支付宝 2088 账号 |
| `sceneId` | string | ❌ | 场景 ID，常见值：`arec2_asap_python`、`arec2_asap_aiglasses` |

**常见错误与规避**：

| 错误 | 原因 | 规避 |
|------|------|------|
| `MCP error -52301: 无权限` | 用户未授权该工具 | 首次遇到即停止，不可重试 |
| `Unknown MCP server 'tools'` | mcporter list 中 tools 被误解析为服务名 | 不使用 mcporter list/tools/inspect 探测 |
| `Unknown MCP server 'mcp.ant.agentix.150490'` | 服务名截断，缺少 `.uctmcptools` | 使用完整服务名 |
| `Too many positional arguments` | 使用 key=value 格式传参 | 统一使用 `--args` JSON 格式 |
| `Unable to load tool metadata` | 使用 `--params` 而非 `--args` | 禁止使用 `--params` |