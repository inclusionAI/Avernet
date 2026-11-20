# FSM 群 A2A：D0 合同决策（2026-11-24）

状态：**五项设计方向已选定，物理协议待验证，D0 未冻结**。本文细化[讨论稿](spec.md)，验证工作归入[实施计划](plan.md)的 T1～T9。以下记录和字段是设计，不是 `dev` 已交付的 API。[#4245 固定版本的受理说明](https://github.com/inclusionAI/Avernet/blob/c7187edcc99354f017f2e1a9bfcb3d6e88c15b96/apps/bcs/crates/services/bcs-dist-store/src/inbox/README.md)区分 input 文档、schedule、checkpoint 和 Inbox；现行 input 级 CAS 本身不能裁决不同 input 争用同一 Sid。

## 1. 受理：在 `ckpt:<sid>` 上安装一轮，而非另加 Session 锁

严格入口通过 Worker 受理服务写入；Gateway 不写 checkpoint。选用 checkpoint 的精确版本 CAS，校验当前写者的 owner fence、前轮状态和代次。同一次 CAS 安装新 `generation`、`Tid`、原请求引用、`Accepted` 许可和待调度义务。只有这次成功才算受理，也是 Get/List 可见资格的起点。`schedule:<sid>`、Inbox 和索引各自可补写，均不能代替该许可。通用 JSON-RPC 路径不改。

| 同一 Sid 上的持久切面 | 宕机或竞争后的行为 |
| --- | --- |
| 原请求已准备，受理 CAS 尚未确认 | 无许可则不运行、不公开。用原请求身份读回裁决；不能因超时判定未写。准备须绑定预期空闲代次/版本，不是等候下一代的排队票。 |
| A 赢得 CAS，尚未唤醒 Worker | 同请求重试返回原 Tid；不同请求 B 见 A 占用得 BUSY。接任者补发 A 的 Start/唤醒，执行前校验许可、Tid 和代次；物理重复不产生第二轮。 |
| B 的条件写失败 | 读回区分自己的成功、其他请求胜出和单纯的版本冲突。争用输家不获 Task；废弃该次准备，旧准备即使晚于 A 完成也不能被后台升级。调用方可在空闲后用 B 的 messageId 重新提交。 |

**冻结前必证（T1/T2）：** 首次无 checkpoint 的受保护创建、无活跃 owner 时的受理与唤醒、旧 owner 在交接后迟到写。须列出如何发现已提交却从未发出 hint 的 Sid，以及旧请求如何在多轮后按 Tid 找回许可；不假设多键事务。若 #4245 的 owner 路径不能支撑此协议，阻塞 T2/T7 并重评写者，不降级为 Gateway 先查后写。

## 2. 重试：按消息定位，按首次依据判等

先检查当前权限，再按 `(tenant, principal, group, messageId)` 查准备、已受理记录或过期墓碑。服务端分配的 Sid/Tid 不进查找键。首次保留客户端显式字段（特别是 context 有无）、有序 Parts，以及首次解释这些字段的 schema、默认值、有效配置、扩展和语义摘要。命中时用首次依据解释重试；只有确认未命中才验证当前发布能力。并发准备的输家重读赢家，存储故障返回依赖错误。

T1 固定向量：首轮未给 context 且丢回包，部署改默认值后原样重发仍取 A；显式改 context、输出配置或有序 Parts 造成语义改变则冲突；撤权后不可读；结果过期则报过期不重跑；BUSY 不消耗消息键。不得把“准备中尚未裁决”当作成功、过期或新请求。

## 3. 成功：归档先写，`Completed` 一次提交，不等通知

1. Worker 只取 `final_output`，按 Tid 固定完整公开表示、校验预算并幂等归档，读回核对摘要。此时无终态证明则 Get 不得公开成功，B 仍忙。
2. 持有 fence 的 Worker 在 A 的精确代次上一次提交 `Completed`、终态证明（含归档引用与摘要）及首次 Feed 写许可。该状态本身允许 B 立即竞争；不存在另一次解锁。提交回包丢失只读回/补原决定，不重跑 A。
3. B 推进不得覆盖 A 的证明或未兑现的发布许可。证明按 Tid 转存并读回相符后才可压缩；`AppendEvents` 与 `PublishSnapshot` 各有持久许可，分别绑定事件身份/内容/Sid 前驱和快照摘要/source_commit/snapshot_event_id。Publisher 先按前驱 append 再 publish，两步均不阻塞 B 的业务派发；公共 Get 不依赖 feed 快照。T7 故障验收让 A 首次 append 失败、B 已派发后恢复，再在 A append 成功但 publish 前宕机；两步分别读回完成后才清许可和 pin，不靠当前 checkpoint 授权旧快照，不重复、不越序。
4. Dispatch 目录回收同时修改扫描合同：使用稳定单调键、每轮上界和版本化游标，不按会收缩的数组下标续扫；保护当前扫描 work，兼容转换须允许受保护的幂等重扫。测试删除游标之前的已完成项并重启，存活调用仍能发现和接管；不得仅提高容量或全量重扫历史。

## 4. 下游：分开判断可重发、可查询、已停止

首次发送前按节点 attempt 固定操作、目标去重域、稳定请求 ID、有效截止时间、正文及发送配置摘要；首次受理后绑定可核验的远端 Task/context。每次操作从可信 scope、群、轮次、节点、attempt、目标和操作重新检查授权。冻结绑定不等于沿用旧权限。

- 有未送达证明，或仍在原去重域保证窗口内且身份、操作、内容不变，才可在获准后重发。只有查询能力则只查原调用；暂时 NotFound 不证明未送达或停止。
- 只有与原目标、远端引用及 attempt/操作相符的终止事实才证明该 attempt 已停；不能替其他分支作证。首批要求能按原 Task 取回有序完整产物。正常流的 Completed 和断流接管都先核验停止，再用完整 GetTask 校验结果清单/摘要与入站预算、持久固定后首次报告产物 Final；不先固定残缺 Final 再尝试覆盖。不完整或超限的结果走有界失败，已核验的停止事实单独保留。
- T6/T7 采用包含 #4245 配套 Connector/Inbox 的联合版本，以 `ConnectorAdmission` / `admit_connector` 交付稳定终态身份；不得把 dev 通用 admit 直接接到该 Inbox。真实 GetTask 无远端事件 ID 的完成结果必须经过真实 Inbox 到达 Runner；重复、丢报告响应仍只应用一次。
- Worker shard Supervisor 接管已 ACK 后死 holder 的调用，provider 必须支持固定版本的 `RetainedLease`，重启后保留 Terminated 供判定 Dead，不把 Absent 当 Dead。短期查询耗尽时在权威 Dispatch 上以 CAS 登记核实模式、独立 claim epoch 和可修复的调度义务，使普通 claim 失效后才释放槽；原进程存活、旧 hint 重投也不得重新拉起普通 actor。核实者持独立 fence 仅执行获准 query/cancel 和报告，旧 holder 写被拒。
- 当前真实 A2A 的 ByRequest 不支持反查；按请求恢复须有明确扩展/adapter 交付及真实 HTTP 验收。扩展 profile 测索引迟到后主动发现，标准 profile 测安全保持未知且不重发；不能以假 port 宣称支持。停止仍未知则按 spec 的核实期与隔离规则对外，不因 deadline、重启或普通管理员解锁提交终态。

## 5. 读取：按证明 Get，按同一版本 List，联冻入站、内部与公开预算

每次读、订阅、翻页或取消都校验当前资源来源和相应权限。撤销发布阻止新调用，既有资源仍按当前权限访问；撤销读取权限则旧游标、订阅也不能继续披露。Get 校验本 Tid 的证明与归档，不等待列表索引。

List 首页取得按授权群过滤、按 Tid 唯一的版本化快照，行、total 与状态都按该 cut，排序 `(status_updated_at DESC, tid ASC)`；游标绑定主体、群、筛选、排序、cut、末键和有效期。**T1 存储原型**须证明：查询开始前已公开的提交均被 cut 覆盖，即使部署有多个写者、索引异步且热数据正转冷。有界等待后仍不能证明则返回可重试依赖错误，不返回过时 total。不能仅凭索引“最近更新时间”宣告追平。

公开响应/单 SSE 帧暂以 **8 MiB 为候选**；#4245 内部 Feed 为 128 KiB/事件、256 KiB/批，Worker 入站解引用另受 8 MiB 的 `PHASE_ONE_MAX_INPUT_PAYLOAD_BYTES` 限制。D0 同时冻结下游 IO/报告/Worker 解引用与公开编码预算；中间节点也受入站限额约束，小最终结果或引用外壳不能豁免。超限须保留独立核验的停止事实，再交付有界错误经过停止屏障，不走损坏隔离或静默截断。测试大中间产物、小候选最终产物，以及入站边界值。大公开产物在 Feed 内使用带摘要的不可变引用，保护到 append/publish 均完成；提交前分别验证入站合同、实际存储编码和完整 wire 表示，失败不提交 `Completed`。D0 冻结前须签定权限矩阵与撤权生效点、游标期限、默认及非默认投影的实际预算；不把候选值写成已上线配置。

**评审顺序：** 先用同一对 A/B 走通第 1、3 项每个写入窗口，再验证第 5 项的快照覆盖证明；第 2、4 项可并行固定重试和远端证据向量。上述验证尚未执行。真实 provider、跨进程接管和发布验收仍按 plan 单独交付，不以 D0 评审替代。
