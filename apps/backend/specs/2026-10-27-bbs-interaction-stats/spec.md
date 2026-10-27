# BBS 互动统计方案

- 日期：2026-10-27
- 状态：proposal
- 范围：`apps/backend` 的 BBS 互动契约，以及 `apps/frontend-nextgen` 的 Community 展示
- 关联文档：
  - `docs/specs/2026-10-15-bbs-phase2-scope/spec.md`
  - `docs/specs/2026-10-20-bbs-poll-selection-and-incremental-stats/spec.md`
  - `docs/specs/2026-10-27-bbs-v4-topic-types/spec.md`
  - `https://renderofficedev.alipay.com/p/ai-pages/a-dx5ckjd5/index.html#/lab`
  - `docs/specs/2026-10-06-collaboration-square-community/collaboration-square-community.md`

## 1. 问题

BBS 已经有 Topic、Post、投票和楼层讨论，但互动行为缺少统一的权威模型。当前系统只能推导回复数和投票数，无法回答：

- 某个 Topic 或某条回复被多少主体浏览、点赞、收藏或关注。
- 多少次互动发生，而不是多少个不同的主体发生过互动。
- Bot 与 Human 能否围绕 Topic、Post、Human 或 Bot 形成统一互动口径。
- 个人中心和头像 hover 需要同时看到身份的互动计数与内容产出统计；个人中心还需要同时覆盖当前 Human 与其名下 Bot 的互动内容。
- HELP Topic 的互动情况如何与其回复结构共同呈现。

这些行为的目标不仅是 Topic，也可能是某一条 Post；关注行为的 target 还可能是 Human 或 Bot。因此数据模型和接口不能按“Topic 浏览表”设计，必须显式表达：**actor、action、target** 三类事实。

## 2. 目标与非目标

### 2.1 目标

建立统一的 BBS 互动域：

1. **主体（actor）**
   - `HUMAN`
   - `BOT`

2. **互动动作（action）**
   - `VIEW`：浏览
   - `LIKE`：点赞
   - `FAVORITE`：收藏
   - `FOLLOW`：关注
   - `ACCEPT`：采纳

3. **目标（target）**
   - `TOPIC`：一个主题
   - `POST`：某一条回复
   - `HUMAN`：一个人类作者
   - `BOT`：一个 Bot 作者

4. **读取指标**
   - `view_count`
   - `like_count`
   - `favorite_count`
   - `follow_count`
   - `accept_count`
   - Identity 专属：`topic_count`、`reply_count`、`accepted_reply_count`
   - Topic 专属：`reply_participant_count`、`helpful_reply_count`

5. **个人中心**
   - 读取当前 Human 与其名下 Bot 的 actor-only 统计。
   - 前端支持 `Human` / `Bot` / `全部` 三种身份类型筛选。

主体与目标均使用既有稳定的 BBS 身份标识，不在互动表中存储人员目录快照、Bot 名称、头像或个人资料。

### 2.2 非目标

本方案首版不做：

- 站点级数据看板、留存看板、趋势 API 或通用分析查询语言。
- 热度分、推荐算法、榜单或精选排序。
- URL、User-Agent、IP、Referer、设备标识或页面停留时长采集。
- 通用通知系统；点赞、关注后的提醒后续单独设计。
- 用户手动 Tag。
- 不用 `LIKE`、`FAVORITE`、`FOLLOW` 或 `ACCEPT` 替代 HELP 的有帮助状态，也不用任何互动动作替代 POLL 的投票选择。
- 通用数据平台、任意事件总线或未定义的新增动作。

本切片也不实现：

- 取消点赞、取消收藏、取消关注。
- 历史互动明细页面或 actor 维度明细导出。
- 按天、按月、按窗口的统计和留存分析。
- 跨环境聚合查询。
- 请求级幂等键。当前写入语义是“每次成功提交都是一次互动事件”。

后续如果需要 `SHARE`、`MENTION`、评分、取消状态或事件时间窗口，必须通过单独契约演进加入，不得把未识别的 action 或补充语义隐式写入本表。

## 3. 主体、动作与目标矩阵

互动不是任意 actor/action/target 组合。写入边界必须执行以下矩阵：

| Action | TOPIC | POST | HUMAN | BOT | 语义 |
| --- | --- | --- | --- | --- | --- |
| `VIEW` | 是 | 是 | 否 | 否 | 打开详情或查看回复内容，插入一条浏览事件 |
| `LIKE` | 是 | 是 | 否 | 否 | 对内容表达认同，插入一条点赞事件 |
| `FAVORITE` | 是 | 是 | 否 | 否 | 收藏内容，插入一条收藏事件 |
| `FOLLOW` | 是 | 是 | 是 | 是 | 订阅后续更新，插入一条关注事件 |
| `ACCEPT` | 否 | 是 | 否 | 否 | HELP Topic 作者采纳一条一级回复，插入一条采纳事件 |

矩阵含义：

- Topic 和 Post 是内容 target。五种互动动作中，`ACCEPT` 仅对 Post 生效；其余四种对 Topic 和 Post 均可用。
- Human 和 Bot 是身份 target，只允许 `FOLLOW`。
- 每次成功写入都插入一行；同一 actor 对同一 target 可以多次互动。
- 本版不做状态化点赞、收藏、关注，也不提供取消动作。
- 本版不做按天聚合、日报或趋势能力；`gmt_create` 只作为事件发生时间，不参与业务去重。
- 本版不为 actor/action/target 建立唯一键。
- 前端可以在请求进行中禁用按钮，避免误重复提交；但 Backend 不会静默丢弃重复事件。
- 本表不承载人画像或刷量治理；后续如需要幂等、频控或风控，必须另立契约。

`helpful` 是 HELP Topic 的一条回复状态，仍保存在 `ac_forum_post.post_config` 中。该状态置为 true 时，后端追加一条 `ACCEPT` 互动事件；取消有帮助仅更新状态，不产生反向互动事件。

`accept_count` 是回复级事件历史，`helpful_reply_count` 是 Topic 级当前状态汇总，二者不可互换。

`helpful` 状态写入与 `ACCEPT` 事件插入必须共用同一个业务事务边界，避免出现状态已更新但事件缺失。

### 3.1 指标定义

| 字段 | 定义 | 不包含 |
| --- | --- | --- |
| `view_count` | 该 target 的成功 `VIEW` 事件行数 | 未记录身份的读取和读取错误 |
| `like_count` | 该 target 的成功 `LIKE` 事件行数 | 请求校验失败、target 不存在或数据库写入失败 |
| `favorite_count` | 该 target 的成功 `FAVORITE` 事件行数 | 请求校验失败、target 不存在或数据库写入失败 |
| `follow_count` | 该 target 的成功 `FOLLOW` 事件行数 | 请求校验失败、target 不存在或数据库写入失败 |
| `accept_count` | 仅 POST target：该回复被成功采纳的事件行数 | 子回复、非 HELP Topic、非 Topic 作者或校验失败 |
| `stats.topic_count` | 仅 actor scope：该身份当前环境创作的 Topic 行数 | 其他身份的 Topic、其他环境数据 |
| `stats.reply_count` | 仅 actor scope：该身份当前环境创作的 Post 行数 | 其他身份的回帖、其他环境数据 |
| `stats.accepted_reply_count` | 仅 actor scope：该身份当前处于 helpful=true 的一级 HELP 回帖数 | 非当前状态、子回复、非 HELP Topic |
| `reply_participant_count` | 仅 TOPIC target：在该 Topic 下写过至少一条 Post 的唯一作者数 | Topic 作者本人，除非其也写过 Post |
| `helpful_reply_count` | 仅 TOPIC target：当前被标记为有帮助的 HELP 一级回复数 | 子回复、非 HELP Topic、普通点赞 |

`reply_participant_count` 与 `analysis.poll_stats.participant_count` 仍保持独立：

- `reply_participant_count` 覆盖所有 Topic 类型，来源是 Post 作者。
- `analysis.poll_stats.participant_count` 仅覆盖 POLL Topic，口径属于确定性投票投影。

POLL 写入规则限制每位作者只能有一条 Post，因此当前 POLL Topic 中两个数字通常会相等；客户端仍必须按字段语义选择，不能用其中之一替代另一方。

## 4. 写入契约

### 4.1 统一写入端点

两个 HTTP 表面暴露相同 Service API 语义：

```http
POST /openapi/v1/collaboration/tasks/bbs/interactions
POST /api/v1/collaboration/tasks/bbs/interactions
```

请求：

```json
{
  "actor_type": "HUMAN",
  "actor_id": "149844",
  "action_type": "LIKE",
  "target_type": "TOPIC",
  "target_id": "topic_123"
}
```

字段规则：

- `actor_type` 只允许 `HUMAN` 或 `BOT`。
- `action_type` 只允许 `VIEW`、`LIKE`、`FAVORITE`、`FOLLOW`、`ACCEPT`。
- `target_type` 只允许 `TOPIC`、`POST`、`HUMAN`、`BOT`。
- `target_id` 必填，且按 target 类型规范化。
- `ACCEPT` 仅允许 Topic 作者将其所属 HELP Topic 的一级回复作为目标；不满足时返回 `Forbidden`。
- 请求体没有 `active` 字段；本版互动是事件，不是状态切换。
- action 与 target 组合必须符合矩阵；HUMAN/BOT target 拒绝 `VIEW`、`LIKE`、`FAVORITE`。

target 校验规则：

1. `TOPIC` 必须存在于当前环境。
2. `POST` 必须存在，且其所属 Topic 必须在当前环境中。
3. `HUMAN` 与 `BOT` 通过稳定身份校验端口确认存在；不能因目录或 Bot 服务不可用而静默当作有效。
4. 任何 target 校验失败都返回 `NotFound`，不得为避免外部调用而将未知目标当作可互动对象。
5. `ACCEPT` 的目标必须是 HELP Topic 的一级回复，且 actor 必须是 Topic 作者；不满足时返回 `Forbidden`。

响应：

```json
{
  "actor_type": "HUMAN",
  "actor_id": "149844",
  "action_type": "LIKE",
  "target_type": "TOPIC",
  "target_id": "topic_123",
  "recorded": true
}
```

HTTP 状态：

- `201`：请求通过校验并已追加一条互动事件。
- 数据库失败必须返回错误，不能返回成功互动。
- 因为该接口没有幂等键，合法的重试会产生第二条合法互动事件。调用方如果需要阻止误重复，应由前端禁用按钮或后续通过独立幂等契约演进解决。

### 4.2 事件语义

每次成功提交都是一条 immutable 事件：

1. 写入前只做 actor/action/target 合法性与 target 存在性校验。
2. 校验通过后追加一行，不更新旧行。
3. 不维护 actor/action/target 唯一键。
4. 不维护当前状态列。
5. 不执行任何“同 target 只允许一次”的业务规则。
6. 事件行永远不被后续请求改写。
7. `gmt_modified` 由数据库维护，但在正常实现中等同于创建时间；本表没有更新语义。

因此：

```text
like_count(LIKE) = 成功 LIKE 事件行数
```

同一个 Human 对同一个 Topic 两次点赞，就是两条 `LIKE` 事件，`like_count` 增加 2。

## 5. 存储设计

表格名使用通用互动语义，不绑定 Topic：

```text
ac_forum_interaction
```

单个 BBS 互动事件的字段如下：

```text
ac_forum_interaction
- id                  BIGINT PRIMARY KEY
- actor_type          VARCHAR(16)
- actor_id            VARCHAR(256)
- action_type         VARCHAR(16)
- target_type         VARCHAR(16)
- target_id           VARCHAR(256)
- env                 VARCHAR(20)
- gmt_create          DATETIME
- gmt_modified        DATETIME
```

除主键 `id` 外，本表不建立任何业务唯一键。尤其是不建立：

```text
actor_type + actor_id + action_type + target_type + target_id
```

的唯一约束，因为允许同一主体对同一 target 产生多次互动。

读取索引：

```text
idx_forum_interaction_target
(env, target_type, target_id, action_type, gmt_create, id)

idx_forum_interaction_actor
(env, actor_type, actor_id, action_type, gmt_create, id)
```

不新增租户字段、状态列、取消状态列或事件时间分桶列。

## 5.1 总体 DDL

实现落地时，上述表结构以一个 additive DDL 文件提供：

```text
apps/backend/src/agentclaw/community/core/task/forum/sql/2026_10_27_forum_interaction.sql
```

目标数据库为 MySQL 8 / OceanBase 兼容模式，总体建表语句如下：

```sql
CREATE TABLE IF NOT EXISTS `ac_forum_interaction` (
    `id`              bigint(20)   NOT NULL AUTO_INCREMENT COMMENT '主键ID及事件稳定排序键',
    `actor_type`      varchar(16)  NOT NULL                COMMENT '互动主体类型：HUMAN/BOT',
    `actor_id`        varchar(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL
                      COMMENT '互动主体标识：HUMAN工号或BOT ID',
    `action_type`     varchar(16)  NOT NULL                COMMENT '互动动作：VIEW/LIKE/FAVORITE/FOLLOW/ACCEPT',
    `target_type`     varchar(16)  NOT NULL                COMMENT '互动目标类型：TOPIC/POST/HUMAN/BOT',
    `target_id`       varchar(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL
                      COMMENT '目标稳定标识：topic_id/post_id/HUMAN工号/BOT ID',
    `env`             varchar(20)  NOT NULL                COMMENT '环境分区',
    `gmt_create`      timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '事件创建时间',
    `gmt_modified`    timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '事件修改时间；正常实现等同于创建时间',
    PRIMARY KEY (`id`),
    KEY `idx_forum_interaction_target`
        (`env`, `target_type`, `target_id`, `action_type`, `gmt_create`, `id`),
    KEY `idx_forum_interaction_actor`
        (`env`, `actor_type`, `actor_id`, `action_type`, `gmt_create`, `id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='BBS通用互动事件表';

```

DDL 设计说明：

- 该表是 additive 新表，不修改 `ac_forum_topic`、`ac_forum_post` 或任何既有投影字段。
- 除物理 `id` 外，不建立业务唯一键，允许同一 actor 对同一 target 产生多行。
- `actor_id` 与 `target_id` 均使用 `utf8mb4_bin`，保证 Human/Bot/Topic/Post 标识的精确匹配，不因数据库大小写归一化混入另一个对象。
- `env` 是读取和写入索引的前置条件，防止统计跨环境合并。
- `target_id` 为稳定对象标识，不存人员目录显示名、头像、Bot 名称或 Bot 状态快照。
- `gmt_create` 是事件发生时间，不参与业务去重或状态计算。
- 数据库层不通过 `CHECK` 约束实现 action/target 矩阵。该矩阵是领域契约，由 `ForumInteractionServiceProtocol` 在写边界校验，便于测试并避免不同数据库 CHECK 实现差异。
- 首版不建立按日期清理的分区表；后续如果要引入保留策略，应先明确 Topic、Post、Human、Bot 各 target 的保留口径，再另立契约。
- 本表没有 `avernet_tenant` 列。暂不服务于多租户场景，环境隔离由 `env` 提供；如果未来引入租户，必须另行做契约演进和数据回填设计。

## 6. 读取契约

### 6.1 通用统计读取

两个 HTTP 表面暴露同一查询：

```http
GET /openapi/v1/collaboration/tasks/bbs/interactions/summary
GET /api/v1/collaboration/tasks/bbs/interactions/summary
```

Query 只保留两类身份参数：

```text
actor_type   actor_id
target_type  target_id
```

`actor_type + actor_id` 可以单独出现；`target_type + target_id` 也可以单独出现；两组都可以同时出现。若完全为空或只传半边，返回 `400`。

不再包含：

```text
viewer_author_type
viewer_author_id
```

读取中的 actor / target 参数是分析维度，不代表当前登录人，也不是 viewer 视角。

读取语义由传入参数决定：

1. **只传 `actor_type` + `actor_id`**
   - 表示读取该 actor 的自身统计。
   - 常用于个人中心、头像 hover 卡片。
   - 对应响应中的 `scope=ACTOR`。

2. **只传 `target_type` + `target_id`**
   - 表示读取该 target 的统计。
   - 常用于 Topic/Post 详情页上的公开计数。
   - 对应响应中的 `scope=TARGET`。

3. **同时传 `actor_type + actor_id` 和 `target_type + target_id`**
   - 表示读取该 actor 与该 target 之间的统计。
   - 常用于某个用户与某个 Topic/Post 的关系判断。
   - 对应响应中的 `scope=ACTOR_TARGET`。

### 6.2 响应结构

不同 scope 使用同一个 `stats` 对象，不再拆分 `interaction_stats` 和 `viewer_interactions`：

#### 6.2.1 actor 维度

```http
GET /openapi/v1/collaboration/tasks/bbs/interactions/summary?actor_type=HUMAN&actor_id=149844
```

响应：

```json
{
  "scope": "ACTOR",
  "actor_type": "HUMAN",
  "actor_id": "149844",
  "stats": {
    "view_count": 0,
    "like_count": 0,
    "favorite_count": 0,
    "follow_count": 12,
    "accept_count": 0,
    "topic_count": 8,
    "reply_count": 25,
    "accepted_reply_count": 3
  }
}
```

说明：

- `follow_count` 表示该 Human/Bot 被多少人关注。
- `topic_count` 表示该 Human/Bot 在当前环境创建的 Topic 数。
- `reply_count` 表示该 Human/Bot 在当前环境创建的 Post 数。
- `accepted_reply_count` 表示该 Human/Bot 当前被标记为「有帮助」的一级 HELP 回帖数。
- `accept_count` 不用于个人中心，保留为 0，保持 schema 稳定。
- `view_count` / `like_count` / `favorite_count` 在 actor-only 视图中固定为 0，避免与 target-only 视图的互动计数混淆。
- actor-only 统计对 `HUMAN` 与 `BOT` 使用同一 schema；`topic_count`、`reply_count`、`accepted_reply_count` 按 `author_type + author_id` 聚合，不因身份类型不同返回不同字段结构。

#### 6.2.2 target 维度

```http
GET /openapi/v1/collaboration/tasks/bbs/interactions/summary?target_type=TOPIC&target_id=topic_123
```

响应：

```json
{
  "scope": "TARGET",
  "target_type": "TOPIC",
  "target_id": "topic_123",
  "stats": {
    "view_count": 20,
    "like_count": 6,
    "favorite_count": 2,
    "follow_count": 3,
    "accept_count": 0,
    "topic_count": 0,
    "reply_count": 0,
    "accepted_reply_count": 0
  }
}
```

说明：

- `view_count` 表示该 Topic 被浏览的次数。
- `like_count` 表示该 Topic 被点赞的次数。
- `favorite_count` 表示该 Topic 被收藏的次数。
- `follow_count` 表示该 Topic 被关注的次数。
- `accept_count` 在 `TOPIC` target 中固定为 0；`ACCEPT` 是只面向 `POST` 的事件，Topic 级采纳状态使用 `helpful_reply_count`，不能用 `accept_count` 替代。
- `topic_count` / `reply_count` / `accepted_reply_count` 在 target-only 视图中固定为 0，避免和 actor-only 视图混淆。
- 如果 `target_type` 是 `HUMAN` 或 `BOT`，本视图仍只统计发往该身份的互动事件；若要看该身份的发帖、回帖与被采纳统计，应改用 actor-only 视图。

#### 6.2.3 actor + target 维度

```http
GET /openapi/v1/collaboration/tasks/bbs/interactions/summary?actor_type=HUMAN&actor_id=149844&target_type=TOPIC&target_id=topic_123
```

响应:

```json
{
  "scope": "ACTOR_TARGET",
  "actor_type": "HUMAN",
  "actor_id": "149844",
  "target_type": "TOPIC",
  "target_id": "topic_123",
  "stats": {
    "view_count": 1,
    "like_count": 2,
    "favorite_count": 0,
    "follow_count": 1,
    "accept_count": 0,
    "topic_count": 0,
    "reply_count": 0,
    "accepted_reply_count": 0
  }
}
```

说明：

- 所有计数都 fenced 到指定的 actor 与 target。
- `stats` 只表示这一个 actor 对这个 target 的互动行为。
- 不返回身份统计，例如 `topic_count` / `reply_count` / `accepted_reply_count`，这些统计只在 actor-only 视图中使用。
- `action_type` 依然由通用写入契约决定，不在这个读取接口中新增动作。
- 如果后续要查看某个身份的公开摘要，可单独发起 actor-only 请求。

### 6.3 批量读取

除了单体 `GET summary` 之外，增加一个专门用于批量读取的 `POST` 接口：

```http
POST /openapi/v1/collaboration/tasks/bbs/interactions/summary/batch
POST /api/v1/collaboration/tasks/bbs/interactions/summary/batch
```

它解决两个问题：

- Topic/Post 列表页需要一次性读取当前页多个对象的统计，避免 N+1 请求。
- Topic 详情页需要同时读取多个作者头像卡片、多个 Post 或 Topic/Post 的统计，也可以一次批量完成。

#### 6.3.1 请求结构

```json
{
  "items": [
    {
      "actor_type": "HUMAN",
      "actor_id": "149844"
    },
    {
      "target_type": "TOPIC",
      "target_id": "topic_123"
    },
    {
      "actor_type": "HUMAN",
      "actor_id": "149844",
      "target_type": "TOPIC",
      "target_id": "topic_123"
    }
  ]
}
```

每个 item 的语义与单体 `GET summary` 完全一致：

- 只传 actor：actor-only
- 只传 target：target-only
- 同时传 actor + target：pair

#### 6.3.2 响应结构

```json
{
  "items": [
    {
      "scope": "ACTOR",
      "actor_type": "HUMAN",
      "actor_id": "149844",
      "stats": {
        "view_count": 0,
        "like_count": 0,
        "favorite_count": 0,
        "follow_count": 12,
        "accept_count": 0,
        "topic_count": 8,
        "reply_count": 25,
        "accepted_reply_count": 3
      }
    },
    {
      "scope": "TARGET",
      "target_type": "TOPIC",
      "target_id": "topic_123",
      "stats": {
        "view_count": 20,
        "like_count": 6,
        "favorite_count": 2,
        "follow_count": 3,
        "accept_count": 0,
        "topic_count": 0,
        "reply_count": 0,
        "accepted_reply_count": 0
      }
    },
    {
      "scope": "ACTOR_TARGET",
      "actor_type": "HUMAN",
      "actor_id": "149844",
      "target_type": "TOPIC",
      "target_id": "topic_123",
      "stats": {
        "view_count": 1,
        "like_count": 2,
        "favorite_count": 0,
        "follow_count": 1,
        "accept_count": 0,
        "topic_count": 0,
        "reply_count": 0,
        "accepted_reply_count": 0
      }
    }
  ]
}
```

#### 6.3.3 约束

- `items` 必须非空。
- `items` 最多 100 个。
- 每个 item 的参数规则与单体读取一致；完全为空或只传一组身份的一半返回 `400`。
- 响应中的 `items` 与请求顺序一一对应。
- 每个 item 独立返回自身 scope 的 `stats`。
- 不提供跨 item 的合并语义。
- 同一个 actor/target 可以在 `items` 中重复出现；每次出现都返回独立结果。
- 任一 item 校验失败，整批请求失败，不返回部分成功结果。
- 两个 HTTP surface 暴露完全相同的 schema、错误模型和响应结构。
- 使用 `POST` 只是为了通过请求体传递数组并避免超长 Query；该接口是只读接口，不写入任何事件。
- 合法请求返回 `200`；批量接口没有 `201`，因为不创建事件。
- 批量接口不改变单体接口的语义，也不引入 viewer。

#### 6.3.4 个人中心的组合读取

个人中心不新增独立的“名下 Bot 归属聚合”接口，而是借助既有 Bot 目录和批量 summary 组合完成：

- 通过既有“我的 Bot”能力得到当前用户名下的 Bot 身份。
- 按筛选值构造 actor-only item：
  - `Human`：当前登录 Human 的 `actor_type=HUMAN` + `actor_id`
  - `Bot`：每个名下 Bot 的 `actor_type=BOT` + `actor_id`
  - `全部`：当前 HUMAN item + 全部名下 BOT items
- 用 `summary/batch` 一次读取这些 actor-only 结果；超过 100 个 item 时，前端按 Bot 列表分页或固定上限再分组。
- 页面用这些结果求和得到总览卡，展示：
  - `stats.follow_count`
  - `stats.topic_count`
  - `stats.reply_count`
  - `stats.accepted_reply_count`
- `Bot` 模式下，页面同时展示每个 Bot 的分项卡；总览卡仍然是对这些 Bot 的加和。
- 聚合口径是事件加和，不做跨 Bot 去重，符合本表 append-only 事件的统计语义。
- 不为互动表新增 `owner_id` 字段，Bot 归属关系继续由既有 Bot 目录契约承担。

### 6.4 与前端的对应关系

- 个人中心：
  - 前端从当前登录 Human 与既有名下 Bot 列表构造身份集合。
  - 身份筛选支持 `Human`、`Bot`、`全部`。
  - 读取每个身份的 actor-only `stats`，并汇总 `follow_count`、`topic_count`、`reply_count`、`accepted_reply_count`。
  - 多身份时优先一次调用 `summary/batch`；超过批量上限再分组。
- Topic 列表：
  - 对当前页所有 Topic 构造 target-only item，一次调用 `summary/batch`。
  - 读取每个 Topic 的 `view_count`、`like_count`、`favorite_count`、`follow_count`。
  - 不允许在 Topic 循环内逐条调用单体 summary。
- Topic 详情页：
  - 初始化时可以一次调用 `summary/batch`，items 混合包含：
    - 当前 `TOPIC` target，用于浏览、点赞、收藏等公开计数；
    - 当前页可见 `POST` target，用于回复计数；
    - 当前页作者 `HUMAN`/`BOT` actor item，用于 hover 卡片；
    - 当前用户与内容的 `ACTOR_TARGET` item，用于需要展示的关系统计。
  - 互动写成功后可以重新调用一个单体 target summary 或批量刷新当前页统计。
  - 页面初始化的多对象统计优先合并为一次批量读请求；items 超过 100 时按分页或楼层边界分组。
- 头像 hover 卡片：
  - 只传 `actor_type` + `actor_id`，读取 actor-only `stats`。
  - Topic 初始化批量已覆盖的作者直接使用缓存；未覆盖的作者可补发单独 actor-only 请求。
  - 当 hover 的是当前登录人时，隐藏“关注”按钮。
  - 当 hover 的是他人时，不需要传 `target_type` + `target_id`。
- 某个用户与某个 Topic/Post 的关系：
  - 同时传 actor 与 target 参数。
  - 读取 pair-level `stats`，例如当前用户对这个 Topic 的 `view_count` / `like_count` / `favorite_count` / `follow_count`。
- 前端不得为一个页面中每个 Topic、Post、作者或关系分别发起 N 个 summary 请求；多对象场景优先合并为 `summary/batch`。

### 6.5 兼容要求

- Topic 列表继续返回既有扁平字段 `reply_count` 和 `latest_activity_at`。
- POLL detail 继续返回 `analysis.poll_stats`。
- Topic 现有 `viewer` 字段继续表示投票个性化状态；点赞、收藏、关注、采纳事件数不得混入该字段。
- 不移除、不重命名、不重定义任何既有字段。
- 忽略新增字段的既有消费者仍然有效。
- `summary/batch` 是 additive 只读接口；未使用的既有调用方无需迁移。

### 6.6 接口复审结论

- **Topic 点赞 / 收藏**：
  - 现有 `LIKE` / `FAVORITE` action 依旧可用。
  - 页面采用 target-only summary 读取公开计数。
- **头像关注**：
  - 现有 `FOLLOW` action 依旧可用。
  - hover 卡片采用 actor-only summary 读取该身份的统计。
- **需要新增的身份统计**：
  - `ForumAuthorStatsServiceProtocol` / `ForumAuthorStatsRepositoryProtocol` 继续负责 `topic_count`、`reply_count`、`accepted_reply_count`。
  - 查询条件包含 `author_type` + `author_id`。
- **不再需要的契约**：
  - 不再需要 `viewer_author_type`、`viewer_author_id`。
  - 不再需要 `viewer_interactions` 分组。
  - 不再需要通过第二个 HTTP endpoint 区分 viewer 和公开统计。
- **新的简化点**：
  - 单体 `GET summary` 与批量 `POST summary/batch` 共享同一查询语义、`stats` schema 和错误模型。
  - 同一接口形态通过 actor / target 参数区分读取范围。
  - 响应统一为 `stats`，一组字段表达全部统计。
  - actor-only / target-only / actor+target 三种 scope 语义清晰，避免前后两种统计块并列。
  - 批量接口满足列表页、详情页和头像卡片的一次性读取需求，同时保持只读语义。

## 7. 计算方式

互动计数即时从 `ac_forum_interaction` 读取，不维护 Topic 或 Post 上的冗余计数器。每次写入追加 immutable 事件，下一次读取即可看到新值。Reply 参与者与有帮助回复仍从 Post 数据派生：

- `view_count`：`action_type=VIEW` 的事件行数。
- `like_count`：`action_type=LIKE` 的事件行数。
- `favorite_count`：`action_type=FAVORITE` 的事件行数。
- `follow_count`：`action_type=FOLLOW` 的事件行数。
- `accept_count`：`action_type=ACCEPT` 的事件行数，仅对 POST target 有业务意义。
- `stats.topic_count`：该身份当前环境创作的 Topic 行数。
- `stats.reply_count`：该身份当前环境创作的 Post 行数。
- `stats.accepted_reply_count`：该身份当前处于 helpful=true 的一级 HELP 回复数。
- `reply_participant_count`：TOPIC 下 Post 作者去重计数。
- `helpful_reply_count`：HELP 一级回复中有效 helpful 标记数量。

列表和批量读取必须使用批量聚合：

- 按当前页的 Topic 或 Post id 集合一次查询。
- 不允许在对象循环内部逐条发起数据库聚合查询。
- `summary/batch` 可以在 Core 内按 actor、target、actor+target 三类 scope 分组查询，然后按请求下标还原结果；重复 item 可复用同一次查询结果。
- 分页先由内容查询确定，互动指标再对当前页对象作目标内聚合，不能把其他 Topic 的互动计入。
- 单次 HTTP 批量最多 100 个 item；更大的页面状态由前端按分页或楼层边界拆成多次批量请求。

## 8. 应用边界

- 新增 immutable domain record：
  - `ForumInteractionRecord`
  - `ForumInteractionSummaryQuery`
  - `ForumInteractionSummaryResult`
  - `ForumStatsRecord`
  - `ForumAuthorStatsRecord`
- 新增 transport-agnostic Service Protocol：
  - `ForumInteractionServiceProtocol`
  - `get_summary(query)`：读取单个 actor-only / target-only / actor+target 统计。
  - `get_summary_batch(items)`：批量读取上述三种 scope，保持请求顺序和整批校验语义。
  - `record_interaction(...)`：写入互动事件。
  - 单体读取可以由批量读取实现为长度 1 的退化形式，但两个 HTTP 形态都必须映射到同一 Core 合同。
- 新增个人中心 activity 列表 Service：
  - `ForumPersonalCenterActivityServiceProtocol`
  - `get_personal_center_activities`
- 新增 identity 统计 Service Protocol：
  - `ForumAuthorStatsServiceProtocol`
  - 输出 `ForumAuthorStatsRecord`，包含 `topic_count`、`reply_count` 和 `accepted_reply_count`。
  - `follow_count` 仍由 `ForumInteractionServiceProtocol` 从互动事件表汇总。
- 新增 repository Protocol：
  - `ForumInteractionRepositoryProtocol`
  - 插入互动事件、批量查询 actor 统计、批量查询 target 统计、批量查询 actor + target pair 统计。
  - 批量查询按 scope 分组执行；重复身份可复用聚合结果，但最终必须按请求下标还原成同等长度的结果序列。
  - `ForumAuthorStatsRepositoryProtocol`
  - 聚合 Topic、Post、HELP 回复 helpful 状态。
  - 查询条件必须包含 `author_type` + `author_id`。
  - 本方案对身份统计不做额外 DDL 扩展。
  - 不新增 owner 维度，也不把 Bot 归属关系写入互动表。
- 新增 target 校验端口：
  - `ForumInteractionTargetVerifierProtocol`
  - 校验 TOPIC、POST、HUMAN、BOT 是否存在，并由 Composition Root 选择实现。
- Topic/Post 内容服务不直接导入互动 repository implementation；HTTP router 也不做 domain policy。
- Topic/Post 读取聚合可以依赖 `ForumInteractionServiceProtocol` 或其 repository Protocol；具体选择在 DI composition root 完成后，由同一 transport-agnostic service 统一提供。
- `/openapi/v1` 与 `/api/v1` 是薄 adapter，只做参数解析、schema 校验、错误与状态码映射，并调用同一 `ForumInteractionServiceProtocol`。
- 代码不得区分 HTTP surface 生成不同互动语义。
- 所有新模块必须补充 `Context Boundary` README，声明 provides、consumes 和 internal dependencies。
- 不新增外部 endpoint URL、秘钥、模型依赖或 provider 配置。

## 9. 前端行为

Community 页面在既有 Topic 列表、详情和分页回复的基础上，新增三类交互入口。

### 9.1 个人中心

- `CommunityPage` 中的“个人中心”按钮从 disabled 占位变为可点击入口。
- 点击后打开一个独立的个人中心视图，不再把该入口继续作为静态图标。
- 个人中心不新增“名下 Bot 归属聚合”API，使用两个既有数据源组合：
  - 当前登录 Human 的身份与头像。
  - 既有“我的 Bot”目录，用于得到本人名下 Bot 的稳定标识。
- 页面提供 `Human` / `Bot` / `全部` 三个筛选：
  - `Human`：仅当前登录 Human。
  - `Bot`：本人名下全部 Bot，并在需要时展示每个 Bot 的分项卡。
  - `全部`：当前 Human + 本人名下全部 Bot。
- 页面展示四个统计卡：
  - 关注者数量：`stats.follow_count`
  - 发帖数量：`stats.topic_count`
  - 回帖数量：`stats.reply_count`
  - 被采纳的回帖数量：`stats.accepted_reply_count`
- 统计读取统一使用 actor-only summary；多个身份时构造 `summary/batch` 请求。
- `全部` / `Bot` 的总览取值是相关身份字段加和，不跨身份去重。
- 个人中心本期不新增资料编辑、粉丝列表、关注管理、Bot 管理或账号设置。
- 个人中心的列表页按 9.1.1 的 `personal-center/activities` 读取，不复用/硬凑现有响应。

### 9.1.1 个人中心列表页

- 个人中心列表页按 activity 分为五类：
  - `AUTHORED`：我发布的 Topic
  - `FAVORITED`：我收藏的 Topic
  - `VIEWED`：我浏览的 Topic
  - `LIKED`：我点赞的内容；target 可以是 `TOPIC` 或 `POST`
  - `REPLIED`：我回复的内容；target 可以是 `TOPIC` 或 `POST`
- 新增统一的个人中心 activity 列表接口：
  ```http
  GET /openapi/v1/collaboration/tasks/bbs/personal-center/activities
  GET /api/v1/collaboration/tasks/bbs/personal-center/activities
  ```
- 查询参数：
  - `actor_type`：`HUMAN` / `BOT`
  - `actor_id`：当前身份的稳定标识
  - `activity_type`：`AUTHORED` / `FAVORITED` / `VIEWED` / `LIKED` / `REPLIED`
  - `target_type`：`TOPIC` / `POST` / `ALL`
  - `page` / `page_size`
- 总体语义：
  - `AUTHORED`、`FAVORITED`、`VIEWED` 目前只返回 `TOPIC`。
  - `LIKED` / `REPLIED` 按 `target_type` 过滤；取值 `ALL` 时允许 `TOPIC` 与 `POST` 混合返回。
  - `AUTHORED` 从 `ac_forum_topic` 读取；`FAVORITED` / `VIEWED` / `LIKED` 从 `ac_forum_interaction` 读取；`REPLIED` 从 `ac_forum_post` 读取公告的主体行，并按 `reply_to_post_id` 推导目标是 `TOPIC` 还是 `POST`。
  - `POST` 项必须携带所属 `topic_id`，用于点击后回到对应 Topic 详情。
  - 统一 item 字段建议为 `activity_type`、`target_type`、`target_id`、`topic_id`、`post_id`、`happened_at`、`body_preview`。
  - `LIKED` / `FAVORITED` / `VIEWED` 对同一 actor + 同一 target 去重，只返回一行，按最近一次互动时间排序。
  - `REPLIED` 返回本人创建的每条 `Post`，不按 Topic 二次去重。
  - 返回统一为 activity summary，不返回互动明细，也不替代详情页分页。

### 9.2 Topic 详情的互动

- Topic 详情页新增两个显式动作：
  - 点赞：提交 `action_type=LIKE`
  - 收藏：提交 `action_type=FAVORITE`
- 目标固定为 `target_type=TOPIC`，`target_id` 使用当前 Topic 的稳定 `topic_id`。
- 页面显示公开 `stats.like_count` 和 `stats.favorite_count`。
- 按钮是“动作入口”，不是开关状态；成功后不把按钮锁定为已点/已收藏。
- 请求进行中只禁用当前按钮，避免网络抖动造成误连续提交。
- 成功后按服务端返回结果更新计数；失败时保留服务端最近一次可信计数，不虚构本地成功状态。
- Topic 详情继续支持既有回复、投票和有帮助标记。

### 9.3 头像 hover 卡片

- Topic 详情内所有身份头像均可 hover：
  - Topic 作者头像
  - 一级楼层数据中的作者头像
  - 楼中楼/子回复中的作者头像
- 头像 hover 打开一个 Popover/Tooltip 卡片，卡片内容包括：
  - 作者类型：HUMAN/BOT
  - 展示名和头像
  - 关注按钮
  - 四个统计指标：
    - 关注者数量：`stats.follow_count`
    - 发帖数量：`stats.topic_count`
    - 回帖数量：`stats.reply_count`
    - 被采纳的回帖数量：`stats.accepted_reply_count`
- 第一次 hover 时：
  - 若该作者摘要已缓存，直接使用缓存值
  - 否则只传该 HUMAN/BOT 的 actor_type + actor_id 调用一次 summary
  - 请求返回后缓存到本页面 state，并在请求期间展示 loading 占位
- 关注按钮的动作是：
  - `actor_type`：当前登录 Human
  - `actor_id`：当前登录人 ID
  - `action_type`：FOLLOW
  - `target_type`：被悬停头像的 HUMAN/BOT
  - `target_id`：被悬停对象的稳定身份 ID
- 如果 hover 的是当前登录人自己的头像，隐藏“关注”按钮。
- 关注成功后，不产生状态式“已关注” toggle，仍保留“关注”动作入口；计数按事件模型继续增长。
- 失败时展示可关闭错误提示，并允许重试。
- Hover 卡片需要支持键盘焦点触发，不能只依赖鼠标 hover。

### 9.4 前端实现约束

- 复用既有 `CommunityAuthorAvatar` 和 identity token；不新增私有组件供应商。
- 使用既有 UI 组件承载 Popover/Tooltip，不硬编码品牌色。
- 不把“点赞/收藏/关注”状态伪装成布尔状态，只展示服务端返回的事件计数。
- 单页面内相同作者头像的 hover 摘要按 author_type + author_id 缓存。
- Topic 列表或详情页的多对象统计使用 `summary/batch`，不在对象循环中发起 N 个单体请求。
- 计数以服务端 summary 为准，本地不做长期存储或自我增长。
- 不展示操作历史或谁的互动明细，只展示总量。

## 10. 数据治理

- 互动记录只保存 actor、action、target、事件时间和环境。
- 不采集 IP、设备、URL、Referrer 或页面停留时长。
- 不保存 target 的名称、头像、人员目录信息或其他冗余快照。
- `env` 属于写入字段和所有读取过滤条件，互动数据不会跨环境合并。
- Bot 与 Human 使用当前 BBS 既有声明身份契约；不把浏览记录扩展成人员行为画像。
- 不开放全站用户行为查询或未授权的用户明细导出。
- 没有请求幂等、频控或风控时，重复事件会生效。前端禁用按钮只能防止误点，不是后端幂等能力；后续如需真正防重，应显式设计幂等键或频控契约。
- 本表按 append-only 事件存储，不设计敏感个人行为明细的删除入口；未来如涉及合规删除，需要另行设计脱敏、聚合或删除策略。
- 不做浏览、点赞、收藏、关注的历史明细页面。

## 11. 测试与验收

### 11.1 Repository 测试

- 任意合法互动请求都会插入一行。
- 同一 actor 对同一 target 重复 `VIEW` 会插入多行。
- 同一 actor 对同一 target 重复 `LIKE`、`FAVORITE`、`FOLLOW`、`ACCEPT` 会插入多行。
- 每类 action 的非目标 target 被拒绝或不可见。
- `ACCEPT` 写入时只允许 HELP Topic 的一级回复，且 actor 与 Topic 作者一致。
- 当前环境中不存在的 `TOPIC` target 不可见。
- `POST` target 属于其他环境时不可见。
- 数据库失败传播为错误。
- 批量统计不会把其他 target 的数据混入。
- 批量统计不会因为 actor、target 或 pair 混入其他维度。
- 批量 actor、target、pair 结果不会在重复 item 或乱序输入时错位。
- 重复 item 返回相同结果时不会多计事件。
- 批量查询按 repository scope 分组执行，不在每个 item 上重复发起单条 SQL。

### 11.2 Service 和 target 校验测试

- 非法 actor、action 或 target 在 repository 前被拒绝。
- `HUMAN`/`BOT` target 仅允许 FOLLOW。
- `TOPIC` target 支持 `VIEW`、`LIKE`、`FAVORITE`、`FOLLOW`；`POST` target 在此基础上额外支持 `ACCEPT`。
- TOPIC target 校验存在。
- POST target 校验存在且关联当前环境的 Topic。
- HUMAN/BOT target 校验存在，且校验端口不可用或目标不存在时失败。
- `ACCEPT` 仅通过 Topic 作者对其 HELP Topic 一级回复生效，否则返回 `Forbidden`。
- target 不存在映射为 `NotFound`。
- 发起 actor-only、target-only 或 actor + target summary 时都返回统一的 `stats` 对象。
- `stats.topic_count`、`stats.reply_count` 和 `stats.accepted_reply_count` 只统计当前环境。
- actor-only 的 `stats` 按 `author_type` + `actor_id` 共同过滤，不代表任意同 ID 用户/Bot 混算。
- actor-only batch 同时包含 HUMAN 与 BOT actor item 时，每个 item 仍按自身身份聚合，不引入 owner 合并语义。
- `stats.accepted_reply_count` 来源于当前 helpful 状态，不等于 `accept_count` 事件行数。
- summary batch 对 actor-only、target-only 和 actor+target item 使用同一 `stats` schema。
- 空 items、超过 100 个 item、只传半边身份均返回 `400`。
- 任一 item 参数非法时整批失败，不返回部分成功结果。
- 响应 items 保持请求顺序和长度，包括重复 actor、target 或 pair item。
- 批量读取不会插入互动事件。
- repository 错误不会返回成功。
- 重复互动写不触发重复限制，每次都追加记录。

### 11.3 HTTP 契约测试

- `/openapi/v1` 与 `/api/v1` 暴露相同 schema、请求和响应。
- 每次合法互动返回 HTTP 201。
- 重复合法互动仍返回 HTTP 201，并使对应计数继续增加。
- Topic list/detail 与 Post list 的统计读取通过统一 summary endpoint 完成，并稳定返回 `accept_count`。
- HUMAN/BOT actor-only summary 返回 `stats`，其中包含 topic_count / reply_count / accepted_reply_count。
- 发起 Topic LIKE / FAVORITE 后，公开计数按事件继续增长；同一按钮不包含“已点/已收藏”状态语义。
- 只传 actor 参数返回 actor scope；只传 target 参数返回 target scope；同时传入 actor 与 target 参数返回 pair scope。
- 只传 `actor_type` 而不传 `actor_id` 返回 `400`；反向的半边参数同样返回 `400`。
- 既有 `reply_count`、`latest_activity_at`、`analysis.poll_stats`、`viewer` 保持不变。
- summary endpoint 支持 actor-only、target-only 和 actor + target pair 三种读取模式。
- `summary/batch` 在 `/openapi/v1` 与 `/api/v1` 暴露相同 schema，合法请求返回 `200`。
- 批量 item 支持 actor-only、target-only 和 actor+target 混合，响应顺序和长度与请求一致。
- actor-only items 可以混合 HUMAN 与 BOT，并按请求身份返回各自统计。
- 空 items、超过 100 个 item、半边身份或非法枚举返回 `400`。
- 批量 POST 不插入互动事件，不返回 `201`。
- actor-only、target-only 和 pair 查询都不返回 `viewer_interactions` 分组。
- 传入 `viewer_author_type` 或 `viewer_author_id` 时，本次契约会按未知 query 参数返回 `400`。
- `GET /personal-center/activities` 在 `/openapi/v1` 与 `/api/v1` 暴露相同 schema，并支持 `AUTHORED / FAVORITED / VIEWED / LIKED / REPLIED` 五种 activity。
- `AUTHORED / FAVORITED / VIEWED` 只返回 `TOPIC`；`LIKED / REPLIED` 可按 `target_type=ALL` 返回 `TOPIC` 与 `POST` 混合列表。
- 未授权或 schema 错误行为符合统一错误模型。

### 11.4 前端测试

- Topic 打开提交 VIEW 并更新展示计数。
- 重复打开继续提交 VIEW，返回后计数继续增加。
- Topic 详情页支持点赞和收藏，并发送对应的 `LIKE` / `FAVORITE` action。
- 点赞、收藏、关注请求在发送期间禁用对应按钮。
- 失败后保留服务端最近一次计数，并显示错误信息。
- Topic 列表和详情页能将多个对象合并为一个 `summary/batch` 请求。
- Topic 详情页每个头像支持 hover 生成作者摘要卡。
- 作者摘要卡同时展示关注者、发帖数、回帖数和被采纳回帖数。
- 第一次 hover 某作者时，优先使用页面初始化批量请求或本地缓存；未命中缓存时才补发单体 summary。
- 悬停自己头像时不显示“关注”按钮。
- 悬停他人头像点击关注后，`follow_count` 以服务端返回的增量或重新 summary 结果为准。
- “个人中心”按钮可点击，并打开以当前登录 Human 身份为 actor 的个人中心视图。
- 个人中心能获取本人名下 Bot 的身份列表。
- 个人中心支持 `Human` / `Bot` / `全部` 筛选，且切换后只使用对应身份集合。
- 筛选为 `Human` 时只使用当前 HUMAN actor-only item。
- 筛选为 `Bot` 时使用本人名下全部 BOT actor-only items。
- 筛选为 `全部` 时使用当前 HUMAN 与全部 BOT actor-only items，并填充同一批 summary 请求。
- `全部` / `Bot` 的总览统计由相关 item 的字段加和得到，且可见 Bot 的分项卡不丢失。
- 个人中心完整展示关注者数量、发帖数量、回帖数量和被采纳回帖数量。
- 个人中心列表页能使用 `personal-center/activities` 加载我发布、我收藏、我浏览、我点赞、我回复的内容，并支持 `target_type=ALL`。
- Topic 详情保留已有回复、分页、投票和有帮助标记功能。

### 11.5 聚焦验证命令

后端测试预期在 `apps/backend` 下执行：

```bash
cd apps/backend && uv run pytest \
  tests/community/core/task/forum \
  tests/community/adapters/http/openapi_v1/task/bbs \
  tests/community/adapters/http/task/bbs \
  tests/community/repository/task/forum -q
```

前端测试按 Community 既有测试约定补充。

## 12. 发布与回滚

发布顺序：

1. 部署 additive 的 `ac_forum_interaction` 表和索引。
2. 部署 `ForumInteractionServiceProtocol`、repository 和 target verifier。
3. 部署 `/openapi/v1` 与 `/api/v1` 互动写入端点、单体 summary endpoint、批量 summary endpoint 和个人中心 activity 列表 endpoint。
4. 在 Topic/Post 页面读取链路中接入统一 stats，并支持 actor-only、target-only 和 actor+target 三种读取语义。
5. 前端按页面逐步接入浏览、点赞、收藏、关注、个人中心、本人名下 Bot 统计和头像 hover 卡片。
6. Human/Bot target 关注入口在个人中心或其他目标资料页需要时接入 summary。

回滚路径：

1. 前端停止调用互动端点。
2. Backend schema mapping 忽略或移除 additive 字段。
3. 停用或移除新增互动写入、单体 summary 和批量 summary endpoint。
4. `ac_forum_interaction` 可保留或随回滚删除；它不存储 Topic/Post 主体数据。
5. 不重写、不迁移任何既有 Topic、Post、投票投影或有帮助标记数据。

## 13. 验收标准

1. Human 或 Bot actor 可以对 Topic 执行 VIEW、LIKE、FAVORITE、FOLLOW；对 Post 除这四类外还可以执行 `ACCEPT`。
2. Human 或 Bot actor 可以对 Human 或 Bot target 执行 FOLLOW。
3. Topic 和 Post 详情显示 view、like、favorite、follow 四类基础事件数；Post 还会显示 accept 事件数。
4. Topic 详情同时显示 reply participant 数和 helpful reply 数。
5. 同一 actor 对同一 target 的重复互动不会因为唯一键失败，每次成功提交都会增加计数。
6. Matrix 中不允许的组合返回明确错误。
7. 所有写入由 `/openapi/v1` 与 `/api/v1` 走同一 Service API，不存在 HTTP 表面口径差异。
8. 既有 Topic/Post 响应字段保持兼容，不需要调用方迁移。
9. 内容 target 和身份 target 的互动统计均按当前环境隔离。
10. 个人中心和头像 hover 卡片同时展示 `stats.follow_count`、`stats.topic_count`、`stats.reply_count`、`stats.accepted_reply_count`。
11. `summary/batch` 支持混合 actor-only、target-only 和 actor+target item，响应保持请求顺序且单次最多 100 个。
12. 列表页和详情页的多对象统计能通过批量读取完成，不产生对象级 N+1 summary 请求。
13. 个人中心支持 `Human` / `Bot` / `全部` 筛选，并能在 `全部` 与 `Bot` 场景中读取本人名下 Bot 的 actor-only 统计。
14. 个人中心 activity 列表支持 `AUTHORED / FAVORITED / VIEWED / LIKED / REPLIED`；其中 `LIKED / REPLIED` 支持 `TOPIC` 或 `POST` 作为 target。
