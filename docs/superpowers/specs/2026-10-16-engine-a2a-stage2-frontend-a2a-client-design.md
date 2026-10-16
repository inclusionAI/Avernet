# A2A 阶段 2：前端 fetch 流式客户端设计（ExpertChat 对话面）

状态：**条件通过 / 待终审**。approved 前必须关闭的四项契约与正确性问题
（能力声明生成、ResolveInteraction 双契约分离、取消结局四态、reason 分类
与只读恢复收敛）已在本稿落定方案，待评审人核验；测试矩阵与里程碑按
2026-10-16 第三轮评审意见修订。终审通过前不开实现。

上游设计：[2026-10-13-engine-a2a-protocol-replacement-design.md](2026-10-13-engine-a2a-protocol-replacement-design.md)
§10 迁移蓝图阶段 2。阶段 0/1a/1b 已随 PR#2743 合入 dev（merge `0ddae512d`），
引擎 A2A 面与 BaaS 灰度已就绪。

## 1. 范围与基线

**做什么**：ocb 仓 `apps/frontend`（ExpertChat 对话面）新增 A2A fetch 流式
客户端，v3 全程可回退，默认 off。

**不做什么**：backend grt_chat（线上 30 天零流量，10-15 已整体回退
d998d24）；桌面（Rust/Tauri，主设计已预留阶段 3 C 方案转换层兜底）；BCS
（阶段 3，另行）。

**基线钉死**（实现启动时必须重新核验并回填实际 commit）：

| 仓 | 角色 | 基线 |
|----|------|------|
| Avernet（本仓） | 引擎 A2A 面（已合入 dev）；P0 小前置交付载体 | dev（含 PR#2743） |
| ocb | 全部前端改造落点 `apps/frontend` | 探索期实锚 `REL20261015_avernet_env_param` @ `2420af82a` |
| — | 前端协议契约包 | `@alipay/aix-chat-adapters`（版本以 lockfile 为准），仓内蓝本 `apps/frontend/src/adapters/aicoding/AICodingProvider.ts` |
| — | 配置载体 | 基线实际存在的 `@umijs/max` defineConfig 契约（config.ts 为 bigfish 条件桥接，不预设 Bigfish 专属面）；开关唯一读取入口 = `capability.ts`，默认 off |

"唯一消费方改动"为**待配置验证的主张**而非承诺：注入点 =
`useMultiSessionChat.ts` 的 `specialProvider ?? new OpenClawProvider(providerConfig)`
工厂覆层，不触碰 providerConfig 构造与 v3 分支代码（grep 可证，实现期复核）。

## 2. 架构

```
apps/frontend/src/adapters/a2a/
├── A2AProvider.ts    # 同构 OpenClawProvider 公开面（钉住方式照 AICodingProvider implements AICodingWsEntry 先例）
├── wire.ts            # fetch POST /rpc + ReadableStream SSE 解析 + JSON-RPC 信封 + 错误映射
├── capability.ts      # card 探测/场景能力校验/开关唯一读取/缓存(Map)
└── __tests__/
useMultiSessionChat.ts  # 工厂覆层（探测→注入 A2AProvider 或 null 走 v3）
```

**鉴权**：`x-iam-token` 头走既有 **credentialProvider/xIAMToken 通道**
（每请求取新鲜值）；tokenGetter 是 connect 级 xProxypassToken，与 A2A
无关（`OpenClawProvider.d.ts:37/43/86-95` 实证）。card 探测请求不携带
任何 token。

**地址映射**（a2a router 无前缀挂载，app.py:293；前端永远用自己派生的
地址，不消费 card 自报 url——那是引擎本机视角，非浏览器可达承诺）：

| 形态 | v3（今天） | A2A（映射后） |
|------|-----------|---------------|
| 直连 | `ws://localhost:20003/api/{engine}/ws` | 探测 `http://localhost:20003/.well-known/agent-card.json`；RPC `http://localhost:20003/rpc` |
| 企业代理链 | ConnectionStore 现有 ws url 工厂 | 同前缀规则换面；具体映射随 M4 企业链验收一并钉死（正式验收项，非可选） |

**传输选择状态机**。缓存身份 = `(sessionKey, endpoint_base)` 二元组：

```
entry = { kind: 'a2a' | 'probe-v3' | 'locked-v3' }
开关 off                      → v3（与现状逐字节一致）
开关 on + 探测通过 + 场景校验全过 → 'a2a'
探测失败 / 场景能力缺项        → 'probe-v3'——普通 reconnect 重探可翻
A2A 运行期硬失败               → 'locked-v3'——本页面会话内优先级最高，
                                 自动 reconnect 不得翻案；页面刷新才解除
```

## 3. 恢复与新发送分离（核心总纲，五条）

1. **已知 taskId 的断流**：始终优先按**已应用游标** `ReplayEvents`。
   `SubscribeToTask` 只承担"明确只订阅未来事件、允许不补历史"的场景
   （kernel.subscribe `since_seq=None` 时 backlog 为空，不回放内容——
   `a2a/kernel.py:410-427` 实证），**不得用于断流恢复**：本地状态完整只
   说明断流前无丢失，不说明断流期间无新事件。
2. **未知 taskId 且受理不明**（首 Task 到达前断流）：标记**结果未知**，
   不承诺自动找回。当前无按幂等键查找任务的面（GetTask/SubscribeToTask/
   ReplayEvents 均以 taskId 为入参），客户端 messageId 不可用于查询。
   用户显式重发 = 新幂等键（D14 合规路径）。
3. **传输降级不重发原操作**。"不自动重试"限定为**执行类操作**（发送/
   重发）；只读订阅与恢复不受此限。已知 taskId 的旧任务保留**独立的
   A2A 生命周期处理器**，负责恢复、取消、必要的交互决议，直到终态或
   用户明确放弃——与新发送 provider（可切 v3）分离。
4. **能力不满足的会话在发送前留在 v3**。能力校验按"该会话**可能产生**
   的交互"预判（普通聊天可中途触发 HITL，能力校验不得等到出现审批时
   才把会话改判为 HITL 场景）。
5. **历史 + GetTask 补齐 = 待验证降级路径**，不承诺无损：GetTask 与
   后续请求之间存在无游标可衔的事件窗口，artifacts 为 latest_text 文本
   快照（metadata 空，非结构化全量，`_get_task` R12-7 注释实证）。
   无法补齐时 UI 明示"恢复不完整 / 结果未知"，不假装无损接续。

### 3.1 生命周期归属

- 遗留任务处理器持有旧任务全部状态与监听；回调按 `taskId + 实例代次`
  双键隔离，销毁后 seq fence 使晚到帧一律丢弃，不污染已切换的新会话
  （v3 新会话与降级回调的隔离以代次 fence 测试钉死）。
- 销毁时点（三者之一）：run 终态事件到达 / GetTask 确认任务不存在
  （`not_found_or_expired`）/ 用户明确放弃恢复。终态事件可能先于
  CancelTask 响应到达——销毁流程必须保留接收**该次在途控制请求结果**
  的能力，不得丢 engineAbort。
- 解除同 context 禁发（§4.3）：以 **kernel 侧 run 终态事件**为准
  （终态帧是事实源——服务端终态后不再向该 run 发事件）；engineAbort
  未确认时 UI 附注"底层执行停止未确认"（§4.2 第四态）。终审可改，
  但必须写死一条对称可实现的规则。

### 3.2 幂等键与标识

客户端只保证自己生成的 `message.messageId` 唯一性与重发稳定语义；
服务端可能以 gate 幂等键**覆盖**客户端键（router:163/384 `gated_key or
msg["messageId"]`），且**响应不回显最终采用键**（Task/GetTask 输出均无）。
前端不做也不依赖"回显检测"；跨协议幂等由服务端规范化判定负责（F8）。

| 标识 | 用途 | 前端规则 |
|------|------|----------|
| JSON-RPC `id` | 请求/响应关联 | provider 内部队列 |
| `message.messageId` | 客户端操作幂等键（可能被 gate 覆盖） | 保证唯一与重发稳定，不做采用键检测 |
| A2A `taskId` | 订阅/回放/取消/GetTask 的任务标识 | 仅 A2A 面内使用 |
| `message.contextId` | 会话上下文 | 由 sessionKey 派生 |
| 引擎原生 runId | 跨协议恢复标识 | **不假设兼容**：taskId 交 v3 chat.resume 属未实证路径，本阶段禁止；发现必须跨协议接续的实例按新 gap 上报（引擎侧 native_run_id 注册表先例） |

### 3.3 硬失败分类（locked-v3 窄定义）

非 2xx ≠ 锁定。联合分类 = HTTP 状态 × JSON-RPC reason × 是否主动
abort × 流结束条件：

- locked-v3 仅收"传输面不兼容 / 鉴权面持续拒绝"（如探测通过但 wire
  持续违反协议信封）；
- **普通可恢复断流不锁定**——走总纲第 1 条 ReplayEvents 恢复正路；
- 业务错误（-32000 家族 with reason、-32602 参数错、resolve_conflict、
  unknown_outcome）上浮 UI，不锁定；
- 主动 AbortController.abort() 不是失败。

### 3.4 去重

内容/普通状态事件的去重与**终态确认分开处理**：同一 seq 上未应用的
final marker 必须应用（终态 marker 与最后内容事件共享 seq——
`wire_terminal_marker(seq=last_seq)` 实证）。不预设未经验证的复合去重键。

## 4. 语义映射与流程规则

### 4.1 事件识别顺序（先 resolved 后 requested）

解析顺序**必须先判 `metadata.kind == "interaction.resolved"`，再判
requested 特征**。resolved 事件可能仍处于 INPUT_REQUIRED 态并携带
interactionId 与 engine_frame，反向解析会把它误判为新请求。

- resolved 判定：`metadata.kind == "interaction.resolved"`
  （`wire_interaction_resolved`，独立标识，不依赖帧状态）；
- requested 判定：`state == TASK_STATE_INPUT_REQUIRED` +
  `metadata.interactionId` + `metadata.engine_frame`（requested 侧无
  kind——`wire_interaction_requested:120-131` 实证）；
- interactionId 只做**待决项关联**，不用于区分事件类型。

### 4.2 ResolveInteraction 双契约分离

| 层 | 形状 |
|----|------|
| 引擎内部契约（router→适配器结果） | `{success, payload\|error}`（R17-3 判定锚） |
| 前端 wire 契约（JSON-RPC） | 成功 `result.interaction`（resolved）；失败 JSON-RPC error，如 `data.reason=resolve_unsupported / not_found / resolve_conflict` |
| 状态事件 | resolved 以 `metadata.kind` 识别 |

### 4.3 取消结局四态（按 `_cancel_task` 真实契约）

引擎侧顺序：kernel 先 `cancel_run` 转 CANCELED → 再 best-effort
`_forward_engine_abort` → 响应附录 engineAbort。因此 task=CANCELED 不能
等价于"底层执行已停止"：

| 结果 | UI 表达 |
|------|---------|
| 取消请求被明确拒绝（含已终态重复取消 `task_terminal`） | 取消未成功，保留已知任务状态（以 `data.state` 区分终局） |
| task=CANCELED 且底层停止已确认 | 已取消 |
| task=CANCELED 且底层停止未确认 | 已取消，底层执行停止未确认 |
| 请求断流，取消结果未知 | 取消结果未知，尝试查询任务状态 |

### 4.4 只读恢复失败分类（按真实 reason）

reason 实字：`cache_limit`（r 存在且缓存越限）/ `not_found_or_expired`
（任务不存在**或**回放窗口过期——两者同面，不可直接等价任务丢失）。

```
ReplayEvents 被拒
  → GetTask
      ├─ 找到且终态 → 展示可取得结果，标注缺失内容（恢复不完整）
      ├─ 找到且活跃 → 恢复不完整，尝试未来订阅；须处理 GetTask 与
      │              SubscribeToTask 之间任务已终结的竞态——不得无限
      │              等待不会到来的 final
      └─ 确认 not_found_or_expired → 结果未知
```

只读恢复配置**有限退避 + 停止条件 + 用户放弃入口**——"继续到终态"
不得演变为无限后台请求。

## 5. HITL 能力声明契约（P0 前置交付，Avernet 仓）

路由存在 ≠ 能力存在：`_forward_resolve` 按交互类型检查引擎方法，不满足
时出结构化 `resolve_unsupported`（router:852/943）。P0 不是往全局
`EXTENSION_URIS` 加一个 resolve URI：

- **按当前引擎真实支持的交互类型生成能力声明；无法证明支持的形态不**
  **声明**。"一个代表完整能力集的 URI"还是"附带支持类型细分"在 P0 的
  扩展契约中一并定义（P0 内拍板）。
- 前端仅在声明**覆盖该会话可能产生的全部交互**时启用 A2A；覆盖不了
  → 发送前该会话留 v3。
- 运行期出现"声明了但 resolve_unsupported"：遗留任务处理器上浮"不可
  决议"，任务保留（不悬挂、不误标完成）。
- 形态白名单只作 P0 落地前的过渡，且必须**绑定已验证的引擎版本/部署**
  **范围**——仅凭形态名称不能证明老部署具备实现。
- 单测覆盖支持形态与不支持形态两侧，不只是 card 金样更新。

## 6. 测试策略

### 6.1 对拍口径分层（真实模型不进确定性门槛）

- **契约对拍（确定性）**：同一引擎事件 fixture 分别经 v3/A2A 双面转换，
  比较文本、工具块、交互身份、终态与错误码。
- **真实引擎 E2E（行为验证）**：发送/恢复/取消/审批的可观察行为正确，
  **不要求**两次真实生成逐字一致。

### 6.2 关键复现场景

- wire 单测：真 ReadableStream SSE 解析（分块跨界/半行/多事件/超长行）；
  JSON-RPC 信封；三层标识提取。
- 首事件前断流：**stub 先记录服务端受理、再截断响应**；断言恰好一次
  执行且前端未自动重发（F5 上游持证同款）。不表述为"首事件前失败必然
  已执行"——有些请求根本未到服务端。
- 断流后续：appliedSeq 游标回放补齐断流期间事件（seq 11/12 复现）。
- 共 seq 双事件（内容+final marker）都应用。
- locked 误判矩阵：abort/close/业务错误不锁；协议损坏才锁。
- 降级代次隔离：遗留任务晚到帧不污染 v3 新会话。
- 回放被拒→GetTask 三分支收敛（含 cache_limit 与 not_found_or_expired
  分辨、GetTask-Subscribe 竞态不悬等）。
- 取消四态各一。
- HITL：requested→决议→resolved(kind) 全链；INPUT_REQUIRED 态的
  resolved 不误判为 requested（**先 resolved 后 requested 解析顺序的**
  **复现断言**）。

### 6.3 本轮新增四条

1. card 不为不支持形态声明 resolve（P0 契约反向断言）；
2. INPUT_REQUIRED 状态的 resolved 不被误判 requested；
3. task CANCELED 且 engineAbort 未确认（第三态 UI，第二态确认用例成对）；
4. replay 报 not_found_or_expired 但 GetTask 仍找到活跃/终态任务。

## 7. 交付切分

| # | 交付 | 退出线 |
|---|------|--------|
| P0 | 【Avernet】HITL 能力声明契约：按真实支持生成、双测、扩展契约文件内拍板支持粒度 | 引擎单测绿（支持/不支持两侧）；card 可分场景校验 |
| M1 | wire 层 + 探测 + 场景校验 + 开关默认 off（不接线） | wire 单测全绿 |
| M2 | 接线 chat.send 恢复/取消/locked/代次隔离；**范围限定为确定不触发未实现交互可验证的会话**，功能保持未放量 | 发送/恢复/取消/锁定/代次隔离测试全绿 + 契约对拍逐字 |
| M3 | 结构化事件全套、HITL 全链、能力声明与前端消费对拍 | HITL 契约测试 + resolve_unsupported 上浮用例绿 |
| M4 | 真实部署链路验收（**企业代理链地址映射/鉴权/SSE 可达性为正式验收项**）+ 灰度 + 回滚语义 | 双面真引擎冒烟证据文档（先例：SECOND_STAGE_EVIDENCE） |

**回滚语义（M4）**：关闭开关只阻止新的 A2A 任务，**不销毁已有任务处理
器**——已有任务继续完成、取消，或由用户明确放弃。

**灰度观测与停止放量**：至少记录探测回退、协议错误、恢复失败、结果
未知、不可决议、底层停止未确认六类事件，并在 spec 终审时定下哪些触发
停止放量。

## 8. 终审关闭清单（approved 前必须逐条核验）

1. §5 能力声明生成契约（前四项契约问题之一）；
2. §4.1/4.2 resolved 识别顺序与双契约分离；
3. §4.3 取消四态与 §3.1 禁发解除规则（engineAbort 未确认场景）；
4. §4.4 reason 分类与只读恢复退避/停止/放弃参数；
5. §7 灰度六类观测事件中哪些触发停止放量。

首版范围不再扩大；桌面/BCS/GRT 维持排除。