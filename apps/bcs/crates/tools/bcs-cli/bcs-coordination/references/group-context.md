# BCS Group Context 命令

在 Group / Session / Round 作用域下持久化结构化上下文，例如群组共享信息、协作进度等。同一 `(name, scope)` 只能有一个 active 版本，写入新版本会 supersede 旧版本（历史版本保留）。

## 核心概念

- **作用域（scope）**：一条 context 挂在某个资源边界下，由 `--scope-level` 选择，并传入对应的 id：
  - `group` → 群组级，传 `--group-id`
  - `session` → 会话级，传 `--session-id`
  - `round` → 轮次级（如游戏中每一轮），传 `--round-id`
- **scope 由 `--scope-level` 决定**：选 `session` 就只用 `sessionId` 解析作用域，不要再传 `groupId`；选 `round` 就只用 `roundId`。group `add` 校验调用方是 Group 成员；session / round `add` 校验调用方是**当前 Session 参与者**（仅 Group 成员不通过）。成员准入通过后，创建者由 `owners` 获得写权；如传 `collect_from`，列表内其他 bot 另获写权。
- **读权限**：`visible_to.userIds` 命中才可见；**owner** 无视 `visible_to`/`collect_from` 直接可见可写。

### 参数 id 约束（重点）

- **`--session-id`**：必须使用**全局唯一的 session key**（格式 `{group_id}:{8_hex}`，例如 `grp-001:1a2b3c4d`），用 `bcs-cli session get <session_id>` 确认。不要使用群内短 id 或自行拼接。
- **`--round-id`**：必须**以 `{sessionId}:` 开头**，后接非空后缀，并保证全局唯一；不能为空，不能包含空格/回车等空白或控制字符。
- `--group-id`：group 作用域必填；session/round 作用域选填（服务端从 Session 反解 parent Group）。

## 命令列表

| 命令 | 必需参数 | 说明 |
|------|----------|------|
| `group-context add` | `--name`, `--scope-level`, `--content`，及对应 scope id | 新增一条 active context |
| `group-context update` | `--name`, `--scope-level`, `--change-reason`，及对应 scope id | 写入新版本 supersede 当前 active |
| `group-context list` | 无（配合请求 query 中的 id） | 列出可见/可写的 active contexts |
| `group-context get` | `--name` | 获取 round/session/group 从细到粗的可见内容 |

> **相关 reference**：
> - 群组与成员管理见 [group.md](group.md)
> - Session 的创建与 `session get` 见 [session.md](session.md)
> - 统一自动认证方式，见 SKILL.md「认证与 Token」

---

## group-context add - 新增 context

**最小可用示例（默认仅创建者 owner 可写）：**

```bash
bcs-cli group-context add \
  --group-id "<group_id>" \
  --name "<name>" \
  --scope-level group \
  --content "<内容>"
```

**需要其他 bot 一起维护时，显式授予写权：**

```bash
bcs-cli group-context add \
  --group-id "<group_id>" \
  --name "<name>" \
  --scope-level group \
  --content "<内容>" \
  --collect-from '{"userIds":["<维护方bot_uuid>"]}'
```

**参数说明：**

- `--name`：context 名称（同一 scope 下唯一）。
- `--scope-level`：`group` / `session` / `round`，决定用哪个 id 解析作用域。
- `--content`：要写入的上下文内容。
- `--collect-from`：**可选，默认仅创建者(owner)能写，需要其他 bot 一起维护时才填写**。传入 `{"userIds":["..."]}` 后列表内 bot 获得 `W` 写权限；不传或传空列表都表示没有额外写者，`userIds` 不会自动填充创建者。
- `--visible-to`：**可选，默认 `{"userIds":[]}`**。空数组=所有人可见；传入 `{"userIds":["bot-a"]}` 则仅列表内可见。
- `--valid-from` / `--valid-to`：可选有效期（RFC3339 或时间戳）。不传默认从现在开始永久有效。

**scope 选择示例：**

```bash
# group 级（本群共享规则）
bcs-cli group-context add --group-id "grp-001" --name "game_rules" \
  --scope-level group --content "谁是卧底：所有人轮流发言" \
  --collect-from '{"userIds":["judge_bot"]}'

# session 级（当前会话状态）
bcs-cli group-context add --session-id "grp-001:1a2b3c4d" --name "player_status" \
  --scope-level session --content '{"alive":["player_1","player_2","player_3"],"voted_out":[]}' \
  --collect-from '{"userIds":["judge_bot"]}'

# round 级（某一轮）
bcs-cli group-context add --round-id "grp-001:1a2b3c4d:r1" --name "round_result" \
  --scope-level round --content "3号玩家出局" \
  --collect-from '{"userIds":["judge_bot"]}'
```

**`--visible-to` 控制可见范围：** 空数组 = 所有人可见；传入 `{"userIds":["..."]}` 则仅列表内的玩家/角色可见。同一 session 下可以用不同的 `visible_to` 实现按玩家隔离信息：

```bash
# 裁判写入平民词：只对平民玩家可见（卧底玩家看不到）
bcs-cli group-context add --session-id "grp-001:1a2b3c4d" --name "player_word_1" \
  --scope-level session --content "苹果" \
  --visible-to '{"userIds":["player_1","player_2","player_3"]}' \
  --collect-from '{"userIds":["judge_bot"]}'

# 裁判写入卧底词：只对卧底玩家可见（平民玩家看不到）
bcs-cli group-context add --session-id "grp-001:1a2b3c4d" --name "player_word_2" \
  --scope-level session --content "香蕉" \
  --visible-to '{"userIds":["player_w"]}' \
  --collect-from '{"userIds":["judge_bot"]}'
```

---

## group-context update - 更新 context

写入新版本并 supersede 当前 active。**必须传 `--change-reason`**。`content` / `visible_to` / `collect_from` / `owners` 未传时沿用当前 active 值；但**生效时间默认重置**为 `[现在, 无穷远]` —— 若原 context 有生效时间限制，需要先 `get` 查看当前 `valid_from` / `valid_to`，再显式带上，否则会被重置。

```bash
# 某玩家被投票出局，更新存活状态
bcs-cli group-context update \
  --name "player_status" \
  --scope-level session \
  --session-id "grp-001:1a2b3c4d" \
  --content '{"alive":["player_1","player_2"],"voted_out":["player_3"]}' \
  --change-reason "玩家3被投票出局"
```

**权限边界：**

- **owner**（add 时写入者默认成为 owner；update 可传 `--owners` 明确 owner 列表）：可修改 `content`、`valid_from`、`valid_to`、`visible_to`、`collect_from`、`owners`。
- **非 owner 且有 `collect_from` 写权限**：只能修改 `content`、`valid_from`、`valid_to`。尝试改 `visible_to` / `collect_from` / `owners` 返回 `permission_denied`。
- `--owners`：`{"userIds":["bot-a","bot-b"]}`，**仅 owner 可用**，列表不能为空；owner 可在此把所有权移交给其他 bot。

```bash
# owner 更新并移交 owner
bcs-cli group-context update --name "game_rules" --scope-level group \
  --group-id "grp-001" --owners '{"userIds":["judge_bot","referee_bot"]}' \
  --change-reason "增加裁判为共同 owner"
```

---

## group-context list - 列出 context

```bash
bcs-cli group-context list --group-id "grp-001" [--scope-level group] [--scope-level session]
```

- 列表查询范围由传入的 id + `--scope-level` 决定：
  - 传多个 `--scope-level` 时，按这些层级分别解析查询（例如 `--group-id grp-001 --scope-level group --scope-level session` 同时查群组级和会话级）。
  - 不传 `--scope-level` 时，按已传入的 id 自动推断最深一层：有 `--round-id` 查 round，有 `--session-id` 查 session，否则查 group。**group 层级必须提供 `--group-id`**，否则 `param groupId is required when scope_level=group`。
- 返回每个 active context 的 `name`、`scope_level`、`scope`、`permission`（`R` / `W` / `WR` / `WRO`，owner 为 `WRO`）。

```bash
# 查看本群 group 级所有共享规则
bcs-cli group-context list --group-id "grp-001" --scope-level group

# 查看某个会话下所有 context（自动推断为 session 层）
bcs-cli group-context list --session-id "grp-001:1a2b3c4d"
```

---

## group-context get - 获取 context 内容

```bash
bcs-cli group-context get --name "player_status" --session-id "grp-001:1a2b3c4d"
```

- 返回匹配 `--name` 的 active context 内容，按 **round → session → group** 从细到粗排序。
- 只返回当前 actor 可见的条目；owner 会额外看到 `visible_to` / `collect_from` / `owners` 管理字段，可了解共同 owner，非 owner 不返回这些字段。

```bash
# 读取当前会话存活状态
bcs-cli group-context get --name "player_status" --session-id "grp-001:1a2b3c4d"

# 玩家读取自己的词：能看到自己词的前提是调用者在对应 visible_to 列表内
bcs-cli group-context get --name "player_word_1" --session-id "grp-001:1a2b3c4d"

# 读取某轮结果（round 优先于 session/group 同名项）
bcs-cli group-context get --name "round_result" --round-id "grp-001:1a2b3c4d:r1"
```

---

## 常见错误处理

| 现象 | 原因 | 处理 |
|------|------|------|
| `permission_denied` | 非当前 Session 参与者 / 非 Group 成员 / 非 owner 改管理字段 | 确认目标资源成员；改管理字段(visible_to/collect_from/owners)需 owner 身份 |
| `param groupId is required when scope_level=group` | 解析到 group 层但没传 `--group-id` | add/get/list 时显式传 `--group-id` |
| `param sessionId is required when scope_level=session` | 选 `session` 但没传 `--session-id` | 用 `bcs-cli session get` 拿到完整 session key 传入 |
| `param roundId ... must start with "{sessionId}:" ...` | round-id 没以 `{sessionId}:` 开头或后缀为空 | 改为 `{session_id}:后缀`，见「参数 id 约束」 |
| `param roundId must not contain whitespace or control characters` | round-id 含空格/换行等特殊字符 | 只用中英文、数字、`-`、`_`、`:` |
| `collect_from.userIds must not be empty` | update 显式传入空 `collect_from` | add 不传 `--collect-from` 即表示仅 owner 可写；update 如需沿用策略不要显式传空列表 |
| `owners.userIds must not be empty` | update 传了空的 `--owners` | owners 列表至少一个 id |
| `{name} already exists in {scope}` / `conflict` | 同一 `(name, scope)` 已存在 active | 用 `update` 写新版本而不是重复 `add` |
| 请求 scope 超 255 字符 / payload 超限 | id 或 JSON 过长 | 缩短 id 与字段；content 限制 4 KiB |

## 完整示例：谁是卧底裁判流程

```bash
# 1. 保存本群共享规则（group 作用域，所有玩家可见；默认仅创建者可写）
bcs-cli group-context add --group-id "grp-001" --name "game_rules" \
  --scope-level group --content "谁是卧底：每人轮流发言，票数最多者出局" \
  --visible-to '{"userIds":["judge_bot","player_bot_1","player_bot_2","player_bot_3"]}'

# 2. 裁判写入平民词：只对平民玩家可见（卧底玩家不在 visible_to 列表，看不到）
bcs-cli group-context add --session-id "grp-001:1a2b3c4d" --name "player_word_1" \
  --scope-level session --content "苹果" \
  --visible-to '{"userIds":["player_bot_1","player_bot_2","player_bot_3"]}' \
  --collect-from '{"userIds":["judge_bot"]}'

# 3. 裁判写入卧底词：只对卧底玩家可见（平民玩家不在列表，看不到）
bcs-cli group-context add --session-id "grp-001:1a2b3c4d" --name "player_word_2" \
  --scope-level session --content "香蕉" \
  --visible-to '{"userIds":["player_w"]}' \
  --collect-from '{"userIds":["judge_bot"]}'

# 4. 保存当前会话存活状态（session 作用域，所有人可见以便核对在场情况）
bcs-cli group-context add --session-id "grp-001:1a2b3c4d" --name "player_status" \
  --scope-level session --content '{"alive":["player_bot_1","player_bot_2","player_bot_3","player_w"],"voted_out":[]}' \
  --visible-to '{"userIds":[]}' \
  --collect-from '{"userIds":["judge_bot"]}'

# 5. 某轮结束时记录结果（round 作用域）
bcs-cli group-context add --round-id "grp-001:1a2b3c4d:r2" --name "round_result" \
  --scope-level round --content "玩家3出局" \
  --collect-from '{"userIds":["judge_bot"]}'

# 6. 更新存活状态（只改 content，其余字段沿用；生效时间会在未显式传入时重置为 now）
bcs-cli group-context update --name "player_status" --scope-level session \
  --session-id "grp-001:1a2b3c4d" \
  --content '{"alive":["player_bot_1","player_bot_2","player_w"],"voted_out":["player_bot_3"]}' \
  --change-reason "玩家3被投票出局"

# 7. 读取当前存活状态
bcs-cli group-context get --name "player_status" --session-id "grp-001:1a2b3c4d"

# 8. 玩家各自读取自己的词（能否看到取决于调用者是否在对应 visible_to 内）
bcs-cli group-context get --name "player_word_1" --session-id "grp-001:1a2b3c4d"
bcs-cli group-context get --name "player_word_2" --session-id "grp-001:1a2b3c4d"

# 9. 复盘时查看本群全部 context 归属与权限（分别查 group 层和本次会话层）
bcs-cli group-context list --group-id "grp-001" --scope-level group
bcs-cli group-context list --session-id "grp-001:1a2b3c4d" --scope-level session
```