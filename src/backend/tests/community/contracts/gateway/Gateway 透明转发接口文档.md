# Gateway 透明转发接口文档

> 本文档梳理 Gateway 中所有透明转发规则，涵盖 HTTP PathMapping 和 WebSocket 路由，包含完整的接口级（含子路径、HTTP 方法约束）信息。
>
> 最后更新：2026-05-26

## 1. 上游服务地址

| 变量名 | 生产环境 | 预发环境 | 开发环境 | 说明 |
|---|---|---|---|---|
| `AGENTCLAW_URL` | `https://agentclaw-prod.teamclaw.com` | `https://agentclaw-pre.teamclaw.com` | `https://agentclaw-pre.teamclaw.com` | AgentClaw 主服务 |
| `AGENTCLAWPROXY_URL` | `https://agentclawproxy-prod.teamclaw.com` | `https://agentclawproxy-pre.teamclaw.com` | `https://agentclawproxy-pre.teamclaw.com` | 引擎代理（透传到 Bot 实例） |
| `BCS_URL` | `https://bcn.teamclaw.com` | `https://bcn-pre.teamclaw.com` | `https://bcn-pre.teamclaw.com` | BCN 协作服务 |
| `MCP_CENTER_URL` | `https://antllmbase-prod-124800013.antgroup-inc.cn` | `https://antllmbase-prod-124800013.antgroup-inc.cn` | `https://antllmbase-prod-124800013.antgroup-inc.cn` | MCP Center |

## 2. 路由优先级规则

- **字面量优先**：精确前缀规则优先于通配符 `{param}` 规则
- **长前缀优先**：相同类型规则中，前缀越长越优先匹配
- **白名单机制**：`allowed_suffixes` 为白名单，未列出的子路径会被拒绝（返回无匹配，不会转发）
- **方法限制**：部分 suffix 规则通过 `methods` 字段约束 HTTP 方法；未指定 `methods` 则允许所有 HTTP 方法

## 3. 接口明细

---

### Rule #1 — Bot 管理（AgentClaw）

| 前缀 | 后端前缀 | 上游 |
|---|---|---|
| `/api/v1/bots` | `/api/bots` | AGENTCLAW_URL |

| 接口 | 方法 | 上游路径 | 说明 |
|---|---|---|---|
| `GET /api/v1/bots/by-owner` | GET | `/api/bots/by-owner` | 按 OWNER 查询 Bot 列表 |
| `POST /api/v1/bots` | * | `/api/bots` | 创建 Bot（空后缀匹配） |
| `GET /api/v1/bots/{id}` | * | `/api/bots/{id}` | 获取 Bot 详情 |
| `PUT /api/v1/bots/{id}` | * | `/api/bots/{id}` | 更新 Bot |
| `DELETE /api/v1/bots/{id}` | * | `/api/bots/{id}` | 删除 Bot |
| `GET /api/v1/bots/{id}/status` | * | `/api/bots/{id}/status` | 获取 Bot 状态 |
| `POST /api/v1/bots/{id}/restart` | * | `/api/bots/{id}/restart` | 重启 Bot |
| `GET /api/v1/bots/{id}/engine-config` | * | `/api/bots/{id}/engine-config` | 获取引擎配置 |
| `PUT /api/v1/bots/{id}/engine-config` | * | `/api/bots/{id}/engine-config` | 更新引擎配置 |
| `GET /api/v1/bots/{id}/passport` | * | `/api/bots/{id}/passport` | 获取 Bot Passport |
| `GET /api/v1/bots/check/name` | * | `/api/bots/check/name` | 校验 Bot 名称 |
| `GET /api/v1/bots/auth-status` | * | `/api/bots/auth-status` | 查询授权状态 |

**参数明细：**

| 接口 | 入参 | 出参 |
|---|---|---|
| `GET /api/v1/bots/by-owner` | — | `{success, message, error_code, data: {total, items: [{id, bot_id, bot_name, bot_desc, entity_id, entity_type, creator_id, owner_id, owner_name, engine_types[], active_engine, status, binding_id, device_id, gmt_create, gmt_modified, modifier_id, share_policy, is_delete, public, ext: {passport: {status, is_first_bot}, avatar_url, start_status, start_message, friend_approval, public_approval: {puid, public, status, applicant, approval_url, processed_at, friend_approval, permission_owner}, permission_owner}, env, bot_type, can_edit_bot, engine_paths, bot_work_dir}]}}` |
| `POST /api/v1/bots` | body: `bot_name`, `entity_id`*, `entity_type`*(staff), `engine_type`, `bot_desc`, `ext` | 成功: `{success, data: {bot: {同items单条}, passport: {agent_code, status}}}`; 401: `{data.need_authorization, data.iframe_url, data.redirect_url}` |
| `GET /api/v1/bots/{id}` | path: `id` | `{success, data: {同items单条完整对象}}` |
| `PUT /api/v1/bots/{id}` | path: `id`; body: `bot_name`, `bot_desc`, `ext` | `{success, data: {更新后的Bot对象}}` |
| `DELETE /api/v1/bots/{id}` | path: `id` | `{success, message, error_code, data: null}` |
| `GET /api/v1/bots/{id}/status` | path: `id` | `{success, data: {bot_id, bot_status(ACTIVE/INACTIVE/CREATING), binding_status, device_id, device_provider, error_message, is_ready, ext: {passport, avatar_url, start_status, start_message, friend_approval, public_approval, permission_owner}}}` |
| `POST /api/v1/bots/{id}/restart` | path: `id`; body: `{}` | `{success, data: {bot_id, status: "RESTARTING"}}` |
| `GET /api/v1/bots/{id}/engine-config` | path: `id` | `{success, data: {meta: {lastTouchedVersion, lastTouchedAt}, logging: {level, file, maxFileBytes}, models: {mode, providers: {providerName: {baseUrl, apiKey, api, models: [{id, name, reasoning, input[], cost: {input, output, cacheRead, cacheWrite}, contextWindow, maxTokens}]}}}, agents: {defaults: {model: {primary}, models: {}, compaction: {mode}, maxConcurrent, subagents: {maxConcurrent}, imageModel: {primary}}}, commands: {native, nativeSkills, restart, ownerDisplay}, hooks, discovery, gateway: {port, mode, auth}, skills: {load: {extraDirs[], watch, watchDebounceMs}, entries: {}}, plugins: {load: {paths[]}, entries: {}, installs: {}}, session: {dmScope}, channels: {dingtalk: {enabled}, bcs: {bcsUrl, enabled}}, tools: {media: {image: {enabled, maxBytes}}}}}}` |
| `PUT /api/v1/bots/{id}/engine-config` | path: `id`; body: `model`, `config` | 同 GET engine-config |
| `GET /api/v1/bots/{id}/passport` | path: `id`(bot_id) | `{success, data: {agent_id, agent_code, credential_id, expire_at, access_mode, mcps: [{mcp_code, mcp_name, mcp_desc}], certificate_url}}` |
| `GET /api/v1/bots/check/name` | query: `bot_name`* | `{success, data: {exists(bool), bot_name}}` |
| `POST /api/v1/bots/auth-status` | body: `bot_id`* | `{success, data: {status(AUTHORIZED/PENDING/EXPIRED), bot: {bot_id, bot_name, ...同bot详情}}}` |

---

### Rule #2 — 公开 Bot 互动（AgentClaw）

| 前缀 | 后端前缀 | 上游 |
|---|---|---|
| `/api/v1/bot-public` | `/api/v1/bot-public` | AGENTCLAW_URL |

| 接口 | 方法 | 上游路径 | 说明 |
|---|---|---|---|
| `GET /api/v1/bot-public/my-friend-bots` | * | `/api/v1/bot-public/my-friend-bots` | 我的好友 Bot 列表 |
| `* /api/v1/bot-public/friend-record` | * | `/api/v1/bot-public/friend-record` | 好友记录 |
| `* /api/v1/bot-public/search` | * | `/api/v1/bot-public/search` | 搜索公开 Bot |
| `* /api/v1/bot-public/search/authorized` | * | `/api/v1/bot-public/search/authorized` | 搜索已授权 Bot |
| `* /api/v1/bot-public/search/unauthorized` | * | `/api/v1/bot-public/search/unauthorized` | 搜索未授权 Bot |
| `POST /api/v1/bot-public/friend-request-approval` | * | `/api/v1/bot-public/friend-request-approval` | 好友请求审批 |

**参数明细：**

| 接口 | 入参 | 出参 |
|---|---|---|
| `GET /api/v1/bot-public/my-friend-bots` | — | `{success, data: {total, items: [{id, requester_entity_id, requester_name, target_entity_id, target_name, target_bot_id, target_owner_name, status, gmt_create, gmt_modified, ext, env, bot: {id, bot_id, bot_name, bot_desc, entity_id, entity_type, owner_id, owner_name, engine_types[], active_engine, status, binding_id, ...}}]}}` |
| `GET /api/v1/bot-public/friend-record` | query: `target_entity_id`, `target_bot_id` | `{success, data: 好友记录对象或null}` |
| `GET /api/v1/bot-public/search` | query: `search`*, `page`, `page_size`(或`size`) | `{success, data: {total, items: [{id, bot_id, bot_name, bot_desc, entity_id, entity_type, owner_id, owner_name, engine_types[], active_engine, status, binding_id, device_id, ext, env, bot_type, friend_record_approval}]}}` |
| `GET /api/v1/bot-public/search/authorized` | query: `page`, `size` | `{success, data: {同search格式}}` |
| `GET /api/v1/bot-public/search/unauthorized` | query: `page`, `size` | `{success, data: {同search格式}}` |
| `POST /api/v1/bot-public/friend-request-approval` | body: `bot_id`*, `owner_id`*, `request_id`*, `action`(approve/reject) | `{success, message}` |

---

### Rule #3 — 专家聊天（AgentClaw）

| 前缀 | 后端前缀 | 上游 |
|---|---|---|
| `/api/v1/expert-chats` | `/api/v1/expert-chats` | AGENTCLAW_URL |

| 接口 | 方法 | 上游路径 | 说明 |
|---|---|---|---|
| `GET /api/v1/expert-chats` | * | `/api/v1/expert-chats` | 专家聊天列表（空后缀匹配） |
| `POST /api/v1/expert-chats` | * | `/api/v1/expert-chats` | 创建专家聊天（空后缀匹配） |
| `* /api/v1/expert-chats/{bot_id}/{owner_id}` | * | `/api/v1/expert-chats/{bot_id}/{owner_id}` | 按_bot+owner查询 |
| `DELETE /api/v1/expert-chats/{bot_id}/{owner_id}/session` | * | `/api/v1/expert-chats/{bot_id}/{owner_id}/session` | 删除会话 |
| `GET /api/v1/expert-chats/grt-chat/stream` | * | `/api/v1/expert-chats/grt-chat/stream` | GRT 流式聊天（SSE） |

**参数明细：**

| 接口 | 入参 | 出参 |
|---|---|---|
| `GET /api/v1/expert-chats` | — | `{success, data: {total, items: [{bot_id, owner_id, bot_name, owner_name, status, binding_available(bool), binding_id, ext: {passport, avatar_url, start_status, start_message, public_approval, permission_owner, friend_approval}}]}}` |
| `POST /api/v1/expert-chats` | body: `bot_id`*, `owner_id`* | `{success, message}` |
| `GET /api/v1/expert-chats/{bot_id}/{owner_id}` | path: `bot_id`, `owner_id` | 同列表单条 |
| `DELETE /api/v1/expert-chats/{bot_id}/{owner_id}/session` | path: `bot_id`, `owner_id` | `{success, message: "Session deleted"}` |
| `GET /api/v1/expert-chats/grt-chat/stream` | query: `bot_id`*, `owner_id`*, `message`* | SSE 流: `data: {"type": "delta/final", "text": "..."}` |

---

### Rule #5 — 设备连接（AgentClaw）

| 前缀 | 后端前缀 | 上游 |
|---|---|---|
| `/api/v1/devices` | `/api/v1/devices` | AGENTCLAW_URL |

| 接口 | 方法 | 上游路径 | 说明 |
|---|---|---|---|
| `* /api/v1/devices/{id}/connection` | * | `/api/v1/devices/{id}/connection` | 设备连接状态 |
| `GET /api/v1/devices/connectable` | * | `/api/v1/devices/connectable` | 可连接设备列表 |

**参数明细：**

| 接口 | 入参 | 出参 |
|---|---|---|
| `GET /api/v1/devices/{id}/connection` | path: `id`(binding_id) | `{success, data: {type(local/remote), target(如ARCA_xxx@0:20003), token(proxy_token), engine_type, available(bool), message}}` |
| `GET /api/v1/devices/connectable` | query: `entity_id`*, `entity_type`*(如HUMAN) | `{success, data: {total, items: [{id, entity_id, entity_type, device_id, device_provider, device_props}]}}` |

---

### Rule #6 — Token 认证（AgentClaw）

| 前缀 | 后端前缀 | 上游 |
|---|---|---|
| `/api/v1/token` | `/api/v1/token` | AGENTCLAW_URL |

| 接口 | 方法 | 上游路径 | 说明 |
|---|---|---|---|
| `POST /api/v1/token/exchange` | * | `/api/v1/token/exchange` | Token 交换 |

**参数明细：**

| 接口 | 入参 | 出参 |
|---|---|---|
| `POST /api/v1/token/exchange` | body: `{}`(空 JSON) | `{access_token: "eyJ..."}` |

---

### Rule #7 — 群组管理（BCS）

| 前缀 | 后端前缀 | 上游 |
|---|---|---|
| `/api/v1/engine/groups` | `/groups` | BCS_URL |

| 接口 | 方法 | 上游路径 | 说明 |
|---|---|---|---|
| `POST /api/v1/engine/groups` | **仅 POST** | `/groups` | 创建群组 |
| `GET /api/v1/engine/groups/{id}` | * | `/groups/{id}` | 获取群组详情 |
| `DELETE /api/v1/engine/groups/{id}` | * | `/groups/{id}` | 删除群组 |
| `PUT /api/v1/engine/groups/{id}` | * | `/groups/{id}` | 更新群组 |
| `GET /api/v1/engine/groups/{id}/messages` | * | `/groups/{id}/messages` | 获取群组消息 |
| `GET /api/v1/engine/groups/{id}/sessions` | * | `/groups/{id}/sessions` | 获取群组会话列表 |
| `PUT /api/v1/engine/groups/{id}/participants/{actor_id}/mode` | * | `/groups/{id}/participants/{actor_id}/mode` | 更新参与者模式 |

> **注意**：`GET /api/v1/engine/groups`（列表）**不被允许**，空后缀仅限 POST。

**参数明细：**

| 接口 | 入参 | 出参 |
|---|---|---|
| `POST /api/v1/engine/groups` | body: `label`*, `driver_bot`*(bot_uuid格式), `participants[]`*({id, type(HUMAN/BOT), mode(present/absent/muted), bot_uuid, role}), `routing_policy` | `{id(group_id), driver_bot, participants[](actor_id), group_kind, created(bool), chat_url, context, context_injected, dm_pair_key}` |
| `GET /api/v1/engine/groups/{id}` | path: `id` | `{id, label, driver_bot, group_kind, group_strategy, status, created_at(int), updated_at(int), message_count, latest_running_session_id, participants: [{actor_kind, bot_name, bot_uuid, mode, role, staff_id, nickName}], workspace: {audit_log[], decisions[], notes[], tasks[]}, service_group_uuid, service_mode, service_spec, dm_pair_key, context}` |
| `DELETE /api/v1/engine/groups/{id}` | path: `id`; query: `bot_id`(driver bot_uuid) | `{success, message}` |
| `PUT /api/v1/engine/groups/{id}` | path: `id`; body: 更新字段 | 更新后的群组对象 |
| `GET /api/v1/engine/groups/{id}/messages` | path: `id`; query: `limit` | 消息数组 `[]` |
| `GET /api/v1/engine/groups/{id}/sessions` | path: `id`; query: `limit`, `offset` | `{group_id, items: [{id, session_id, session_kind, session_title, status, participants: [{actor_kind, bot_name, bot_uuid, mode, role}], activation_count, created_at, env, group_version}], total, limit, offset}` |
| `PUT /api/v1/engine/groups/{id}/participants/{actor_id}/mode` | path: `id`, `actor_id`; body: `mode`*(present/absent/muted) | `{success, data: {group_id, actor_id, mode}}` |

---

### Rule #7b — 会话成员管理（BCS）

| 前缀 | 后端前缀 | 上游 |
|---|---|---|
| `/api/v1/engine/sessions` | `/sessions` | BCS_URL |

| 接口 | 方法 | 上游路径 | 说明 |
|---|---|---|---|
| `PATCH /api/v1/engine/sessions/{session_id}/members/{bot_id}` | **仅 PATCH** | `/sessions/{session_id}/members/{bot_id}` | 更新会话成员模式（human: present/absent, bot: muted/auto） |

**参数明细：**

| 接口 | 入参 | 出参 |
|---|---|---|
| `PATCH /api/v1/engine/sessions/{session_id}/members/{bot_id}` | path: `session_id`(URL编码), `bot_id`(human_{staffNo}或{prefix}:{staffNo}); body: `mode`*(present/absent/muted/auto) | `{session_id, group_id, participants: [{actor_kind, bot_name, bot_uuid, mode, role}], status}` |

---

### Rule #8 — 引擎代理（AgentClawProxy，通配符）

| 前缀 | 后端前缀 | 上游 |
|---|---|---|
| `/api/v1/engine/{target}` | `/proxypass/{target}/api` | AGENTCLAWPROXY_URL |

> `{target}` 为 Bot 引擎实例标识（如 `ARCA_xxx@0:20003`），后端路径会被重写为 `/proxypass/{target}/api/...`。

| 接口 | 方法 | 上游路径 | 说明 |
|---|---|---|---|
| `POST /api/v1/engine/{target}/sessions` | **仅 POST** | `/proxypass/{target}/api/sessions` | 创建会话 |
| `GET /api/v1/engine/{target}/sessions` | **仅 GET** | `/proxypass/{target}/api/sessions` | 查询会话（内部 filter_sessions） |
| `GET /api/v1/engine/{target}/sessions/mine` | * | `/proxypass/{target}/api/sessions/mine` | 我的会话列表 |
| `GET /api/v1/engine/{target}/sessions/others` | * | `/proxypass/{target}/api/sessions/others` | 他人会话列表 |
| `* /api/v1/engine/{target}/sessions/{session_id}` | * | `/proxypass/{target}/api/sessions/{session_id}` | 会话详情/操作 |
| `POST /api/v1/engine/{target}/sessions/{session_id}/update` | * | `/proxypass/{target}/api/sessions/{session_id}/update` | 更新会话 |
| `DELETE /api/v1/engine/{target}/sessions/{session_id}/messages` | * | `/proxypass/{target}/api/sessions/{session_id}/messages` | 删除会话消息 |
| `GET /api/v1/engine/{target}/models` | * | `/proxypass/{target}/api/models` | 模型列表 |
| `GET /api/v1/engine/{target}/models/{model_id}` | * | `/proxypass/{target}/api/models/{model_id}` | 模型详情 |
| `GET /api/v1/engine/{target}/engine/status` | * | `/proxypass/{target}/api/engine/status` | 引擎状态 |
| `POST /api/v1/engine/{target}/engine/restart` | * | `/proxypass/{target}/api/engine/restart` | 重启引擎 |
| `POST /api/v1/engine/{target}/engine/switch` | * | `/proxypass/{target}/api/engine/switch` | 切换引擎 |

**参数明细：**

| 接口 | 入参 | 出参 |
|---|---|---|
| `POST /api/v1/engine/{target}/sessions` | path: `target`; Header: `X-PROXYPASS-TOKEN`; body: `title` | `{success, data: {id(如session:xxx:user:default), title, user_id, agent_id, model, permission_mode, cwd, gmt_create, gmt_modified, message_count, last_message: {id, session_id, role, content, metadata}}}` |
| `GET /api/v1/engine/{target}/sessions` | path: `target`; Header: `X-PROXYPASS-TOKEN` | `{success, data: [{id, title, user_id, agent_id, model, gmt_create, gmt_modified, message_count, last_message}]}` |
| `GET /api/v1/engine/{target}/sessions/mine` | path: `target`; query: `user_id`*, `limit`; Header: `X-PROXYPASS-TOKEN` | 同 sessions 列表格式 |
| `GET /api/v1/engine/{target}/sessions/others` | path: `target`; query: `user_id`*, `limit`; Header: `X-PROXYPASS-TOKEN` | 同 sessions 列表格式 |
| `* /api/v1/engine/{target}/sessions/{session_id}` | path: `target`, `session_id`; Header: `X-PROXYPASS-TOKEN` | session 详情对象 |
| `POST /api/v1/engine/{target}/sessions/{session_id}/update` | path: `target`, `session_id`; Header: `X-PROXYPASS-TOKEN`; body: `title` | `{success, data: {id, title}}` |
| `DELETE /api/v1/engine/{target}/sessions/{session_id}/messages` | path: `target`, `session_id`; Header: `X-PROXYPASS-TOKEN` | `{success, message}` |
| `GET /api/v1/engine/{target}/models` | path: `target`; Header: `X-PROXYPASS-TOKEN` | `{success, data: {models: [{id, provider_id, provider, name, display_name, description, enterprise_enabled, enterprise_default, capabilities: {context_window, max_output_tokens, vision, function_calling, reasoning, streaming}}]}}` |
| `GET /api/v1/engine/{target}/models/{model_id}` | path: `target`, `model_id`; Header: `X-PROXYPASS-TOKEN` | 同 models 列表单条 |
| `GET /api/v1/engine/{target}/engine/status` | path: `target`; Header: `X-PROXYPASS-TOKEN` | `{engine(str), active_connections(int), process: {running(bool), pid, exit_code(int), last_error, command_enabled(bool), managed_process(bool)}, transition}` |
| `POST /api/v1/engine/{target}/engine/restart` | path: `target`; Header: `X-PROXYPASS-TOKEN`; body: `{}` | `{success, data: {status: "restarting"}}` |
| `POST /api/v1/engine/{target}/engine/switch` | path: `target`; Header: `X-PROXYPASS-TOKEN`; body: `engine_type`* | `{success, data: {engine_type, status: "switching"}}` |

---

### Rule #10 — 技能市场（AgentClaw）

| 前缀 | 后端前缀 | 上游 |
|---|---|---|
| `/api/v1/skills` | `/api/skills` | AGENTCLAW_URL |

| 接口 | 方法 | 上游路径 | 说明 |
|---|---|---|---|
| `GET /api/v1/skills/market/list` | * | `/api/skills/market/list` | 技能市场列表 |
| `POST /api/v1/skills/market/search` | * | `/api/skills/market/search` | 技能市场搜索 |
| `GET /api/v1/skills/{id}` | * | `/api/skills/{id}` | 技能详情 |
| `POST /api/v1/skills/skillset/activate` | * | `/api/skills/skillset/activate` | 激活能力集中的技能 |
| `POST /api/v1/skills/skillset/deactivate` | * | `/api/skills/skillset/deactivate` | 停用能力集中的技能 |
| `GET /api/v1/skills/skillset/active` | * | `/api/skills/skillset/active` | 查询已激活技能 |

> **已废弃**：`/api/v1/skills/{id}/activate` 和 `/api/v1/skills/{id}/deactivate` 不再支持，由能力集模型替代。

**参数明细：**

| 接口 | 入参 | 出参 |
|---|---|---|
| `GET /api/v1/skills/market/list` | query: `page_num`, `page_size`, `user_id` | `{success, data: [{id, name, description, git_path, link_name, category, tags(str), input_schema, output_schema, is_public(bool), is_builtin(bool), user_id, gmt_created, gmt_modified, risk_tags[], mcp_dependencies[], bolt_id, env, status, version(int), skill_uuid, source_type, category_path, package_url, zip_url, use_count(int)}], count}` |
| `POST /api/v1/skills/market/search` | body: `query`*, `page_num`, `page_size` | `{success, data: [{同market/list}], count}` |
| `GET /api/v1/skills/{id}` | path: `id`; query: `user_id` | `{success, data: {id, name, description, git_path, link_name, category, tags[], risk_tags[], mcp_dependencies[], input_schema, output_schema, is_public, is_builtin, user_id, bot_id, gmt_created, gmt_modified, status, version, skill_uuid, source_type, members[], category_path, package_url, zip_url}}` |
| `POST /api/v1/skills/skillset/activate` | body: `skillset_id`*(或`skill_set_id`), `bot_id`*, `entity_id`, `entity_type`(staff) | `{success, data: {activated[], failed[]}, message}` |
| `POST /api/v1/skills/skillset/deactivate` | body: `skillset_id`*(或`skill_set_id`), `bot_id`*, `entity_id`, `entity_type`(staff) | `{success, data: {deactivated[], failed[]}, message}` |
| `GET /api/v1/skills/skillset/active` | query: `bot_id`* | `{success, data: [{id, name, description, is_default(bool), is_builtin(bool), user_id, gmt_created, gmt_modified, env, is_active(bool), engine_type, bot_id}], count}` |

---

### Rule #12 — BCN Bot 管理（BCS）

| 前缀 | 后端前缀 | 上游 |
|---|---|---|
| `/api/v1/engine/bots` | `/bots` | BCS_URL |

| 接口 | 方法 | 上游路径 | 说明 |
|---|---|---|---|
| `GET /api/v1/engine/bots/{uuid}` | * | `/bots/{uuid}` | Bot 详情 |
| `GET /api/v1/engine/bots/discover` | * | `/bots/discover` | 发现 Bot |
| `POST /api/v1/engine/bots/query` | * | `/bots/query` | 查询 Bot |
| `GET /api/v1/engine/bots/{uuid}/visibility` | * | `/bots/{uuid}/visibility` | Bot 可见性 |
| `PUT /api/v1/engine/bots/{uuid}/visibility` | * | `/bots/{uuid}/visibility` | 更新 Bot 可见性 |
| `GET /api/v1/engine/bots/{uuid}/friends` | * | `/bots/{uuid}/friends` | Bot 好友列表 |
| `GET /api/v1/engine/bots/{uuid}/groups` | * | `/bots/{uuid}/groups` | Bot 所在群组列表 |

**参数明细：**

| 接口 | 入参 | 出参 |
|---|---|---|
| `GET /api/v1/engine/bots/{uuid}` | path: `uuid`(格式: bot_id:owner_id) | `{actor_kind, bot_uuid, capabilities: {binding_channels: {antding: {binding_key}}, domains[], hidden(bool), name, scopes[], skills[], summary, visibility}, created_by, dynamic_status: {status}, env, status}` |
| `GET /api/v1/engine/bots/discover` | query: `name` | `{bots: [{bot_uuid, capabilities: {domains[], hidden, name, scopes[], skills[], summary, visibility}, visibility}], count}` |
| `POST /api/v1/engine/bots/query` | body: `bot_uuids`*(array) | 直接数组: `[{actor_kind, bot_uuid, capabilities: {binding_channels, domains[], hidden, name, scopes[], skills[], summary, visibility}, dynamic_status: {status}, status, visibility}]` |
| `GET /api/v1/engine/bots/{uuid}/visibility` | path: `uuid` | `{success, data: {bot_uuid, visibility}}` |
| `PUT /api/v1/engine/bots/{uuid}/visibility` | path: `uuid`; body: `visibility`*(public/private) | `{success, bot_uuid, visibility}` |
| `GET /api/v1/engine/bots/{uuid}/friends` | path: `uuid` | `{success, data: []}(好友对象数组)` |
| `GET /api/v1/engine/bots/{uuid}/groups` | path: `uuid`(格式: bot_id:owner_id) | `{bot_uuid, items: [{group_id, label, group_kind, group_strategy, coordinator_bot, participants: [{actor_kind, bot_name, bot_uuid, mode, role, type}], created_at(int), updated_at(int)}], total, limit, offset}` |

---

### Rule #12b — 好友管理（BCS）

| 前缀 | 后端前缀 | 上游 |
|---|---|---|
| `/api/v1/engine/friends` | `/friends` | BCS_URL |

| 接口 | 方法 | 上游路径 | 说明 |
|---|---|---|---|
| `POST /api/v1/engine/friends/request` | * | `/friends/request` | 发送好友请求 |
| `GET /api/v1/engine/friends/requests` | * | `/friends/requests` | 好友请求列表 |
| `POST /api/v1/engine/friends/requests/{id}/accept` | * | `/friends/requests/{id}/accept` | 接受好友请求 |
| `POST /api/v1/engine/friends/requests/{id}/reject` | * | `/friends/requests/{id}/reject` | 拒绝好友请求 |

**参数明细：**

| 接口 | 入参 | 出参 |
|---|---|---|
| `POST /api/v1/engine/friends/request` | body: `from_bot_uuid`*, `to_bot_uuid`*, `message` | `{success, request_id, status: "PENDING"}` |
| `GET /api/v1/engine/friends/requests` | query: `bot_uuid`*, `status`(PENDING/ACCEPTED/REJECTED) | `{success, data[]}(id, from_bot, to_bot, status, created_at, updated_at)` |
| `POST /api/v1/engine/friends/requests/{id}/accept` | path: `id` | `{success, request_id, status: "ACCEPTED"}` |
| `POST /api/v1/engine/friends/requests/{id}/reject` | path: `id` | `{success, request_id, status: "REJECTED"}` |

---

### Rule #13 — 定时任务（AgentClaw）

| 前缀 | 后端前缀 | 上游 |
|---|---|---|
| `/api/v1/cron` | `/api/cron` | AGENTCLAW_URL |

| 接口 | 方法 | 上游路径 | 说明 |
|---|---|---|---|
| `GET /api/v1/cron` | * | `/api/cron` | 任务列表 |
| `POST /api/v1/cron` | * | `/api/cron` | 创建任务（空后缀匹配） |
| `GET /api/v1/cron/status` | * | `/api/cron/status` | 任务状态汇总 |
| `GET /api/v1/cron/{taskId}` | * | `/api/cron/{taskId}` | 任务详情 |
| `PUT /api/v1/cron/{taskId}` | * | `/api/cron/{taskId}` | 更新任务 |
| `DELETE /api/v1/cron/{taskId}` | * | `/api/cron/{taskId}` | 删除任务 |
| `POST /api/v1/cron/{taskId}/run` | * | `/api/cron/{taskId}/run` | 手动触发执行 |
| `GET /api/v1/cron/{taskId}/runs` | * | `/api/cron/{taskId}/runs` | 执行历史列表 |
| `GET /api/v1/cron/running` | * | `/api/cron/running` | 正在运行的任务 |

**参数明细：**

| 接口 | 入参 | 出参 |
|---|---|---|
| `GET /api/v1/cron` | query: `bot_id`(默认 all) | `{success, data: [{id, name, enabled(bool), schedule: {kind, expr, tz}, payload: {kind, message, timeout_secs}, session_target, state: {next_run_at_ms(int), last_run_at_ms(int), last_run_status, last_status, last_duration_ms(int), last_delivered(bool), last_delivery_status, consecutive_errors(int)}, notify: {enabled(bool), user_ids[]}, created_at_ms(int), updated_at_ms(int), bot_id, bot_name}]}` |
| `POST /api/v1/cron` | body: `bot_id`*, `name`*, `schedule`*, `command`*, `timezone`, `enabled`, `timeout_secs`, `model`, `notify{enabled,user_ids}` | `{success, data: {同列表单条含id}}` |
| `GET /api/v1/cron/status` | query: `bot_id`* | `{success, data: {running(bool), job_count(int), enabled_count(int), next_run_at_ms, bot_id, bot_name}}` |
| `GET /api/v1/cron/{taskId}` | path: `taskId`; query: `bot_id`* | 同列表单条 |
| `PUT /api/v1/cron/{taskId}` | path: `taskId`; query: `bot_id`*; body: `name`, `enabled`, `schedule`, `timezone`, `command`, `timeout_secs`, `model`, `notify` | 同创建 |
| `DELETE /api/v1/cron/{taskId}` | path: `taskId`; query: `bot_id`* | `{success, message, error_code, data: null}` |
| `POST /api/v1/cron/{taskId}/run` | path: `taskId`; query: `bot_id`*, `force` | `{success, data: {ok(bool), ran(bool), reason, bot_id, bot_name}}` |
| `GET /api/v1/cron/{taskId}/runs` | path: `taskId`; query: `bot_id`*, `limit` | `{success, data: {input, runs: [{job_id, started_at_ms(int), finished_at_ms(int), status, error, duration_ms(int), output, input_tokens(int), output_tokens(int)}], bot_id, bot_name, unread_count(str)}, error_code, message}` |
| `GET /api/v1/cron/running` | query: `bot_id`(默认 all) | `{success, data: []}(直接数组)` |

> `unread_count` 为 Gateway 增强字段，基于 `read_cursor` 表计算未读执行记录数。

---

### Rule #14 — Bot 入网（BCS）

| 前缀 | 后端前缀 | 上游 |
|---|---|---|
| `/api/v1/admin/bots` | `/admin/bots` | BCS_URL |

| 接口 | 方法 | 上游路径 | 说明 |
|---|---|---|---|
| `POST /api/v1/admin/bots/onboard` | * | `/admin/bots/onboard` | Bot 加入 BCN 网络 |

**参数明细：**

| 接口 | 入参 | 出参 |
|---|---|---|
| `POST /api/v1/admin/bots/onboard` | body: `bot_uuid`*(格式: bot_id:owner_id), `name`*, `capabilities[]`(含name, summary, hidden, visibility, skills[], domains[]) | `{bot_uuid, onboarded, name, capabilities{name, summary, hidden, visibility, skills[], domains[]}, created_by}` |

---

### Rule #15 — 能力集管理 SkillSet（AgentClaw）

| 前缀 | 后端前缀 | 上游 |
|---|---|---|
| `/api/v1/skillsets` | `/api/skillsets` | AGENTCLAW_URL |

| 接口 | 方法 | 上游路径 | 说明 |
|---|---|---|---|
| `GET /api/v1/skillsets` | * | `/api/skillsets` | 能力集列表 |
| `POST /api/v1/skillsets` | * | `/api/skillsets` | 创建能力集（空后缀匹配） |
| `GET /api/v1/skillsets/{id}` | * | `/api/skillsets/{id}` | 能力集详情 |
| `PUT /api/v1/skillsets/{id}` | * | `/api/skillsets/{id}` | 更新能力集 |
| `DELETE /api/v1/skillsets/{id}` | * | `/api/skillsets/{id}` | 删除能力集 |
| `POST /api/v1/skillsets/{id}/skills` | * | `/api/skillsets/{id}/skills` | 添加技能到能力集 |
| `DELETE /api/v1/skillsets/{id}/skills/{skill_id}` | * | `/api/skillsets/{id}/skills/{skill_id}` | 从能力集移除技能 |
| `GET /api/v1/skillsets/with-mcps` | * | `/api/skillsets/with-mcps` | 含 MCP 的能力集列表 |
| `POST /api/v1/skillsets/{id}/mcps` | * | `/api/skillsets/{id}/mcps` | 添加 MCP 到能力集 |
| `DELETE /api/v1/skillsets/{id}/mcps/{mcp_id}` | * | `/api/skillsets/{id}/mcps/{mcp_id}` | 从能力集移除 MCP |

**参数明细：**

| 接口 | 入参 | 出参 |
|---|---|---|
| `GET /api/v1/skillsets` | query: `user_id`*, `bot_id`* | `{success, data: [{id, name, description, is_default(bool), is_builtin(bool), user_id, bot_id, engine_type, gmt_created, gmt_modified, is_active(bool), skills: [{id, name, description, path}], type}], count}` |
| `POST /api/v1/skillsets` | body: `name`*, `owner_id`(或`user_id`) | `{success, data: {id, name, description, is_default(bool), is_builtin(bool), user_id, bot_id, engine_type, gmt_created, gmt_modified, is_active(bool), skills[], type}}` |
| `GET /api/v1/skillsets/{id}` | path: `id`; query: `user_id`, `bot_id` | 能力集详情对象（同创建返回） |
| `PUT /api/v1/skillsets/{id}` | path: `id`; body: 更新字段(`name`, `description`等) | 更新后的能力集对象 |
| `DELETE /api/v1/skillsets/{id}` | path: `id` | `{success, message}` |
| `POST /api/v1/skillsets/{id}/skills` | path: `id`; body: `skill_id`* | `{success, data: {activated[], failed[]}, message}` |
| `DELETE /api/v1/skillsets/{id}/skills/{skill_id}` | path: `id`, `skill_id`; query: `user_id`*, `bot_id`* | `{success, message}` |
| `GET /api/v1/skillsets/with-mcps` | query: `user_id`*, `bot_id`* | `{success, data: [{id, name, description, is_default(bool), user_id, bot_id, mcps: [{id, server_code, name, description, icon, status}]}], count}` |
| `POST /api/v1/skillsets/{id}/mcps` | path: `id`; query: `entity_id`*, `entity_type`*, `bot_id`*; body: `mcp_id`* | `{success, server_code, requires_api_key, requires_permission}` |
| `DELETE /api/v1/skillsets/{id}/mcps/{mcp_id}` | path: `id`, `mcp_id`; query: `entity_id`, `entity_type`, `bot_id` | `{success, message}` |

---

### Rule #16 — MCP 市场（AgentClaw）

| 前缀 | 后端前缀 | 上游 |
|---|---|---|
| `/api/v1/mcp` | `/api/mcp` | AGENTCLAW_URL |

| 接口 | 方法 | 上游路径 | 说明 |
|---|---|---|---|
| `GET /api/v1/mcp/market/list` | * | `/api/mcp/market/list` | MCP 市场列表 |
| `GET /api/v1/mcp/market/detail` | * | `/api/mcp/market/detail` | MCP 详情 |
| `GET /api/v1/mcp/market/permission` | * | `/api/mcp/market/permission` | MCP 权限查询 |
| `GET /api/v1/mcp/tenants` | * | `/api/mcp/tenants` | MCP 租户查询 |

**参数明细：**

| 接口 | 入参 | 出参 |
|---|---|---|
| `GET /api/v1/mcp/market/list` | query: `page_num`, `page_size`, `statuses`(如ONLINE), `network_types[]`, `search_key` | `{success, data: [{serverCode, source, name, icon, description, status, runMode, hostPlatform, platformServerCode, hostAppName, site, tenant, category, accessLevel, networkTypes[], endpoints: [{networkType, transportProtocol, env, url, headers}], stdioConfigs, buCode, productCode, archDomainCode, creator: {userId, userName}, owner: {userId, userName}, tools: [{name, description}], tags[], codeRepoUrl, launchChannels[], vendor}], total, page_num, page_size}` |
| `GET /api/v1/mcp/market/detail` | query: `server_code`* | `{success, data: {同list单条, tools: [{name, description, 含完整inputSchema}], endpoints[], docs, tags[], codeRepoUrl, launchChannels[]}}` |
| `GET /api/v1/mcp/market/permission` | query: `server_code`*, `user_id`* | `{success, has_permission(bool), access_level(str), tool_permissions: {toolName: {code, name}}}` |
| `GET /api/v1/mcp/tenants` | — | `{success, data: [{site, archDomainCode, archDomainName, code, name, categories: [{parentCode, parentType, code, name, children[]}]}]}` |

---

### Rule #17 — MCP 办公网权限（MCP Center）

| 前缀 | 后端前缀 | 上游 |
|---|---|---|
| `/api/v1/mcp/meta/auth` | `/mcp/meta/auth` | MCP_CENTER_URL |

| 接口 | 方法 | 上游路径 | 说明 |
|---|---|---|---|
| `POST /api/v1/mcp/meta/auth/applyPermission` | * | `/mcp/meta/auth/applyPermission` | 申请办公网 MCP 权限 |

> **注意**：`/api/v1/mcp/meta/auth` 优先于 `/api/v1/mcp` 匹配（长前缀优先），因此该路径转发至 MCP Center 而非 AgentClaw。

**参数明细：**

| 接口 | 入参 | 出参 |
|---|---|---|
| `POST /api/v1/mcp/meta/auth/applyPermission` | body: `serverCode`*, `reason`, `expireTime`, `targetCodes[]`, `avatarUrl`, `targetPermission` | `{success, data: true, traceId, errorCode, errorMsg, errorTips}` |

> Gateway 拦截此接口，自动补全权限码：先调用 MCP Center `/mcp/meta/auth/tools` 获取 `accessLevel` 和工具权限码，再根据 `accessLevel`（PUBLIC/AUTHORIZED）自动填充 `targetCodes` 和 `toolPermissionCodes`，最后提交申请。

---

## 4. 路由冲突与优先级说明

以下路径可能因前缀重叠产生歧义，实际匹配结果如下：

| 请求路径 | 命中规则 | 上游服务 |
|---|---|---|
| `/api/v1/engine/bots/*` | Rule #12 | BCS |
| `/api/v1/engine/groups/*` | Rule #7 | BCS |
| `/api/v1/engine/sessions/*` | Rule #7b | BCS |
| `/api/v1/engine/friends/*` | Rule #12b | BCS |
| `/api/v1/engine/{其他}/*` | Rule #8（通配符兜底） | AgentClawProxy |
| `/api/v1/mcp/meta/auth/*` | Rule #17 | MCP Center |
| `/api/v1/mcp/*`（非 meta/auth） | Rule #16 | AgentClaw |
| `/api/v1/bots/*` | Rule #1 | AgentClaw |
| `/api/v1/bot-public/*` | Rule #2 | AgentClaw |

## 5. 禁用路径前缀

以下路径前缀默认被禁用（返回 404），仅例外路径允许通过：

| 禁用前缀 | 例外 |
|---|---|
| `/proxypass` | 无 |
| `/access` | 无 |
| `/bcn` | `/bcn/ws`（WebSocket） |
| `/api/` | `/api/v1/`（即所有 PathMapping 路径） |

## 6. 配置来源

PathMapping 规则定义在以下 TOML 配置文件的 `[[proxy.path_mappings]]` 段中：

- `scripts/admin/config/application-default.toml`
- `scripts/admin/config/application-dev.toml`
- `scripts/admin/config/application-pre.toml`
- `scripts/admin/config/application-prod.toml`

四个环境的 path_mappings 规则完全一致，仅上游 URL 不同。

运行时可通过环境变量 `PATH_MAPPINGS` 覆盖（格式：`prefix=URL_VAR|backend_prefix,...`），以及通过 `AGENTCLAW_URL` / `AGENTCLAWPROXY_URL` / `BCS_URL` / `MCP_CENTER_URL` 环境变量覆盖各上游地址。

---

## 7. WebSocket 透明转发接口

Gateway 提供两类 WebSocket 代理：**独立 WS 连接**（1:1 代理）和**多路复用 WS 连接**（单连接多通道）。

### 7.1 独立 WebSocket 路由

| # | 路径 | 上游服务 | 上游地址 | 说明 |
|---|---|---|---|---|
| WS-1 | `/ws/v1/engine/chat/{target}` | AgentClawProxy | `wss://{AGENTCLAWPROXY_URL}/proxypass/{target}/api/openclaw/ws` | OpenClaw 1:1 Bot 聊天 |
| WS-1a | `/ws/v1/engine/aicoding/chat/{target}` | AgentClawProxy | `wss://{AGENTCLAWPROXY_URL}/proxypass/{target}/api/ws` | AICoding 1:1 Bot 聊天 |
| WS-2 | `/ws/v1/engine/group-chat` | BCS | `wss://{BCS_URL}/ws` | 群聊 |

#### WS-1 — `/ws/v1/engine/chat/{target}`

- **协议**：WebSocket Upgrade (GET)
- **认证**：必须提供 `x-proxypass-token` 查询参数（浏览器无法设置 WS 自定义 Header，因此通过 Query 传递，Gateway 转换为 `X-PROXYPASS-TOKEN` Header 注入上游）
- **路径参数**：`{target}` — Bot 引擎实例标识（如 `ARCA_xxx@0:20003`）
- **上游重写规则**：
    - Gateway 路径 → 上游路径：`/ws/v1/engine/chat/{target}` → `wss://{agentclawproxy}/proxypass/{target}/api/openclaw/ws`
    - HTTPS → WSS 协议自动转换

#### WS-1a — `/ws/v1/engine/aicoding/chat/{target}`

- **协议**：WebSocket Upgrade (GET)
- **认证**：必须提供 `x-proxypass-token` 查询参数
- **路径参数**：`{target}` — Bot 引擎实例标识
- **上游重写规则**：
    - `/ws/v1/engine/aicoding/chat/{target}` → `wss://{agentclawproxy}/proxypass/{target}/api/ws`
    - 与 WS-1 的区别：AICoding 引擎的上游路径为 `/proxypass/{target}/api/ws`（无 `/openclaw` 段）

#### WS-2 — `/ws/v1/engine/group-chat`

- **协议**：WebSocket Upgrade (GET)
- **认证**：IAM 鉴权（通过网关中间件白名单处理）
- **上游重写规则**：`/ws/v1/engine/group-chat` → `wss://{bcs}/ws`
- **用途**：BCN 群聊消息的实时双向通信

### 7.2 多路复用 WebSocket 路由

| # | 路径 | 上游服务 | 说明 |
|---|---|---|---|
| WS-MUX | `/ws/v1/engine/mux` | 多个（按通道类型路由） | 单连接多通道复用 |

#### WS-MUX — `/ws/v1/engine/mux`

- **协议**：WebSocket Upgrade (GET)
- **认证**：IAM 鉴权（在 Mux 处理器内部完成，提取 `X-Staff-Id` / `X-Nick-Name` 注入上游 Header）
- **用途**：允许客户端通过单条 WS 连接承载多个逻辑通道（bot-chat、group-chat），减少连接开销
- **限制**：每个 Mux 连接最多 `mux_max_channels`（默认 20）个并发通道

**通道类型**：

| 通道类型 | `type` 字段值 | 必需参数 | 上游地址 | 说明 |
|---|---|---|---|---|
| Bot 聊天 | `bot-chat` | `target`, `x-proxypass-token`, `engine`（可选，默认 `openclaw`） | 依 engine 路由（见下表） | 1:1 Bot 对话 |
| 群聊 | `group-chat` | 无 | `wss://{BCS_URL}/ws` | BCN 群聊消息 |

**bot-chat 引擎路由**：

| engine 值 | 上游地址 |
|---|---|
| `openclaw`（默认） | `wss://{AGENTCLAWPROXY_URL}/proxypass/{target}/api/openclaw/ws` |
| `aicoding` | `wss://{AGENTCLAWPROXY_URL}/proxypass/{target}/api/ws` |
| 其他值 | `wss://{AGENTCLAWPROXY_URL}/proxypass/{target}/api/{engine}/ws` |

**Mux 协议**：

客户端通过 JSON 帧发送控制命令，主要帧类型：

| 帧类型 | 方向 | 说明 |
|---|---|---|
| `mux.connect` | 客户端 → Gateway | 建立连接握手（必须首帧） |
| `channel.open` | 客户端 → Gateway | 打开新通道（含 `type`、`target`、`engine`、`x-proxypass-token` 等参数） |
| `channel.close` | 双向 | 关闭通道 |
| `channel.data` | 双向 | 通道数据传输 |
| `channel.resume` | 客户端 → Gateway | 恢复断连通道 |
| `channel.suspend` | Gateway → 客户端 | 通知通道挂起（上游断连） |

**通道生命周期**：

- 通道断开后进入 `disconnected` 状态，有 TTL（默认 300 秒，`mux_channel_disconnect_ttl_secs`）
- 在 TTL 内可通过 `channel.resume` 恢复通道（保留 `ch_id`）
- TTL 过期后通道自动关闭，需重新 `channel.open`
- group-chat 通道共享一条 BCS WS 连接（引用计数管理，最后一个 group-chat 通道关闭时释放连接）

### 7.3 WebSocket 辅助端点

| 路径 | 方法 | 说明 |
|---|---|---|
| `/ws/info` | GET | 返回 WS 配置信息：`{heartbeat_interval_secs(int), idle_timeout_secs(int), max_connections_per_ip(int), max_frame_size(int), max_message_size(int), status(str)}` |
| `/ws/health` | GET | WS 健康检查：`{status(str), websocket_enabled(bool)}` |

### 7.4 WebSocket 连接配置

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `max_connections_per_ip` | 100 | 单 IP 最大 WS 连接数 |
| `max_message_size` | 10MB | 单条 WS 消息最大大小 |
| `max_frame_size` | 1MB | 单帧最大大小 |
| `idle_timeout` | 300s | 连接空闲超时 |
| `heartbeat_interval` | 30s | 上游心跳间隔 |
| `heartbeat_timeout` | 10s | 心跳超时 |
| `mux_max_channels` | 20 | 单条 Mux 连接最大通道数 |
| `mux_connect_handshake_timeout` | 10s | Mux 连接握手超时 |
| `mux_bcs_ping_interval` | 30s | BCS WS Ping 间隔（须 < 60s） |
| `mux_channel_disconnect_ttl_secs` | 300s | 断连通道 TTL |
| `mux_client_write_buffer` | 256 | 客户端写缓冲帧数 |
| `mux_channel_write_buffer` | 64 | 通道写缓冲帧数 |
| `mux_bcs_shared_write_buffer` | 256 | 共享 BCS 连接写缓冲帧数 |
| `mux_id_map_ttl_secs` | 60 | ID 重写映射 TTL |

### 7.5 WebSocket 路由总览图

```
客户端                         Gateway                              上游
  │                              │                                  │
  │  /ws/v1/engine/chat/{target} │                                  │
  │ ───────────────────────────> │ ──> wss://{agentclawproxy}       │
  │  ?x-proxypass-token=xxx     │     /proxypass/{target}          │
  │                              │     /api/openclaw/ws             │
  │                              │                                  │
  │  /ws/v1/engine/aicoding/     │                                  │
  │  chat/{target}               │ ──> wss://{agentclawproxy}       │
  │ ───────────────────────────> │     /proxypass/{target}/api/ws   │
  │  ?x-proxypass-token=xxx     │                                  │
  │                              │                                  │
  │  /ws/v1/engine/group-chat   │ ──> wss://{bcs}/ws              │
  │ ───────────────────────────> │                                  │
  │                              │                                  │
  │  /ws/v1/engine/mux          │                                  │
  │ ───────────────────────────> │── bot-chat (openclaw) ─────────> │
  │  (mux.connect)              │── bot-chat (aicoding) ─────────> │
  │  (channel.open type=...)    │── group-chat ──────────────────> │
  │                              │     (共享 BCS 连接)              │
```

### 7.6 独立 WS 连接上下行消息格式

#### 7.6.1 WS-1 — OpenClaw Bot 聊天（`/ws/v1/engine/chat/{target}`）

连接为 1:1 透传代理，客户端与 AgentClawProxy 之间的 JSON 帧格式完全透传，Gateway 不修改内容。

**上行（Client → Gateway → AgentClawProxy）：**

| 帧类型 | 方法 | 说明 | 格式 |
|---|---|---|---|
| 握手 | `connect` | 连接后首条消息，10s 内需发送 | `{"type":"req","id":"conn-001","method":"connect","params":{"minProtocol":3,"maxProtocol":3,"client":{"id":"cli","version":"1.0.0","platform":"web","mode":"cli"},"role":"operator","scopes":["operator.admin","operator.read","operator.write"],"caps":["tool-events"],"auth":{"token":"<proxyToken>"},"user_id":"<userId>"}}` |
| 发送消息 | `chat.send` | 发送聊天消息 | `{"type":"req","id":"msg-001","method":"chat.send","params":{"sessionKey":"session:abc-def","message":"你好","deliver":false,"idempotencyKey":"idem-001"}}` |
| 中止消息 | `chat.abort` | 中止当前回复 | `{"type":"req","id":"abort-001","method":"chat.abort","params":{"sessionKey":"session:abc-def"}}` |
| 审批模式 | `exec.approvals.set` | 设置审批模式 | `{"type":"req","id":"apl-001","method":"exec.approvals.set","params":{"mode":"auto-allow","sessionKey":"session:abc-def"}}` |
| 审批决策 | `exec.approval.resolve` | 响应审批请求 | `{"type":"req","id":"apr-001","method":"exec.approval.resolve","params":{"id":"approval-001","decision":"allow-once"}}` |
| 心跳 | — | 应用层心跳 | `{"type":"ping"}` |

**下行（AgentClawProxy → Gateway → Client）：**

| 帧类型 | 事件/方法 | 说明 | 格式 |
|---|---|---|---|
| 握手响应 | — | 对 connect 的响应 | `{"type":"res","id":"conn-001","ok":true,"payload":{"type":"hello-ok","protocol":3,"server":{"version":"1.0.0","connId":"...","host":"openclaw-enterprise"},"features":{"methods":["chat.send","chat.abort","sessions.reset","exec.approval.resolve"],"events":["tick","chat","agent"]},"auth":{"device_token":"","role":"operator","scopes":["operator.admin","operator.read","operator.write"]},"policy":{"maxPayload":1048576,"maxBufferedBytes":65536,"tickIntervalMs":30000}}}` |
| 发送响应 | — | 对 chat.send 的响应 | `{"type":"res","id":"msg-001","ok":true,"payload":{"runId":"run-xxx"}}` |
| Agent 事件 | `agent` (lifecycle start) | 流式回复开始 | `{"type":"event","event":"agent","payload":{"stream":"lifecycle","data":{"phase":"start"}}}` |
| Agent 事件 | `agent` (assistant) | 流式文本增量 | `{"type":"event","event":"agent","payload":{"stream":"assistant","data":{"delta":"好的,","text":"好的,"}}}` |
| Agent 事件 | `agent` (lifecycle end) | 流式回复结束 | `{"type":"event","event":"agent","payload":{"stream":"lifecycle","data":{"phase":"end"}}}` |
| Chat 事件 | `chat` | 最终回复结果 | `{"type":"event","event":"chat","payload":{"state":"final","message":{"role":"assistant","content":[{"type":"text","text":"完整回复"}]}}}` |
| Tick 事件 | `tick` | 心跳（30s 间隔） | `{"type":"event","event":"tick","payload":{"ts":1713849600}}` |
| 审批请求 | `exec.approval.requested` | 请求用户审批 | `{"type":"event","event":"exec.approval.requested","payload":{"id":"approval-001","request":{...},"sessionKey":"session:abc-def"}}` |
| 错误 | — | 连接或操作错误 | `{"type":"error","message":"Upstream connection timeout"}` |
| 心跳响应 | — | 对 ping 的响应 | `{"type":"pong"}` |

#### 7.6.2 WS-1a — AICoding Bot 聊天（`/ws/v1/engine/aicoding/chat/{target}`）

协议与 WS-1 OpenClaw **完全一致**，仅上游路径不同（`/proxypass/{target}/api/ws` 而非 `/proxypass/{target}/api/openclaw/ws`）。

差异点：
- AICoding `hello-ok` 中 `features.events` 包含 `"agent"`
- AICoding `features.methods` 不含 `exec.approval.resolve`

#### 7.6.3 WS-2 — 群聊（`/ws/v1/engine/group-chat`）

BCS Server 协议，Gateway 1:1 透传。群聊事件帧在顶层额外携带 `bot_uuid` 和 `group_id` 字段。

**上行（Client → Gateway → BCS Server）：**

| 帧类型 | 方法 | 说明 | 格式 |
|---|---|---|---|
| 订阅群组 | `connect` | 订阅群组消息推送 | `{"type":"req","id":"conn-002","method":"connect","params":{"group_id":"grp-001"}}` |
| 发送消息 | `chat.send` | 发送群聊消息（可 @提及） | `{"type":"req","id":"grp-msg-001","method":"chat.send","params":{"sessionKey":"main","group_id":"grp-001","message":"@DBA 排查死锁","bot_uuid":"bot-002","mentions":["bot-002"],"sender_id":"<userId>","thinking":"enabled","timeoutMs":60000}}` |
| 中止消息 | `chat.abort` | 中止群聊回复 | `{"type":"req","id":"grp-abort-001","method":"chat.abort","params":{"group_id":"grp-001"}}` |
| 审批决策 | `exec.approval.resolve` | 群聊审批决策 | `{"type":"req","id":"grp-approval-001","method":"exec.approval.resolve","params":{"id":"approval-001","decision":"allow-once","group_id":"grp-001"}}` |
| 心跳 | — | 应用层心跳（30s+random(0,5s)） | `{"type":"ping"}` |

**下行（BCS Server → Gateway → Client）：**

| 帧类型 | 事件/方法 | 说明 | 格式 |
|---|---|---|---|
| 订阅响应 | — | 对 connect 的响应 | `{"type":"res","id":"conn-002","ok":true,"payload":{"group_id":"grp-001","participants":[{"bot_uuid":"bot-001","role":"Driver","type":"Bot"},{"bot_uuid":"bot-002","role":"Consultant","type":"Bot"}]}}` |
| Agent 事件 | `agent` | Bot 流式回复 | `{"type":"event","event":"agent","bot_uuid":"bot-002","group_id":"grp-001","payload":{"run_id":"run-abc","stream":"assistant","data":{"delta":"我来排查...","text":"我来排查..."}}}` |
| Chat 事件 | `chat` | Bot 最终回复 | `{"type":"event","event":"chat","bot_uuid":"bot-002","group_id":"grp-001","payload":{"run_id":"run-abc","state":"final","message":{"role":"assistant","content":"排查结果：..."}}}` |
| 发送响应 | — | 对 chat.send 的响应 | `{"type":"res","id":"grp-msg-001","ok":true,"payload":{"runId":"run-abc","status":"started"}}` |
| 心跳响应 | — | 对 ping 的响应 | `{"type":"pong"}` |

> **注意**：群聊事件帧在顶层额外携带 `bot_uuid` 和 `group_id` 字段（BCS 协议特性），客户端据此区分同一群组内不同 Bot 的回复。
