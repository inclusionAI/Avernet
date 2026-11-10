# BCS 独立好友发现 · 人工浏览器验收底稿（Task 5）

本文件是 `docs/specs/2026-11-09-bcs-standalone-friend-discovery` Task 5 的人工验收底稿：
环境准备、逐场景操作步骤与证据记录模板。结果必须如实记录；环境无法提供某场景
所需条件时，把该场景标为 **未执行** 并写明原因，不得虚写为已通过（见文末
「执行记录」）。

## 验收对象与镜像

- Frontend Nextgen 镜像：`docker build -f apps/frontend-nextgen/Dockerfile .`
  - BCS-only 形态：`FRONTEND_API_PROFILE=bcs-only` + `BCS_UPSTREAM=<bcs-origin>`
  - full 形态（默认）：`docs/deployment.md`「Required upstreams」全部必填变量
- BCS v1：本仓 `apps/bcs`，提供 `/openapi/v1/auth/**`、
  `GET /openapi/v1/collaboration/bots/mine`、
  `GET /openapi/v1/collaboration/bots/{actor}/candidates`、
  `/openapi/v1/collaboration/friend-connections/**` 与内部
  `GET /api/v1/collaboration/bots/{actor}/candidates/search`。
- BCS-only 形态不部署 Backend/Gateway/Admin/Task Engine；full 形态按现网配置部署。

## 环境准备

1. **OAuth provider（真实）**：使用现有真实 OAuth provider 的两套测试账号；
   不允许 mock 生产身份。
   - BCS `[api.auth]` 采用现成 OAuth-only 配置；`auth.oauth.base_url`
     指向本前端唯一 HTTPS 公网源（回调 `/auth/callback/<provider>` 同源可达，
     见 `docs/deployment.md`「OAuth behavior」）。
   - BCS `trusted_browser_origins` 配置为该前端公网源。
2. **BCS 后台数据**：
   - 目标 Bot T：candidate 列表里对两名测试用户都可见的公开 Bot。
   - 准备一个 Human-only 候选（`user_visibility=public` 且 `visibility≠public`，
     audience=user）与一个 Bot-only 候选（`visibility=public` 且
     `user_visibility≠public`，audience=bot），来自 `apps/bcs` 规则。
3. **人物与角色**：
   - 用户 A（申请者 Human）：真实 OAuth 登录的自然人。
   - 用户 B（Owner）：目标 Bot T 与受管 Bot M 的 Owner；`bots/mine` 对 B 返回
     Human + Bot 两类身份（Human 本人 + 受管 Bot M）。
4. **抓包设置（每个场景执行前重做）**：
   - 浏览器开发者工具 Network 面板勾选 **Preserve log**；
   - 从**空身份 Store 的冷启动**开始记录（全新隐身窗口，或先
     `POST /openapi/v1/auth/logout` 清 `bcs_session` Cookie）；
   - 佐证用 BCS 服务端日志（HTTP access log / request_id），浏览器面板与
     服务端日志按时间窗互证。
   - 需要“停留 ≥30 秒并切后台再恢复”的场景，在抓包窗口完整覆盖该时间窗，
     不能只从发现页列表加载完成后才开始计时。

## 场景记录字段（以下每个场景都按此模板填写）

```
### <场景名>
- 环境：浏览器版本 / 测试账号 / 前端源（镜像 digest 或域名）/ BCS 版本（commit）
- 步骤：
- 预期：
- 实际结果：（待填；未执行时写 **未执行** 及原因）
- 证据：（HAR 摘要 / 截图 / BCS 日志时间窗）
```

## A. BCS-only profile 场景

### A1 普通列表与名称搜索
- 步骤：用户 A 登录 → 广场 Bot 页 → 普通列表分页加载 → 在名称过滤框输入
  目标 Bot T 名称关键字。
- 预期：普通列表按 `GET /openapi/v1/collaboration/bots/human_<A>/candidates`
  返回条目并分页；名称搜索走同一列表接口的 `name` 参数命中 T。

### A2 无 BCSFuse 时智能搜索 name_fallback
- 步骤：A 在智能搜索框输入关键词（部署中不含 BCSFuse）。
- 预期：结果区域标识「名称匹配」（`search_mode=name_fallback`），请求为
  `GET /api/v1/collaboration/bots/<actor>/candidates/search`，浏览器 Network
  确认为同源且返回 20000 信封。

### A3 Human-only 与 Bot-only 交叉可见性
- 步骤：A 先以 Human 视角查候选，再（以 B 登录后）切到受管 Bot M 视角查候选，
  对 Human-only 与 Bot-only 两条准备好的候选分别核对两视角下的显示。
- 预期：Human-only 候选仅 Human 视角出现，Bot-only 候选仅 Bot 视角出现；
  交叉视角下既不显示也不可申请，界面无假可用态。

### A4 申请后刷新 pending
- 步骤：A 以 Human 视角对目标 Bot T 发送好友申请 → 刷新页面再次进入发现页。
- 预期：刷新后 T 的卡片仍显示申请中（sent+pending），请求从
  `/openapi/v1/collaboration/friend-connections/**` 读回真实状态。

### A5 Owner 切 Bot 角色收件并批准／拒绝
- 步骤：B 登录 → 角色选择器切到受管 Bot M → 打开「好友申请」收件列表 →
  对 A 的申请分别演练批准与拒绝（拒绝后再由 A 重新申请一批）。
- 预期：列表请求带 `actor_type=bot, actor_id=M, direction=received,
  status=pending`；批准／拒绝均回读 BCS；操作后列表与 A 侧状态刷新正确。

### A6 申请方刷新看到好友
- 步骤：A5 批准后，A 刷新发现页。
- 预期：A 视角 T 卡片直接显示好友关系（不再 pending）。

### A7 无 Backend 请求（冷启动抓包）
- 步骤：BCS-only 前端源上从空 Store 冷启动 → 打开发现页 → `bots/mine` 返回
  Human + 受管 Bot → 停留 ≥30 秒 → 切后台再恢复可见。
- 预期：整个窗口内 `GET /openapi/v1/bots`、
  `POST /openapi/v1/bots/spaces/personal/initialize`、
  `GET /openapi/v1/bots/work-order-notifications/unread-count` 均为 **0 次**；
  恢复可见后通知轮询仍未启动（通知铃铛不挂载）。粗包必须覆盖首条页面导航，
  不能只从列表加载完成后开始。

### A8 无自动建私聊
- 步骤：A4/A5 批准操作后观察 A 浏览器。
- 预期：不出现 Backend 建会话请求，也不发生旧会话跳转；卡片保持好友态。

### A9 未授权角色被拒
- 步骤：B 切到无管理权限的 Bot（或 A 尝试以非 Owner Bot 视角打开收件列表）。
- 预期：BCS 返回鉴权错误，界面如实展示错误与重试入口，不显示假成功。

## B. full profile 回归场景（不得以 BCS-only 验收代替）

### B1 service unavailable 过滤三态
- 步骤：full 前端源重复普通列表、智能语义、智能名称回退三种浏览。
- 预期：同一 `service:false` 的 Bot 在三种结果中都不出现；unknown/native Bot
  保留；列表顺序稳定（metadata 乱序响应不改变 BCS 排名）。

### B2 旧分享链接的 BCS online 目标
- 步骤：打开指向 BCS `bcsStatus=online` 目标 Bot 的旧分享链接（该目标在
  过滤后第二展示页亦可验证）。
- 预期：不再被误报为下线；`hidden` 或查无目标仍保持失效提示。

### B3 操作进行中切换角色（含 A→B→A）
- 步骤：发起好友申请（批准／撤销任一）后在 Promise 未落定前切到另一角色，
  再演练 A→B→A；对建聊中场景（full）在发出后切角色。
- 预期：旧角色写入成功不污染当前卡片与申请列表、不触发旧会话跳转；
  旧失败不弹新角色的错误提示；切回原角色的新读回反映真实持久化结果，
  不发补偿性撤销请求。

## 执行记录

| 场景 | 结果 | 证据 |
| --- | --- | --- |
| A1–A9、B1–B3 | **未执行** | 见下 |

**未执行原因（如实记录）**：Task 5 的当前执行环境（OpenCode 子代理所在的
macOS 本机）无法提供本验收所需的任一前提条件——没有可登录两套测试账号的
真实 OAuth provider，没有可公网/内网访问的运行中 BCS 服务，也没有可用的
Docker 构建环境（本机无 `docker` CLI 与守护进程、无 Docker Desktop/
OrbStack/colima/podman，`command not found: docker`，无法执行
`docker build -f apps/frontend-nextgen/Dockerfile .` 与双 profile 容器启动）。
因此无法在本会话内产出浏览器 Network 面板或 BCS 日志证据。以上场景由
具备部署条件的操作者按本文档逐场景执行后回填「实际结果/证据」列；
任何场景在环境无法满足时保持 **未执行**，不得虚写为已通过。
