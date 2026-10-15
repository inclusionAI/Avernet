# 渠道配置接口迁移指南（老两个配置页 → 新统一 openapi）

> 面向前端。老渠道配置是两条相互独立的链路，新体系合并为一个统一入口。本文覆盖：两条老链路的表单字段如何逐一映射到新接口、请求/响应示例、语义差异（哪些交互行为变了）、错误码，以及 TS 侧的接入点。
>
> 适用版本：`forward_sender_identity` 补齐 + 流式-模板联动校验 + stage（生效环境）维度移植之后（分支 `rel20261015-channel-forward-sender-identity`）。
> 设计文档：[docs/superpowers/specs/2026-09-03-channel-binding-mode-design.md](superpowers/specs/2026-09-03-channel-binding-mode-design.md)
> 新契约权威定义：`apps/gateway/configs/schemas/bots.openapi.json`（`channels` tag / `DingTalkChannelConfig*` 组件）

---

## 0. 三面接口总览

| | 新统一面（**前端唯一对接面**） | 老开源插件链 | 老 BCN 链 |
|---|---|---|---|
| 端点 | `/openapi/v1/bots/{bot_id}/channels`（CRUD + 状态） | `/api/channels` | BCS `/openapi/v1/collaboration/channels/bindings`（4 个端点） |
| 绑定方式 | `binding_mode: "plugin" \| "bcn_gateway"`（创建时选） | 固定插件直连 | 固定 BCN 网关 |
| BCS 由谁同步 | **后端自动**（激活/改配置/停用/删除时后端调 BCS） | 不涉及 BCS | **前端直接调**，要自己维护绑定生命周期 |
| 鉴权 | 读 = Bot 成员；写 = Bot 管理员（+协作者编辑锁） | 浏览器会话 + 拦截器 | 人类会话 |
| 响应封套 | `{code, message, data, request_id}`，`code = HTTP状态码×1000` | `{success, ...}` | `{code, message, data, request_id}` |

**核心变化一句话**：老页面上"选一种绑定方式再各自填表单"，现在是一个入口、一次提交——`binding_mode` 写在请求顶层，模式专属字段写在 `config` 里；BCN 模式下前端**不再直接调 BCS 的任何接口**。

---

## 1. 端点清单（新统一面）

| 操作 | 方法与路径 | 说明 |
|---|---|---|
| 列表 | `GET /openapi/v1/bots/{bot_id}/channels` | 当前 owner 的钉钉渠道；个人 Bot 只回 **draft 行**，服务 Bot 额外带出 **verify/online 行**（见 §4.6/§4.7） |
| 创建 | `POST /openapi/v1/bots/{bot_id}/channels` → 201 | 创建后是 **inactive** 状态，需要再调激活 |
| 单查 | `GET /openapi/v1/bots/{bot_id}/channels/{channel_id}` | |
| 部分更新 | `PATCH /openapi/v1/bots/{bot_id}/channels/{channel_id}` | omit/null = 保持（见 §4.3） |
| 启停 | `PUT /openapi/v1/bots/{bot_id}/channels/{channel_id}/status` | body: `{"status": "active" \| "inactive"}` |
| 删除 | `DELETE /openapi/v1/bots/{bot_id}/channels/{channel_id}` | |

---

## 2. 字段映射

### 2.1 老开源插件页（`/api/channels`，ChannelManageTab）→ `binding_mode: "plugin"`

| 老页面字段 | 新请求字段 | 说明 |
|---|---|---|
| 场景描述 | 顶层 `description` | |
| `app_key`（必填） | `config.client_id` | 老页面的"app_key"就是新契约的 `client_id`，仅改名 |
| `app_secret`（必填） | `config.client_secret` | **write-only**：读接口不回传明文，只回 `has_client_secret`（见 §4.2） |
| 启用钉钉端流式输出 | `config.enable_streaming_cards` | |
| 卡片 templateId | `config.card_template_id` | |
| 卡片变量 key 值 | `config.card_template_key` | |
| 钉钉机器人 ID | `config.robot_code` | |
| 生效环境（草稿/预发/线上，仅服务 Bot） | 顶层 `stage` | 忠实移植老选择器：`"draft"`（缺省）/`"verify"` /`"online"`；**verify/online 仅服务 Bot 可写**，个人 bot 传了 422；**创建后不可变**。见 §4.7 |
| — | （新增可填）`dm_policy` / `allowlist` / `reply_to_message` / `aix_enable` / `include_sender_name` | 私聊策略、白名单、回复关联原消息、AIX 卡片、发送者姓名进上下文——老插件页没有、新契约支持的扩展，有交互需要可以自由补 |

### 2.2 老 BCN 页（BcsChannelManageTab / BCS bindings）→ `binding_mode: "bcn_gateway"`

| 老页面字段 | 新请求字段 | 说明 |
|---|---|---|
| 机器人 ID / Robot Code | `config.robot_code` | **bcn 模式下必填**，缺失创建直接 422 |
| app_key | `config.client_id` | |
| app_secret | `config.client_secret` | 同样 write-only |
| 流式卡片开关 | `config.enable_streaming_cards` | 老 BCN 契约的 `send_mode`（`normal`/`streaming_card`）**不再是提交字段**——后端由这两个字段**派生**（见 §4.8） |
| 卡片模板 ID | `config.card_template_id` | `send_mode.streaming_card.card_template_id` 的来源 |
| **透传钉钉发送人身份** | `config.forward_sender_identity` | 本次新补的字段；缺省 `false`。开启后消息会把钉钉发送人身份透传给 Bot 上下文（BCS 侧有归因披露语义，仅 bot 绑定支持） |
| 群聊会话模式 | `config.group_chat_scope` | `per_sender`（默认）/ `conversation_shared`；不传则后端按 `per_sender` 落库 |
| 主动消息可见范围 | `config.outbound_visibility` | `full_transcript`（默认）/ `lead_only` |
| BCN Bot ID（`{botId}:{userId}` 拼接） | **不再传递** | BCS 侧 `target` 由**后端**从路径 `bot_id` 派生，前端删掉 `resolveBcsBotId` 逻辑 |
| `account_ref` | **不再传递** | 后端派生（= `client_id`） |
| `env`（部署环境） | **不再提交**（也从来不该提交——老 BCS 契约 write 端就是拒绝的） | env 是 BCS 注入的运行态 |
| 回调地址指引（复制按钮） | **不经过接口** | 纯前端拼串展示，沿用老逻辑即可（见 §4.9） |
| 绑定启停（PATCH `{active}`） | `PUT .../status` | 前端不再维护 BCN 绑定的 activate/deactivate |
| 绑定删除（DELETE bindings/{id}） | `DELETE .../channels/{channel_id}` | 后端顺带清理 BCS 绑定 |

### 2.3 模式校验矩阵（服务端 422 拒绝）

字段只属于一种模式，传错必被拒（`extra="forbid"`，未知字段同样 422）：

| `config` 字段 | plugin | bcn_gateway |
|---|---|---|
| `client_id` / `client_secret` / `card_template_id` / `robot_code` / `enable_streaming_cards` | ✅ | ✅（`robot_code` **必填**） |
| `card_template_key` / `dm_policy` / `allowlist` / `reply_to_message` / `aix_enable` / `include_sender_name` | ✅ | ❌ 422 |
| `group_chat_scope` / `outbound_visibility` / `forward_sender_identity` | ❌ 422 | ✅ |
| 顶层 `binding_mode` | 创建时可指定（缺省 `plugin`）；**创建后不可变**，更新请求里传值必须与存量一致 | 同左 |

> 切换绑定方式 = **删除旧渠道 + 重新创建**，没有"改模式"的请求。

> **流式联动校验**（创建与更新都查，两种模式一体适用）：`enable_streaming_cards: true` 时 `card_template_id` 必须非空，否则 422——`create` 侧响应是统一的 `"Invalid request"`，`update` 侧是 `"Channel mode violation"`。老表单有同款前端校验（"请输入卡片 templateId"），现在后端补齐为强约束。
>
> **stage 规则**：`stage` 只在创建时传（顶层字段），创建后不可变（更新请求传它会被 `extra="forbid"` 拒掉）；`"verify"/"online"` 只对 `bot_type=service` 的 Bot 开放，传给个人 Bot 422（`"Channel mode violation"`）；bcn_gateway 渠道一律 `"draft"`（422 拒其他档位——`"Invalid request"`）。

---

## 3. 请求 / 响应示例

### 3.1 创建 plugin 渠道（对应老开源插件页提交）

```json
POST /openapi/v1/bots/bot-1/channels
{
  "type": "dingding",
  "binding_mode": "plugin",
  "stage": "verify",          // ✳ 服务 Bot 才能传 verify/online；草稿档省略该字段即可
  "description": "客服群机器人",
  "config": {
    "client_id": "ding_app_key_xxx",
    "client_secret": "ding_app_secret_xxx",
    "robot_code": "robot_xxx",
    "enable_streaming_cards": true,
    "card_template_id": "tpl-1",
    "card_template_key": "body",
    "dm_policy": "open",
    "allowlist": ["*"]
  }
}
```

### 3.2 创建 bcn_gateway 渠道（对应老 BCN 页提交）

```json
POST /openapi/v1/bots/bot-1/channels
{
  "type": "dingding",
  "binding_mode": "bcn_gateway",
  "description": "客服群机器人（BCN）",
  "config": {
    "client_id": "ding_app_key_xxx",
    "client_secret": "ding_app_secret_xxx",
    "robot_code": "robot_xxx",
    "enable_streaming_cards": true,
    "card_template_id": "tpl-1",
    "group_chat_scope": "per_sender",
    "outbound_visibility": "full_transcript",
    "forward_sender_identity": true
  }
}
```

### 3.3 响应（Envelope 封套，201/200 同形）

```json
{
  "code": 201000,
  "message": "Created",
  "request_id": "r-1",
  "data": {
    "id": 12,
    "type": "dingding",
    "binding_mode": "bcn_gateway",
    "description": "客服群机器人（BCN）",
    "bot_id": "bot-1",
    "owner_id": "owner-1",
    "config": {
      "client_id": "ding_app_key_xxx",
      "has_client_secret": true,
      "card_template_id": "tpl-1",
      "card_template_key": null,
      "enable_streaming_cards": true,
      "dm_policy": "open",
      "allowlist": ["*"],
      "reply_to_message": true,
      "robot_code": "robot_xxx",
      "aix_enable": true,
      "include_sender_name": true,
      "group_chat_scope": "per_sender",
      "outbound_visibility": "full_transcript",
      "forward_sender_identity": true
    },
    "status": "inactive",
    "stage": "draft",
    "created_at": "2026-10-15T08:00:00Z",
    "updated_at": "2026-10-15T08:00:00Z"
  }
}
```

> 注意三态字段：`group_chat_scope` / `outbound_visibility` / `forward_sender_identity` 在 **plugin 行回显为 `null`**（不代表"没配置"的 bcn 行）。

### 3.4 编辑（PATCH）与激活

```json
PATCH /openapi/v1/bots/bot-1/channels/12
{ "config": { "forward_sender_identity": false } }
```

```json
PUT /openapi/v1/bots/bot-1/channels/12/status
{ "status": "active" }
```

激活即触发后端实际接线：plugin 模式写引擎侧渠道配置；bcn 模式在 BCS 创建/激活绑定（失败则本调用失败，渠道保持 inactive——fail-closed）。

---

## 4. 交互语义差异（迁移最容易踩的九条）

1. **创建 ≠ 生效**。POST 完是 `inactive`，必须显式 `PUT status=active`。老页面"保存即生效"的交互要拆成保存/启用两步（或保存后自动帮用户激活）。
2. **secret 不回显**。读接口只有 `has_client_secret` 布尔。编辑表单的 secret 输入框：已存渠道留空/回传 `null` = **保持原 secret 不变**，填了新值才覆盖。老 `/api/channels` 是把 `client_secret` 明文回传的——**新前端严禁**依赖回显补框。
3. **omit 与 null 等价**（写操作全字段如此）：`PATCH` 里字段没传、或传了 `null`，都是"保持不变"。
4. **`binding_mode` 不可变**。编辑页把模式选择器做成禁用态；换模式走"删除重建"。
5. **bcn 专属三字段对 plugin 渠道是 422**（矩阵见 §2.3），反之亦然。编辑表单按 `binding_mode` 条件渲染字段就不会踩。
6. **列表口径变窄**：个人 Bot 只回当前 owner 的 draft 钉钉渠道（老接口会带出的共享默认行 `aideskdingding`、verify/online 行、其他渠道类型一并消失）；服务 Bot 例外——它的 verify/online 行**会**带出（§4.7），前端按 `stage` 字段分组渲染。
7. **"生效环境"选择器已移植进公开面（仅服务 Bot + 仅 plugin 模式）**。创建时顶层传 `stage`（`draft` 缺省 / `verify` / `online`），语义与老插件页一致：每一档是一行独立配置，发布流按 stage 现读各行合成对应环境的渠道配置。四条规则：①创建后不可变（老页面同样不给改）；②`verify/online` 行**不接运行时**——对它们做 PATCH/启停只改 DB（发布时才被读到），不会像 draft 行那样同步 openclaw/BCS；③个人 Bot 不开放这两个档位（422）；④**bcn_gateway 模式一律 draft**（老 BCN 页本来就没有档位概念，且 bcn 行不进发布流的 engine_overrides——传了 422）。列表/单查对服务 Bot 会带出 verify/online 行，卡片按 `stage` 字段分组渲染即可。
8. **没有 `send_mode` 提交字段**。老 BCN 的"普通文本 / 流式卡片"单选，映射为 `enable_streaming_cards` 开关（+ `card_template_id`）。后端推 BCS 时自动还原结构化 `send_mode`，前端不需要也没法直接表达 fallback message type。
9. **回调地址指引不调接口**。老 BCN 页的"蚂蚁钉回调地址"展示+复制块是纯前端逻辑（按部署 env 拼串），新页面想在 bcn 模式下保留这个引导，把这个组件搬过来即可，与后端无关。
10. **流式开关与模板是联动校验**（§2.3 末尾注）。表单上开"流式输出"就必须同时有模板 ID——提交前自己做这个前置校验，别等服务端 422。**注意 update 语义**：哪怕本次 PATCH 只改别的字段，只要"合并后的存量配置"处于"流式开+模板空"状态就会 422——历史上（含内部老入口）配出的这类缺口存量数据，会在下次任意编辑时被强制纠正（要么补模板，要么 `enable_streaming_cards: false`）。

---

## 5. 错误码

| HTTP | code | 场景 | 前端处理 |
|---|---|---|---|
| 403 | 403xxx | 写操作需要 Bot 管理员 | 提示无权限 |
| 404 | 404xxx | 渠道不在该 Bot 范围（含非 draft、非 owner、已删） | 刷新列表 |
| 409 | 409xxx | bcn 激活时 BCS 绑定冲突（如同 robot 已绑到别的 target） | 提示检查钉钉侧绑定归属 |
| 422 | 422xxx | 三类：① 请求体校验失败——必填缺失、未知字段（`extra="forbid"`）、类型错误、**创建时**的模式矩阵违规、**创建时**的 bcn+stage 违规。这类响应 `message` 固定为 `"Invalid request"`，**不带具体字段细节**，排查靠本地表单校验对齐 §2.3 矩阵；② 路由侧违规——个人 Bot 的 stage 请求、**更新时**的模式矩阵违规，`message: "Channel mode violation"` | 按矩阵做前置校验，不要依赖服务端报文定位字段 |
| 423 | 423xxx | Bot 有协作者且当前用户未持编辑锁 | 引导去拿编辑锁 |
| 502 | 502xxx | 激活/同步运行时失败（含 BCS 不可达） | 提示重试，渠道保持 inactive |

---

## 6. TS 侧接入点（frontend-nextgen）

现有映射已在 `src/services/botWorkshop/botAdvancedConfigService.ts`（`listChannels` / `createChannel` / `updateChannel` 内已经按 `bindingMode` 分流 config 字段），控制器在 `src/services/backendApi/bots/botEditorChannelDtos.ts`。迁移补齐只需三处：

1. **DTO**：`botEditorChannelDtos.ts` 的 config 类型加 `forward_sender_identity?: boolean | null`。
2. **领域模型**：`src/domain/botAdvancedConfig.ts` 的 `BotChannel` / `BotChannelInput` 加 `forwardSenderIdentity?: boolean`。
3. **表单**：`src/components/BotWorkshop/Editor/ChannelFormModal.tsx` 的 bcn 分支加"透传钉钉发送人身份"开关（仅 `bindingMode === 'bcn_gateway'` 渲染），`botAdvancedConfigService.createChannel/updateChannel` 的 bcn config 分支带上该字段（不传 = 后端按 false）。

对照校验：新读投影回显的 `forward_sender_identity` 为 `null`（plugin 行）时表单不要当成 false 渲染，直接隐藏即可。

4. **stage（生效环境）**：`botEditorChannelDtos.ts` 的 create body 加顶层 `stage?: 'draft'|'verify'|'online'`，读模型透传 `stage` 字段（服务 Bot 列表会带出 verify/online 行，按它分组）；表单上"生效环境"选择器仅在 `bot_type=service && bindingMode==='plugin'` 时渲染（老页面同款条件），其余场景不提供该控件即可——后端本来就是 422 兜底，前端前置隐藏最顺。