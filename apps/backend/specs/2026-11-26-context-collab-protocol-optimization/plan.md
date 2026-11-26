# R3 实施计划：保留 Method 组合、YAML 阶段与 L6 五方法服务

当前状态：**A–E 新域主路径已实施并通过专项及完整 Backend CI；F 的旧产品/存量历史全量切换与外部验证仍有边界，逐项结果见 [delivery.md](delivery.md)。** 以下保留原实施顺序与验收目标。原设计阶段状态为“待实施”。按 [方案 R3](spec.md) 和本轮用户要求组织，ProductProfile 同时保留 composition 与 stages YAML，不沿用初稿“只做 Rollout 视图/只保留资源引用”的目标。[证据与初稿被替换的选择](research.md) 独立保存。

2026-11-26 接口精简修订：五方法及其 HTTP 正文移除 `context`、`request_id`，身份通过可信 G1 作用域传递，回执标识内部生成；保留 `expected_revision`。以下历史创建/干预的客户端幂等目标由 [spec §5.2](spec.md#52-服务签名草图) 的独立调用合同替代，独立进度上报与仓储内部回执幂等不变。新增验收：缺失身份拒绝、并发身份隔离、相同创建内容产生独立 ID、干预旧 revision 冲突和 HTTP schema 无旧参数。

2026-11-26 后续干预合同修订：在 `interactive_context` 前新增必填 `interactive_uuid`，移除公开 `expected_revision`，替代上一段中干预按次调用及传入 revision 的合同；创建仍为独立调用。新增验收：四种干预顺序/跨服务重放、当前授权先于回执、同 UUID 改内容/改 rollout 冲突、相同反馈不同 UUID 独立生效、回执与所有副作用原子提交、并发重复在 save 回执读取及 CAS 两种竞态下只提交一次。详见 [spec §5.6](spec.md#56-interactive) 与 [delivery §6](delivery.md#6-用户追加修订干预唯一-id-与内部并发围栏)。

## 1. 实施顺序

```text
A 替换合同与迁移清单
        ↓
B 领域对象 + Method 组合 + YAML + codec
        ↓
C 仓储/执行适配/会话记录
        ↓
D ProductProfileService 五方法 + DI/API
        ↓
E ProgressEvent 接纳/交互后继轮次/真实消息验收
        ↓
F 兼容切换、完整测试与 owning 文档
```

原方案阶段范围记录（历史）：当时用户只要求修改方案，未开始代码实施，也未预报人日。后续用户已明确要求按本方案完成开发和测试；实际工作及差异单独记录在 [delivery.md](delivery.md)。

## 2. 切片与验收

### A. 合同替换与影响清单

- 确认新 ProductProfile/Collet/Rollout/Artifact/Event 的精确字段集合与三个枚举，不增加旧模型字段来规避本次目标。
- 列出 ProductProfileDefinition、同名 ProductProfile 枚举、旧 Collet/Artifact、codec/reader/publication、API、DI 和产品调用方。
- 保留 `composition: MethodCompositionTemplate`，固定 governance/Task/Method 与新域的适配边界；明确 Method relations 与阶段 depends_on 分属两个层级，权限前提不得因其他字段删除而消失。
- 明确破坏性更改、旧数据/字符串 ID 到新整数 ID 的映射、状态机替换和回滚界限；需要时按 ADR 生命周期评审。
- 恢复当前合同相关但已缺失的专项测试/导航，不从 pycache 或历史通过数推断今日可验收。
- 验收：当前/目标合同差异和消费者迁移项有清单；不能只改类名就称完成。

### B. 正式领域对象、YAML 与 codec

- 新 ProductProfile 精确包含 `id/name/composition/stages`；composition 必填并复用现有 MethodCompositionTemplate 的 required_methods/optional_methods/relations。该对象承接定义、联合校验、存储及读取，删除 ProductProfileDefinition 的运行时依赖，最终不保留永久 alias。
- 修改 Collet 与 Artifact；新增 ColletRollout、StageRollout、ArtifactContent/三个子类、ProgressEvent、AcceptanceResult。
- 共享值只依赖低层合同，L4 不导入 L5/L6。
- `ProductProfile.stages` 是 YAML 文本，顶层为 stages；支持图示 `acceptance` 单 mapping 与列表两种形态，内部统一编译。
- 不要求图外 node_id、outputs、I/O 合同字段；严拒重复 key、非法标签/类型、重复节点和有环依赖。
- 联合校验 MethodProfile/policy/relations 与阶段执行及验收能力，不从 YAML 推断 Method 组合或隐式启用 optional methods；未声明 policy 不自动获得执行/录取权限。
- stages YAML 是阶段 DAG 唯一配置源；迁移 SwarmWorkflowPolicy.workflow 时提取图并适配策略，冲突显式拒绝，不保留第二份可独立修改的图。
- ArtifactContent 按封闭子类及互斥字段 shape 解码，内嵌内容支持且有体积/日志安全限制。
- 验收：每个类成员集合与 spec 一致；三个枚举值不擅自扩充；新整数 ID 拒绝 bool；composition/YAML/Artifact 往返正确，组合策略完整保留；未知/混合字段拒绝。

### C. 持久化、单一执行权威与会话记录

- 固定 Profile/Collet/Rollout/Stage 记录、当前轮次指针、历史关系、回执/投递意图的实际 DDL 与 codec；Profile 中 composition 与 stages 原子持久化，读取返回同一冻结配置。
- 新领域对象由明确仓储读取/提交，不是仅在读接口上拼装 Plan 视图；内部复用 Task/Plan 时有持久化映射和唯一写入口。
- 原治理/参与/资源授权、租约、尝试等恢复前提不得只存在内存。
- 按 composition 初始化实际 MethodBinding/relations，按 stages 创建阶段；创建时原子保存 Collet/绑定及关系/初轮/全阶段/回执及推进意图。网络 IO 在事务外，未知派发结果显式对账而非盲重试。
- 执行适配器把真实 session_id、provider、实际 Bot/owner、运行时阶段及必要运行句柄写入受控记录。
- 验收：重建进程可读完整轮次及会话路由；创建失败无孤立初轮；同请求不重复创建或投递；未创建 session 不生成伪 session_id。

### D. L6 ProductProfileService 及五方法

建议模块：`l6_products/product_profile_service/`，分别放 Protocol、service、YAML 校验和窄 ports；API 重导出与 DI 同步增加。

| 方法 | 必须完成的行为 |
| --- | --- |
| create_product_profile | 必填 name/composition/stages；授权 + 联合校验 Method 组合与 YAML + 持久化并返回完整 ProductProfile；幂等内容包括 composition 与 stages。 |
| create_product_collet | 精确 Profile + 按 composition 初始化 Method 绑定/关系 + 按 YAML 创建 Collet/首轮/阶段 + 记录推进意图；返回 Collet，不要求调用者再调用公开 create_rollout。 |
| query_collet_rollout | all_rollout=true 返回全部，false 返回当前，均为 list[ColletRollout]；纯查询不补建轮次。 |
| interactive | 精确轮次和 revision 校验；四类调整，创建后继轮次或停止；返回 InteractiveResult。 |
| query_stage_rollout_details | 服务端读 Stage.session_id 并调用真实会话消息 Protocol；返回消息明细，禁止用协作 Events/摘要替代。 |

- 五方法都在显式 ProductProfileServiceProtocol 中定义，由 ProductProfileService 实现。
- create_product_profile 改成受权的持久化创建，迁移旧纯 authoring/独立发布调用方，不能绕过管理权限。
- 独立报告 ingress 与内部 create_collet_rollout 不成为该类第六/第七个公共业务方法。
- 验收：DI 解析正确、无实现反向 import、五方法签名及结果类型可检验；旧 facade 只作兼容调用方；阻塞能力不堵 HTTP event loop。

### E. 上报、干预及真实会话明细闭环

- 事件入口接纳 ProgressEvent 三类型；Artifact/AcceptanceResult 持久化并维护图示状态，运行完成用真实运行事实而非 dispatch 返回。
- 校验条件覆盖、可信验收者、实际分配/attempt/lease，保留无产物运行失败；回执支持成功和失败事实的幂等。
- interactive 的 OPTIMIZE/RE_DO 失效目标与后继，保留旧轮次，使用新 Stage ID/会话；失败轮次可受权创建后继。
- SHUTDOWN 单独保存停止事实，阻断调度并按能力取消远端；不伪装成 RUN_FAILED、不擅自新增状态枚举。
- 消息详情优先复用 OpenApiBotPort/BcsClientPort；按真实 session/provider 查询，精确路由，未绑定与上游失败分开。
- 验收：明确证明会话 port 被调用且参数等于已保存 session_id；越权时无调用；不是 mock 一个消息数组就宣称跑通真实会话链路。

### F. 迁移、门禁与知识更新

- 更新 codecs/仓储/产品配置/HTTP schemas/Service API/Plugin contracts/DI/架构 gate 与受影响客户端。
- 为旧模型及 wire 字段制定有期限 adapter；新域最终移除 ProductProfileDefinition，不并存两个含义不同的 ProductProfile/Artifact。
- 保留旧事实与显式迁移结果；不可表达的老配置/产物不静默丢失、填默认验收通过或假造历史会话。
- 停止旧 writer，优先排空进程内在途执行；只有完整可信快照才能迁移恢复。
- 完成目标数据库 DDL/事务/collation、真实执行/会话集成、相关架构 gate、公开 HTTP 快门禁与 Backend 完整 CI/coverage。
- 实施后再更新模块 README、L5/L6 执行/存储指南和采用的 ADR；本次文档不把目标伪装为现状。

## 3. 必须覆盖的验收矩阵

| 场景 | 结果 |
| --- | --- |
| 单条件 mapping、多条件列表 YAML | 两种均接受，内部统一条件集合，保存的 stages 仍是 YAML。 |
| composition + stages 保存、读取与创建 Collet | 完整往返且配置冻结；绑定/Method 关系来自 composition，阶段 DAG 来自 YAML。 |
| composition 缺失/非法、policy 不匹配、重复 DAG 冲突 | 拒绝创建；不暗加 Method、不静默覆盖任一配置。 |
| optional methods 声明但未显式选用 | 不因 YAML 中存在阶段/候选而激活，也不创建对应绑定。 |
| 同一 request_id 仅改变 composition | 幂等冲突，不返回原 Profile 或覆盖旧配置。 |
| 创建 Profile 后直接创建 Collet | 新流程不要求额外拿 Definition/publish；管理权限必须先验证。 |
| 错误 YAML/未知能力/未授权配置管理 | 不创建可运行 Profile 或副作用。 |
| 创建 Collet 返回前事务失败 | Collet/初轮/阶段/回执无部分成功；外部派发尚未执行。 |
| 同请求创建或干预重放 | 返回原对象/回执，不再建 ID、不再派发。 |
| query_collet_rollout true/false | 分别全部与当前；历史状态/产物保持，不按列表顺序猜当前。 |
| Stage 尚无 session_id | messages 为空，响应明确未绑定，不合成逻辑 session。 |
| 查询已绑定真实会话 | 调用匹配 provider 的消息 port，保留实际消息明细和顺序范围限制。 |
| 任意 session 注入、跨租户/阶段越权 | 查询入口不接受任意 session_id；越权不调用 provider。 |
| 消息 provider 超时/缺会话/不支持 | 明确错误，不返回假空消息或 Event-backed fallback。 |
| RUN_FAILED 无产物 | 有错误原因且身份合法则接纳失败事实。 |
| 验收成功缺条件、未知条件、伪造 passed | 拒绝推进，真实校验不能被正文绕过。 |
| File/Link/Text content 往返或混合字段 | 合法三类保持字段；混合/未知子类拒绝；内容不在日志泄露。 |
| 初轮验收失败 → interactive 重跑 | 新 Rollout/Stage ID 和正确输入；旧事实不改。 |
| 旧轮次未见过的迟到事件 | 拒绝，不能污染当前阶段及整体状态。 |
| report 与 interactive/停止竞争 | 持久化 revision 上唯一裁决；已接纳事实不被覆盖。 |
| 全部阶段通过但旧产品 root 尚待 owner 验收 | 轮次通过，不冒充根 Task 已接受。 |
| 重启只有 SQL child Task、缺治理/Method 事实 | fail closed，不合成可恢复执行。 |

## 4. 设计阶段验证与待评审项（历史）

本轮只执行文档级结构/链接/YAML/类型草图检查，结果见 [research.md](research.md)。前一轮 48 项通过仅代表旧代码局部基线，不代表本方案对象或五个方法已经有运行测试。

实施前仍需固定：新旧状态权威与 Work 适配、旧复杂 Profile 的可迁移集合及 SwarmWorkflowPolicy 的具体适配形态、目标 DDL、跨 provider 消息范围及排序能力、停止后用户展示信息。已确定的领域字段和五方法职责不再被这些技术待决项替换回初稿设计。


## 5. 开发阶段验收导航

新的五方法/领域对象、事务/CAS/幂等、Method 组合、YAML 图、独立进度接纳、干预后继与真实会话端口的源码及测试均见 [delivery.md](delivery.md)。该报告区分本地验证、真实 provider/目标数据库验证、旧 facade 切换和部署，不以新测试通过掩盖 F 中尚未完成的迁移项。
