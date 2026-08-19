# 知识库搜索详细参考

> 本文为 SKILL.md 步骤1 的详细参考，包含搜索工具的完整参数、示例和技巧。

---

## GRT 向量知识库搜索

**工具名称**：`mcp__knowledgebaseservice__search`

**功能**：通过 GRT 向量知识库进行语义检索，支持向量召回 + BM25 + KV 多路召回，返回与问题最匹配的知识片段。

### 固定参数

| 参数 | 固定值 | 说明 |
|------|--------|------|
| `query` | **用户问题** | 唯一需要填写的参数，使用用户原话中的核心术语 |
| `userName` | `"楚生"` | 调用者花名（固定） |
| `userId` | `"103892"` | 调用者工号（固定） |
| `token` | `"52e0eeb1546f04a2d786d729eb7a5b28"` | GRT 鉴权 Token（固定，禁止修改） |
| `topK` | `"3"` | 精排后返回结果数 |
| `vectorThreshold` | `"0.6"` | 向量相似度阈值，低于此值的结果被过滤 |
| `rankingThreshold` | `"0.01"` | 精排置信度阈值 |
| `rankingModel` | `"bge-reranker-base"` | 精排模型 |

### 调用示例

```python
mcp__knowledgebaseservice__search(
  query="Bot权限怎么配置",
  userName="楚生",
  userId="103892",
  token="52e0eeb1546f04a2d786d729eb7a5b28",
  topK="3",
  vectorThreshold="0.6",
  rankingThreshold="0.01",
  rankingModel="bge-reranker-base"
)
```

### 返回结果解读

- `content`：知识正文（直接用于回复用户）
- `title`：知识标题
- `source`：来源标识（如产品知识库、问答知识库、BCN手册等）
- `score`：匹配分数（越高越相关，一般 ≥0.5 为有效匹配）

---

## 语雀知识库搜索

**工具名称**：`mcp__skylarkmcpserver__skylark_search`

**⛔ 实际调用方式**：`mcporter call mcp.ant.faas.skylarkmcpserver.skylarkmcpserver.skylark_search`（禁止使用 `mcp__xxx__yyy(...)` 函数调用格式）

**功能**：搜索语雀知识库中的文档，返回标题匹配的文档列表，可通过 `doc_id` 获取文档正文。

**⚠️ 调用方式**：本环境通过 `mcporter call` 调用 MCP 工具，**禁止使用** `mcp__xxx__yyy(...)` 函数调用格式。

```bash
# mcporter 调用格式
mcporter call mcp.ant.faas.skylarkmcpserver.skylarkmcpserver.skylark_search q="关键词" book_id=234552452
```

### 必搜知识库

| 知识库 | Book ID | 权限 | 用途 | 必搜 |
|--------|---------|------|------|------|
| 产品知识库 | `234552452` | 只读 | 产品文档、功能说明、配置指南 | ✅ 是 |
| 问答知识库 | `238052695` | 可读可写 | 已解决的问答记录、问题排查方案 | ✅ 是 |
| BCN产品手册 | `239152297` | 只读 | BCN 群聊、Bot 协作手册 | ✅ 是 |

> ⚠️ **语雀搜索红线**：必须使用 `book_id` 参数指定知识库，**禁止使用 `scope` 参数**（会返回 "scope invalid" 错误）。参数直接传值，**禁止用 JSON 格式传参**。

### 调用示例

```bash
# 搜索产品知识库
mcporter call mcp.ant.faas.skylarkmcpserver.skylarkmcpserver.skylark_search q="Bot权限配置" book_id=234552452

# 搜索问答知识库
mcporter call mcp.ant.faas.skylarkmcpserver.skylarkmcpserver.skylark_search q="Bot权限配置" book_id=238052695

# 搜索BCN产品手册
mcporter call mcp.ant.faas.skylarkmcpserver.skylarkmcpserver.skylark_search q="Bot权限配置" book_id=239152297

# ❌ 错误写法（会报错）
mcporter call mcp.ant.faas.skylarkmcpserver.skylarkmcpserver.skylark_search q="Bot权限配置" scope="zeodup/vh3397"
# ← scope 在此环境中不支持，会返回 "scope invalid"
```

### 参数说明

| 参数 | 值 | 说明 |
|------|------|------|
| `q` | **用户问题** | 搜索关键词，支持模糊匹配 |
| `book_id` | `234552452` / `238052695` / `239152297` | 知识库 ID（必填，禁止用 scope 代替） |
| `pageSize` | 默认 `20` | 返回结果数（可选） |

### 获取文档正文

搜索返回文档列表（含 `title`、`slug`、`id`），如需正文：

```bash
mcporter call mcp.ant.faas.skylarkmcpserver.skylarkmcpserver.skylark_doc_detail doc_id={文档ID}
```

---

## 搜索技巧

- **并行搜索（强制）**：GRT 和 3个语雀知识库搜索无依赖关系，**必须同时发起**，禁止串行
- **关键词拆分（⛔ 最重要）**：GRT 向量检索对长词组匹配更严格，**必须用短核心词搜索**
  - ❌ 错误：`数据归因分析 SKILL MCP`（5个词同时匹配，向量距离过大）
  - ✅ 正确：`归因` 或 `数据归因`（1-2个核心词，命中率高）
  - ❌ 错误：`如何配置 MCP 工具权限`（含虚词"如何""配置"）
  - ✅ 正确：`MCP 权限` 或 `工具权限`（去掉虚词，保留核心名词）
- **关键词构造步骤**：
  1. 提取核心名词（去掉"如何""怎么""是否"等虚词）
  2. 构造第一组：最核心的1-2个词（如 `归因`、`MCP 权限`）
  3. 构造第二组：同义词/通俗表述（如 `数据溯源`、`工具授权`）
  4. ⛔ 禁止一次性使用5个以上词组成长查询
- **渐进搜索（首轮无结果时必须执行）**：
  1. 缩短关键词：用最核心的1个词重搜
  2. GRT 降阈值：`vectorThreshold` 从 `0.6` 降为 `0.4`
  3. 换用同义词：用通俗/替代表述重搜
  4. 增大召回量：`topK` 从 `3` 增为 `5`
- **GRT 降阈值**：若首次查无结果，可将 `vectorThreshold` 降为 `"0.4"` 扩大召回
- **语雀正文**：语雀搜索仅返回标题，需要文档正文时务必调用 `skylark_doc_detail`

### CLI 备选方式

GRT 仅在 MCP 不可用时使用：
```bash
python3 scripts/grt_search.py -q "Bot权限怎么配置" --json
python3 scripts/grt_search.py -q "openclaw插件" --top-k 5 --env pre
```

---

## TeamClaw 专有名词对照表

以下词语在 TeamClaw 语境下有特殊含义，**禁止用通用含义解释**，必须先搜索知识库确认：

| 用户可能说的 | TeamClaw 语境下的含义 | ❌ 禁止理解为 |
|-------------|---------------------|-------------|
| node / Node | TeamClaw Node 客户端 | Node.js |
| bot / Bot | TeamClaw Bot 实例 | 通用聊天机器人 |
| skill / 技能 | TeamClaw Skill 能力 | 通用技能 |
| MCP | MCP 工具协议 | 其他含义 |
| 适配器 / Adaptor | OpenClawEnterprise 适配器 | 通用适配器 |
| 引擎 | Moltis/OpenClaw 引擎 | 搜索引擎/游戏引擎 |
| 设备 | ARCA 设备/容器 | 手机/硬件设备 |
| 工作台 | TeamClaw Web 端 | 通用工作台 |
| 能力市场 | TeamClaw Skill/MCP 市场 | 通用市场 |

**意图消歧规则**：当用户提到上述词语时，必须先搜索知识库确认 TeamClaw 语境下的含义，禁止直接用通用知识回答。

---

## 命中判断

| 结果 | 处理 | 回复模板 |
|------|------|---------|
| 问答库命中 | 直接用知识库内容回复 → 跳到步骤4 | **知识库回复模板** |
| 产品库命中 | 引用产品文档回复 → 跳到步骤4 | **知识库回复模板** |
| BCN产品手册命中 | 引用BCN产品手册回复 → 跳到步骤4 | **知识库回复模板** |
| GRT向量库命中 | 直接用返回内容回复 → 跳到步骤4 | **知识库回复模板** |
| 部分相关 | 辅助后续分析，继续步骤1.5 | — |
| 未找到 | 继续步骤1.5 | — |

回复模板见 [references/reply-templates.md](references/reply-templates.md)