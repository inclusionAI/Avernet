# Monitoring diagnosis-event/v4：标签与 Action 分离

更新：2026-10-26。状态：本地实现；未提交、未部署、未调用真实自愈。
工作树：`feat/monitoring-runtime-identity`。本文扩展 v3，不改变运行身份安全契约。

## 1. 统一对外定义

- 任务是否完成：截至诊断截止时间，诊断范围内用户要求 Bot 完成的任务，是否已经按要求完成。
- 是否人工纠错：诊断范围内，用户是否发出过针对 Bot 此前响应或执行的纠正、返工或调整指令。正常新任务、补充信息、授权、审批及确认不属于人工纠错。
- TC Code：有证据支持的范围内问题归因，不等同当前未恢复状态。
- `UNRESOLVED`：诊断失败；`ALERT`：诊断成功且任务未完成且有合法 TC；其余成功为 `PASS`。
- 实时自愈业务资格：诊断成功、任务未完成、无人工纠错、有合法 TC。仅发送前计算，不新增持久标签。

实时诊断当前轮次；历史诊断其窗口内的有效请求。二者范围不同，公式相同；历史不请求自愈。人工纠错/Biz_Tag 不参与告警判定，Biz_Tag 暂不参与自愈判定。

PASS 不是“无故障”或“已完成”。过程发生问题而最终完成任务，可为 PASS + Code + confidence + Biz_Tag。未完成但没有可支持 Code，也是 PASS。不能通过清空 Code 或伪造完成标签改变 Action。

## 2. 传输和验证

新写 schemaVersion：`claw-monitoring/diagnosis-event/v4`，对应 `diagnosis-actions/v1`。

- 沿用完整 v3 字段集，所有键必需，runtimeIdentity 为平铺字符串对象或 null；保持 botId/entityId/env 复合身份。
- 成功标签 handlerName 仅允许 COMPLETED / NOT_COMPLETED，humanIntervention 为严格布尔。
- tcFaultLabel 必须属于 `tc-catalog.ts` 固定的 67 个合法 Code；有 Code 必有有限 `[0,1]` confidence，没有 Code 则 confidence=null。
- 服务端重算 Action 并核对；矛盾事件拒绝，不落库、不触发自愈。
- UNRESOLVED 的 handlerName、Code、confidence 和 finding 业务分类均为 null；humanIntervention=false 为兼容占位，UI 展示“未判定”。
- 生产者负责原始证据引用及业务分类组合的验证；HTTP 层不声称验证未传入的原始证据。
- v2/v3 按原分支验证，v1 仅保留已有读取，不开放新的旧版本写入。
- bot-check、身份解析、鉴权、授权范围与 ACK 格式不变。

`action-policy.ts` 是无 I/O 的策略模块；`validation.ts` 负责 DTO 完整性和一致性；repository 原样存储标签及 schemaVersion，浏览器 DTO 不泄露 runtimeIdentity。

## 3. 自愈发送与幂等

`session-recovery-trigger.ts` 在现有白名单与身份校验前应用资格筛选：

| 场景 | 跳过原因 |
|---|---|
| 旧事件 | legacy_policy |
| 诊断失败 | diagnosis_unresolved |
| 已完成 | task_completed |
| 无合法 Code | no_tc_code |
| 用户人工纠错 | human_correction |
| 标签组合不合法（防御性） | invalid_tags |

仅 v4 未完成、无纠错、有 Code 的 ALERT 继续走白名单/身份/任务创建。优先返回的原因只用于定位，不改变公式；日志不记录运行凭据。

重复上报仍会调用 trigger，补偿“诊断落库成功、首次任务创建失败”的情况；保持稳定 delivery key，由既有任务服务幂等。不允许改成“只有首次插入触发”。拒绝冲突重放、权限隔离和失败传播保持原样。

OC v4 与 v3 一样传完整 `target.runtime_identity`，不改写监控事件身份，不用 traceId 冒充运行身份。TE 不因此伪造 OC 身份。

2026-10-26 已 fetch 并核对 `origin/dev@6cd36d885`：接收端自 `dfe3be1e4`（2026-10-22）已支持 `target.runtime_identity`，`75d32bdde` 还补充了集成交接测试。此前基于旧工作树 `91b860c76` 得出的“不支持”结论撤回。fetch 仅更新远端引用，未合并/变基含未提交改动的工作树；最终集成需基于最新代码重跑测试，不能把旧检出的拒绝行为当作当前产品限制。

运行中人工纠错导致暂停的执行器逻辑未改变；入口资格测试不等同执行中竞态暂停的生产验收。

## 4. 前端与统计

- “是否人工干预”改为“是否人工纠错”，内部字段不改名。
- PASS 展示“不告警”，UNRESOLVED 展示“未判定”。失败占位 false 不显示为“无纠错”。
- PASS 的 Code、归因置信度、业务分类照常展示。
- 详情标注“统一口径 v1”或“旧口径 / 版本未知”；旧结果不重新计算。
- 新旧策略分开统计；诊断覆盖、曾有 Code、当前 ALERT、自愈资格、已创建、已执行、已修复不是同一指标。已有 session 去重口径也不能和新轮次统计直接比较。

## 5. 实时自愈轮次过滤

实时保留已有的诊断前过滤：仅检查当前轮首个 user/human 输入的 `[会话自愈` 前缀（允许已有渠道包络）。命中后将内部作业记为 `SKIPPED / SELF_HEALING_ORIGIN`，不调用诊断模型，不生成诊断结果、投影、上报或告警，不触发自愈，也不在 ClawInsight 展示为 UNRESOLVED。它不计入诊断覆盖或诊断失败；仅保留内部跳过记录。

这是沿用发送端标记的业务约定，不是发送者身份认证。本期不新增可信来源字段，也不引入来源不明降级状态。assistant/tool 文本、嵌入历史、正文中引用该标记或后续用户消息均不能导致整个当前轮次被跳过；不会排除整个 session。历史仍可检测自愈消息，但消息本身不证明已执行或已修复，也不能抹去真实人工纠错。

## 6. 测试与发布

共享 `server/fixtures/monitoring/action-policy-v1.json` 与 CV 一致，覆盖失败及 8 个标签组合；fixture SHA-256：
`c93d606b4a1b18d571e7f7c1eb2b2568ab6c72a5b1ec3c94df282b272a633bd9`。

覆盖：版本专用解析、Code/confidence/Action 矛盾、PASS 标签、纠错跳过、旧事件存储 ACK、重复/并发/丢 ACK/创建失败重试、运行身份保密、浏览器展示。
本地命令：`npm run test:monitoring`、`npm run check`、`npm run build`、`npm run test:monitoring-preview`、`npm run check:monitoring-preview`、`npm test`。

先接收/UI 后生产者；过渡阶段旧事件不再触发自动自愈，必须明确安排切换窗口。CV 日级历史默认 file-only，只有经确认的新口径独占目标才允许写 ODPS。
回滚先暂停新生产或现有白名单中的自动自愈，保留可读 v4 的服务及 outbox 原始字节；不得改写协议版本来重新投递。最新基线集成回归和真实执行链路验收完成前不宣称可全量上线。
