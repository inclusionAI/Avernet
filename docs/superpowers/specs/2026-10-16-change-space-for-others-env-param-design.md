# change-space-for-others 显式 env 订正能力设计

- 日期: 2026-10-16
- 状态: 待评审
- 基线分支: `origin/REL20261015`（`change-space-for-others` 端点当前只存在于该 REL，见 #2699）
- 建议分支: `feat/change-space-for-others-env-param`

## 1. 背景与动机

`POST /api/bots/change-space-for-others`（#2699，管理员批量迁移 bot 归属空间）在预发实测时（2026-10-15 20:16，trace `0b446a4d17920666103644083efd55`）对生产 bot 返回 `Bot不存在`。

根因不是 bug：`ac_bots` 为 prepub/prod 共库共表（同一内部数据源 datasource `agentclawdb_ds`），环境隔离靠 `env` 列在**仓储层查询条件**实现——`get_by_id_and_owner` / `update_space_by_owner` 均硬挂 `env == get_current_env()`。预发实例（`get_current_env() == "pre"`）永远匹配不到 `env='prod'` 的行。

诉求（用户确认）：**在预发实例上订正 prod 行的 `ac_bots.space_id`**。性质 = 当下权宜（prod 尚无此端点）+ 长期运维能力（带外修数）。

## 2. 目标与非目标

**目标**
- 端点支持调用方显式指定目标数据环境 env；
- 校验链全环节按目标 env 取行（读 bot、空间成员、配额、写）；
- 不传 env 时行为与现状完全一致（零回归）；
- 审计日志可区分环境，失败原因可从日志直读（本次排障暴露的死角）。

**非目标**
- 不扩展到 for-others 家族其他端点（仅本端点，用户确认）；
- 不做 env 取值白名单（用户确认；参照 sibling `repair-default-passport-for-others` 的 `Literal["pre","prod"]` 惯例故开放，非法值自然查空失败，无写入风险）；
- 不改变失败隔离语义与错误分类。

## 3. HTTP 契约

```json
POST /api/bots/change-space-for-others
{
  "space_id": 267,
  "bot_list": [{"owner_id": "137454", "bot_id": "20260729_oup3wzek"}],
  "env": "prod"
}
```

- `env`: `str | None`，可选。空白字符串视为未传（strip 后空 → None）。
- 不传 → effective_env = `get_current_env()`（现状）。
- 响应 `data` 增加 `env` 字段回显 effective_env。
- `super_admin()` 门不变。

## 4. 核心层穿透（单一码道）

统一模式：协作者签名加 `env: str | None = None`，在 `BotSpaceService.change_space` 入口归一**一次** `effective_env = env or get_current_env()`，之后全程仅传递 effective_env（字符串），避免多个环节各自兜底导致的不一致。

```
router(change_space_for_others, env=…)
  → BotSpaceService.change_space(bot_id, owner_id, space_id, env=…)
      → repo.get_by_id_and_owner(…, env)            # bot 行按 target env
      → space_access.require_space_member(…, env)    # 空间本体+成员按 target env
      → [desktop/teclaw/个人空间约束]                  # 纯内存判断，无 env 依赖
      → bot_quota.guard_add(…, env)                   # 配额按 target env
      → repo.update_space_by_owner(…, env)            # 写按 target env
```

需改签名的协作者（协议 + 实现同步）：
- `BotSpaceServiceProtocol.change_space` / `BotSpaceService.change_space`
- `SpaceAccessServiceProtocol.require_space_member` / `require_space`（require_space_member 依赖 require_space；quota `_scope` 也依赖）
- `BotQuotaServiceProtocol.guard_add`（内部穿透到 `_scope` / `_inspect` / `_locked`）

## 5. 仓储原语

改 4 个现有方法（不新增 `*_and_env` 平行方法，保持单业务码道）：
- `get_by_id_and_owner(…, env=None)`
- `update_space_by_owner(…, env=None)`
- `count_cloud_bots_in_personal_space(…, env=None)`
- `count_cloud_bots_by_space(…, env=None)`

`_env()` 改为 `_env(env: str | None = None)`：`self.Model.env == (env or get_current_env())`。

**配额锁键**：`BotQuotaService._lock_key` 中的 `get_current_env()` 必须替换为 effective_env 并把 env 参数透传进 `_locked(scope, env)`。否则预发实例订正 prod 配额计数时锁键落在 `pre` 段，与 prod 实例对同一行的配额写形成跨实例写竞争。

## 6. 审计日志

- 端点开始行：追加请求显式传入的 `env=%s`（未传时打 `env=none` 以便与显式空值区分）。
- 汇总行：追加 `env=%s`（effective_env）
- **新增失败原因计数**：汇总行追加 `failed_reasons={"BotNotFound": 1, …}`，从 `results[].message` 归类（bot 不存在 / 空间校验 / 约束 / 配额）。消灭"日志只有 failed=1、原因只在 HTTP 响应"的排障死角（本次排障实证）。

## 7. 错误处理

- 失败隔离语义原样：单项失败只落该条 `results[]`，不阻断批次。
- 错误分类不变：`BotNotFoundError` / `SpaceError` / `BotOperationNotAllowedError` / `BotQuotaError`。
- env 与行不匹配 → 自然落 `BotNotFoundError`（`Bot不存在: {bot_id}`），不造新错误码。
- 未识别 env 值 → 同上，查空失败，无副作用。

## 8. 测试计划

1. **穿透链单测**：`BotSpaceService` 单测采用 mock 协作者，断言链上每个环节（read / member / quota guard 含锁键 / write）收到的 env 均等于显式传入值。
2. **默认行为单测**：不传 env → 所有协作者收到 `get_current_env()`（向后兼容断言）；空白字符串 → 等价不传。
3. **路由测试**：`TestChangeSpaceForOthers` 扩展——env 字段解析、回显、汇总行含 env 与 failed_reasons。
4. **配额测试**：`guard_add(env=…)` 显式路径计数与锁键按 target env。
5. 家族回归：`bot_router` 既有用例全量通过。

## 9. 发布与合流

- 实现基于 REL20261015（预发当前已滚该版本，上线即可用）。
- 合流进 dev 时需先确认 #2699 端点主体已随 REL→dev 回流；若尚未回流，则本能力与端点主体同批带回（实现含 REL 版与 dev 版两份 router 时以 dev 为准做适配）。
- CI 前置：changed-line coverage ≥ 80%；供应商词禁令适用于注释与测试描述。

## 10. 风险与取舍记录

- **放开 env 白名单**（用户决策）：与 `repair-default-passport-for-others` 的 `Literal["pre","prod"]` 不一致；接受理由=super_admin 门即授权边界，非法值查空失败无副作用。
- **optional 参数而非平行方法**（与家族 `_and_env` 先例不同）：保持单业务码道，避免策略漂移；先例（`get_live_by_id_owner_and_env`）服务的是"恰一行严格语义"场景，与本次批量语义不同。
- **跨环境写的安全性**：预发实例操作 prod 行是本设计的**目的**而非缺陷；防线=super_admin 门 + 审计日志 + 配额按目标环境校验。风险点在预发代码版本可能领先 prod（写语义差异）——接受，因订正功能通常正是为了用新逻辑修旧数据。