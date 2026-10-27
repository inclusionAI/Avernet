# A2A 用户灰度放量设计（签发面门控）

- 日期：2026-10-27
- 范围：`apps/backend`（浏览器聊天传输的 A2A face 签发门控）
- 关联：`docs/superpowers/specs/2026-10-16-engine-a2a-stage2-frontend-a2a-client-design.md`（阶段二签发面）、总体迁移设计 §10.2 放量条件

## 1. 问题

A2A 迁移主干已合入 dev，但放量缺一个**用户粒度**灰度载体：需要「灰度部分用户 → 观察一段时间 → 全量切流」的运营能力，且每档调整必须即时生效（不重发镜像）、可秒级回滚。

## 2. 方案（已评审选型）

**服务端权威门控「双层闸」**：

```
放量 = 前端总闸（构建 ARG，一次放全） × 服务端名单闸（本设计）
名单命中 → 签发 a2a face → 前端探针走 A2A
名单未中 → 签空串 → 前端探针自动落 v3（阶段二既有语义，零前端改动）
```

- 决策唯一源点：`BaasDeviceService.get_device_connection`
  （`operator.staff_id` 在手；`engine_runtime.connection` 的 sockets 面与
  `devices converter` 的 DeviceConnectionResponse 面都消费其
  `DeviceConnectionInfo`，单点门控双面继承）。
- 备选否决：前端远程开关（面不收敛、客户端可篡改、双端新代码）、
  构建/部署形态放量（比例不可渐调）。

## 3. 配置载体与判定语义

存储于既有 `system_config` 表（category=`system`，config_key=`a2a_issuance_gray`），
`SystemConfigService.get_config` 读取（值解析自动回 dict）：

```json
{"enabled": true, "mode": "whitelist", "users": ["工号或用户标识", ...]}
{"enabled": true, "mode": "percent",  "percent": 10}
```

判定规则（`is_user_in_gray` 纯函数）：

- `whitelist`：`staff_id ∈ users`；
- `percent`：`sha256("a2a-gray:" + staff_id)` 前 8 字节整数 `% 100 < percent`
  （稳定 hash，同用户永远同侧，跨设备/跨会话粘性由构造保证）。

语义矩阵（fail 方向对每条明确；**R1 评审修订**：配置对象已存在而缺 `enabled`/`enabled:null` 也按非法 fail-closed——"解除门控"只有键不存在或显式 `enabled:false` 两个正形）：

| 状态 | 结果 |
|---|---|
| 配置**键**不存在 / 显式 `enabled:false` | **不门控**（face 照常签发——维持阶段二加性面，存量行为零变化） |
| 配置对象存在但缺 `enabled` / `enabled:null` / 非布尔 | **fail-closed 等价空名单**（全部拒发）——运营贴了配置却漏字段绝不能静默全量放行 |
| `whitelist` 合法 | 名单内签发，名单外拒发 |
| `percent` 合法（0-100） | 按分桶命中 |
| 配置存在但 JSON/结构非法 | **fail-closed 等价空名单**（全部拒发）+ 告警日志 |
| 配置读取异常（DB 故障） | **fail-closed 本次拒发** + 告警日志 |
| 身份（`caller_id`，缺席回退 `operator.staff_id`）为空 | fail-closed 不签 |

**身份（R1 评审修订）**：灰度键取**经认证的实际访问用户**。engine_runtime 连接面有意把 operator 重标为 resolved owner（权限兼容 + 实例亲和，见 `connection.py` 的 operator 构造注释），该面必须通过 `caller_id` 参数把认证用户传到门控；operator 本身就是 caller 的面（devices HTTP 面）回退 `operator.staff_id`。协作者不继承 owner 的灰度桶。

**明确拒绝协议（R2 评审修订）**：门拒发时签发 `a2a_withheld=true`（不只有空地址）。空串 = 旧服务端/无地址（客户端 ws 推导兜底**保留**）；`a2a_withheld` = 治理后明确拒绝（客户端禁用推导、探测与缓存命中构造，`bootstrap.ts` 两大构建入口拦截）。`resolveA2aUrls` 的 send-ban 遗留键查询不受影响——回滚换轨窗口的在途任务禁令必须继续生效。

**生效边界（R2 评审遗留确认）**：配置只在**下次连接签发**时重新读取；已初始化会话复用 provider，不重读灰度配置。"清空名单"的秒级回滚对**新连接**成立，对**在线会话**是软着陆（在途 A2A 任务继续跑完/由遗留任务面收尾）——运营 sensitivity 在灰度收放节奏上考虑，不承诺在线即断。

回滚与放量操作形态（无需重发镜像、下次签发即时生效）：

- 灰度白名单：配置 whitelist+名单；
- 抬档百分比：`percent` 0→10→50；
- 回滚：名单清空（`whitelist, users=[]`）即全体落回 v3；
- 全量：`{"enabled": false}` 解除门控（前端总闸届时已全局 on）。

**操作顺序硬约束：先配置灰度名单，后放开前端构建总闸。**
相反顺序会在开闸瞬间对未门控用户全量生效。

## 4. 组件与数据流

```
core/devices/services/a2a_issuance_gray.py     ← 新增（纯判定层 + 门服务，无 DI 状态）
  ├─ A2aIssuanceGrayConfig (frozen dataclass)
  ├─ parse_gray_config(raw) -> Config | None   ← None=不门控；非法=封闭名单
  ├─ is_user_in_gray(config, staff_id) -> bool
  └─ A2aIssuanceGate(config_service)            ← allows_face(staff_id) -> bool
                                                  读失败→False+warn；未配置→True

core/devices/services/baas_device_service.py   ← 改
  ctor 增 a2a_issuance_gate（默认 None=未接线，沿构造器既有可选接线 idiom）
  get_device_connection: a2a_base_url 出口前经 gate 判定

di/modules/devices_module.py + singlebox/devices.py ← 改，注入 A2aIssuanceGate(system_config_service)
```

观察埋点：gate 判定处一行结构化日志
`[a2a_gray] decision=allow|withhold mode=... staff=<遮蔽>` ——
antlogs 可查；服务端观测链（Engine /rpc 成功率 + wire 失败 taxonomy + 任务终态异常率）
为观察期主判据，**零前端埋点依赖**。

## 5. 明确不在本设计内

- 前端总闸打开（构建 ARG `A2A_CLIENT_DEFAULT` 注入）——发布工程操作，非代码；
- BaaS `bot_run.a2a_client_mode`（服务端→引擎路径，独立灰，正交不动）；
- OpenAPI 第三方消费面 `get_ws_info_by_bot_uuid`（由 BaaS 侧 mode 自行治理）；
- 前端埋点/上报体系（百分比大档阶段再立项）；
- 全量切流的前置判据（附件/文件引用按能力回退 P0 + 遗留任务 UI 闭环 P0）——
  本设计提供载体，切流时点由运营按前置判据拍板。

## 6. 测试

- 纯函数直测：parse 全语义矩阵（含非法 fail-closed）、percent 粘性与 0/100 边界、staff 空；
- 门服务：fake ConfigService 验 5 态（未配置/命中/未命中/DB 异常/坏形状）；
- `test_baas_device_service`：gate 未接线透传 / 接线命中签发 / 接线未命中签空；
- DI wiring：devices_module 与 singlebox 工厂产 gate；
- 存量契约快照与阶段二 40 项连接测试零回归（gate 缺省 None）。