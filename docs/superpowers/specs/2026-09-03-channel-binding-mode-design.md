# 渠道绑定方式统一设计（binding_mode）

- 日期：2026-09-03
- 状态：设计已口头确认，待评审落定
- 范围决策：方案 B（agentclaw 统一契约）；只做 bot 维度；服务 bot / 群绑定面不动

## 1. 背景与目标

老 TC 的渠道管理支持两种**用户可选**的钉钉机器人绑定方式：

| 维度 | 基于开源插件 | 基于 BCN |
|---|---|---|
| 用户配置 | app_key/app_secret/卡片 templateId/卡片变量 key | ak/sk/卡片 id + 生成蚂蚁钉回调地址去开放平台配 HTTP 网关 |
| 接入路径 | openclaw.json `channels.dingding` → openclaw-channel-dingtalk 插件直连 | BCN 网关收钉钉消息 → 路由到 bot 实例 |
| session 语义 | 群内共享 session | 群内每用户独立 session + 多实例亲和 |
| 能力边界 | 插件高阶能力全量（结构化配置只覆盖基础） | 只支持流式消息/图片/文件/`/new` |

新体系现状（2026-09-03 盘点）：

- **插件路径已产品化**：`/openapi/v1/bots/{bot_id}/channels`（agentclaw，Python）全链路钉钉独占，激活写 openclaw.json，发布走 `engine_overrides.channels.dingding`。
- **"BCN 类"能力长在 BCS**（`src/bcs`，Rust）：`/openapi/v1/collaboration/channels/bindings` 已有 CRUD（`channel_type: dingtalk`、`target: bot|group`、`group_chat_scope: per_sender`＝session 隔离、`outbound_visibility`、robot_code/app_key/app_secret/send_mode 脱敏回显）；前端群面板 `DingTalkConfigPanel` 已接；运行时插件 `openclaw-channel-bcn`（WebSocket 连 BCS，`channels.bcs` 配置）已发布 npm。
- **缺口**：①BCS 的 `ChannelProviderRegistry` 仅注册 test provider，真实钉钉 provider 不在本仓库；②产品入口错位——老 TC 在 bot 工坊选绑定方式，新体系 BCS 绑定入口在群管理面板；③两套 API 无统一契约。

**目标**：`/openapi/v1/bots/{bot_id}/channels` 增加 `binding_mode` 维度，复刻"一个渠道管理入口、两种绑定方式"，对外一份 OpenAPI 契约。

## 2. 决策记录

| 决策点 | 结论 | 理由 |
|---|---|---|
| 统一层次 | agentclaw 统一契约（非前端聚合） | 鉴权模型统一（bindings API 是人类会话鉴权，无 bot admin/edit-lock）；对外一份文档面 |
| 范围 | 只做 bot 维度 | 服务 bot 已自动走 BCN 类链路，加显式选择引入配置漂移风险 |
| `binding_mode` 可变性 | 创建后不可变，切换＝删除重建 | 避免"openclaw.json 已写插件配置 + 还要清 BCS 绑定"的双面清理 |
| 存储位置 | `config` JSON 内（`config["binding_mode"]`），不加表列 | `aix_preview_url` 有同类先例；改列需动 repository 协议全链，收益仅可查询性 |
| config schema | 单一扁平模型 + 模式校验矩阵，不做 discriminated union | gateway `bots.openapi.json` 手工维护，扁平增量同步成本最低 |
| 同步失败语义 | 激活 fail-closed / 删除 best-effort | 见 §5 |
| 发布链路 | bcn_gateway 行不进 engine_overrides | `channels.dingding.accounts` 是插件直连语义；bcn 运行时 wiring 由 provisioning 注入 |

## 3. 契约层

### 3.1 字段

- `ChannelCreate` / `Channel` 顶层新增 `binding_mode: Literal["plugin", "bcn_gateway"]`，**缺省 `"plugin"`**——存量客户端与存量数据零感知。
- `DingTalkChannelConfig*` 三个模型新增两个字段（bcn 专属）：
  - `group_chat_scope: Literal["per_sender", "conversation_shared"]`，默认 `per_sender`（session 隔离）
  - `outbound_visibility: Literal["full_transcript", "lead_only"]`，默认 `full_transcript`

### 3.2 模式校验矩阵（服务端 422 拒绝）

| 字段 | plugin | bcn_gateway |
|---|---|---|
| client_id / client_secret / card_template_id / robot_code / enable_streaming_cards | ✅ | ✅（robot_code **必填**） |
| card_template_key / dm_policy / allowlist / reply_to_message / aix_enable / include_sender_name | ✅ | ❌ |
| group_chat_scope / outbound_visibility | ❌ | ✅ |
| `binding_mode` 本身 | 创建时可指定，更新请求中传值必须与存量一致（否则 422） | 同左 |

> Update 模型中"❌ 拒绝"指**显式传入**该字段才拒绝（Update 字段缺省 = 保持不变，与既有 omit-to-keep 语义一致）。

- 读投影 `_safe_config` 延续脱敏（`has_client_secret`），`binding_mode` 顶层返回。
- 服务端内部记账字段 `config["bcs_binding_id"]`（BCS 绑定创建后回写）不进公开 schema（`extra="forbid"` 天然挡住客户端伪造）。

### 3.3 存量兼容

- 现有 `ac_channel_config` 行无 `config["binding_mode"]` → 一律按 `plugin` 解释，行为不变。
- 对 plugin 行，`engine_overrides_reader`、`sync_channel_to_openclaw`、publish 投递行为不变；**bcn 行是新增跳过/分流**（reader 过滤、sync 分派第三分支），不是"条件不变"。

## 4. 编排层

### 4.1 分派

`ChannelService._dispatch_channel_sync`（`core/channel/services/channel_service.py`）在 teclaw / openclaw 两分支之外增加第三分支：

```
config.get("binding_mode") == "bcn_gateway"
  → BcsChannelBindingClient（新 infrastructure adapter，DI 注入）
```

`BcsChannelBindingClient` 的 DI 注册模式对齐现有 `BcsClientPort`（`di/modules/task_module.py` 的 token provider + corp overlay 结构）。

### 4.2 字段映射（agentclaw config → BCS `DingTalkConfigPayload`）

| agentclaw | BCS |
|---|---|
| client_id | client_id |
| client_secret | client_secret（agentclaw 是 SoT，全量发送，规避 BCS `<redacted>` 不回显问题） |
| robot_code | robot_code |
| enable_streaming_cards + card_template_id | `send_mode`: `streaming_card{card_template_id, fallback_message_type: "markdown"}` / `normal{message_type: "markdown"}` |
| group_chat_scope / outbound_visibility | 同名透传 |
| — | `target: { bot_id }`（bot 维度） |
| — | `account_ref`：由 client_id 派生（实现期与 BCS 对齐唯一性规则） |

### 4.3 生命周期 → BCS 操作

| 本地操作 | BCS 调用 | 失败语义 |
|---|---|---|
| 激活（PUT status=active） | POST 创建（无 bcs_binding_id）或 PATCH `{active: true}` | **fail-closed**：失败 → `ChannelSyncError`（502），不落 active（对齐 openclaw 写文件先行的次序） |
| 更新配置（active 中） | PATCH `{config: 全量映射}`（BCS 的 config 是全量替换） | fail-closed，同上 |
| 停用 | PATCH `{active: false}` | fail-closed |
| 删除渠道 | active 行先走停用 PATCH（fail-closed）→ 删本地行 → DELETE 绑定 | DELETE 为 **best-effort**：失败记 warning；不做自动对账（一期 YAGNI，残留绑定人工处理） |

- 创建（POST /channels）仍只落库 status="0"，不触 BCS——与 plugin 路径一致。
- 成功创建绑定后回写 `config["bcs_binding_id"]`，后续 PATCH/DELETE 复用。
- **错误映射**：BCS 不可达 / 5xx / 超时 → `ChannelSyncError`（既有映射 502，`responses.py:455`）；BCN 绑定冲突（BCS 409 `channel_binding_conflict`，如同一 robot 已绑给其他 target）→ 新增 `ChannelBindingConflictError` → 409。

## 5. 下发链路（publish / engine_overrides）

- `engine_overrides_reader.overrides_for_stage` 跳过 `config.get("binding_mode") == "bcn_gateway"` 的行——`channels.dingding.accounts` 是插件直连语义，bcn 凭证不进 openclaw.json。
- bcn 模式运行时 wiring **已存在、不在本设计内**：`plugins/local/process_manager.py:215-233` 在 openclaw 配置注入 `channels.bcs`（bcsUrl/botId）并加载 `openclaw-channel-bcn` 插件（插件未构建时跳过，不影响无 bcn 渠道的 bot）。
- 一期口径：bcn_gateway 仅 draft/live 生效（个人 bot 直接对话）；verify/online 发布投递不支持 bcn 行（服务 bot 用 bcn 渠道属后续扩展）。

## 6. 鉴权

bcn_gateway 写路径复用现有全部门槛：`require_granted_addressed_bot`（路由声明 ADMIN）→ `resolve_operable_bot`（stage=draft, surface=channels）→ `_require_edit_lock`（423）→ 模式校验矩阵 → BCS 编排。读路径同现有 member 门槛。

## 7. 外部依赖（上线阻塞项）

| # | 依赖 | 现状 | 动作 |
|---|---|---|---|
| 1 | BCS 真实钉钉 provider | 本仓库仅 test provider；现状未知 | 与 BCS/B 线确认位置、负责人、排期；编排层可先开发，用 test provider + singlebox 验证 |
| 2 | BCS bindings API 服务间鉴权 | 当前 `require_authenticated_user` 人类会话 | BCS 增加 service token + 操作者身份透传（跨团队） |
| 3 | account_ref 唯一性规则、`per_sender` 在 provider 侧实际生效性 | 未确认 | 实现期与 BCS 对齐 |

## 8. 测试与交付

- endpoint gate 补用例：422 模式矩阵（plugin 字段进 bcn / bcn 字段进 plugin / robot_code 缺失 / binding_mode 变更）、502（BCS 不可达）、409（绑定冲突）、`binding_mode` 缺省 plugin 的存量回归。
- changed-line coverage ≥ 80%，本地 `ci_test.sh` 验证（PR CI 与本地全量等价，push 前一次）。
- `BcsChannelBindingClient` 按 `BcsClientPort` 的 fake/DI 测试基建做，不打真实 BCS。
- `bots.openapi.json` **手工增量编辑**（勿全量 regen）：Channel/ChannelCreate/ChannelUpdate 及 config 三模型加字段、新增 409/502 响应说明；**avernet + ocb 双侧同步**（ocb 仓库 `~/IdeaProjects/ocb`）。
- 二期（另行计划）：bot 工坊渠道 Tab 前端（当前占位符）按新契约实现双方式表单；群管理面板维持 BCS API 直连不动。

## 9. 开放问题（实现期收敛）

1. `account_ref` 派生规则与 BCS 唯一性约束的最终口径（§7-3）。
2. BCS service token 的发放与配置面（走 yaml corp overlay 还是独立 secret 管理——对齐 `BcnConfig`/`BcsClientPort` 既有模式）。
3. 删除路径 BCS 残留绑定的可见性（是否在响应/日志中给出人工清理指引）。
