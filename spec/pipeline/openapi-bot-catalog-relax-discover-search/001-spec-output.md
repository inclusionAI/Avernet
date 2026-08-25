---
agent: tc-review
status: completed
created: 2026-08-22T20:00:00+08:00
iteration: 1
---

# 系分 Spec: 放宽 Catalog Discover 字段并取消 Catalog Search 的 public 过滤

## 需求概述

修复 `GET /openapi/v1/bots/catalog/discover` 对旧推荐服务有效历史输出的兼容性：公开响应仍是严格的字段白名单，但暂不对 `bot_type`、`owner_name`、`recommendation.reasons`、`recommendation.short_profile` 施加枚举或类型约束，避免这些字段的实际值触发 OpenAPI 适配层 `502000`。

同时，`GET /openapi/v1/bots/catalog/search` 的 Backend 侧精确地址 join 不再以 `ac_bots.public = "1"` 过滤；BCS 当前页、当前 tenant、未删除记录、当前环境和精确 `(bot_id, entity_id)` join 仍为必须条件。此变更不改变旧 `/api/v1/bot-public/search` 与 Discover 的既有 public 查询规则。

## 编码 Spec

### 功能点

- [ ] Discover 的公开模型中，`bot_type` 不再限定为 `personal | service | desktop`；`owner_name`、`recommendation.reasons`、`recommendation.short_profile` 不再限定为字符串、字符串数组或 `null` 的组合。
- [ ] Discover router 继续仅从 service record 明确投影公开字段；上述四个字段按原值进入公开响应，不能因其值的类型或枚举不符合旧模型而映射为 `502000`。
- [ ] Discover 仍保留 `bot_id`、`entity_id`、`name`、`description`、`engine`、`status`、`recommendation.score` 的现有接口约束，以及推荐服务不可用/缺少 `recommend_response` 时的固定 `502000` 语义。
- [ ] Catalog Search 改为使用现有“live exact pair”读取能力查询 BCS 当前页的地址集合，不再调用带 `public = "1"` 条件的 repository 方法。
- [ ] Search 结果仍按 BCS 返回地址顺序做精确 inner join，并以 join 后数量作为该 BCS 页的 `total`；不得回退到 Backend-only、跨 entity 的 `bot_id` 匹配或额外二次分页。
- [ ] 更新前端中文 Catalog 文档和生成后的 Gateway OpenAPI schema，使 Discover 的上述四个字段不再声明过窄的 enum/type；Search 文档明确其成员资格由 BCS 当前页与 Backend live exact pair 共同决定，而非 Backend `public` 标记。

### 技术方案

Discover 保持现有 `BotDiscoverService -> OpenAPI router` 链路。仅在 OpenAPI schema/投影边界将四个兼容字段表示为不作值域限制的 JSON 值（Python `Any` 等等价的无约束声明）；不要把整个原始 `record` 或 `recommend` 对象透传。router 仍显式构造 `PublicBot` / `Recommendation` 的 allowlist，只放行约定字段，因而 `binding_id`、数据库主键、`device_id`、`ext`、token、环境字段、`profile_key`、原始 `recommend_response` 等内部字段继续不可见。

Catalog Search 不新增 BCS 请求参数、路由、DI binding 或 repository SQL。BCS metadata adapter 继续用当前请求的 `search/page/page_size` 取得地址页；应用 service 使用已存在的 `list_bots_by_owner_bot_pairs(pairs, page=1, page_size=len(addresses))`（或语义等价的既有 live exact-pair read）取回全部可 join 的本 tenant、未删除、当前环境 Bot，并丢弃其数据库分页 total。随后以地址复合键恢复 BCS 顺序，得出对外 `items` 和 `total`。

不得修改当前 cherry-pick 冲突文件、无关 Discover BCSFuse format 日志、gateway 鉴权、admission、BCS HTTP 客户端、旧接口或 `.superpowers/`。不得进行格式化、重排或相邻重构；每一处生产改动必须可追溯至本需求。

### 关键方法抽象

| 抽象/方法 | 所在层或模块 | 职责与边界 | 输入与输出 | 协作对象与副作用 |
|---|---|---|---|---|
| `_public_bot(record)` / Discover response projection | OpenAPI HTTP adapter | 从 service record 明确生成公开 allowlist；不得校验或转换本次放宽的四个值，也不得透传原始 record。 | `Mapping[str, Any]` → 公开 Bot 字段；原有必需结构缺失仍是 adapter 异常。 | `PublicBot`、`DiscoveredPublicBot`；无网络/持久化副作用。 |
| `search_catalog_public_bots_by_keyword` | Bot public application service | BCS 当前页与 Backend live exact pairs 的 inner join 编排；不负责 BCS HTTP 解析、HTTP response 或 public 字段投影。 | 已验证 caller、请求分页/关键词、request ID → BCS 顺序的 joined items 与 join 后 total；metadata 异常仍 fail closed。 | metadata service、Bot repository；只读查询与脱敏计数日志。 |
| `list_bots_by_owner_bot_pairs` | Bot repository | 在 tenant guard、`is_delete == 0` 和当前环境约束内按精确 `(bot_id, owner_id)` 读取 live Bot；不施加 public 条件。 | pairs + 覆盖 BCS 地址数量的页面大小 → 所有匹配记录。 | ORM read；无写副作用。 |

Discover adapter 之所以保留显式投影，是为了“放宽四个字段”不演变成原始推荐结果透传。Search service 复用现有 live exact-pair read，避免新建仅为取消一个条件的 repository/SQL 抽象；调用方必须保证 `page_size` 覆盖经校验、去重后的 BCS 地址数。

### 关键领域模型设计

#### `PublicBot`（公开投影，字段约束调整）

**模型说明**：OpenAPI 对外返回的 allowlist DTO，不持久化、不承载内部 Bot 实体。生命周期只在 HTTP response 构造期间；`DiscoveredPublicBot` 继承它。

| 字段 | 类型/格式 | 必填 | 默认值或约束 | 来源/所有者 | 字段说明与兼容性影响 |
|---|---|---|---|---|---|
| `bot_id` | string | 是 | 非空由既有 Backend record 保证 | Backend Bot | 稳定公开地址的一半；保持原约束。 |
| `entity_id` | string | 是 | 非空由既有 Backend record 保证 | Backend owner/entity | 稳定公开地址的另一半；保持原约束。 |
| `bot_type` | 无约束 JSON 值 | 是 | 不做 enum 或类型校验 | Backend/旧推荐链路 | 临时兼容字段；不得用于权限或 join 判定。 |
| `owner_name` | 无约束 JSON 值或省略 | 否 | 不做类型校验 | Backend owner projection | 临时兼容字段；不透传 owner 其它内部字段。 |
| `name`、`description`、`engine`、`status` | string | 是 | 保持既有公开投影 | Backend Bot | 不在本次放宽范围。 |

**关系与不变量**：该模型只允许 router 列出的字段；任何不存在于表中的 service record 字段均不得序列化进入 HTTP response。

#### `Recommendation`（公开投影，字段约束调整）

**模型说明**：Discover 对单个 `PublicBot` 的推荐补充 DTO，不持久化；来源是旧推荐服务结果经 `BotDiscoverService` 关联后的 `recommend` 子对象。

| 字段 | 类型/格式 | 必填 | 默认值或约束 | 来源/所有者 | 字段说明与兼容性影响 |
|---|---|---|---|---|---|
| `score` | float | 是 | 保持既有数值约束 | 推荐服务 | 不在本次放宽范围。 |
| `reasons` | 无约束 JSON 值 | 是 | 不做数组或元素类型校验 | 推荐服务 | 临时兼容字段，仅输出 allowlisted `reasons` 值。 |
| `short_profile` | 无约束 JSON 值或省略 | 否 | 不做类型校验 | 推荐服务 | 临时兼容字段，仅输出 allowlisted `short_profile` 值。 |

**关系与不变量**：不得输出 `recommend` 的任何其它字段（例如 `profile_key`），不得输出原始 BCSFuse 推荐 payload。

#### `BotCatalogAddress`（既有值对象，使用语义调整）

**模型说明**：由 BCS metadata 的 `bot_id + entity_id` 构成的内部复合地址；本次不改字段或生命周期，但 Search 的 Backend join 不再以 `public` 作为额外成员条件。

| 字段 | 类型/格式 | 必填 | 默认值或约束 | 来源/所有者 | 字段说明与兼容性影响 |
|---|---|---|---|---|---|
| `bot_id` | string | 是 | 非空、与 entity 组合精确匹配 | BCS metadata | 同 bot_id 不同 entity 必须隔离。 |
| `entity_id` | string | 是 | 非空、与 bot 组合精确匹配 | BCS metadata | 不能由调用方省略或用 owner 名替代。 |

**关系与不变量**：只允许当前 tenant、未删除、当前环境的 Backend Bot 参与 join；`public` 不再是 Catalog Search 的不变量。BCS 地址未命中 Backend 时必须被 inner join 排除。

### 文件改动范围

| 文件路径 | 改动类型 | 改动说明 |
|---|---|---|
| `src/backend/src/agentclaw/community/adapters/http/openapi_v1/bot_public/schemas.py` | 修改 | 仅放宽 Discover 所列四个公开字段的 enum/type 声明。 |
| `src/backend/src/agentclaw/community/adapters/http/openapi_v1/bot_public/router.py` | 修改 | 保持显式 allowlist 投影，使上述值不触发 Pydantic 约束错误。 |
| `src/backend/src/agentclaw/community/core/bot_public/services/bot_public_service.py` | 修改 | Catalog Search 改用现有 live exact-pair repository read，保留 BCS 顺序与 fail-closed。 |
| `src/backend/tests/community/adapters/http/openapi_v1/bot_public/test_bot_public_router.py` | 修改 | 为四个非传统类型/非枚举值的 Discover success 与敏感字段零泄露添加回归断言。 |
| `src/backend/tests/community/core/bot_public/test_bot_public_service.py` | 修改 | 断言 Catalog Search 使用非-public live exact-pair read，并保留精确 join/顺序/total。 |
| `src/backend/tests/community/repository/bot/test_bot_tenant_raw_sql_and_threads.py` | 修改（仅必要时） | 锁定复用的 live exact-pair read 不过滤 `public`、仍 tenant/is_delete/environment scoped。 |
| `src/backend/docs/openapi-v1/bot-public-catalog-api.zh-CN.md` | 修改 | 同步前端可见字段约束与 Search 成员资格。 |
| `src/gateway/configs/schemas/bots.openapi.json` | 生成更新 | 同步后端 OpenAPI schema 中放宽后的 Discover 字段。 |
| `log.md` | 修改 | 记录本次最小功能变更与验证结果，不记录 payload、token 或敏感 ID。 |

### 验收标准

- [ ] 对同一真实 Discover 结果形状，`bot_type` 为非三枚举值、`owner_name` 为非字符串、`reasons` 非字符串数组、`short_profile` 非字符串时，接口仍返回 `200000`，且四个 allowlisted 值完整保留。
- [ ] Discover 响应仍不含 `binding_id`、数据库 `id`、`device_id`、`ext`、token、环境字段、`profile_key`、`recommend_response` 或其它原始 record/recommend 字段。
- [ ] 推荐服务抛异常或未返回 `context.recommend_response` 时，Discover 仍返回固定 `502000`；本次不能把真实上游不可用伪装为成功空列表。
- [ ] Search 中 BCS 返回一个 `public="0"` 且 tenant/current-env/`is_delete=0`/精确 pair 均匹配的 Bot 时，该 Bot 出现在结果中。
- [ ] Search 仍排除跨 tenant、已删除、跨环境或同 bot_id 不同 entity 的 Backend 记录；BCS 未命中 Backend 的地址不返回。
- [ ] Search 的 `items` 顺序等于 BCS 当前页的地址顺序，`total == len(items)`，不因移除 public filter 改变 BCS 分页边界。
- [ ] 改动文件单测行覆盖率 > 90%，以 `pytest --cov --cov-report=term-missing` 实测；并通过 Ruff、unused import 检查和 `git diff --check`。

## Review Spec

### 关注点

- 放宽仅限指定四个 Discover 字段，绝不扩大为 service record/raw recommendation 的透传。
- Search 仅移除 `public="1"` 这一业务过滤；tenant guard、`is_delete`、environment、复合键精确 join 和 BCS 页边界必须不变。
- 不得以 catch-all 忽略 adapter 错误、跳过异常记录或修改推荐服务/BCSFuse format 日志来掩盖契约问题。

### 检查项

- [ ] `bot_type`、`owner_name`、`reasons`、`short_profile` 的 OpenAPI schema 不再包含旧 enum/type 限制；其它公开字段约束未被放宽。
- [ ] Discover 仍对 `record` 和 `recommendation` 的容器 shape 做最小结构检查，且仅手工列出的公开字段进入模型。
- [ ] `recommendation.score` 未被降级为无约束值，502 的真实“推荐服务不可用”分支保持。
- [ ] Search 没有调用 `list_public_bots_by_owner_bot_pairs`；选用的 read 已由测试证明没有 public 条件，且保留 tenant/is_delete/environment/exact pair 约束。
- [ ] 关键方法的职责边界、输入输出、错误处理和副作用与「关键方法抽象」一致，未将业务规则泄漏到不应承担的层。
- [ ] 领域模型的关系、不变量及字段类型、必填性、默认值和约束均按设计实现；序列化、接口兼容性影响已处理。
- [ ] 本次变更单测行覆盖率 > 90%（改动文件实测，未达标判 REJECT）。

### 不可接受的模式

- 将 `dict(record)`、`record["recommend"]` 或原始 BCSFuse 响应直接作为 HTTP response：会泄露未在 allowlist 中的内部字段。
- 为解决 Discover 502 而删除全部 response model 或改为吞掉所有异常并返回部分结果：会失去公开契约和真正上游故障信号。
- 在 Search 中删除 tenant/is_delete/environment/复合地址任一约束，或仅用 `bot_id` join：会产生跨租户、历史或同名 Bot 泄露。
- 通过改动 BCS 请求、Gateway、鉴权、Discover service format 日志或无关 repository 重构来实现本需求：均超出最小变更。
- 未使用的 import / 局部变量（IDE/linter ACI 告警）；因本次改动残留的孤儿代码。
- Python 风格违规：`:` 前有空格、block comment 未以 `# ` 开头。

## QA Spec

### 测试用例

| 编号 | 用例名称 | 操作步骤 | 预期结果 |
|---|---|---|---|
| TC-01 | Discover 兼容旧 bot_type | Mock 推荐记录的 `bot_type` 为非 `personal/service/desktop` 值并调用 Discover。 | `200000`；返回该 allowlisted 值，无 502。 |
| TC-02 | Discover 兼容非字符串元数据 | 分别令 `owner_name`、`reasons`、`short_profile` 为 JSON object/number/mixed array 等有效 JSON 值。 | `200000`；值原样位于对应 allowlisted 字段。 |
| TC-03 | Discover 仍隔离敏感字段 | 在 record/recommend/context 注入 binding、device、ext、token、profile_key、raw response。 | 成功响应不包含任何敏感或未白名单字段。 |
| TC-04 | Discover 上游不可用 | service 抛异常或 context 中 `recommend_response=None`。 | 固定 `502000`，不输出内部错误。 |
| TC-05 | Search 包含 non-public live Bot | BCS metadata 返回一个精确地址；Backend 返回 `public="0"`、live/current tenant/env 的 Bot。 | 该 Bot 被 join，并计入 total。 |
| TC-06 | Search 保留隔离条件 | 同时准备跨 tenant、deleted、跨 env、同 bot_id 不同 entity、BCS 未命中 Bot。 | 仅同 tenant/live/current-env/exact pair 返回。 |
| TC-07 | Search 页顺序与 total | BCS 当前页返回乱序复合地址，其中部分无 Backend 命中。 | items 按 BCS 顺序，total 等于 joined items 数。 |
| TC-08 | 文档与生成 schema | 生成 OpenAPI 并检查文档。 | 前端文档与 `bots.openapi.json` 不再声明四字段的旧限制。 |

### 前置条件

- 使用现有 Backend 单测 fixture/mocks，不请求真实 BCSFuse 或 BCS。
- repository 测试需处于可切换 tenant 与当前环境的数据库 fixture 中；测试数据只含非敏感虚拟标识。

## Ship Spec

### 部署目标环境

- [ ] 线下环境
- [ ] 预发环境

### 分支策略

- 开发分支: 基于最新 `dev_refactory_collaboration` 的新分支（不创建新 worktree）。
- 目标分支: `dev_refactory_collaboration`。

### 回滚方案

回滚本次提交即可恢复旧 Discover 字段约束和 Search `public="1"` 条件。若预发发现非 public 搜索结果不符合产品授权预期，停止发布并回滚；不得在线添加临时按 bot_id 的放行名单或改为 Backend-only fallback。
