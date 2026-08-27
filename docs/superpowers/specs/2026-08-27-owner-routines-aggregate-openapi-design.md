# Owner 级定时任务聚合列表 OpenAPI 接口设计

- **日期**: 2026-08-27
- **整理人**: rongzhi（A 线）
- **需求来源**: 前端功能页面需要"查询当前用户（owner）名下所有 bot 的定时任务"平铺列表
- **本文性质**: 新增单端点的技术设计（方案已与需求方口头对齐收敛）

---

## 1. 背景与动机

定时任务（routine）配置存储在**每个 bot 自己的引擎侧**，后端不落库；查询必须 fan-out 到各 bot
的 runtime target。现有公开面 OpenAPI 只有 per-bot 列表：

```
GET /openapi/v1/bots/{bot_id}/routines        # 单 bot、仅 draft 运行态、Routine 无 bot_name
```

前端页面要展示"我的所有定时任务"时面临两个缺口：

1. 没有 owner 级聚合入口（要么前端 N+1 逐 bot 查，要么走 legacy `/api/cron`，违反
   "工坊交互全走 `/openapi/v1` 新接口"的刚性口径）；
2. `Routine` 响应结构**没有 `bot_name`**，聚合列表无法标注任务归属。

### 1.1 现状能力盘点（复用基础）

| 层 | 现状 | 位置 |
|---|---|---|
| 服务层 | `list_all_crons(user_id, nick_name, bot_id, runtime_stage)` 已支持 owner 级聚合：拉 owner-or-collaborator 全部 bot（上限 100）→ 并发 fan-out 各 runtime target → 每条装饰 `bot_id`/`bot_name`/`owner_id`/`runtime_stage` → 同 bot+stage+task 去重 → 部分失败容忍（`failed_targets`，warning 日志） | `core/cron/services/cron_relay.py:95` |
| legacy HTTP | `GET /api/cron?bot_id=all` 即 owner 级平铺列表（全运行态、不分页、原始 cron dict、响应含 `failed_targets`） | `adapters/http/cron/router.py:118` |
| 公开面 | 仅 per-bot 路由，`runtime_stage=DRAFT`，内存分页 | `adapters/http/openapi_v1/routines/router.py:120` |

**结论**：服务层零新逻辑，本次工作 = 把既有聚合能力按公开面契约暴露。

## 2. 需求口径（已收敛）

| 决策点 | 结论 |
|---|---|
| 调用面 | 新 TC / 门户前端，走 gateway 的 `/openapi/v1/bots/...` OpenAPI 契约 |
| owner 语义 | **查自己**：owner = 认证的当前用户（`user_id` query 惯例），不收 `owner_id` |
| 运行态范围 | **全运行态聚合**（draft+verify+online），对齐 legacy；同一配置可多行，靠 `runtime_stage` 区分 |
| 归属范围 | 服务层 `list_bots_by_owner_or_collaborator` 行为保持——用户**协作参与的他人 bot 的任务也出现**（legacy 页面现状） |
| bot_name | 必须返回（本需求的直接动因之一） |

## 3. 方案选型

- **选定方案 1**：新增 `GET /openapi/v1/bots/routines/all`，复用 `list_all_crons`。
  新地址干净，不动 deprecated 迁移故事；"all" 用词与 legacy `bot_id="all"` 一致。
- 否决方案 2（复活 deprecated 地址 `GET /openapi/v1/bots/routines`、`bot_id` 改可选）：
  deprecated 地址计划删除，改参数语义搅乱迁移故事（本仓库"地址即契约"立场见
  `deprecated/routines.py` 注释）。
- 否决方案 3（前端先拉 `/bots/all` 再逐 bot 查）：N+1 且无法分页。

## 4. 接口契约

```
GET /openapi/v1/bots/routines/all?user_id=xxx&page=1&page_size=20
→ 200 Envelope[Page[Routine]]
```

- `user_id`（必填 query，`UserIdDep`）：owner = 当前用户
- `page` / `page_size`（`PageParamsDep`）：**内存分页**（服务层一次性全量返回后切片，与
  per-bot 路由同款实现）
- 服务端不做过滤/排序：`status` 等过滤由前端按 `enabled` / `runtime_stage` 自行完成
  （与 per-bot 路由现状立场一致）

### 4.1 `Routine` 结构扩展（纯加法）

新增三个**可选**字段（默认 `None`，不破坏既有契约与 required 集合）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `bot_name` | `str \| None` | 任务归属 bot 的名称（服务层已装饰） |
| `owner_id` | `str \| None` | bot 的 owner 工号 |
| `runtime_stage` | `str \| None` | `draft` / `verify` / `online`（仅 service bot 有意义）同时给 per-bot 路由的 `_map_routine` 回填 `bot_name`（该路由仅有 `bot_id`，场景上 bot 身份已知）。`owner_id` / `runtime_stage` 仅聚合路由映射。

## 5. 后端实现

1. **新路由模块**：`openapi_v1/routines/` 下新增 owner 级小 router（单条 `GET ""`，
   prefix 即完整路径 `/openapi/v1/bots/routines/all`），不挂在既有
   `{bot_id}/routines` 前缀下（寻址语义不同）。
2. Handler 流程：`list_all_crons(user_id=user_id, nick_name=user_id, bot_id=None,
   runtime_stage=None)` → `_map_routine` 逐条映射（含三个新字段）→ 内存分页 →
   `page_envelope`。
3. `_map_routine` 由 per-bot 路由模块与聚合路由共享（同包内引用，避免复制漂移）。

### 5.1 鉴权、准入与挂载

- `admission.py`：声明 `("GET", "/openapi/v1/bots/routines/all"): AdmissionMode.GRANT_FILTERED`
  ——与 `GET /openapi/v1/bots/all` 同模式（owner 范围集合：授权应用见自己被委托范围内的结果，
  无授权拿空页）。
- `authorization.py`：加 `NoCheck` 条目（collection，非被寻址单 bot）。
- **挂载顺序**：挂进常规组（`_SUBGROUPS` / 相关组之后、`_LEGACY_*` 之前）。已核实
  `openapi_v1/__init__.py`：常规组先于 legacy shim 挂载，因此 deprecated 的
  `/openapi/v1/bots/routines/{routine_id}` 不会把字面量 `all` 当 `routine_id` 吞掉。
  （`{"routine_id": "all"}` 的 deprecated 详情请求仍按原语义 404/按实现处理，互不影响。）

## 6. 错误处理

- **部分失败**：个别 bot 引擎查询失败时，服务层返回成功部分 + `failed_targets`（已打
  warning 日志）；聚合路由只返回成功部分，**不暴露 `failed_targets`**（与 legacy 的差异，
  页面不需要，YAGNI）。
- `@envelope_errors` + `_PUBLIC_AUTH` 统一 401/403 错误信封。
- `user_id` 与认证 principal 不一致等场景沿用公开面统一 403 处理。

## 7. 契约与网关同步

1. 重新生成 `src/gateway/configs/schemas/bots.openapi.json`（新增 path + `Routine` 扩展字段）；
2. OCB/Sofapy Gateway 侧同步 schema（`~/IdeaProjects/ocb`，双写约定）；
3. 转发与鉴权：现有宽泛 `/openapi/v1/bots/**` 规则已覆盖，**无需新增网关路由规则**。

## 8. 测试

| 类型 | 内容 |
|---|---|
| Handler 单测 | 三新字段映射、分页切片、空 owner（无 bot → 空页 200）、多运行态多行（同 bot 同配置 draft+online 两条） |
| per-bot 回归 | per-bot 路由 `_map_routine` 回填 `bot_name` 的断言；既有测试不回归 |
| 准入清单 | `test_admission_inventory.py` 机制强制新路由在 admission 表中声明（漏声明直接红） |
| legacy parity | 不动 deprecated 地址，既有 parity 测试不受影响 |
| 覆盖率 | changed-line ≥80%，本地 `ci_test.sh` 验证（pre-push 只查 lint，看不到 coverage 门禁） |

## 9. 非目标（本次不做）

- 不支持查询他人（`owner_id` 参数）——协作者/管理员视角另议；
- 不解决服务层 100-bot 上限（`page_size=100` 封顶，个人场景够用）；
- 不做跨运行态去重（同 bot 同配置保留多行）；
- 不做服务端排序/`status` 服务端过滤；
- 不改 legacy `/api/cron` 行为。

## 10. 已知限制与后续

- 全运行态 = 同一配置多行，**前端必须用 `runtime_stage` 标注**（草稿/已发布），否则用户会
  认为"重复"；这是 legacy 页面已有的语义，接口不做隐藏。
- 内存分页 = 每次全量 fan-out，owner 名下 bot 多时 RT 放大；100-bot 上限内可接受，
  若后续页面有性能诉求再评估缓存或游标化。
