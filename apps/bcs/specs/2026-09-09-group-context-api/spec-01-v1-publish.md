# Group Context API — Spec

- **Date:** 2026-10-12
- **Version:** v4（v1 发布细化：元信息列表、业务有效期、superseded 版本状态、演示 prompt 简化）

---

## 1. Group Context 是什么

Group Context 是 BCS 群组内 bot 共享的持久化 KV 存储。bot 可以在运行时自由读写，
不需要预先注册模板。每条 context 是一个带权限标签的字符串键值对。

核心约束：

- 同 `(name, scope)` 下最多一条当前版本；当前版本定义为 `is_superseded = false`。重复创建返回 conflict。
- 更新走 superseded 版本链：旧版本保留并标记 `is_superseded = true`、写入 `gmt_superseded`，新版本写入为当前版本。
- `content` / `valid_from` / `valid_to` 是业务信息；系统不根据 `valid_from` / `valid_to` 判断当前版本，也不因 supersede 自动改写旧版本的业务有效期。
- 读 / 写权限由条目自身的 `visible_to` / `collect_from` 控制。

---

## 本次新增/变更（2026-10-12）

> 本节是 v4 相对当前已实现 v1 的增量需求，方便 coding session 直接读取。

1. `list` 调整为只返回 meta 信息：`name`、`scope_level`、`scope`、`permission`、`gmt_create`、`gmt_modified`；不返回 `content`、`valid_from`、`valid_to`。
2. `add` / `update` 支持业务有效期字段 `valid_from` / `valid_to`。`add` 未传 `valid_from` 时默认服务端当前时间，`valid_to` 默认 null；`update` 不是完整 patch 语义：`content`、`visible_to`、`collect_from` 未传时继承上一版本，`valid_from` 未传时默认服务端当前时间，`valid_to` 未传时默认 null。
3. 存储与查询当前版本从 `valid_to IS NULL` 切换为 `is_superseded = false`；新增 `gmt_superseded` 表示旧版本被替代的系统时间。
4. `content` / `valid_from` / `valid_to` 是业务信息，系统不按业务有效期过滤 `get` / `list`，supersede 时也不改写旧版本的 `valid_to`。
5. 本期不提供 history API，只保留历史行；`get` / `list` / `update` 默认只处理当前版本。
6. 时间输入支持 RFC3339、无时区本地时间字符串（按服务器 local timezone 解析）、Unix 秒/毫秒时间戳；不支持纯日期字符串。
7. 谁是卧底演示 prompt 简化，不依赖 state_machine 阶段机，优先保证 GroupContext add/get/update/list 测试链路稳定。

## 2. Context 条目模型

| 字段 | 类型 | 含义 |
|------|------|------|
| `id` | bigint |存储主键|
| `context_id` | string | 条目ID（服务端生成，不随版本更新变化） |
| `name` | string | 版本标识。同 (name, scope) 互为版本链 |
| `scope_level` | enum | 版本链作用域层级：`group` / `session` / `round` |
| `scope` | string | 该层级对应的实例值 |
| `content` | string | 内容本体，业务字段 |
| `visible_to` | object | 可见策略。当前仅支持 `{ "userIds": string[] }`，`userIds` 空数组 = 全员可见 |
| `collect_from` | object | 可写策略。当前仅支持 `{ "userIds": string[] }`，`userIds` 不可为空 |
| `valid_from` | timestamp \| null | 业务有效开始时间。add / update 不传时默认服务端当前时间 |
| `valid_to` | timestamp \| null | 业务有效结束时间。null = 未声明结束时间；系统不据此过滤 get/list |
| `is_superseded` | bool | 系统版本状态。false 表示当前版本；true 表示已被同 `(name, scope)` 的新版本取代 |
| `gmt_create` | timestamp | 系统创建时间 |
| `gmt_modified` | timestamp \| null | 系统最后修改时间（如存储模型保留该字段，则对外可作为元信息返回） |
| `gmt_superseded` | timestamp \| null | 当前行被 supersede 的系统时间；当前版本为 null |
| `supersedes` | bigint \| null | 取代链指针：我取代了哪个旧版本物理 `id` |
| `change_reason` | string \| null | 取代原因（update 时可选传入） |

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

- 两者都是 JSON object 策略对象。当前仅支持一种格式：`{ "userIds": string[] }`。
- `visible_to.userIds` 为空数组表示全员可见。
- `collect_from.userIds` 不可为空。
- bot 在 **create 和 update 时都可以设置/修改** `visible_to` 和 `collect_from`。
- 框架在检索/更新时验证：当前 `actor_id` 是否在 `visible_to.userIds` / `collect_from.userIds` 中。

---

## 3. API

Public HTTP/CLI 暴露 4 个端点，统一 POST：`add`、`update`、`list`、`get`。另有内部 service operation `systemset` 供 BCN 系统代码调用，本期不挂载 HTTP router，也不提供 CLI 入口。

`groupId`（必填）、`sessionId`（按场景选填）、`roundId`（按场景选填）固定在 URL query param 中。

`actorId` 由 BCN/CLI 的统一 URL/request 机制提供：普通 bot API 必填，且不得放在 JSON body 中；内部 `systemset` 不填。

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
| `visible_to` | object | 是 | 可见策略。当前格式 `{ "userIds": string[] }`，空 `userIds` = 全员 |
| `collect_from` | object | 是 | 可写策略。当前格式 `{ "userIds": string[] }`，`userIds` 不可为空 |
| `valid_from` | timestamp/string/int | 否 | 业务有效开始时间；不传默认为服务端当前时间 |
| `valid_to` | timestamp/string/int \| null | 否 | 业务有效结束时间；不传默认为 null |

行为：
1. 校验 `actor_id` 是否在 `collect_from.userIds` 中 → 否则 `permission_denied`
2. 查同 `(name, scope)` 是否已有当前版本（`is_superseded = false`）→ 有则 `conflict`
3. 写入新条目，`is_superseded = false`、`gmt_superseded = null`，返回成功

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
| `visible_to` | object | 否 | 新可见策略。不传则沿用旧值。当前格式 `{ "userIds": string[] }`，空 `userIds` = 全员 |
| `collect_from` | object | 否 | 新可写策略。不传则沿用旧值。当前格式 `{ "userIds": string[] }`，`userIds` 不可为空 |
| `valid_from` | timestamp/string/int | 否 | 新业务有效开始时间；不传默认为服务端当前时间 |
| `valid_to` | timestamp/string/int \| null | 否 | 新业务有效结束时间；不传默认为 null |

行为：
1. 根据 `scope_level` 计算出 `scope`
2. 按 `(name, scope, is_superseded=false)` 查当前版本 → 不存在则 `not_found`
3. 校验 `actor_id` 是否在旧条目当前版本的 `collect_from.userIds` 中 → 否则 `permission_denied`
4. 生成新版本：`content` / `visible_to` / `collect_from` 传了就更新，没传则继承旧版本；`valid_from` 未传时默认服务端当前时间，`valid_to` 未传时默认 null
5. 原子操作：旧条目标记 `is_superseded = true` 并写入 `gmt_superseded = now` → 写入新条目（`is_superseded = false`，`supersedes` 指向旧版本物理 `id`，记录 change_reason）
6. 返回成功

> 如果并发 update，使用数据库锁保障，并发 update 时只有一条请求成功，其他返回 `conflict` 失败。

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
| `scope_levels` | string[] | 否 | 按 URL 中最细粒度自动推断 | 过滤 scope_level 范围 |

行为：
1. 未传 `scope_levels` 时，只按 URL 已提供的最细层级查询：只有 `groupId` 查 group 层；有 `sessionId` 且无 `roundId` 查 session 层；有 `roundId` 查 round 层。显式传入 `scope_levels` 时按传入范围计算 `scope` 实例值列表，根据 `(name, scope)` 过滤条目
2. 保留 `actor_id` 在 `visible_to.userIds` 或 `collect_from.userIds` 中的条目（即可见或可写）
3. 每个 `(name, scope)` 只返回当前版本（`is_superseded = false`），不根据 `valid_from` / `valid_to` 过滤
4. 每条只返回元信息，不返回 `content`，也不返回业务有效期字段 `valid_from` / `valid_to`
5. 每条附 `permission`：`R`（可读）、`W`（可写）、`WR`（可读可写）

响应：
```json
{
  "contexts": [
    {
      "name": "game_rule",
      "scope_level": "group",
      "scope": "game-room-7",
      "permission": "R",
      "gmt_create": "2026-10-12T06:56:08Z",
      "gmt_modified": "2026-10-12T06:56:08Z"
    },
    {
      "name": "player_word_1",
      "scope_level": "session",
      "scope": "sess-001",
      "permission": "WR",
      "gmt_create": "2026-10-12T06:57:01Z",
      "gmt_modified": "2026-10-12T06:57:01Z"
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
| `scope_levels` | string[] | 否 | 按 URL 中最细粒度自动推断 | 过滤 scope_level |
| `limit` | int | 否 | `10` | 返回条数上限 |

行为：
1. 未传 `scope_levels` 时，只按 URL 已提供的最细层级查询：只有 `groupId` 查 group 层；有 `sessionId` 且无 `roundId` 查 session 层；有 `roundId` 查 round 层。显式传入 `scope_levels` 时按传入范围计算 `scope` 实例值列表，根据 `(name, scope)` 过滤条目
2. 先按 `(name, scope)` 找到当前版本（`is_superseded = false`）；如果匹配到同名当前版本但 actor 对匹配项均不在 `visible_to.userIds` 中，则返回 `permission_denied`
3. 只返回 `actor_id` 在 `visible_to.userIds` 中的条目；不根据 `valid_from` / `valid_to` 过滤
4. 从细到粗排序（round → session → group）

响应：
```json
{
  "items": [
    {
      "name": "player_word_1",
      "scope_level": "session",
      "content": "你的词语是：香蕉",
      "valid_from": 1728374400000,
      "valid_to": null
    }
  ]
}
```

### 3.5 systemset — 内部写入/更新 context，仅 BCN 系统代码调用，bot 不可调用

`systemset` 是 application/service 层内部操作，本期不挂载为 HTTP endpoint，也不提供 bcs-cli 入口；文档中的参数说明仅约束内部调用语义。


| 参数 | 类型 | 必填 | 含义 |
|------|------|------|------|
| `name` | string | 是 | context名字 |
| `scope_level` | string | 是 | `group` / `session` / `round` |
| `content` | string | 是 | context 内容 |
| `collect_from` | object | 是 | 可写策略。当前格式 `{ "userIds": string[] }`，`userIds` 不可为空 |
| `visible_to` | object | 是 | 可见策略。当前格式 `{ "userIds": string[] }`，空 `userIds` = 全员 |
| `valid_from` | timestamp/string/int | 否 | 业务有效开始时间；不传默认为服务端当前时间 |
| `valid_to` | timestamp/string/int \| null | 否 | 业务有效结束时间；不传默认为 null |

行为：
1. 例如 `name = "system_prompt"`, `scope_level = "group"`,
   计算出`scope = "{group_id}"`（由框架填入，调用方无需传）
2. 同 `(name, scope)` 如已有当前版本（`is_superseded = false`）→ supersede 旧版本（不像 create 那样 conflict）
3. 返回成功

> systemset 本质是 update-or-create：首次调用 create，后续调用自动 supersede。

响应：与 add/update 同构，返回 `{"status":"ok","error_msg":null}`。

### 3.6 时间字段输入格式

`valid_from` / `valid_to` 接受以下输入形态：

- 带时区的 RFC3339 字符串，例如 `2026-10-12T14:56:08+08:00` 或 `2026-10-12T06:56:08Z`。
- 无时区的本地时间字符串，例如 `2026-10-12 14:56:08`；按服务器 local timezone 解析，不写死特定时区。
- Unix 时间戳秒或毫秒，例如 `1791797768` / `1791797768000`。

纯日期字符串（例如 `2026-10-12`）不支持，避免结束日语义歧义。

### 3.7 历史版本

本期不提供 history API。系统保留被 superseded 的历史行，但 `get` / `list` / `update` 默认只面向当前版本（`is_superseded = false`）。

### 3.8 错误码

| HTTP | `status` | 适用接口 | 含义 |
|------|----------|----------|------|
| 200 | `ok` | 全部 | 成功 |
| 400 | `invalid_param` | 全部 | 参数校验失败 |
| 403 | `permission_denied` | add / update / get | add/update 调用方不在 collect_from.userIds 中；get 调用方没有匹配条目的读权限 |
| 404 | `not_found` | update | 没有可以被更新的context |
| 409 | `conflict` | add / update | add：同 (name, scope) 已有活跃版本；update：并发 update 抢败 |
| 413 | `payload_too_large` | add / update / systemset | content 超上限（默认 4KB） |
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
POST /groupcontext/list?groupId=game-room-7
scope_levels: ["group","session"]
actor_id: judge_bot
```

响应：只有管理员预设的 `game_rule`。注意 `list` 只返回元信息，不返回 `content` 或业务有效期；如需规则正文，裁判再调用 `get game_rule`。

```json
{
  "contexts": [
    {
      "name": "game_rule",
      "scope_level": "group",
      "scope": "game-room-7",
      "permission": "R",
      "gmt_create": "2026-10-12T06:56:08Z",
      "gmt_modified": "2026-10-12T06:56:08Z"
    }
  ]
}
```

#### 4.1.2 创建两张底牌（add ×2：卧底词 + 平民词）

裁判只为本轮建 **两条** context：`player_word_1` 是卧底词、`player_word_2` 是平民词。
哪几位玩家是"卧底"、哪几位是"平民"，靠每条 context 的 `visible_to` 来分发——卧底玩家名单写进 `player_word_1` 的 `visible_to.userIds`，平民玩家名单写进 `player_word_2` 的 `visible_to.userIds`。

本局设定：卧底是张三、赵六，平民是李四、王五、钱七；卧底词=香蕉，平民词=苹果。

```
POST /groupcontext/add?groupId=game-room-7&sessionId=sess-001
name: player_word_1        ← 卧底词
scope_level: session
content: 你的词语是：香蕉
visible_to: { userIds: [judge_bot, 张三, 赵六] }      ← 卧底玩家 + 裁判
collect_from: { userIds: [judge_bot] }
```

```
POST /groupcontext/add?groupId=game-room-7&sessionId=sess-001
name: player_word_2        ← 平民词
scope_level: session
content: 你的词语是：苹果
visible_to: { userIds: [judge_bot, 李四, 王五, 钱七] }  ← 平民玩家 + 裁判
collect_from: { userIds: [judge_bot] }
```

响应：
```json
{ "status": "ok", "error_msg": null }
```

同一 session 内只有 2 条 word context，`name` 不同所以不冲突，系统按 `scope_level=session` 自动生成 `scope = sess-001`。

> **底牌的分发方式（不是 API 调用，只是交互流程）**：裁判 bot 私聊每位玩家 bot，
> 告诉卧底玩家"去查 `player_word_1`"、告诉平民玩家"去查 `player_word_2`"。
> **玩家只知道自己是 1 还是 2，不知道自己 1=卧底 / 2=平民**——拿到词后，所有人所见都是一句普通的"你的词语是：X"，身份信息只有裁判的 `player_role` 里有（见 4.1.3）。
> 玩家再自己 `get` 被告知的那条 name（见 4.2.2）拿词。信息隔离不靠 API 隔，靠 `visible_to.userIds`：玩家能 `get` 到的，正是他在 `visible_to.userIds` 里的那一条。

#### 4.1.3 记录卧底对应关系（add）

```
POST /groupcontext/add?groupId=game-room-7&sessionId=sess-001
name: player_role
scope_level: session
content: 卧底词=香蕉(player_word_1)，平民词=苹果(player_word_2)。卧底玩家=张三、赵六；平民玩家=李四、王五、钱七
visible_to: { userIds: [judge_bot] }
collect_from: { userIds: [judge_bot] }
```

响应：
```json
{ "status": "ok", "error_msg": null }
```

#### 4.1.4 写入玩家状态（add）

```
POST /groupcontext/add?groupId=game-room-7&sessionId=sess-001
name: player_state
scope_level: session
content: 张三存活、李四存活、王五存活、赵六存活、钱七存活
visible_to: { userIds: [] }           ← 全员可见
collect_from: { userIds: [judge_bot] }
```

响应：
```json
{ "status": "ok", "error_msg": null }
```

#### 4.1.5 更新玩家状态（update）

一轮投票后张三出局：

```
POST /groupcontext/update?groupId=game-room-7&sessionId=sess-001
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

#### 4.1.6 查询底牌（裁判 get）

裁判想核对两张底牌，分别 get `player_word_1` 和 `player_word_2`：

```
POST /groupcontext/get?groupId=game-room-7&sessionId=sess-001
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
      "valid_from": 1728374400000,
      "valid_to": null
    }
  ]
}
```

裁判在两条 `visible_to.userIds` 中，都能查到。需要时同样 get `player_role` 看身份对应关系。

### 4.2 玩家视角（以张三为例）

#### 4.2.1 查看自己可见的 context

```
POST /groupcontext/list?groupId=game-room-7&sessionId=sess-001
scope_levels: ["group","session"]
actor_id: 张三
```

响应：看到 `game_rule`（全员）+ `player_word_1`（visible_to.userIds 含张三）+ `player_state`（全员）。注意 `list` 只告诉张三这些 context 存在和权限，不暴露正文；张三需要对被告知的 name 调用 `get` 获取内容。

```json
{
  "contexts": [
    {
      "name": "game_rule",
      "scope_level": "group",
      "scope": "game-room-7",
      "permission": "R",
      "gmt_create": "2026-10-12T06:56:08Z",
      "gmt_modified": "2026-10-12T06:56:08Z"
    },
    {
      "name": "player_word_1",
      "scope_level": "session",
      "scope": "sess-001",
      "permission": "R",
      "gmt_create": "2026-10-12T06:57:01Z",
      "gmt_modified": "2026-10-12T06:57:01Z"
    },
    {
      "name": "player_state",
      "scope_level": "session",
      "scope": "sess-001",
      "permission": "R",
      "gmt_create": "2026-10-12T06:58:10Z",
      "gmt_modified": "2026-10-12T07:05:00Z"
    }
  ]
}
```

注意：`player_role` 只有裁判在 `visible_to.userIds` 中，张三看不到；`player_word_2`（平民词）也不在张三的 `visible_to.userIds` 里，所以张三**连平民的词是什么都不知道**，更无从知道自己拿的 `player_word_1` 是卧底词。卧底/平民的不对称，全靠 `visible_to.userIds` 把对方那条隔掉。

#### 4.2.2 查自己的底牌

```
POST /groupcontext/get?groupId=game-room-7&sessionId=sess-001
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
      "valid_from": 1728374400000,
      "valid_to": null
    }
  ]
}
```

如果张三尝试 get `player_role`：
```
POST /groupcontext/get?groupId=game-room-7&sessionId=sess-001
name: player_role
scope_levels: [session]
actor_id: 张三
```

响应：
```json
{ "items": [] }
```

——不在 `visible_to.userIds` 中，返回空。**信息隔离靠 name + visible_to.userIds 共同实现**：不存在的 name 查不到，存在的 name 但不在 visible_to.userIds 里同样返回空。
