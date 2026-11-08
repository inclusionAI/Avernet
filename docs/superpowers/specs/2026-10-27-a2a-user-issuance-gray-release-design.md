# A2A 用户灰度放量设计（签发面门控）

- 日期：2026-10-27（评审定稿版）
- 范围：`apps/backend`（浏览器聊天传输的 A2A face 签发门控）+ 前端拒绝消费
- 关联：`docs/superpowers/specs/2026-10-16-engine-a2a-stage2-frontend-a2a-client-design.md`（阶段二签发面）、总体迁移设计 §10.2 放量条件

## 1. 问题

A2A 迁移主干已合入 dev，但放量缺一个**用户粒度**灰度载体：需要「灰度部分用户 → 观察一段时间 → 全量切流」的运营能力，且每档调整必须即时生效（不重发镜像）、可秒级回滚。

## 2. 方案

**服务端权威门控「双层闸」**：

```
放量 = 前端总闸（构建 ARG，一次放全） × 服务端名单闸（本设计）
名单命中 → 签发 a2a face（或保留推导兜底）→ 前端走 A2A
名单未中 → 空地址 + a2a_withheld=true → 前端禁用推导/探测/缓存 → v3
```

- 判定唯一源点：`BaasDeviceService.get_device_connection`
  （`engine_runtime.connection` 的 sockets 面与 `devices converter` 的
  DeviceConnectionResponse 面都消费其 `DeviceConnectionInfo`，单点门控双面继承）。
  **Desktop 例外（R5 修）**：desktop 的 direct/relay 分支不经由设备服务，
  在 `EngineConnectionService` 的 desktop 分支就地执行同一判定（identity=
  authenticated caller），direct 早退返回与 relay 的 `SimpleNamespace` 组装都
  带出 `a2a_withheld`；门的异常只收敛面不炸连接（与 BaaS 路径同契约）。
- 拒绝信号是 wire 契约的一部分：`a2a_withheld` 逐层透传
  （`DeviceConnectionInfo` → `ConnectionResult` → HTTP 组装 → DTO →
  `botChatProvider` → 包/`bootstrap` 双入口拦截）。空地址与明确拒绝是
  **两个语义**：前者是旧服务端/无地址（推导兜底保留——它也是名单成员
  在直连拓扑使用 A2A 的正路），后者禁止推导、探测与缓存命中构造。
  `resolveA2aUrls` 的 send-ban 遗留键查询不受拦截——回滚换轨窗口的
  在途任务禁令必须继续生效。
- 备选否决：前端远程开关（面不收敛、客户端可篡改、双端新代码）、
  构建/部署形态放量（比例不可渐调）。

## 3. 配置载体与判定语义

存储于既有 `system_config` 表（category=`system`，
config_key=`a2a_issuance_gray`）。**门服务直持 `ConfigRepositoryProtocol`**：
repo record 的 `config_value` 是未 parse 的原始字符串，使「配置项存在」
与「配置值合法」成为两个可分判据（`SystemConfigService.get_config` 会把
「键不存在」和「值是 'null'」折叠成同一个 `None`，两者运营含义恰好相反）。

```json
{"enabled": true, "mode": "whitelist", "users": ["工号或用户标识", ...]}
{"enabled": true, "mode": "percent",  "percent": 10}
```

判定规则（`is_user_in_gray` 纯函数）：

- `whitelist`：身份 ∈ `users`；
- `percent`：`sha256("a2a-gray:" + 身份)` 前 8 字节整数 `% 100 < percent`
  （稳定 hash，同用户永远同侧，跨设备/跨会话粘性由构造保证）。

**灰度身份**取**经认证的实际访问用户**：engine_runtime 连接面有意把
operator 重标为 resolved owner（权限兼容 + 实例亲和），该面必须通过
`caller_id` 参数把认证用户传到门控；operator 本身就是 caller 的面
（devices HTTP 面）回退 `operator.staff_id`。协作者不继承 owner 的灰度桶。

判定触发：gate 接线后**无论上游是否签发 `a2a_base_url` 都执行判定**——
local/单机 provider 不填地址，但直连拓扑客户端可从 WS 推导 A2A 地址
探测使用；拒绝 = 空地址 + `withheld=true`，允许时空地址原样保留
（`withheld=false`）。gate 未接线（测试/旧装配形态）原样透传。

语义矩阵（fail 方向对每条明确）：

| 状态 | 结果 |
|---|---|
| 配置**键**不存在 / 显式 `enabled:false` | **不门控**（face 照常签发——维持阶段二加性面，存量行为零变化） |
| 配置记录存在但值为 `null`/空串/空白 | **fail-closed 等价空名单**（半配置事故，全部拒发） |
| 配置对象缺 `enabled` / `enabled:null` / 非布尔 | **fail-closed 等价空名单**——运营贴了配置却漏字段绝不能静默全量放行 |
| `whitelist` 合法 | 名单内签发，名单外拒发 |
| `percent` 合法（0-100） | 按分桶命中 |
| 配置存在但 JSON/结构非法 | **fail-closed 等价空名单** + 告警日志 |
| 配置读取异常（DB 故障） | **fail-closed 本次拒发** + 告警日志 |
| 身份（`caller_id`，缺席回退 `operator.staff_id`）为空 | fail-closed 不签 |

回滚与放量操作形态（无需重发镜像、下次签发即时生效）：

- 灰度白名单：配置 whitelist+名单；
- 抬档百分比：`percent` 0→10→50；
- 回滚：名单清空（`whitelist, users=[]`）即新连接全体落回 v3；
- 全量：`{"enabled": false}` 解除门控（前端总闸届时已全局 on）。

**操作顺序硬约束：先配置灰度名单，后放开前端构建总闸。**
相反顺序会在开闸瞬间对未门控用户全量生效。

**生效边界**：配置只在**下次连接签发**时重新读取；已初始化会话复用
provider，不重读灰度配置。"清空名单"对**新连接**是秒级硬回滚，对
**在线会话**是软着陆（在途 A2A 任务继续跑完/由遗留任务面收尾）——
运营节奏按此设计，不承诺在线即断。

## 4. 组件与数据流

```
core/devices/services/a2a_issuance_gray.py     纯判定层 + 门服务，无 DI 状态
  ├─ A2aIssuanceGrayConfig (frozen dataclass)
  ├─ parse_gray_config(raw) -> Config | None   None=显式 enabled:false 解除；
  │                                            存在但任何非法形态=封闭名单
  ├─ is_user_in_gray(config, staff_id) -> bool
  └─ A2aIssuanceGate(ConfigRepositoryProtocol)  allows_face(staff_id) -> bool
                                                record 缺失→True；读失败→False+warn

core/devices/services/baas_device_service.py   出口门控
  ctor 可选注入 a2a_issuance_gate（未接线透传）
  get_device_connection: 有 face 与否都过门，返回 (face, withheld)

di/modules/devices_module.py + singlebox/devices.py   注入 A2aIssuanceGate(config_repository)
```

观察埋点：gate 判定处一行结构化日志
`[a2a_gray] decision=allow|withhold mode=... staff=<遮蔽>` ——
antlogs 可查；服务端观测链（Engine /rpc 成功率 + wire 失败 taxonomy + 任务终态异常率）
为观察期主判据，**零前端埋点依赖**。

## 5. 明确不在本设计内

- 前端总闸打开（构建 ARG `A2A_CLIENT_DEFAULT` 注入）——发布工程操作，非代码；
- BaaS `bot_run.a2a_client_mode`（服务端→引擎路径，独立灰，与白名单的运维协同见 §7；按人白名单在服务端路径不成立——调用链无个人身份可判，物理上只能全局三态）；
- OpenAPI 第三方消费面 `get_ws_info_by_bot_uuid`：有调用方身份、技术上可接 gate，
  本期按"简化优先"不扩；如后续要按名单灰第三方签发面，增量接 gate 即可（判定/测试模式全部现成）；
- 前端埋点/上报体系（百分比大档阶段再立项）；
- 全量切流的前置判据（附件/文件引用按能力回退 P0 + 遗留任务 UI 闭环 P0）——
  本设计提供载体，切流时点由运营按前置判据拍板。

## 6. 测试

- 纯函数直测：parse 全语义矩阵（含记录存在但空值/缺 `enabled`/非布尔的
  fail-closed）、percent 粘性与 0/100 边界、身份空；
- 门服务：repo record 形态 fake，验 未配置（键/分类缺失）/命中/未命中/
  `'null'`/`''`/空白/DB 异常/坏形状；
- `test_baas_device_service`：未接线透传 / 命中签发 / 未命中签空+withheld /
  **无 face 仍判定**（单机/直连拓扑）；
- HTTP 值传递：core `True` → HTTP JSON `True`（组装点手工复制行为钉板）；
- DI wiring：devices_module 与 singlebox 工厂注入 repo 形态 gate；
- 前端：bootstrap 双入口拒绝（不推导、不探测、缓存命中不构造）、
  连接响应 `a2a_withheld` 读取透传；存量契约快照与阶段二连接测试零回归。

## 7. 上线操作卡（运维协同版）

两条链路、两个键、各有现成 HTTP 运维面，**全部远程操作、不碰数据库、不发版**：

| 链路 | 键（存储） | 运维面 | 生效节奏 |
|---|---|---|---|
| 聊天（浏览器→引擎，按人） | `a2a_issuance_gray`（backend `ac_config_item`） | `POST /api/v1/config/set`（upsert） | 下次连接签发，**秒级** |
| 管理接口/服务端（BaaS bot_run→引擎，全局） | `bot_run.a2a_client_mode`（BaaS `baas_system_config`） | `POST /api/v1/system-configs` 首建 / `PUT /api/v1/system-configs/{key}` 改值 | **排干式**：池中连接无活跃会话后重建 |

### 动作 ① 开白名单（灰度开始）

两笔调用，环境各做一遍（env 不传默认当前环境）：

```jsonc
// backend：名单内的用户聊天走 A2A
{ "category": "system", "config_key": "a2a_issuance_gray",
  "config_value": "{\"enabled\":true,\"mode\":\"whitelist\",\"users\":[\"240841\"]}" }
// BaaS：服务端调用走 A2A，探测失败自动回 v3（grey 即安全灰度形态）
{ "conf_key": "bot_run.a2a_client_mode", "conf_value": "grey" }
```

**顺序铁律不变：先配 backend 白名单、再放开前端总闸（发前端 ARG 镜像那一步）。**

### 动作 ② 抬档

只动 backend：`config_value` 改 `{"enabled":true,"mode":"percent","percent":10}` → 50。
BaaS 保持 `grey`；`on`（探测失败抛错）留到阶段三验收后拍板。

### 动作 ③ 回滚

两笔都做，别只改一边：

- backend：`{"enabled":true,"mode":"whitelist","users":[]}` —— 新连接秒级全回 v3
- BaaS：`conf_value` 改 `"off"` —— 活跃会话跑完、连接重建后回 v3（软着陆）

### 观察面分工

- 聊天链路：backend 日志 `[a2a_gray] decision=allow|withhold ...` + 签发响应
  `a2a_withheld`；试点用户定向反馈
- 服务端链路：引擎 `/rpc` 流量来源分布与失败率
- 生效边界：两条链路都不打断在途任务；"秒级"仅对新连接成立

### 记忆三句话

1. **聊天看人（白名单、秒级）、服务端看全局（grey、排干式）** ——物理差异，不是复杂度
2. 回滚是两笔，各管各的层，关一层不关另一层
3. `{"enabled":false}` 是全量解除；漏写 `enabled`/空值不是关闭，是全部拒发（fail-closed）