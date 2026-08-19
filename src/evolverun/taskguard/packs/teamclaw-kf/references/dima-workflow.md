# DIMA 创建流程

TeamClaw 问题上报到 DIMA 的完整流程。

---

## 前置判断：是否属于 TeamClaw 问题

### 属于 TeamClaw 的特征

- 使用 TeamClaw 产品过程中遇到的问题
- 涉及 open-claw、agentclaw、OpenClawEnterprise、Moltis 等子系统
- 用户在 TeamClaw 平台上的操作问题
- TeamClaw 相关功能、性能、稳定性问题

### 不属于 TeamClaw 的示例

- 用户个人的钉钉账号问题
- 公司网络、VPN、办公环境问题
- 其他非 TeamClaw 产品问题
- 通用软件使用问题

**不属于 TeamClaw 时**：不创建缺陷，告知用户原因并建议其他途径。

---

## 固定参数（必须使用）

| 参数 | 值 | 说明 |
|------|------|------|
| workspaceId | W26001113566 | 大安全-TeamClaw |
| projectId | **不传** | 需求/缺陷属于空间级别 |

### 红线警告

| 禁止 | 正确 |
|------|------|
| ❌ `workspaceId="W24001004413"` | ✅ `workspaceId="W26001113566"` |
| ❌ 创建需求时指定 projectId | ✅ 只传 workspaceId，不传 projectId |

---

## 类型区分（必须先确认）

| 类型 | 说明 | 工具 | subject 前缀 |
|------|------|------|-------------|
| **需求（Issue）** | 新功能、优化、改进 | MCP `mcp__Dima-MCP__createIssue` | `【线上用户】` |
| **缺陷（Bug）** | 报错、异常、不符合预期 | 脚本 `scripts/dima_create_bug.py bug` | `【线上用户】`（自动添加） |

**判断关键词**：
- 需求：希望、需要、新增、优化、支持、增加
- 缺陷：报错、异常、失败、不生效、挂了、有问题

**无法明确判断时，必须询问用户**。

---

## 创建缺陷（Bug）

### ⛔ 必须先确认所属模块

创建缺陷前，**必须先确定问题所属模块**，否则无法指定 processor 和填写 content。

| 步骤 | 说明 |
|------|------|
| 1. 判断模块 | 根据问题描述确定所属模块（见下方模块映射表） |
| 2. 确认负责人 | 从模块映射表查找对应负责人 |
| 3. 填写模块信息 | content 模板中【问题模块】不得为空 |

**无法判断模块时，必须询问用户**，不可留空或随意填写。

### 必填参数

> ⚠️ 缺陷创建**必须**使用 `scripts/dima_create_bug.py` 脚本，不走 MCP createBug。以下参数对应脚本 CLI 参数。

```yaml
subject: 问题标题（脚本自动添加【线上用户】前缀）
module: 所属模块（必填，AI根据问题描述自动识别：前端/后端/Adaptor/BCN/系统工程/安全权限与隐私/智能能力/产品建议）
processor-id: 模块负责人工号（必填，AI根据module自动匹配对应负责人，见下方模块映射表）
staff-id: 创建人工号
reporter-id: 上报人工号（即提出bug的同学的工号）
reporter-name: 上报人花名（即提出bug的同学的花名）
priority: urgent/high/medium/low
description: 问题描述
```

脚本自动生成 workspaceId、tenantId、content（使用下方模板）等参数。API 密钥已内置于 `_secrets.so`。

### Content 模板（Markdown 格式）

> 脚本使用 `formatType: "MARKDOWN"` + `editorType: "YUQUE"` 提交内容，以下模板为 Markdown 格式。

```markdown
### 【问题上报人】
- 工号：{用户工号}
- 花名：{用户花名}
- 上报时间：{时间}

### 【问题模块】
- 所属模块：{前端/后端/Adaptor/openclaw引擎/BCN/系统工程/安全权限与隐私/智能能力/产品建议}
- 负责人：{模块负责人姓名}

### 【问题描述】
{用户反馈的问题描述}

### 【错误现场】

**前端日志**
> 查询参数：应用=open-claw，时间=近30分钟，关键字={工号/花名}

关键错误：
{从日志中提取的关键错误信息}

**后端日志**
> 查询参数：应用=agentclaw，日志=start.log，时间=近30分钟，关键字={工号}

关键错误：
{从日志中提取的关键错误信息}

**Adaptor日志**
> 查询参数：应用=arcaagentclaw，日志=adaptor_err.log，时间=近30分钟，关键字={仅machine_name}

关键错误：
{从日志中提取的关键错误信息}

**OpenClaw日志**
> 查询参数：应用=arcaagentclaw，日志=openclaw_err.log，时间=近30分钟，关键字={仅machine_name}

关键错误：
{从日志中提取的关键错误信息}

### 【Langfuse对话记录】
会话ID：{session_id}
时间范围：{start_time} - {end_time}
关键错误：{错误类型和详情}
```

### ⛔ 格式禁区

- ❌ 禁止嵌套代码块（``` 内再嵌套 ```）
- ❌ 禁止复杂 Markdown 表格
- ❌ 禁止 HTML 标签

---

## 创建需求（Issue）

> 需求创建**走 MCP 工具** `mcp__Dima-MCP__createIssue`，不走脚本。

### 必填参数

```yaml
workspaceId: "W26001113566"
subject: "【线上用户】{需求标题}"
content: 需求详细描述（使用下方模板）
processor: 模块负责人（必须指定！由所属模块决定）
tenantId: alipay
```

### Content 模板（Markdown 格式）

> 需求内容使用 MCP `createIssue` 工具提交，content 字段应使用以下 Markdown 格式。

```markdown
### 【需求提出人】
- 工号：{用户工号}
- 花名：{用户花名}
- 提出时间：{时间}

### 【需求模块】
- 所属模块：{模块名称}
- 负责人：{模块负责人姓名}

### 【需求描述】
{用户提出的需求描述}

### 【预期效果】
{用户期望的功能效果}

### 【使用场景】
{用户描述的使用场景}
```

---

## 模块负责人映射

| 模块 | 负责人 | 常见问题 |
|------|--------|---------|
| 前端 | 与白(153364)、敛秋(368653) | UI展示、组件、样式 |
| 后端 | 江绅(205357)、墨馠(334018)、安远(405935)、斩秋(061256) | Skill、MCP、钉钉 |
| Adaptor/引擎 | 涔涔(272471)、一朴(165137) | 连接、配置、会话 |
| BCN | 卓人(410025)、元歌(197262) | 群聊、Bot协作 |
| 系统工程 | 萧辚(151614)、毅舒(354194)、温悦(012369) | ARCA、容器、环境 |
| 安全权限 | 允川(111954) | 权限、隐私 |
| 智能能力 | 文汐(260065)、楚生(103892)、山宗(197444)、墨馠(334018) | 上下文、技能优化 |

---

## 创建后回复模板

### 缺陷

```markdown
抱歉给您带来不便 🙏

我已经将您的问题提交到缺陷系统，正在进行处理：

📌 **缺陷编号**：{workItemId}
👤 **负责人**：{负责人姓名}
🔗 **查看进度**：https://project.alipay.com/workItem?workItemId={workItemId}

我会持续跟进此问题，有进展会第一时间通知您。

---
💡 本回答由AI生成，仅供参考。如有疑问请联系本周值班同学。
```

### 需求

```markdown
感谢您的反馈！我已将您的需求提交到需求管理系统：

📌 **需求编号**：{workItemId}
👤 **负责人**：{负责人姓名}
🔗 **查看进度**：https://project.alipay.com/workItem?workItemId={workItemId}

产品团队会评估此需求，有进展会第一时间通知您。

---
💡 本回答由AI生成，仅供参考。如有疑问请联系本周值班同学。
```

---

## 链接格式（必须遵守）

✅ **正确格式**：
```
https://project.alipay.com/workItem?workItemId={workItemId}
```

❌ **禁止格式**：
```
https://project.alipay.com/view?workItemView=xxx&openWorkItemId=xxx
https://project.alipay.com/space/W26001113566/workitemDetail?openWorkItemId=xxx
任何不包含 /workItem?workItemId= 的链接
```

---

## 命令行脚本（Dima OpenAPI）

> ⚠️ **脚本仅用于创建缺陷（Bug）**。创建需求（Issue）和任务（Task）仍使用 MCP 工具（`mcp__Dima-MCP__createIssue` / `mcp__Dima-MCP__createTask`）。

通过 `scripts/dima_create_bug.py` 脚本直接调用 Dima OpenAPI 创建缺陷。

### 环境准备

1. API 密钥已内置于 `_secrets.so`，**无需额外配置**即可直接使用
2. 备用方式：通过环境变量设置 `DIMA_ACCESS_KEY` 和 `DIMA_SECRET_KEY`，或在 `scripts/.env` 中配置

### API 认证

脚本使用 AES-ECB 签名认证，参见 [Project OpenApi 文档](https://yuque.antfin.com/dima/gs1zsi/ht81n00i138e83o5)。

签名算法：
1. 构建 `accessKey={ak}&timestamp={ts}` 字符串
2. 使用 secret_key（16位）进行 AES-ECB 加密
3. 输出大写十六进制字符串作为 Signature

请求头：
- `AccessKey`: 应用 AccessKey
- `Signature`: AES-ECB 签名
- `Timestamp`: 请求时间戳（毫秒）
- `ARK_OPENAPI_TENANT`: 租户标识（alipay）

### 缺陷创建必填自定义字段

脚本自动填充以下三个必填自定义字段（使用 `customFieldValueSimpleParamList`）：

| 字段 | customFieldId | 默认值 | 说明 |
|------|---------------|--------|------|
| 所属模块 | FIELD2023001001071 | 根据 `--module` 参数 | 前端/后端/Adaptor/等 |
| 场景标签 | FIELD2024001001689 | `["业务测试","回归托管"]` | 多选字段 |
| 标签 | FIELD2023001000003 | `["26001060999","26001060997"]` | Tag ID，非显示名 |

> ⚠️ 这三个字段是 DIMA TeamClaw 空间 Bug 类型的必填项。缺少任一字段会返回 `「标签」「所属模块」必填` 错误。

> ⚠️ **Create API** 和 **Update API** 的字段格式不同：
> - Create: `customFieldValueSimpleParamList` + `customFieldId` + `customFieldValueList`
> - Update: `workItemFieldValueList` + `workItemFieldIdentity` + `workItemFieldValueList`
> 脚本内部自动处理格式转换。

### 创建缺陷

```bash
python3 scripts/dima_create_bug.py bug \
    --subject "前端页面白屏" \
    --module 前端 \
    --processor-id 012345 \
    --staff-id 010001 \
    --priority high \
    --reporter-id 103892 \
    --reporter-name 测试人 \
    --description "用户打开页面时白屏"
```

自动生成符合 TeamClaw 模板的 content，包含问题上报人、问题模块、问题描述、错误现场等字段。

### 完整参数

> 以下为 Bug 子命令的参数。脚本也提供 `issue`/`task` 子命令（高级用途），标准流程中需求走 MCP `createIssue`、任务走 MCP `createTask`。

| 参数 | 必填 | 说明 |
|------|------|------|
| `--subject` | 是 | 工作项标题（缺陷自动添加【线上用户】前缀） |
| `--processor-id` | 是 | 处理人工号（补0） |
| `--staff-id` | 否 | 创建人工号，默认读取 `DIMA_STAFF_ID` 环境变量 |
| `--module` | 否 | 所属模块（前端/后端/Adaptor/BCN/系统工程/安全权限与隐私/智能能力/产品建议） |
| `--processor` | 否 | 处理人花名（用于 content 模板，module 可自动推断） |
| `--priority` | 否 | 优先级：urgent/high/medium/low（默认 medium） |
| `--reporter-id` | 否 | 上报人工号（用于 content 模板） |
| `--reporter-name` | 否 | 上报人花名（用于 content 模板） |
| `--workspace-id` | 否 | 空间ID（默认 W26001113566） |
| `--project-id` | 否 | 项目ID（可选） |
| `--content` | 否 | 预格式化内容（提供时跳过模板生成） |
| `--content-file` | 否 | 从文件读取内容 |
| `--dry-run` | 否 | 仅打印请求参数，不实际调用 API |
| `--json` | 否 | JSON 格式输出 |

**缺陷专用参数**：`--description`, `--frontend-log`, `--backend-log`, `--adaptor-log`, `--openclaw-log`, `--langfuse-info`

### Python API 调用

```python
from scripts.dima_create_bug import DimaClient, create_teamclaw_bug, _load_config

config = _load_config()
client = DimaClient(config)

result = create_teamclaw_bug(
    client,
    staff_id="010001",
    subject="前端白屏问题",
    module="前端",
    processor_id="012345",
    reporter_id="103892",
    reporter_name="测试人",
    priority="high",
    description="用户打开页面时白屏",
)
```