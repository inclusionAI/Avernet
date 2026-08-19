# 日志查询完整指南

统一的前端、后端、Adaptor、OpenClaw 日志查询入口。

---

## 快速决策表

| 日志类型 | 问题特征 | 服务器 | 查询关键字 |
|---------|---------|--------|-----------|
| 前端日志 | UI展示、组件、页面交互、API调用 | mcp.ant.yuyanbff.yuyanbff | user_id/花名/session_id |
| 后端日志 | API异常、服务错误、数据处理 | mcp.ant.alipaybase-antlogsmcp.mcp-server | user_id/花名/session_id |
| Adaptor日志 | 协议转换、引擎连接、WebSocket | mcp.ant.alipaybase-antlogsmcp.mcp-server | **仅 machine_name** |
| OpenClaw日志 | 引擎错误、会话异常、消息处理 | mcp.ant.alipaybase-antlogsmcp.mcp-server | **仅 machine_name** |

---

## ⚠️ 环境兼容性说明

MCP 服务器名称有两种格式，**优先使用全称**，失败后改用简称：

| 格式类型 | 前端日志服务器 | 后端日志服务器 |
|---------|--------------|--------------|
| **全称（优先）** | `mcp.ant.yuyanbff.yuyanbff` | `mcp.ant.alipaybase-antlogsmcp.mcp-server` |
| **简称（备选）** | `yuyanbff` | `mcp-server` |

**查询失败时的处理方法**：

如果使用完整前缀格式查询失败（报错 `Unknown MCP server 'mcp'`），请改用简写格式：

```bash
# 前端日志 - 简写格式（ClaudeCode 环境）
mcporter call yuyanbff.yuyan-monitor_query-monitor-log-details \
  yuyanId="180020010001289930" \
  expr='[{"arg":"user_id","expr":"equal","value":"103892"},{"arg":"loged_time","expr":"between","value":"2026-04-15 15:36:00,2026-04-15 16:06:00"}]' \
  limit=50

# 后端日志 - 简写格式（ClaudeCode 环境）
mcporter call mcp-server.queryAppLogContent \
  appName=agentclaw \
  startTime="2026-04-16T14:30:00+08:00" \
  endTime="2026-04-16T15:00:00+08:00" \
  query="103892" \
  logPathKeyword="start.log"
```

---

## ⛔ 红线警告（必须遵守）

### Adaptor/OpenClaw 日志禁止使用工号查询

> ❌ **禁止使用** user_id、花名、域账号、session_id、工号
> 
> ✅ **只能使用** machine_name（从 Langfuse 获取）

| 日志类型 | 允许的关键字 | 禁止的关键字 |
|---------|------------|------------|
| 前端日志 | user_id、花名、域账号、session_id、工号 | - |
| 后端日志 | user_id、花名、域账号、session_id、工号 | - |
| **Adaptor日志** | **仅 machine_name** | user_id、花名、域账号、session_id、工号 |
| **OpenClaw日志** | **仅 machine_name** | user_id、花名、域账号、session_id、工号 |

**错误示例（禁止）**：
```bash
# ❌ 错误：使用工号查询 Adaptor 日志
mcporter call mcp.ant.alipaybase-antlogsmcp.mcp-server.queryAppLogContent \
  appName=arcaagentclaw \
  startTime="2026-04-16T14:30:00+08:00" \
  endTime="2026-04-16T15:00:00+08:00" \
  query="260065" \
  logPathKeyword="adaptor_err.log"  # ❌ 工号，将查询不到任何结果！
```

**正确示例**：
```bash
# ✅ 正确：使用 machine_name 查询
mcporter call mcp.ant.alipaybase-antlogsmcp.mcp-server.queryAppLogContent \
  appName=arcaagentclaw \
  startTime="2026-04-16T14:30:00+08:00" \
  endTime="2026-04-16T15:00:00+08:00" \
  query="0000000032cee70a-mgs4oose3gfnakq2fmby6gf2xy" \
  logPathKeyword="adaptor_err.log"  # ✅ machine_name
```

---

## 查询流程

```
用户问题
    ↓
【第一批并行】前端日志 + 后端日志 + Langfuse
    ↓
Langfuse 输出 machine_name
    ↓
【第二批串行】Adaptor日志 + OpenClaw日志（依赖 machine_name）
```

---

## 前端日志（雨燕 MCP）

### 基本信息

| 参数 | 值 |
|------|------|
| 应用名称 | open-claw 或 TeamClaw |
| 雨燕ID | 180020010001289930 |
| MCP 服务器 | mcp.ant.yuyanbff.yuyanbff |
| 时间范围 | 近30分钟（建议不超过7天）|

### 核心工具

| 工具 | 用途 |
|------|------|
| `mcp.ant.yuyanbff.yuyanbff.yuyan-monitor_query-monitor-log-details` | 查询异常明细日志（最多7天）|
| `mcp.ant.yuyanbff.yuyanbff.yuyan-monitor_query-exception-minutely-metrics` | 查询分钟级异常统计 |
| `mcp.ant.yuyanbff.yuyanbff.yuyan-monitor_query-exception-daily-metrics` | 查询日级异常统计 |
| `mcp.ant.yuyanbff.yuyanbff.yuyan-monitor_get-monitor-config` | 获取应用监控项配置 |
| `mcp.ant.yuyanbff.yuyanbff.yuyan-monitor_analyze-monitor-log-dimensions` | 多维度聚合分析异常 |

### 查询参数

```yaml
yuyanId: "180020010001289930"  # 必须是字符串格式
时间范围: 近30分钟（建议不超过7天）
expr: JSON数组格式的过滤表达式
limit: 返回条数限制（默认50）
```

### expr 表达式格式

```json
[
  {"arg":"user_id","expr":"equal","value":"用户工号"},
  {"arg":"loged_time","expr":"between","value":"开始时间,结束时间"}
]
```

### expr 支持的字段

| 字段 | 说明 | 表达式类型 |
|------|------|-----------|
| `user_id` | 用户ID/工号 | equal |
| `loged_time` | 上报时间 | between (格式: "开始时间,结束时间") |
| `code` | 监控项代码 | equal |
| `msg` | 异常信息 | equal/like |
| `full_url` | 页面地址 | equal/like |
| `env` | 环境 | equal (PROD/PRE/DEV/TEST/STABLE) |

### 扩展字段说明

| 字段类型 | 范围 | 用途 |
|---------|------|------|
| **d1-d20** | 维度字段 | 描述性信息分类（浏览器、地区、设备等）|
| **c1-c20** | 内容字段 | 存储任意字符串内容 |
| **m1-m20** | 度量字段 | 数值计算（count/sum/avg/min/max/p50等）|

### 查询示例

**基础查询：按工号查询**
```bash
mcporter call mcp.ant.yuyanbff.yuyanbff.yuyan-monitor_query-monitor-log-details \
  yuyanId="180020010001289930" \
  expr='[{"arg":"user_id","expr":"equal","value":"103892"},{"arg":"loged_time","expr":"between","value":"2026-04-15 15:36:00,2026-04-15 16:06:00"}]' \
  limit=50
```

**高级查询：按环境+异常类型**
```bash
mcporter call mcp.ant.yuyanbff.yuyanbff.yuyan-monitor_query-monitor-log-details \
  yuyanId="180020010001289930" \
  expr='[{"arg":"env","expr":"equal","value":"PROD"},{"arg":"msg","expr":"like","value":"TypeError"},{"arg":"loged_time","expr":"between","value":"2026-04-15 15:36:00,2026-04-15 16:06:00"}]' \
  limit=100
```

### 时间格式规范

| 查询类型 | 时间格式 | 示例 |
|---------|---------|------|
| 分钟级查询 | YYYY-MM-DD HH:mm:ss | 2026-04-15 15:36:00 |
| 日级查询 | YYYY-MM-DD | 2026-04-15 |

### 关注内容

| 错误类型 | 示例 |
|---------|------|
| JavaScript错误 | TypeError, ReferenceError, SyntaxError |
| API调用失败 | status code ≠ 200, timeout, network error |
| WebSocket问题 | 断开、重连、心跳丢失 |
| 组件错误 | 渲染失败、Props错误 |

### 最佳实践

1. **时间范围选择**：建议不超过30分钟，实时问题排查使用分钟级查询
2. **查询优化**：先获取监控配置了解可用监控项，按需选择聚合粒度
3. **异常分析**：先查看异常统计趋势，确定问题时间点后再查明细
4. **维度分析**：结合浏览器、地区、设备等维度分析异常分布

### 常见错误

**命令格式错误？**

| 错误做法 | 正确做法 |
|---------|---------|
| ❌ `mcporter call yuyanbff.queryLogs ...` | ✅ `mcporter call mcp.ant.yuyanbff.yuyanbff.yuyan-monitor_query-monitor-log-details ...` |
| ❌ 服务器名简写 `yuyanbff.*` | ✅ 使用完整前缀 `mcp.ant.yuyanbff.yuyanbff.*` |
| ❌ 参数格式 `app="open-claw"` `timeRange="30m"` `keyword="103892"` | ✅ 参数格式 `yuyanId="180020010001289930"` `expr='[...]'` `limit=20` |

**错误示例：**
```bash
# ❌ 错误：使用简写服务器名
mcporter call yuyanbff.yuyan-monitor_query-monitor-log-details \
  yuyanId="180020010001289930" \
  expr='[...]'
```

**正确示例：**
```bash
# ✅ 正确：使用完整的 MCP 服务器名称
mcporter call mcp.ant.yuyanbff.yuyanbff.yuyan-monitor_query-monitor-log-details \
  yuyanId="180020010001289930" \
  expr='[{"arg":"user_id","expr":"equal","value":"103892"},{"arg":"loged_time","expr":"between","value":"2026-04-16 16:14:26,2026-04-16 16:44:26"}]' \
  limit=20
```

### 常见问题

**前端日志返回空数据？**
1. 确认时间范围：确保时间格式正确 (YYYY-MM-DD HH:mm:ss)
2. 确认用户活动：用户可能在该时间段内未使用前端
3. 确认环境：检查 `env` 字段是否匹配（PROD/PRE/DEV 等）
4. 尝试扩大时间范围：如"近1小时"

**查询失败？**
检查服务器名称是否正确：
- ✅ 正确：`mcp.ant.yuyanbff.yuyanbff`
- ❌ 错误：`yuyanbff`（简写格式，在 OpenClaw 环境中不支持）

**雨燕ID格式错误？**
- 雨燕ID必须是 **180开头的18位数字**
- TeamClaw/open-claw 的雨燕ID：`180020010001289930`
- 传入参数时使用字符串格式：`yuyanId="180020010001289930"`

---

## 后端日志（antlogs MCP）

### 基本信息

| 参数 | 值 |
|------|------|
| MCP 服务器 | mcp.ant.alipaybase-antlogsmcp.mcp-server |
| 应用名称 | agentclaw |
| 日志文件 | start.log |
| 时间格式 | ISO-8601 (如 `2026-04-16T14:30:00+08:00`) |

### 核心工具

| 工具 | 用途 |
|------|------|
| `mcp.ant.alipaybase-antlogsmcp.mcp-server.listRegions` | 列出所有可用区域/集群 |
| `mcp.ant.alipaybase-antlogsmcp.mcp-server.listAppLogPath` | 列出应用的日志文件路径 |
| `mcp.ant.alipaybase-antlogsmcp.mcp-server.queryAppLogContent` | 按时间范围查询应用日志 |
| `mcp.ant.alipaybase-antlogsmcp.mcp-server.queryAppLogContentByTraceId` | 按 TraceID 查询日志（推荐） |
| `mcp.ant.alipaybase-antlogsmcp.mcp-server.getTimeRangeOfTraceId` | 从 TraceID 提取时间范围 |

### 查询参数

```yaml
appName: 应用名称（必需）
startTime: 查询开始时间，ISO-8601 格式（必需）
endTime: 查询结束时间，ISO-8601 格式（必需）
query: 查询关键字，需用单引号包裹（必需）
logPathKeyword: 日志路径关键字过滤（可选）
regionId: 区域ID（可选）
resultLimitPerLog: 每个文件最大返回数，默认20，最大100（可选）
reverse: 是否倒序，默认false（可选）
```

### query 语法规则

```
- 查询条件必须用单引号包裹：'error'
- 包含空格：'user login failed'
- 包含单引号：'user''s id'（单引号需双写转义）
- 多条件组合：'error' AND 'login'
```

### 查询示例

**基础查询：按工号查询**
```bash
mcporter call mcp.ant.alipaybase-antlogsmcp.mcp-server.queryAppLogContent \
  appName=agentclaw \
  startTime="2026-04-16T14:30:00+08:00" \
  endTime="2026-04-16T15:00:00+08:00" \
  query="146597" \
  logPathKeyword="start.log"
```

**按 TraceID 查询（推荐）**
```bash
mcporter call mcp.ant.alipaybase-antlogsmcp.mcp-server.queryAppLogContentByTraceId \
  appName=agentclaw \
  traceId="0b96615917763211172205984e1386"
```

**多条件查询**
```bash
mcporter call mcp.ant.alipaybase-antlogsmcp.mcp-server.queryAppLogContent \
  appName=agentclaw \
  startTime="2026-04-16T14:30:00+08:00" \
  endTime="2026-04-16T15:00:00+08:00" \
  query="'error' AND 'agentclaw'" \
  logPathKeyword="start.log"
```

**列出应用的日志文件**
```bash
mcporter call mcp.ant.alipaybase-antlogsmcp.mcp-server.listAppLogPath appName=agentclaw
```

### 关注内容

| 错误类型 | 示例 |
|---------|------|
| 异常堆栈 | Exception, Error, Traceback |
| 数据库错误 | SQLException, ConnectionError, Timeout |
| 上游服务失败 | 引擎连接失败, ARCA调用失败 |
| 认证错误 | 401 Unauthorized, Token过期 |

---

## Langfuse 对话记录（获取 machine_name）

### 权限控制（重要）

| 用户类型 | 权限 |
|---------|------|
| **楚生（工号 103892）** | ✅ 可显式调用 Langfuse 工具查询会话信息 |
| **其他用户** | ❌ 禁止显式调用 Langfuse 工具，如需查询对话记录，引导用户联系楚生 |
| **机器人内部使用** | ✅ 可以内部使用 Langfuse 工具获取上下文，但不向用户展示 Langfuse 查询过程 |

### 触发条件

1. 需要了解用户对话现场、错误上下文
2. 需要获取 machine_name 用于查询 Adaptor/OpenClaw 日志
3. 工具调用失败需要定位问题

### 匹配规则（或的关系，任一匹配即可）

> ⚠️ **重要**：只匹配 `metadata` 中的字段，不匹配 trace 级其他字段

**Langfuse trace 结构示例**：
```python
{
    "id": "503288f964da475f887ba3caf23cb9b5",  # ❌ 不匹配
    "userId": None,                               # ❌ 不匹配（trace级别）
    "user_id": "wenxi.sbw",                       # ❌ 不匹配（trace级别）
    "metadata": {
        "session_id": "session_xxx",              # ✅ 匹配
        "user_id": "260065",                      # ✅ 匹配（工号存在这里）
        "user_name": "文汐",                      # ✅ 匹配
        "user_account": "wenxi.sbw"               # ✅ 匹配（域账号）
    }
}
```

| 匹配字段 | Langfuse 路径 | 说明 | 示例 |
|---------|--------------|------|------|
| `session_id` | `metadata.session_id` | 会话ID | `session_abc123` |
| `user_id` | `metadata.user_id` | 用户工号 | `"260065"` |
| `user_name` | `metadata.user_name` | 用户花名 | `"文汐"` |
| `user_account` | `metadata.user_account` | 用户域账号 | `"wenxi.sbw"` |

### 查询条件

```python
# 匹配规则：以上4个字段为"或"的关系，任一匹配即可
# 优先使用 session_id（如有），其次使用 user_id/user_name/user_account

session_id = "从对话上下文或用户消息中提取"  # 最精确
user_id = "工号"                           # 如 "103892"
user_name = "花名"                         # 如 "楚生"
user_account = "域账号"                    # 如 "qianlingke.qlk"
days = 0.02                                # 约30分钟
```

### 查询方式

#### 方式一：使用原生脚本（推荐）

脚本位置：`/home/admin/.openclaw/workspace/skills/skills-local/teamclaw-support/scripts/langfuse_query.py`

**前置配置**：

密钥加载优先级（自动选择）：
1. **安全模块**（最优）：从编译后的 `_secrets.so` 加载（Cython 编译，密钥不可读）
2. **环境变量**：设置 `LANGFUSE_PUBLIC_KEY` 和 `LANGFUSE_SECRET_KEY`

```bash
# 方式一：使用安全模块（推荐，已在 my_skills/teamclaw/secure/ 配置）
# 无需额外配置，脚本自动加载

# 方式二：设置环境变量
export LANGFUSE_PUBLIC_KEY="pk-lf-xxx"
export LANGFUSE_SECRET_KEY="sk-lf-xxx"
export LANGFUSE_HOST="https://langfuse.antfin.com"  # 可选，默认值
```

**安全说明**：
- ✅ 安全模块通过 Cython 编译，密钥编译进二进制文件，用户无法直接读取
- ✅ 环境变量方式适合本地开发，但密钥可能被日志记录
- ❌ 禁止在代码中硬编码密钥

**查询示例**：
```bash
# 按工号查询（近30分钟）
python langfuse_query.py --user-id "103892"

# 按花名查询
python langfuse_query.py --user-name "楚生" --days 1

# 按 session_id 查询（最精确）
python langfuse_query.py --session-id "session_xxx" --days 7

# 按域账号查询
python langfuse_query.py --user-account "qianlingke.qlk"

# JSON 格式输出（便于程序解析）
python langfuse_query.py --user-id "103892" --json
```

**输出说明**：
- 会话列表：包含 session_id、时间范围、对话轮次
- machine_name：用于后续 Adaptor/OpenClaw 日志查询

#### 方式二：使用 python_repl 执行

```python
import asyncio
import sys
sys.path.insert(0, '/home/admin/.openclaw/workspace/skills/skills-local/teamclaw-support/scripts')

from langfuse_query import query_conversations

# 查询用户对话记录
sessions, machine_names = query_conversations(
    session_id="会话ID",     # 可选，最精确
    user_id="工号",          # 可选，如 "103892"
    user_name="花名",        # 可选，如 "楚生"
    user_account="域账号",   # 可选，如 "dapeng.fdp"
    days=0.02,               # 约30分钟
    limit=20
)

# 输出结果
for session in sessions:
    print(f"Session: {session.session_id}")
    print(f"Time: {session.start_time} - {session.end_time}")
    print(f"Turns: {session.total_turns}")
    for turn in session.turns[-5:]:  # 最近5轮
        print(f"  [{turn.role}]: {turn.content[:200]}...")
        for tc in turn.tool_calls:
            print(f"    Tool: {tc.tool_name} -> {'✓' if tc.success else '✗'}")

print(f"\n发现的 machine_name: {machine_names}")
```

**安全说明**：
- 密钥通过环境变量配置，禁止硬编码
- `.env` 文件已加入 `.gitignore`，不会泄露密钥

### 提取关键信息

从 Langfuse 对话记录中提取以下信息：

| 信息类型 | 用途 | 提取位置 |
|---------|------|---------|
| **错误信息** | 定位问题根因 | observations 中的 error 字段 |
| **问题上下文** | 了解用户意图 | 用户消息和Agent回复内容 |
| **已尝试方案** | 避免重复建议 | 历史 tool_calls |
| **machine_name** | 查询 Adaptor/OpenClaw 日志 | session.meta.machine_name |

### machine_name 格式示例

```
0000000032cee70a-mgs4oose3gfnakq2fmby6gf2xy
```

### Langfuse 可视化链接

查询到 trace 后，可访问 AI Vision 查看完整链路：

```
https://aivision.alipay.com/project/cmmt4ynng00c7519j736bxajd/traces/{trace_id}
```

---

## Adaptor 日志

### 基本信息

| 参数 | 值 |
|------|------|
| MCP 服务器 | mcp.ant.alipaybase-antlogsmcp.mcp-server |
| 应用名称 | arcaagentclaw |
| 日志文件 | adaptor_err.log |
| 查询关键字 | **仅 machine_name** |
| 时间格式 | ISO-8601 (如 `2026-04-16T14:30:00+08:00`) |

### 前置条件

**必须先执行 Langfuse 查询获取 machine_name！**

流程：
```
1. 执行 Langfuse 查询
2. 从 session.meta 中提取 machine_name
3. 使用 machine_name 查询 Adaptor 日志
```

### 查询示例

```bash
mcporter call mcp.ant.alipaybase-antlogsmcp.mcp-server.queryAppLogContent \
  appName=arcaagentclaw \
  startTime="2026-04-16T14:30:00+08:00" \
  endTime="2026-04-16T15:00:00+08:00" \
  query="0000000032cee70a-mgs4oose3gfnakq2fmby6gf2xy" \
  logPathKeyword="adaptor_err.log"
```

### 关注内容

| 错误类型 | 示例 |
|---------|------|
| 协议转换错误 | ProtocolError, ConversionError |
| 引擎连接异常 | EngineConnectionError |
| WebSocket 失败 | WebSocketError, HeartbeatError |

---

## OpenClaw 日志

### 基本信息

| 参数 | 值 |
|------|------|
| MCP 服务器 | mcp.ant.alipaybase-antlogsmcp.mcp-server |
| 应用名称 | arcaagentclaw |
| 日志文件 | openclaw_err.log |
| 查询关键字 | **仅 machine_name** |
| 时间格式 | ISO-8601 (如 `2026-04-16T14:30:00+08:00`) |

### 前置条件

**必须先执行 Langfuse 查询获取 machine_name！**

### 查询示例

```bash
mcporter call mcp.ant.alipaybase-antlogsmcp.mcp-server.queryAppLogContent \
  appName=arcaagentclaw \
  startTime="2026-04-16T14:30:00+08:00" \
  endTime="2026-04-16T15:00:00+08:00" \
  query="0000000032cee70a-mgs4oose3gfnakq2fmby6gf2xy" \
  logPathKeyword="openclaw_err.log"
```

### 关注内容

| 错误类型 | 示例 |
|---------|------|
| 引擎错误 | EngineError, ModelError |
| 会话异常 | SessionError |
| 消息处理失败 | MessageProcessingError |

---

## 系统代码查询

### 触发条件

- 需要了解实现逻辑
- 排查特定功能
- 分析代码行为

### 分析方式：通过 ACP 调用 AI 编程助手分析代码

### 模块路径映射

| 问题类型 | 模块路径 | 说明 |
|---------|---------|------|
| **前端问题** | `/home/admin/ocb/src/frontend` | UI展示、组件、页面交互 |
| **后端问题** | `/home/admin/ocb/src/backend` | Skill管理、资源管理、API |
| **群聊/Bot协作** | `/home/admin/ocb/src/bcs` | 群聊功能、Bot协作系统 |
| **Chat/Session问题** | `/home/admin/ocb/src/engine` | 会话管理、对话引擎 |

### 代码位置总览

```yaml
/home/admin/ocb              # 代码库根目录
├── src/frontend/            # 前端代码
├── src/backend/             # 后端代码（Skill、资源管理）
├── src/bcs/                 # 群聊和Bot协作系统
└── src/engine/              # Chat、Session引擎
```

### 查询示例

**核心原则：使用 Claude 结合用户问题分析相应代码**

#### 查询流程

1. **理解用户问题**：明确用户遇到的具体问题或想了解的功能
2. **定位代码模块**：根据问题类型选择对应的模块路径
3. **使用 Claude 分析**：让 Claude 阅读相关代码文件，结合问题进行分析
4. **返回分析结果**：向用户解释代码逻辑、定位问题原因

#### 示例场景

**场景1：用户反馈 WebSocket 连接失败**
```
用户问题：WebSocket 连接总是断开，报错 "Connection closed"

分析步骤：
1. 使用 Claude 阅读前端 WebSocket 相关代码
2. 检查连接配置、心跳机制、重连逻辑
3. 分析可能的断开原因（超时、认证失败、网络问题）
4. 返回问题根因和解决方案
```

**场景2：用户询问 Skill 执行流程**
```
用户问题：调用 Skill 时返回了错误，想知道 Skill 的执行流程

分析步骤：
1. 使用 Claude 阅读 Skill 管理相关代码
2. 追踪从 Skill 调用到执行的完整链路
3. 结合用户的具体错误信息定位问题点
4. 返回流程说明和可能的问题原因
```

**场景3：用户想了解某个 API 的实现逻辑**
```
用户问题：/api/skill/list 这个接口是怎么实现的？

分析步骤：
1. 使用 Claude 阅读后端 API 路由定义
2. 追踪到对应的处理函数
3. 分析数据查询、权限校验、返回格式等逻辑
4. 返回完整的实现流程说明
```

### 查询技巧

| 技巧 | 说明 |
|------|------|
| **精准描述问题** | 提供具体的错误信息、操作步骤、预期结果 |
| **提供上下文** | 说明问题的发生场景、相关配置 |
| **明确分析目标** | 是想了解实现逻辑、排查问题、还是优化性能 |

### 常见问题排查入口

| 问题现象 | 排查方向 | 关键文件/目录 |
|---------|---------|--------------|
| WebSocket 连接失败 | 检查连接配置、认证逻辑 | `src/frontend/ws/`, `src/engine/session/` |
| API 返回错误 | 检查路由、参数验证、权限 | `src/backend/api/`, `src/backend/middleware/` |
| Skill 执行失败 | 检查 Skill 配置、工具调用 | `src/backend/skill/`, `src/engine/tools/` |
| 群聊消息丢失 | 检查消息路由、Bot 状态 | `src/bcs/router/`, `src/bcs/bot/` |

### 与其他 Skill 的协作

代码查询通常在以下场景使用：

1. **日志分析后**：日志定位到错误，需要查看代码实现
2. **Langfuse 分析后**：工具调用失败，需要查看工具实现
3. **创建 DIMA 前**：确认问题范围和模块归属

### 注意事项

- 代码库为只读，禁止修改生产代码
- 查询时注意代码版本，可能与生产环境有差异
- 复杂逻辑建议先看入口函数，再逐步深入