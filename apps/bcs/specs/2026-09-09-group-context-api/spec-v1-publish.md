# Group Context API — Spec

- **Date:** 2026-10-08
- **Version:** v3（精简版：API 定义 + 谁是卧底 walkthrough）

---

## 1. Group Context 是什么

Group Context 是 BCS 群组内 bot 共享的持久化 KV 存储。bot 可以在运行时自由读写，
不需要预先注册模板。每条 context 是一个带权限标签的字符串键值对。

核心约束：

- 同 `(name, scope)` 下最多一条活跃版本。重复创建返回 conflict。
- 更新走 supersede 取代链：旧版本保留（`valid_to` 回填），新版本写入。
- 读 / 写权限由条目自身的 `visible_to` / `collect_from` 控制。

---

## 2. Context 条目模型

| 字段 | 类型 | 含义 |
|------|------|------|
| `id` | bigint |存储主键|
| `context_id` | string | 条目ID（服务端生成，不随版本更新变化） |
| `name` | string | 版本标识。同 (name, scope) 互为版本链 |
| `scope_level` | enum | 版本链作用域层级：`group` / `session` / `round` |
| `scope` | string | 该层级对应的实例值 |
| `content` | string | 内容本体 |
| `visible_to` | string[] | 可见的 userId 列表。空数组 = 全员可见 |
| `collect_from` | string[] | 可写的 userId 列表。不可为空 |
| `supersedes` | string \| null | 取代链指针：我取代了哪个 id |
| `change_reason` | string \| null | 取代原因（update 时可选传入） |
| `valid_from` | int64 | 生效时间（Unix 毫秒） |
| `valid_to` | int64 \| null | 失效时间。null = 当前活跃版本；被 supersede 时系统回填 |
| `tx_time` | int64 | 系统写入时间（Unix 毫秒） |

### 2.1 scope_level 与 scope 实例值

`scope_level` 声明版本链的作用域层级。`scope` 是该层级的**实例值**，
`scope_level` 由调用方传入，`scope` 由系统从 URL 参数生成。
三种层级的id都是全局唯一的，且已经包含了层次信息，所以group context生成`scope` 时不进行拼接直接使用原始值。

| scope_level | scope 格式 | 示例 |
|---|---|---|
| `group` | `{group_id}` | `game-room-7` |
| `session` | `{session_id}` | `sess-001` |
| `round` | `{round_id}` | `round_003` |

同一个 `(name, scope)` 组合内最多一条活跃版本。不同 `scope` 值的条目各自独立成链。

### 2.2 visible_to / collect_from

- 两者都是 `string[]`（userId 列表）。
- `visible_to` 为空数组表示全员可见。
- `collect_from` 不可为空。
- bot 在 **create 和 update 时都可以设置/修改** `visible_to` 和 `collect_from`。
- 框架在检索/更新时验证：当前 `actor_id` 是否在 `visible_to` / `collect_from` 中。

---

## 3. API

5 个端点，统一 POST。

`group_id`（必填）、`session_id`（按场景选填）、`round_id`（按场景选填）固定在 url 的 param 中。

`actor_id` 在 header 中由框架注入，`setSystemPrompt` 不填，其他 api 必填。

以下每个请求示例省略这些框架注入字段，仅展示端点特有字段。

### 3.1 add — 新增一条 context

```
POST /groupcontext/add
```

| 参数 | 类型 | 必填 | 含义 |
|------|------|------|------|
| `name` | string | 是 | context名字 |
| `scope_level` | string | 是 | `group` / `session` / `round` |
| `content` | string | 是 | 内容（最大 4KB） |
| `visible_to` | string[] | 是 | 可见 userId 列表（空=全员） |
| `collect_from` | string[] | 是 | 可写 userId 列表（不可为空） |

行为：
1. 校验 `actor_id` 是否在 `collect_from` 中 → 否则 `permission_denied`
2. 查同 `(name, scope)` 是否已有活跃版本 → 有则 `conflict`
3. 写入新条目，返回成功

响应，有效信息都是bot传入的，response保持简洁不重复展示数据：
```json
{
   "status": "ok", 
   "error_msg": null
}
```

### 3.2 update — 更新一条 context（supersede）

```
POST /groupcontext/update
```

| 参数 | 类型 | 必填 | 含义 |
|------|------|------|------|
| `name` | string | 是 | context名字 |
| `scope_level` | string | 是 | `group` / `session` / `round` |
| `content` | string | 否 | 新内容（不传则沿用旧值） |
| `change_reason` | string | 否 | 取代原因 |

行为：
1. 根据 `scope_level` 计算出 `scope`
2. 查 `(name, scope)` 的活跃版本（`valid_to IS NULL`）：
   - 0 条 → `not_found`
   - >1 条 → `not_found`（属不变量破坏，正常路径不应出现，需同时告警）
3. 校验 `actor_id` 是否在该活跃版本 `collect_from` 中 → 否则 `permission_denied`
4. 单 DB 事务内原子 supersede：
   a. CAS 回填旧版本：
      `UPDATE ... SET valid_to = :tx_time WHERE context_id = :old_id AND valid_to IS NULL`
      影响行数 = 0 → 旧版本已被并发 update 抢先 supersede，回滚并返回 `conflict`
   b. 写入新条目（`supersedes = :old_id`，记录 `change_reason`），提交
5. 返回成功

> **并发与唯一活跃版本保证**
> - 并发 **add** 写同 `(name, scope)` 的第二条活跃版本 → 由部分唯一索引
>   `UNIQUE (name, scope) WHERE valid_to IS NULL` 拒绝，返回 `conflict`。
> - 并发 **update** 抢同一活跃版本 → 回填用的 `valid_to IS NULL` CAS 让其中一方影响 0 行而回滚，
>   返回 `conflict`；抢赢的一方正常 supersede。最终同一 `(name, scope)` 永远只有一条活跃版本，
>   不会出现双活跃或静默成功。
> - `(name, scope, valid_to)` 建联合索引以支撑上述查询与 CAS。

响应：
```json
{
   "status": "not_found",
   "error_msg": "name=xx and scope=xx, group context not found"
}
```

### 3.3 list — 列出当前 actor 可见/可写的 context

```
POST /groupcontext/list
```

| 参数 | 类型 | 必填 | 默认值 | 含义 |
|------|------|------|--------|------|
| `scope_levels` | string[] | 否 | `["group","session","round"]` | 过滤 scope_level 范围 |

行为：
1. 按 `scope_levels` 计算出 `scope` 实例值列表，根据  `(name, scope)` 过滤条目
2. 保留 `actor_id` 在 `visible_to` 或 `collect_from` 中的条目（即可见或可写）
3. 每个 `(name, scope)` 只返回一条活跃版本（`valid_to = null`）
4. 每条附 `permission`：`R`（可读）、`W`（可写）、`WR`（可读可写）

响应：
```json
{
  "contexts": [
    {
      "name": "game_rule",
      "scope_level": "group",
      "content": "【谁是卧底游戏规则】…",
      "valid_from": 1728000000000,
      "permission": "R"
    },
    {
      "name": "player_word_1",
      "scope_level": "session",
      "content": "你的词语是：香蕉",
      "valid_from": 1728374400000,
      "permission": "WR"
    }
  ]
}
```

### 3.4 get — 按 name 查询context

```
POST /groupcontext/get
```

| 参数 | 类型 | 必填 | 默认值 | 含义 |
|------|------|------|--------|------|
| `name` | string | 是 | — | 精确匹配 name |
| `scope_levels` | string[] | 否 | `["group","session","round"]` | 过滤 scope_level |
| `limit` | int | 否 | `10` | 返回条数上限 |

行为：
1. 按 `scope_levels` 计算 `scope` 实例值列表，根据  `(name, scope)` 过滤条目
2. 保留 `actor_id` 在 `visible_to` 中的条目
3. 每个 `(name, scope)` 只返回一条活跃版本
4. 从细到粗排序（round → session → group）

响应：
```json
{
  "items": [
    {
      "name": "player_word_1",
      "scope_level": "session",
      "content": "你的词语是：香蕉",
      "valid_from": 1728374400000
    }
  ]
}
```

### 3.5 setsystemprompt — 写入/更新 group 的 system prompt，仅bcn系统调用，bot不可调用

```
POST /groupcontext/setsystemprompt
```


| 参数 | 类型 | 必填 | 含义 |
|------|------|------|------|
| `name` | string | 是 | context名字 |
| `scope_level` | string | 是 | `group` / `session` / `round` |
| `content` | string | 是 | system prompt 内容 |
| `collect_from` | string[] | 是 | 可写 userId 列表 |
| `visible_to` | string[] | 是 | 可见 userId 列表 |

行为：
1. 例如 `name = "system_prompt"`, `scope_level = "group"`,
   计算出`scope = "{group_id}"`（由框架填入，调用方无需传）
2. 同 `(name, scope)` 如已有活跃版本 → supersede 旧版本（不像 create 那样 conflict）
3. 返回新 `context_id`

> setsystemprompt 本质是 update-or-create：首次调用 create，后续调用自动 supersede。

响应：与 add/update 同构，返回 `{"status":"ok","error_msg":null}`。

### 3.6 错误码

| HTTP | `status` | 适用接口 | 含义 |
|------|----------|----------|------|
| 200 | `ok` | 全部 | 成功 |
| 400 | `invalid_param` | 全部 | 参数校验失败 |
| 403 | `permission_denied` | add / update / setsystemprompt | 调用方不在 collect_from 中 |
| 404 | `not_found` | update | 没有可以被更新的context |
| 409 | `conflict` | add / update | add：同 (name, scope) 已有活跃版本；update：并发 supersede 抢败（CAS 影响 0 行） |
| 413 | `payload_too_large` | add / update / setsystemprompt | content 超上限（默认 4KB） |
| 500 | `internal_error` | 全部 | 服务端内部错误 |

错误响应格式：
```json
{ "status": "conflict", "error_msg": "player_word_1 already exists in sess-001" }
```

---

## 4. 场景 Walkthrough：谁是卧底

### 设定

- 群聊 `game-room-7`（group_id）
- 本轮游戏 session `sess-001`
- 5 个玩家：张三、李四、王五、赵六、钱七
- 裁判 bot：`judge_bot`
- 群管理员：`admin`

### 4.1 裁判视角

#### 4.1.1 查看现有 context

```
POST /groups/game-room-7/groupcontext/list
scope_levels: ["group","session"]
actor_id: judge_bot
```

响应：只有管理员预设的 `game_rule`。

```json
{
  "contexts": [
    {
      "name": "game_rule",
      "scope_level": "group",
      "content": "【谁是卧底游戏规则】每轮每人描述自己的词语…",
      "valid_from": 1728000000000,
      "permission": "R"
    }
  ]
}
```

#### 4.1.2 为每位玩家创建底牌（add ×5）

```
POST /groups/game-room-7/sessions/sess-001/groupcontext/add
name: player_word_1
scope_level: session
content: 你的词语是：香蕉
visible_to: [judge_bot, 张三]
collect_from: [judge_bot]
```

响应：
```json
{ "status": "ok", "error_msg": null }
```

同样为李四(苹果)、王五(苹果)、赵六(香蕉)、钱七(苹果)创建 `player_word_2` 到 `player_word_5`，
每人 `visible_to` 仅含裁判和本人。系统自动生成 `scope = sess-001`，每条 name 不同所以不冲突。

#### 4.1.3 记录卧底对应关系（add）

```
POST /groups/game-room-7/sessions/sess-001/groupcontext/add
name: player_role
scope_level: session
content: 平民词=苹果, 卧底词=香蕉。player_word_1(张三)和player_word_4(赵六)是卧底
visible_to: [judge_bot]
collect_from: [judge_bot]
```

响应：
```json
{ "status": "ok", "error_msg": null }
```

#### 4.1.4 写入玩家状态（add）

```
POST /groups/game-room-7/sessions/sess-001/groupcontext/add
name: player_state
scope_level: session
content: 张三存活、李四存活、王五存活、赵六存活、钱七存活
visible_to: []           ← 全员可见
collect_from: [judge_bot]
```

响应：
```json
{ "status": "ok", "error_msg": null }
```

#### 4.1.5 更新玩家状态（update）

一轮投票后张三出局：

```
POST /groups/game-room-7/sessions/sess-001/groupcontext/update
name: player_state
scope_level: session
content: 张三出局、李四存活、王五存活、赵六存活、钱七存活
change_reason: 第一轮投票
```

系统按 `scope = sess-001` 查到 `(name=player_state, scope=sess-001)` 的活跃版本，
supersede 后写入新版本（新 `id`，`context_id` 不变）。

响应：
```json
{ "status": "ok", "error_msg": null }
```

#### 4.1.6 查询所有底牌（裁判逐一 get）

裁判想查每个玩家的底牌，逐一调用 get：

```
POST /groups/game-room-7/sessions/sess-001/groupcontext/get
name: player_word_1
scope_levels: [session]
```

响应：
```json
{
  "items": [
    {
      "name": "player_word_1",
      "scope_level": "session",
      "content": "你的词语是：香蕉",
      "valid_from": 1728374400000
    }
  ]
}
```

同样 get `player_word_2` 到 `player_word_5`。裁判因在每条 `visible_to` 中，都能看到。

### 4.2 玩家视角（以张三为例）

#### 4.2.1 查看自己可见的 context

```
POST /groupcontext/list?group_id=group-room-7&session_id=sess-001
scope_levels: ["group","session"]
actor_id: 张三
```

响应：看到 `game_rule`（全员）+ `player_word_1`（visible_to 含张三）+ `player_state`（全员）。

```json
{
  "contexts": [
    {
      "name": "game_rule",
      "scope_level": "group",
      "content": "【谁是卧底游戏规则】每轮每人描述自己的词语…",
      "valid_from": 1728000000000,
      "permission": "R"
    },
    {
      "name": "player_word_1",
      "scope_level": "session",
      "content": "你的词语是：香蕉",
      "valid_from": 1728374400000,
      "permission": "R"
    },
    {
      "name": "player_state",
      "scope_level": "session",
      "content": "张三出局、李四存活、王五存活、赵六存活、钱七存活",
      "valid_from": 1728375000000,
      "permission": "R"
    }
  ]
}
```

注意：`player_role` 只有裁判在 `visible_to` 中，张三看不到。`player_word_2` 等也看不到。

#### 4.2.2 查自己的底牌

```
POST /groupcontext/get?group_id=group-room-7&session_id=sess-001
name: player_word_1
scope_levels: [session]
actor_id: 张三
```

响应：
```json
{
  "items": [
    {
      "name": "player_word_1",
      "scope_level": "session",
      "content": "你的词语是：香蕉",
      "valid_from": 1728374400000
    }
  ]
}
```

如果张三尝试 get `player_role`：
```
POST /groupcontext/get?group_id=group-room-7&session_id=sess-001
name: player_role
scope_levels: [session]
actor_id: 张三
```

响应：
```json
{ "items": [] }
```

——不在 `visible_to` 中，返回空。**信息隔离靠 name + visible_to 共同实现**：不存在的 name 查不到，存在的 name 但不在 visible_to 里同样返回空。
