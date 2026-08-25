# `/bots/all` 卡片 `edit` 动作对 MEMBER 放行设计

- **日期**：2026-08-25
- **状态**：待评审
- **范围**：Backend OpenAPI Bot Inventory（`GET /openapi/v1/bots/all`）卡片动作收缩
- **目标分支**：`dev`

## 1. 背景与证据

`/all` 卡片的 `actions` 收缩（`BotInventoryService._actions_for_level`）现状：非 service 卡的
**非 OWNER**（含 ADMIN/MEMBER/NONE）一律只保留 `view`，其余动作 disabled
（"Bot editor permission required"）。

这与同一代码库里三处既有语义冲突：

| 机制 | 语义 | 位置 |
| --- | --- | --- |
| `PermissionLevel.MEMBER` 的定义 | **"仅可编辑内容"** | `bot_collaborator/models.py:44` |
| skill / skill-sets 端点门禁 | `Check/ServiceChecked(PermissionLevel.MEMBER)`——协作者可增删技能 | `openapi_v1/authorization.py:319-347` |
| 老内部接口 `can_edit_bot` | owner-or-collaborator 列表内非 service bot 恒 `True` | `bot_service.py:2494`、`bot_publish_service.py:863` |

后果：前端若按卡片 `actions.edit` 渲染"技能集管理"入口，MEMBER/ADMIN 协作者会被卡片
误导为无权限，端点实际却允许——"按 actions 渲染"这一卡片区设计承诺失效。

## 2. 决策

非 service 卡的非 OWNER 分支：`level >= MEMBER` 时**保留 `EDIT`**：

```text
kept = [EDIT]  当且仅当 level >= MEMBER
actions_out = (VIEW, *kept)
其余动作（restart/delete/data_init/passport 等 owner 型）disabled 逻辑与文案不变
```

- NONE：行为不变（仅 view；disabled 文案沿用 "Bot editor permission required"）。
- OWNER：不变（全额动作）。
- SERVICE 卡：不受影响（原本 MEMBER+ 即保留除 DELETE 外动作，仅重述既有语义）。
- disabled 文案本轮不动；对 MEMBER 级 owner 型动作的文案精确化
  （"Bot Owner permission required"）留作可选后续，避免联动 service-viewer 断言。

卡片与端点的动作-门禁对应（修正后）：

| 卡片动作 | 收缩档位 | 对应端点门禁 |
| --- | --- | --- |
| `edit` | MEMBER+ | skills/skill-sets 族 `Check(MEMBER)` |
| `restart` / `delete` / `data_init` 等 | OWNER+ | `/restart` `PUT /{bot_id}` `DELETE /{bot_id}` 均 OWNER_SCOPED |

## 3. 影响评估

- 老内部路径零影响：`BotInventoryService` 全仓唯一消费方是 `/all` 端点；
  `/api/bots/*`（含 `can_edit_bot`）不经此实现。
- 公开 schema 零变化：`edit` 已在 `BotAction` 枚举，响应属 additive（actions 数组多一个值）。
- 无数据库、无配置、无内部 API 变化。
- `/all` 响应变化仅一处：MEMBER/ADMIN 级非 service 卡 `actions` 增加 `edit`，
  `disabled` 减少对应项（补齐与技能能力的对齐）。

## 4. 测试

- 更新 `tests/community/core/bot_inventory/services/test_bot_inventory_service.py`：
  非 service 卡 member-editor → `(VIEW, EDIT)`；viewer（NONE）→ 仅 view 不变；
  owner 不变；service 卡断言全部不变。
- 路由层回归：`tests/community/adapters/http/openapi_v1/inventory/`、
  bot_management 全量与 architecture 不回归。

## 5. 验收标准

- [ ] MEMBER/ADMIN 协作者的非 service 卡含 `edit`；NONE 不变。
- [ ] service 卡、OWNER 卡行为不变。
- [ ] disabled 文案与既有断言不漂移。
- [ ] 上述测试与架构测试全绿。
